"""Deterministic, preview-first synchronization of a local source directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from config import DOCUMENT_DB_PATH, SUPPORTED_EXTENSIONS
from core.document_store import DocumentStore
from core.ingestion import ingest_file, prepare_document


class SyncConfigError(ValueError):
    """Raised when a sync input or run lock is invalid."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise SyncConfigError("source_read_failed") from error
    return digest.hexdigest()


def _root_key(source: Path) -> str:
    return hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()


def _relative(path: Path, source: Path) -> str:
    try:
        relative = path.relative_to(source)
    except ValueError as error:
        raise SyncConfigError("source_path_escape") from error
    return relative.as_posix()


def _safe_reason(value: str) -> str:
    allowed = {
        "unsupported_format", "parse_error", "source_read_failed", "preview_changed",
        "apply_failed", "source_missing", "lock_conflict", "source_path_escape",
    }
    return value if value in allowed else "sync_error"


@dataclass(frozen=True)
class SyncItem:
    relative_path: str
    action: str
    reason_codes: tuple[str, ...] = ()
    content_sha256: str | None = None
    content_hash: str | None = None
    size: int = 0
    modified_ns: int = 0
    document_id: str | None = None
    proposed_version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "action": self.action,
            "reason_codes": list(self.reason_codes),
            "content_sha256": self.content_sha256,
            "content_hash": self.content_hash,
            "size": self.size,
            "modified_ns": self.modified_ns,
            "document_id": self.document_id,
            "proposed_version": self.proposed_version,
        }


@dataclass(frozen=True)
class SyncReport:
    run_id: str
    status: str
    domain: str
    root_key: str
    started_at: str
    finished_at: str
    items: tuple[SyncItem, ...] = ()
    error_code: str | None = None

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.action] = counts.get(item.action, 0) + 1
        counts.setdefault("failed", 0)
        return counts

    @property
    def retry_needed(self) -> tuple[str, ...]:
        return tuple(item.relative_path for item in self.items if "apply_failed" in item.reason_codes or "parse_error" in item.reason_codes or "preview_changed" in item.reason_codes)

    @property
    def exit_code(self) -> int:
        if self.error_code:
            return 2
        if any(item.action == "failed" or "parse_error" in item.reason_codes for item in self.items):
            return 1
        return 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "domain": self.domain,
            "root_key": self.root_key,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "counts": self.counts,
            "items": [item.as_dict() for item in self.items],
            "retry_needed": list(self.retry_needed),
            "error_code": self.error_code,
            "exit_code": self.exit_code,
        }


class _RunLock:
    def __init__(self, path: Path, run_id: str):
        self.path = path
        self.run_id = run_id
        self._owned = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise SyncConfigError("lock_conflict") from error
        try:
            os.write(fd, self.run_id.encode("ascii"))
        finally:
            os.close(fd)
        self._owned = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._owned:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def _validate_inputs(source: str | Path, domain: str, database: str | Path) -> tuple[Path, str, Path]:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_dir():
        raise SyncConfigError("source_directory_missing")
    clean_domain = str(domain or "").strip()
    if not clean_domain or any(char in clean_domain for char in "\r\n"):
        raise SyncConfigError("invalid_domain")
    return source_path, clean_domain, Path(database).expanduser().resolve()


def _read_snapshot(database: Path, *, root_key: str, domain: str) -> dict[str, dict[str, Any]]:
    if not database.is_file():
        return {}
    try:
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "directory_sync_sources" not in tables:
            connection.close()
            return {}
        rows = connection.execute(
            "SELECT * FROM directory_sync_sources WHERE root_key = ? AND domain = ?",
            (root_key, domain),
        ).fetchall()
        result = {row["relative_path"]: dict(row) for row in rows}
        connection.close()
        return result
    except (OSError, sqlite3.Error) as error:
        raise SyncConfigError("database_read_failed") from error


def _read_documents(database: Path, ids: Iterable[str | None]) -> dict[str, dict[str, Any]]:
    wanted = [value for value in ids if value]
    if not wanted or not database.is_file():
        return {}
    try:
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in wanted)
        rows = connection.execute(f"SELECT * FROM documents WHERE id IN ({placeholders})", wanted).fetchall()
        result = {row["id"]: dict(row) for row in rows}
        connection.close()
        return result
    except sqlite3.Error as error:
        raise SyncConfigError("database_read_failed") from error


