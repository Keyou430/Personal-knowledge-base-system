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
    assert vectorstore.added
    assert store.find_active("制度", "制度.md").index_pending is False
