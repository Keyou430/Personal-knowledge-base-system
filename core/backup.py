"""Recoverable local backups for authoritative records and original materials."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1


class BackupError(RuntimeError):
    """Base error for backup and restore operations."""


@dataclass(frozen=True)
class BackupVerificationReport:
    backup_path: Path
    valid: bool
    checked: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    integrity_failures: tuple[str, ...] = ()
    retry_needed: tuple[str, ...] = ()
    manifest_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "backup_path": str(self.backup_path),
            "valid": self.valid,
            "checked": list(self.checked),
            "missing": list(self.missing),
            "integrity_failures": list(self.integrity_failures),
            "retry_needed": list(self.retry_needed),
            "manifest_error": self.manifest_error,
        }


@dataclass(frozen=True)
class BackupReport:
    backup_path: Path
    manifest_path: Path
    files: tuple[str, ...]
    verification: BackupVerificationReport

    @property
    def valid(self) -> bool:
        return self.verification.valid

    def as_dict(self) -> dict[str, Any]:
        return {
            "backup_path": str(self.backup_path),
            "manifest_path": str(self.manifest_path),
            "files": list(self.files),
            "verification": self.verification.as_dict(),
            "valid": self.valid,
        }


@dataclass(frozen=True)
class RestoreReport:
    backup_path: Path
    destination: Path
    recovered: tuple[str, ...]
    missing: tuple[str, ...] = ()
    integrity_failures: tuple[str, ...] = ()
    retry_needed: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.missing and not self.integrity_failures and not self.retry_needed

    def as_dict(self) -> dict[str, Any]:
        return {
            "backup_path": str(self.backup_path),
            "destination": str(self.destination),
            "recovered": list(self.recovered),
            "missing": list(self.missing),
            "integrity_failures": list(self.integrity_failures),
            "retry_needed": list(self.retry_needed),
            "valid": self.valid,
        }


class BackupIntegrityError(BackupError):
    def __init__(self, message: str, report: BackupVerificationReport):
        super().__init__(message)
        self.report = report


class RestoreDestinationError(BackupError):
    """The requested destination needs explicit overwrite confirmation."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_path(value: str) -> str | None:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    return path.as_posix()


