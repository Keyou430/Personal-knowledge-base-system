import json
from pathlib import Path

import pytest

from core.backup import (
    BackupIntegrityError,
    create_backup,
    restore_backup,
    verify_backup,
)


def make_sources(tmp_path):
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw"
    (raw_dir / "policy").mkdir(parents=True)
    documents_db = data_dir / "documents.db"
    experiences_db = data_dir / "experiences.db"
    observability_db = data_dir / "observability.db"
    documents_db.write_bytes(b"documents-authoritative")
    experiences_db.write_bytes(b"experiences-authoritative")
    observability_db.write_bytes(b"observability-local")
    (raw_dir / "policy" / "policy.md").write_text("报销流程", encoding="utf-8")
    return data_dir, raw_dir, documents_db, experiences_db, observability_db


def test_verified_backup_restores_databases_and_original_materials(tmp_path):
    _, raw_dir, documents_db, experiences_db, observability_db = make_sources(tmp_path)
    backup_dir = tmp_path / "backup"

    created = create_backup(
        backup_dir,
        document_db_path=documents_db,
        experience_db_path=experiences_db,
        observability_db_path=observability_db,
        raw_dir=raw_dir,
    )

    assert created.valid is True
    assert verify_backup(backup_dir).valid is True
    destination = tmp_path / "restored"
    restored = restore_backup(backup_dir, destination)

    assert restored.valid is True
    assert (destination / "documents.db").read_bytes() == documents_db.read_bytes()
    assert (destination / "experiences.db").read_bytes() == experiences_db.read_bytes()
    assert (destination / "raw" / "policy" / "policy.md").read_text(encoding="utf-8") == "报销流程"
    assert (raw_dir / "policy" / "policy.md").read_text(encoding="utf-8") == "报销流程"


def test_restore_falls_back_when_directory_rename_is_denied(tmp_path, monkeypatch):
    _, raw_dir, documents_db, experiences_db, _ = make_sources(tmp_path)
    backup_dir = tmp_path / "backup"
    create_backup(
        backup_dir,
        document_db_path=documents_db,
        experience_db_path=experiences_db,
        raw_dir=raw_dir,
    )
    original_rename = Path.rename

    def deny_restore_rename(path, target):
        if path.name.startswith(".restored.restore."):
            raise PermissionError("simulated Windows directory lock")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", deny_restore_rename)

    restored = restore_backup(backup_dir, tmp_path / "restored")

    assert restored.valid is True
    assert (tmp_path / "restored" / "documents.db").read_bytes() == documents_db.read_bytes()


def test_integrity_failure_is_reported_before_existing_data_is_touched(tmp_path):
    _, raw_dir, documents_db, experiences_db, _ = make_sources(tmp_path)
    backup_dir = tmp_path / "backup"
    create_backup(
        backup_dir,
        document_db_path=documents_db,
        experience_db_path=experiences_db,
        raw_dir=raw_dir,
    )
    (backup_dir / "documents.db").write_bytes(b"tampered")
    destination = tmp_path / "existing"
    destination.mkdir()
    sentinel = destination / "documents.db"
    sentinel.write_bytes(b"active-local-data")

    report = verify_backup(backup_dir)
    assert report.valid is False
    assert "documents.db" in report.integrity_failures
    with pytest.raises(BackupIntegrityError):
        restore_backup(backup_dir, destination, confirm_overwrite=True)
    assert sentinel.read_bytes() == b"active-local-data"


def test_missing_backup_file_is_retry_needed(tmp_path):
    _, raw_dir, documents_db, experiences_db, _ = make_sources(tmp_path)
    backup_dir = tmp_path / "backup"
    create_backup(
        backup_dir,
        document_db_path=documents_db,
        experience_db_path=experiences_db,
        raw_dir=raw_dir,
    )
    (backup_dir / "raw" / "policy" / "policy.md").unlink()

    report = verify_backup(backup_dir)

    assert report.valid is False
    assert "raw/policy/policy.md" in report.missing
    assert "raw/policy/policy.md" in report.retry_needed


def test_malformed_manifest_is_reported_as_retry_needed(tmp_path):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "manifest.json").write_text("[]", encoding="utf-8")

    report = verify_backup(backup_dir)

    assert report.valid is False
    assert report.manifest_error == "manifest 必须是对象"
    assert report.retry_needed == ("manifest.json",)
