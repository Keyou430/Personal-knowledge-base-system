from pathlib import Path

import pytest

from core.document_store import DocumentStore
from core.ingestion import ingest_file
from core.metadata import (
    MetadataValidationError,
    execute_batch,
    normalize_metadata,
    preview_batch,
)


class FakeVectorStore:
    def __init__(self):
        self.added = {}

    def add_documents(self, documents, ids=None):
        for index, document in enumerate(documents):
            self.added[(ids or [None])[index]] = document

    def delete(self, ids):
        for item in ids:
            self.added.pop(item, None)


def write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def seed_document(store: DocumentStore, *, name: str, content: str):
    source = write_text(store.database_path.parent / name, content)
    return ingest_file(
        source,
        domain="制度",
        store=store,
        vectorstore=FakeVectorStore(),
        category="制度",
        owner="财务部",
        source="upload",
    ).document


def test_metadata_defaults_are_explicit_and_invalid_values_are_rejected():
    metadata = normalize_metadata(category="  ", owner=" 财务部 ", source="  ")

    assert metadata.category == "其他"
    assert metadata.owner == "财务部"
    assert metadata.source == "upload"
    assert metadata.warnings == (
        "分类为空，使用默认值“其他”",
        "来源为空，使用默认值“upload”",
    )
    assert metadata.updated_at.endswith("+00:00")
    assert normalize_metadata(category="制度", owner="", source="upload").warnings == (
        "责任人未填写",
    )

    with pytest.raises(MetadataValidationError, match="责任人不能包含换行"):
        normalize_metadata(category="制度", owner="财务\n部", source="upload")
    with pytest.raises(MetadataValidationError, match="版本不能包含换行"):
        normalize_metadata(
            category="制度", owner="财务部", source="upload", version="1\n2"
        )
    with pytest.raises(MetadataValidationError, match="更新时间必须包含时区"):
        normalize_metadata(
            category="制度",
            owner="财务部",
            source="upload",
            updated_at="2026-08-27T10:00:00",
        )


def test_batch_preview_is_read_only_and_classifies_all_items(tmp_path):
    store = DocumentStore(tmp_path / "documents.db")
    seed_document(store, name="重复.md", content="# 重复\n相同内容")
    old = seed_document(store, name="替换.md", content="# 旧版\n旧内容")
    duplicate = write_text(tmp_path / "重复.md", "# 重复\n相同内容")
    replacement = write_text(tmp_path / "替换.md", "# 新版\n新内容")
    fresh = write_text(tmp_path / "新增.md", "# 新增\n新内容")
    unsupported = write_text(tmp_path / "脚本.exe", "not supported")
    before = store.list_documents(include_superseded=True)

    preview = preview_batch(
        [duplicate, replacement, fresh, unsupported],
        domain="制度",
        store=store,
        category="制度",
        owner="财务部",
        source="upload",
    )

    assert [item.action for item in preview.items] == [
        "duplicate",
        "replace",
        "new",
        "unsupported",
    ]
    assert preview.items[1].proposed_version == "2"
    assert preview.items[1].existing_document_id == old.id
    assert preview.items[2].proposed_version == "1"
    assert preview.accepted_count == 2
    assert store.list_documents(include_superseded=True) == before


def test_batch_preview_identifies_duplicate_content_inside_batch(tmp_path):
    store = DocumentStore(tmp_path / "documents.db")
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = write_text(first_dir / "第一份.md", "# 相同\n批次内容")
    second = write_text(second_dir / "第二份.md", "# 相同\n批次内容")

    preview = preview_batch(
        [first, second],
        domain="制度",
        store=store,
        category="制度",
        owner="财务部",
        source="upload",
    )

    assert [item.action for item in preview.items] == ["new", "duplicate"]
    assert preview.items[1].reason == "与批次内第一份.md内容重复"
    assert preview.accepted_count == 1

    first.write_text("预览后变化", encoding="utf-8")
    report = execute_batch(preview, store=store, vectorstore=FakeVectorStore())
    assert report.retry_needed == ["第一份.md", "第二份.md"]
    assert report.failures[1]["reason"] == "依赖的批次文件未成功入库: 第一份.md"


def test_batch_execution_continues_after_changed_file_and_preserves_old_active(tmp_path):
    store = DocumentStore(tmp_path / "documents.db")
    old = seed_document(store, name="替换.md", content="# 旧版\n旧内容")
    replacement = write_text(tmp_path / "替换.md", "# 新版\n新内容")
    fresh = write_text(tmp_path / "新增.md", "# 新增\n可入库")
    preview = preview_batch(
        [replacement, fresh],
        domain="制度",
        store=store,
        category="制度",
        owner="财务部",
        source="upload",
    )
    replacement.write_text("预览后被修改", encoding="utf-8")

    report = execute_batch(preview, store=store, vectorstore=FakeVectorStore())

    assert report.successes == ["新增.md"]
    assert report.retry_needed == ["替换.md"]
    assert report.failures[0]["reason"] == "文件在预览后发生变化"
    assert store.find_active("制度", "替换.md").id == old.id
    assert store.find_active("制度", "新增.md") is not None


def test_batch_execution_is_idempotent_and_commits_reviewed_metadata(tmp_path):
    store = DocumentStore(tmp_path / "documents.db")
    source = write_text(tmp_path / "制度.md", "# 第一版\n内容")
    first_preview = preview_batch(
        [source],
        domain="制度",
        store=store,
        category=" 规章 ",
        owner=" 财务部 ",
        source=" local upload ",
    )

    first = execute_batch(first_preview, store=store, vectorstore=FakeVectorStore())
    duplicate_preview = preview_batch(
        [source],
        domain="制度",
        store=store,
        category="规章",
        owner="财务部",
        source="local upload",
    )
    source.write_text("预览后变化", encoding="utf-8")
    duplicate = execute_batch(duplicate_preview, store=store, vectorstore=FakeVectorStore())

    saved = store.find_active("制度", "制度.md")
    assert first.successes == ["制度.md"]
    assert duplicate.duplicates == []
    assert duplicate.retry_needed == ["制度.md"]
    assert duplicate.failures[0]["reason"] == "文件在预览后发生变化"
    assert saved.category == "规章"
    assert saved.owner == "财务部"
    assert saved.source == "local upload"
    assert saved.version == "1"
    assert saved.updated_at == first_preview.items[0].updated_at
    assert len(store.list_documents(include_superseded=True)) == 1