def _iter_files(source: Path) -> list[Path]:
    paths: list[Path] = []
    for root, dirs, files in os.walk(source, topdown=True, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
        for name in files:
            path = Path(root) / name
            if not path.is_symlink() and path.is_file():
                paths.append(path)
    return sorted(paths, key=lambda path: _relative(path, source))


def _preview_unlocked(
    *, source: Path, domain: str, database: Path, run_id: str,
) -> SyncReport:
    started = _now()
    root_key = _root_key(source)
    snapshot = _read_snapshot(database, root_key=root_key, domain=domain)
    documents = _read_documents(database, (row.get("document_id") for row in snapshot.values()))
    items: list[SyncItem] = []
    seen: set[str] = set()
    for path in _iter_files(source):
        relative = _relative(path, source)
        seen.add(relative)
        try:
            stat = path.stat()
            digest = _sha256(path)
        except (OSError, SyncConfigError) as error:
            items.append(SyncItem(relative, "failed", (_safe_reason(str(error)),)))
            continue
        row = snapshot.get(relative)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            items.append(SyncItem(relative, "unsupported", ("unsupported_format",), digest, None, stat.st_size, stat.st_mtime_ns, row.get("document_id") if row else None))
            continue
        document = documents.get(row.get("document_id")) if row else None
        if row and row.get("file_sha256") == digest and int(row.get("size", -1)) == stat.st_size:
            action = "restored" if row.get("state") == "missing" else "unchanged"
            items.append(SyncItem(relative, action, (), digest, document.get("content_hash") if document else None, stat.st_size, stat.st_mtime_ns, row.get("document_id")))
            continue
        try:
            content, _ = prepare_document(path)
            content_hash = DocumentStore.content_hash(content)
        except Exception:
            items.append(SyncItem(relative, "failed", ("parse_error",), digest, None, stat.st_size, stat.st_mtime_ns, row.get("document_id") if row else None))
            continue
        proposed = DocumentStore.next_version(str(document["version"])) if document else "1"
        items.append(SyncItem(relative, "changed" if row else "new", (), digest, content_hash, stat.st_size, stat.st_mtime_ns, row.get("document_id") if row else None, proposed))

    for relative, row in sorted(snapshot.items()):
        if relative in seen or row.get("state") == "missing":
            continue
        items.append(SyncItem(
            relative, "missing", ("source_missing",), row.get("file_sha256"), None,
            int(row.get("size", 0)), int(row.get("modified_ns", 0)), row.get("document_id"),
        ))
    return SyncReport(run_id, "preview", domain, root_key, started, _now(), tuple(items))


def preview_sync(
    *, source: str | Path, domain: str, database: str | Path = DOCUMENT_DB_PATH,
    lock_file: str | Path | None = None,
) -> SyncReport:
    source_path, clean_domain, database_path = _validate_inputs(source, domain, database)
    run_id = uuid.uuid4().hex
    lock_path = Path(lock_file) if lock_file else database_path.parent / "directory-sync.lock"
    with _RunLock(lock_path, run_id):
        return _preview_unlocked(source=source_path, domain=clean_domain, database=database_path, run_id=run_id)


def _apply_unlocked(
    preview: SyncReport, *, source: Path, database: Path,
    category: str, owner: str, source_label: str,
) -> SyncReport:
    store = DocumentStore(database)
    applied: list[SyncItem] = []
    for item in preview.items:
        path = source / Path(item.relative_path)
        if item.action in {"unsupported", "failed"}:
            applied.append(item)
            continue
        if item.action == "missing":
            if item.document_id:
                try:
                    store.mark_source_present(item.document_id, False)
                except KeyError:
                    pass
            store.upsert_sync_source(
                root_key=preview.root_key, domain=preview.domain, relative_path=item.relative_path,
                document_id=item.document_id, file_sha256=item.content_sha256 or "", size=item.size,
                modified_ns=item.modified_ns, state="missing", last_seen_at=_now(), last_applied_at=_now(),
            )
            applied.append(item)
            continue
        if not path.is_file():
            applied.append(SyncItem(item.relative_path, "failed", ("source_missing",), item.content_sha256, item.content_hash, item.size, item.modified_ns, item.document_id, item.proposed_version))
            continue
        try:
            stat = path.stat()
            digest = _sha256(path)
        except (OSError, SyncConfigError):
            applied.append(SyncItem(item.relative_path, "failed", ("source_read_failed",), item.content_sha256, item.content_hash, item.size, item.modified_ns, item.document_id, item.proposed_version))
            continue
        if item.content_sha256 and (digest != item.content_sha256 or stat.st_size != item.size):
            applied.append(SyncItem(item.relative_path, "failed", ("preview_changed",), digest, item.content_hash, stat.st_size, stat.st_mtime_ns, item.document_id, item.proposed_version))
            continue
        if item.action == "unchanged":
            document_id = item.document_id
        elif item.action == "restored":
            document_id = item.document_id
            if document_id:
                store.mark_source_present(document_id, True)
        else:
            try:
                result = ingest_file(
                    path, domain=preview.domain, store=store, vectorstore=None,
                    category=category, owner=owner, source=source_label,
                    version=item.proposed_version, document_name=item.relative_path,
                    archive_relative_path=item.relative_path, deduplicate_by_hash=False,
                )
                document_id = result.document.id
            except Exception:
                applied.append(SyncItem(item.relative_path, "failed", ("apply_failed",), item.content_sha256, item.content_hash, item.size, item.modified_ns, item.document_id, item.proposed_version))
                continue
        store.upsert_sync_source(
            root_key=preview.root_key, domain=preview.domain, relative_path=item.relative_path,
            document_id=document_id, file_sha256=item.content_sha256 or digest, size=item.size,
            modified_ns=item.modified_ns, state="present", last_seen_at=_now(), last_applied_at=_now(),
        )
        applied.append(item)
    return SyncReport(preview.run_id, "applied", preview.domain, preview.root_key, preview.started_at, _now(), tuple(applied))


def apply_sync(
    preview: SyncReport, *, database: str | Path = DOCUMENT_DB_PATH,
    category: str = "其他", owner: str = "", source_label: str = "directory_sync",
    source: str | Path | None = None, lock_file: str | Path | None = None,
) -> SyncReport:
    if preview.status != "preview":
        raise SyncConfigError("preview_required")
    database_path = Path(database).expanduser().resolve()
    source_path = Path(source).expanduser().resolve() if source is not None else None
    if source_path is None:
        raise SyncConfigError("source_directory_required")
    if not source_path.is_dir():
        raise SyncConfigError("source_directory_missing")
    if _root_key(source_path) != preview.root_key:
        raise SyncConfigError("preview_source_mismatch")
    run_id = preview.run_id
    lock_path = Path(lock_file) if lock_file else database_path.parent / "directory-sync.lock"
    with _RunLock(lock_path, run_id):
        return _apply_unlocked(preview, source=source_path, database=database_path, category=category, owner=owner, source_label=source_label)


def _write_log(path: Path, report: SyncReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "directory_sync", **report.as_dict()}, ensure_ascii=False) + "\n")