def _copy_file(source: Path, staging: Path, relative: str) -> None:
    target = staging / Path(*PurePosixPath(relative).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _publish_staging(staging: Path, destination: Path) -> None:
    """Publish a completed staging directory, tolerating transient Windows locks."""
    try:
        staging.rename(destination)
    except PermissionError:
        # File watchers can briefly deny a directory rename on Windows. Copying
        # keeps the operation recoverable when the atomic publish is unavailable.
        shutil.copytree(staging, destination)
        shutil.rmtree(staging, ignore_errors=True)


def _source_files(
    *,
    document_db_path: str | Path,
    experience_db_path: str | Path,
    observability_db_path: str | Path | None,
    raw_dir: str | Path,
) -> list[tuple[Path, str, str]]:
    sources = [
        (Path(document_db_path), "document_db", "documents.db"),
        (Path(experience_db_path), "experience_db", "experiences.db"),
    ]
    if observability_db_path is not None:
        optional = Path(observability_db_path)
        if optional.exists():
            sources.append((optional, "observability_db", "observability.db"))

    files: list[tuple[Path, str, str]] = []
    for source, kind, relative in sources:
        if not source.is_file():
            raise FileNotFoundError(f"备份源文件不存在: {source}")
        files.append((source, kind, relative))

    raw_root = Path(raw_dir)
    if raw_root.exists():
        for source in sorted(raw_root.rglob("*")):
            if source.is_file() and not source.is_symlink():
                relative = source.relative_to(raw_root).as_posix()
                files.append((source, "raw", f"raw/{relative}"))
    return files


def create_backup(
    backup_path: str | Path,
    *,
    document_db_path: str | Path,
    experience_db_path: str | Path,
    raw_dir: str | Path,
    observability_db_path: str | Path | None = None,
) -> BackupReport:
    """Copy local records and raw materials into a verified directory backup."""
    destination = Path(backup_path)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"备份目录不为空，拒绝覆盖: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_files = _source_files(
        document_db_path=document_db_path,
        experience_db_path=experience_db_path,
        observability_db_path=observability_db_path,
        raw_dir=raw_dir,
    )

    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        entries: list[dict[str, Any]] = []
        for source, kind, relative in source_files:
            _copy_file(source, staging, relative)
            copied = staging / Path(*PurePosixPath(relative).parts)
            entries.append(
                {
                    "path": relative,
                    "kind": kind,
                    "size": copied.stat().st_size,
                    "sha256": _sha256(copied),
                }
            )
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "created_at": _now(),
            "files": entries,
        }
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        verification = verify_backup(staging)
        if not verification.valid:
            raise BackupIntegrityError("备份校验失败", verification)
        if destination.exists():
            destination.mkdir(parents=True, exist_ok=True)
            for child in staging.iterdir():
                shutil.move(str(child), str(destination / child.name))
            shutil.rmtree(staging, ignore_errors=True)
        else:
            _publish_staging(staging, destination)
            staging = None  # type: ignore[assignment]
        return BackupReport(
            backup_path=destination,
            manifest_path=destination / MANIFEST_NAME,
            files=tuple(entry["path"] for entry in entries),
            verification=verify_backup(destination),
        )
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def verify_backup(backup_path: str | Path) -> BackupVerificationReport:
    """Validate every manifest entry without changing the backup or destination."""
    root = Path(backup_path)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return BackupVerificationReport(
            backup_path=root,
            valid=False,
            retry_needed=(MANIFEST_NAME,),
            manifest_error="缺少 manifest.json",
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest 必须是对象")
        if manifest.get("manifest_version") != MANIFEST_VERSION:
            raise ValueError("不支持的 manifest 版本")
        entries = manifest["files"]
        if not isinstance(entries, list):
            raise ValueError("manifest.files 必须是数组")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return BackupVerificationReport(
            backup_path=root,
            valid=False,
            retry_needed=(MANIFEST_NAME,),
            manifest_error=str(error),
        )

    checked: list[str] = []
    missing: list[str] = []
    integrity_failures: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            integrity_failures.append("manifest.files")
            continue
        relative = _relative_path(str(entry.get("path", "")))
        if relative is None:
            integrity_failures.append(str(entry.get("path", "")) or "manifest.files")
            continue
        checked.append(relative)
        target = root / Path(*PurePosixPath(relative).parts)
        if not target.is_file():
            missing.append(relative)
            continue
        expected_size = entry.get("size")
        expected_hash = entry.get("sha256")
        if target.stat().st_size != expected_size or _sha256(target) != expected_hash:
            integrity_failures.append(relative)

    retry_needed = list(dict.fromkeys([*missing, *integrity_failures]))
    return BackupVerificationReport(
        backup_path=root,
        valid=not missing and not integrity_failures,
        checked=tuple(checked),
        missing=tuple(missing),
        integrity_failures=tuple(integrity_failures),
        retry_needed=tuple(retry_needed),
    )


def restore_backup(
    backup_path: str | Path,
    destination: str | Path,
    *,
    confirm_overwrite: bool = False,
) -> RestoreReport:
    """Restore a verified backup to an explicit directory."""
    source = Path(backup_path)
    verification = verify_backup(source)
    if not verification.valid:
        raise BackupIntegrityError("备份未通过完整性校验，未执行恢复", verification)

    target = Path(destination)
    if target.exists() and any(target.iterdir()) and not confirm_overwrite:
        raise RestoreDestinationError(
            f"恢复目标不为空；请明确确认覆盖后重试: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.restore.", dir=target.parent))
    try:
        for relative in verification.checked:
            source_file = source / Path(*PurePosixPath(relative).parts)
            _copy_file(source_file, staging, relative)
        (staging / MANIFEST_NAME).write_bytes((source / MANIFEST_NAME).read_bytes())
        if target.exists():
            target.mkdir(parents=True, exist_ok=True)
            for child in staging.iterdir():
                if child.is_dir():
                    shutil.copytree(child, target / child.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(child, target / child.name)
            recovered = tuple(verification.checked) + (MANIFEST_NAME,)
            shutil.rmtree(staging, ignore_errors=True)
            staging = None  # type: ignore[assignment]
        else:
            _publish_staging(staging, target)
            staging = None  # type: ignore[assignment]
            recovered = tuple(verification.checked) + (MANIFEST_NAME,)
        return RestoreReport(
            backup_path=source,
            destination=target,
            recovered=recovered,
        )
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
