import json
from pathlib import Path

import pytest

from core.directory_sync import (
    SyncConfigError,
    apply_sync,
    preview_sync,
    run_sync,
)
from core.document_store import DocumentStore


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_dry_run_reports_new_without_writing_archive_or_manifest(tmp_path):
    source = tmp_path / "source"
    _write(source / "01-制度.md", "# 报销\n需要审批")
    db = tmp_path / "documents.db"

    report = run_sync(source=source, domain="制度", database=db)

    assert report.status == "preview"
    assert report.counts["new"] == 1
    assert not db.exists()
    assert report.items[0].relative_path == "01-制度.md"
    assert str(source) not in json.dumps(report.as_dict(), ensure_ascii=False)


def test_apply_is_idempotent_and_detects_changed_file(tmp_path):
    source = tmp_path / "source"
    path = source / "制度.md"
    _write(path, "# 第一版\n审批")
    db = tmp_path / "documents.db"

    first = run_sync(source=source, domain="制度", database=db, apply=True)
    assert first.status == "applied"
    assert first.counts["new"] == 1
    store = DocumentStore(db)
    assert len(store.list_documents(domain="制度")) == 1

    unchanged = run_sync(source=source, domain="制度", database=db, apply=True)
    assert unchanged.counts["unchanged"] == 1
    assert len(store.list_documents(domain="制度", include_superseded=True)) == 1

    _write(path, "# 第二版\n需要复核")
    changed = run_sync(source=source, domain="制度", database=db, apply=True)
    assert changed.counts["changed"] == 1
    assert len(store.list_documents(domain="制度", include_superseded=True)) == 2
    assert store.find_active("制度", "制度.md").version == "2"


def test_missing_and_restore_are_non_destructive_and_filter_retrieval(tmp_path):
    source = tmp_path / "source"
    path = source / "制度.md"
    _write(path, "# 保留\n仅供测试")
    db = tmp_path / "documents.db"
    run_sync(source=source, domain="制度", database=db, apply=True)
    path.unlink()

    missing = run_sync(source=source, domain="制度", database=db, apply=True)
    assert missing.counts["missing"] == 1
    store = DocumentStore(db)
    document = store.find_active("制度", "制度.md")
    assert document is not None
    assert document.source_present is False
    assert store.search_keyword("保留", domain="制度") == []

    _write(path, "# 保留\n仅供测试")
    restored = run_sync(source=source, domain="制度", database=db, apply=True)
    assert restored.counts["restored"] == 1
    assert DocumentStore(db).find_active("制度", "制度.md").source_present is True
    assert DocumentStore(db).search_keyword("保留", domain="制度")


def test_rename_is_new_source_plus_missing_old_source(tmp_path):
    source = tmp_path / "source"
    old = source / "old.md"
    _write(old, "# 同一内容")
    db = tmp_path / "documents.db"
    run_sync(source=source, domain="默认", database=db, apply=True)
    old.rename(source / "new.md")

    report = run_sync(source=source, domain="默认", database=db, apply=True)
    assert report.counts["new"] == 1
    assert report.counts["missing"] == 1
    store = DocumentStore(db)
    assert store.find_active("默认", "new.md") is not None
    assert store.find_active("默认", "old.md").source_present is False


def test_apply_rejects_file_changed_after_preview(tmp_path):
    source = tmp_path / "source"
    path = source / "制度.md"
    _write(path, "# 原内容")
    db = tmp_path / "documents.db"
    preview = preview_sync(source=source, domain="制度", database=db)
    _write(path, "# 预览后变化")

    report = apply_sync(preview, database=db, source=source)

    assert report.counts["failed"] == 1
    assert "preview_changed" in report.items[0].reason_codes
    assert not DocumentStore(db).list_documents()


def test_apply_requires_the_same_source_directory_as_preview(tmp_path):
    source = tmp_path / "source"
    other_source = tmp_path / "other-source"
    _write(source / "one.md", "one")
    _write(other_source / "one.md", "one")
    db = tmp_path / "documents.db"
    preview = preview_sync(source=source, domain="默认", database=db)

    with pytest.raises(SyncConfigError, match="preview_source_mismatch"):
        apply_sync(preview, database=db, source=other_source)


def test_lock_conflict_is_configuration_error(tmp_path):
    source = tmp_path / "source"
    _write(source / "one.md", "one")
    lock = tmp_path / "sync.lock"
    lock.write_text("existing", encoding="utf-8")

    with pytest.raises(SyncConfigError):
        run_sync(source=source, domain="默认", database=tmp_path / "documents.db", lock_file=lock)