def run_sync(
    *, source: str | Path, domain: str, database: str | Path = DOCUMENT_DB_PATH,
    apply: bool = False, category: str = "其他", owner: str = "", source_label: str = "directory_sync",
    lock_file: str | Path | None = None, log_file: str | Path | None = None,
) -> SyncReport:
    source_path, clean_domain, database_path = _validate_inputs(source, domain, database)
    run_id = uuid.uuid4().hex
    lock_path = Path(lock_file) if lock_file else database_path.parent / "directory-sync.lock"
    with _RunLock(lock_path, run_id):
        preview = _preview_unlocked(source=source_path, domain=clean_domain, database=database_path, run_id=run_id)
        report = _apply_unlocked(preview, source=source_path, database=database_path, category=category, owner=owner, source_label=source_label) if apply else preview
    if log_file:
        _write_log(Path(log_file), report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview or apply local directory incremental sync")
    parser.add_argument("--source", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--database", default=DOCUMENT_DB_PATH)
    parser.add_argument("--category", default="其他")
    parser.add_argument("--owner", default="")
    parser.add_argument("--source-label", default="directory_sync")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--lock-file")
    parser.add_argument("--log-file")
    parser.add_argument("--json-out")
    try:
        args = parser.parse_args(argv)
        log_file = args.log_file or str(Path(args.database).resolve().parent / "logs" / "directory-sync.jsonl")
        report = run_sync(
            source=args.source, domain=args.domain, database=args.database, apply=args.apply,
            category=args.category, owner=args.owner, source_label=args.source_label,
            lock_file=args.lock_file, log_file=log_file,
        )
        if args.json_out:
            target = Path(args.json_out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return report.exit_code
    except SyncConfigError:
        return 2
    except Exception:
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
