"""Privacy-safe local observability for question traces and index health."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.document_store import DocumentStore

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _basename(value: str) -> str:
    """Return a platform-independent basename without retaining local paths."""
    return str(value or "").replace("\\", "/").rsplit("/", 1)[-1] or "未知来源"


def _normalise_versions(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        if isinstance(value, Mapping):
            name = value.get("name") or value.get("source") or value.get("document_name")
            version = value.get("version") or value.get("document_version")
        elif isinstance(value, (tuple, list)) and len(value) >= 2:
            name, version = value[0], value[1]
        else:
            continue
        label = f"{_basename(str(name))}@{str(version or '未知')}"
        if label not in seen:
            seen.add(label)
            result.append(label)
    return tuple(result)


@dataclass(frozen=True)
class QueryTrace:
    id: int
    recorded_at: str
    domain: str
    retrieval_count: int
    selected_versions: tuple[str, ...]
    refusal_reason: str | None


class QueryTraceStore:
    """Independent SQLite store whose schema excludes user question content."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS query_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    retrieval_count INTEGER NOT NULL,
                    selected_versions_json TEXT NOT NULL,
                    refusal_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_query_traces_recorded_at
                    ON query_traces(recorded_at);
                """
            )

    def record(
        self,
        *,
        domain: str,
        retrieval_count: int,
        selected_versions: Iterable[Any],
        refusal_reason: str | None,
    ) -> QueryTrace:
        safe_domain = str(domain or "未知领域").strip()[:200] or "未知领域"
        count = max(0, int(retrieval_count))
        versions = _normalise_versions(selected_versions)
        reason = str(refusal_reason).strip()[:200] if refusal_reason else None
        recorded_at = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO query_traces (
                    recorded_at, domain, retrieval_count,
                    selected_versions_json, refusal_reason
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (recorded_at, safe_domain, count, json.dumps(versions, ensure_ascii=False), reason),
            )
            trace_id = int(cursor.lastrowid)
        return QueryTrace(trace_id, recorded_at, safe_domain, count, versions, reason)

    def list_recent(self, limit: int = 50) -> list[QueryTrace]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM query_traces ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
            ).fetchall()
        return [
            QueryTrace(
                id=int(row["id"]),
                recorded_at=row["recorded_at"],
                domain=row["domain"],
                retrieval_count=int(row["retrieval_count"]),
                selected_versions=tuple(json.loads(row["selected_versions_json"] or "[]")),
                refusal_reason=row["refusal_reason"],
            )
            for row in rows
        ]


def record_query_trace_safely(
    store: Any,
    *,
    domain: str,
    retrieval_count: int,
    selected_versions: Iterable[Any],
    refusal_reason: str | None,
) -> bool:
    """Best-effort trace write; observability failures never affect answering."""
    try:
        store.record(
            domain=domain,
            retrieval_count=retrieval_count,
            selected_versions=selected_versions,
            refusal_reason=refusal_reason,
        )
        return True
    except Exception as error:
        logger.warning("问答观测写入失败，已忽略: %s", type(error).__name__)
        return False


def get_index_health(store: DocumentStore, domain: str | None = None) -> dict[str, int | str | bool]:
    """Summarise archive and projection states for a domain."""
    documents = store.list_documents(domain=domain, include_superseded=True)
    counts = {
        "active": sum(document.status == "active" for document in documents),
        "superseded": sum(document.status == "superseded" for document in documents),
        "failed": sum(document.status == "failed" for document in documents),
        "index_pending": sum(document.index_pending for document in documents),
        "source_missing": sum(not document.source_present for document in documents),
    }
    if counts["failed"] or counts["index_pending"] or counts["source_missing"]:
        status = "attention"
    elif counts["active"]:
        status = "healthy"
    else:
        status = "empty"
    return {**counts, "total": len(documents), "status": status}


def document_status_label(
    status: str, index_pending: bool = False, source_present: bool = True
) -> str:
    if not source_present:
        return "源文件缺失"
    if index_pending:
        return "待重建索引"
    return {
        "active": "当前生效",
        "superseded": "历史版本",
        "failed": "处理失败",
    }.get(str(status), "未知状态")
