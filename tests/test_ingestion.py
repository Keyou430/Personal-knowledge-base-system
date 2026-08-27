from pathlib import Path

import pytest

import core.ingestion as ingestion
from core.document_store import DocumentStore
from core.ingestion import ingest_file


class FakeVectorStore:
    def __init__(self, fail=False):
        self.fail = fail
        self.added = {}
        self.deleted = []

    def add_documents(self, documents, ids=None):
        if self.fail:
            raise RuntimeError("vector backend unavailable")
        for index, document in enumerate(documents):
            chunk_id = (ids or [None] * len(documents))[index]
            self.added[chunk_id] = document

    def delete(self, ids):
        self.deleted.extend(ids)
        for chunk_id in ids:
            self.added.pop(chunk_id, None)


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_ingest_file_is_idempotent_and_replaces_changed_content(tmp_path):
    source = tmp_path / "制度.md"
    write_text(source, "# 报销\n第一版内容")
    store = DocumentStore(tmp_path / "documents.db")
    vectorstore = FakeVectorStore()

    first = ingest_file(source, domain="制度", store=store, vectorstore=vectorstore)
    again = ingest_file(source, domain="制度", store=store, vectorstore=vectorstore)
    assert first.document.id == again.document.id
    assert len(store.list_documents(include_superseded=True)) == 1

    write_text(source, "# 报销\n第二版内容")
    second = ingest_file(source, domain="制度", store=store, vectorstore=vectorstore)
    assert second.document.version == "2"
    assert second.document.id != first.document.id
    assert vectorstore.deleted
    assert all(chunk_id.startswith(f"{second.document.id}:2:") for chunk_id in vectorstore.added)
    assert store.search_keyword("第二版", domain="制度")


def test_ingest_file_keeps_sqlite_record_when_vector_projection_fails(tmp_path):
    source = tmp_path / "手册.txt"
    write_text(source, "故障排查步骤")
    store = DocumentStore(tmp_path / "documents.db")

    result = ingest_file(
        source,
        domain="手册",
        store=store,
        vectorstore=FakeVectorStore(fail=True),
    )

    assert result.document.status == "active"
    assert result.index_pending is True
    assert store.get(result.document.id).index_pending is True


def test_ingest_file_preserves_active_version_and_raw_file_when_raw_copy_fails(
    tmp_path, monkeypatch
):
    store = DocumentStore(tmp_path / "documents.db")
    first_source = tmp_path / "first" / "制度.md"
    first_source.parent.mkdir()
    write_text(first_source, "# 第一版\n旧内容")
    first = ingest_file(first_source, domain="制度", store=store, vectorstore=FakeVectorStore())
    raw_path = tmp_path / "raw" / "制度" / "制度.md"
    replacement = tmp_path / "replacement" / "制度.md"
    replacement.parent.mkdir()
    write_text(replacement, "# 第二版\n新内容")

    monkeypatch.setattr(
        ingestion.shutil,
        "copy2",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        ingest_file(replacement, domain="制度", store=store, vectorstore=FakeVectorStore())

    assert store.find_active("制度", "制度.md").id == first.document.id
    assert store.find_active("制度", "制度.md").updated_at == first.document.updated_at
    assert len(store.list_documents(include_superseded=True)) == 1
    assert raw_path.read_text(encoding="utf-8") == "# 第一版\n旧内容"
