from pathlib import Path

from core.document_store import DocumentStore
from core.migration import migrate_domain, rebuild_indexes


class FakeVectorStore:
    def __init__(self):
        self.added = {}

    def add_documents(self, documents, ids=None):
        for index, document in enumerate(documents):
            self.added[(ids or [None])[index]] = document

    def delete(self, ids):
        for item in ids:
            self.added.pop(item, None)


def test_migrate_domain_continues_after_bad_file_and_rebuild_keeps_raw_files(tmp_path):
    raw_dir = tmp_path / "raw" / "制度"
    raw_dir.mkdir(parents=True)
    good = raw_dir / "制度.md"
    good.write_text("# 报销\n提交发票。", encoding="utf-8")
    bad = raw_dir / "坏文件.docx"
    bad.write_bytes(b"not a docx")
    store = DocumentStore(tmp_path / "documents.db")
    vectorstore = FakeVectorStore()

    report = migrate_domain("制度", raw_dir=raw_dir, store=store, vectorstore=vectorstore)

    assert report.success_count == 1
    assert report.failure_count == 1
    assert good.exists() and bad.exists()
    assert store.find_active("制度", "制度.md") is not None

    rebuilt = rebuild_indexes(store=store, vectorstore=vectorstore, domains=["制度"])

    assert rebuilt.success_count == 1
    assert rebuilt.failure_count == 0
    assert rebuilt.recovered == ["制度.md"]
    assert rebuilt.missing == []
    assert rebuilt.retry_needed == []
    assert vectorstore.added
    assert store.find_active("制度", "制度.md").index_pending is False


def test_rebuild_reports_missing_chunks_as_retry_needed(tmp_path):
    raw_dir = tmp_path / "raw" / "制度"
    raw_dir.mkdir(parents=True)
    source = raw_dir / "制度.md"
    source.write_text("# 报销\n提交发票。", encoding="utf-8")
    store = DocumentStore(tmp_path / "documents.db")
    migrate_domain("制度", raw_dir=raw_dir, store=store, vectorstore=FakeVectorStore())
    document = store.find_active("制度", "制度.md")
    assert document is not None
    with store._connect() as connection:
        connection.execute("DELETE FROM document_chunks WHERE document_id = ?", (document.id,))

    report = rebuild_indexes(store=store, vectorstore=FakeVectorStore(), domains=["制度"])

    assert report.success_count == 0
    assert report.failure_count == 1
    assert report.missing == ["制度.md"]
    assert report.retry_needed == ["制度.md"]
    assert report.recovered == []
    assert store.find_active("制度", "制度.md").index_pending is True


class FailingVectorStore(FakeVectorStore):
    def add_documents(self, documents, ids=None):
        raise RuntimeError("向量库不可用")


def test_rebuild_reports_failed_projection_for_retry(tmp_path):
    raw_dir = tmp_path / "raw" / "制度"
    raw_dir.mkdir(parents=True)
    source = raw_dir / "制度.md"
    source.write_text("# 报销\n提交发票。", encoding="utf-8")
    store = DocumentStore(tmp_path / "documents.db")
    migrate_domain("制度", raw_dir=raw_dir, store=store, vectorstore=FakeVectorStore())

    report = rebuild_indexes(store=store, vectorstore=FailingVectorStore(), domains=["制度"])

    assert report.success_count == 0
    assert report.failure_count == 1
    assert report.missing == []
    assert report.retry_needed == ["制度.md"]
    assert report.recovered == []
    assert "向量库不可用" in report.failures[0]["reason"]


def test_rebuild_one_domain_preserves_other_keyword_projections(tmp_path):
    store = DocumentStore(tmp_path / "documents.db")
    policy_dir = tmp_path / "raw" / "制度"
    coding_dir = tmp_path / "raw" / "编程"
    policy_dir.mkdir(parents=True)
    coding_dir.mkdir(parents=True)
    (policy_dir / "制度.md").write_text("# 报销\n提交发票。", encoding="utf-8")
    (coding_dir / "编程.md").write_text("# Python\n使用 Python 函数。", encoding="utf-8")
    vectorstore = FakeVectorStore()
    migrate_domain("制度", raw_dir=policy_dir, store=store, vectorstore=vectorstore)
    migrate_domain("编程", raw_dir=coding_dir, store=store, vectorstore=vectorstore)

    rebuild_indexes(store=store, vectorstore=vectorstore, domains=["制度"])

    assert store.search_keyword("Python", domain="编程")
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM document_chunks_fts WHERE domain = ?", ("编程",)
        ).fetchone()[0] > 0
