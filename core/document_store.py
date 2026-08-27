"""SQLite source of truth for versioned document archives and keyword search."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalise(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").split())


def _hash_content(content: str) -> str:
    return hashlib.sha256(_normalise(content).encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _from_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    return json.loads(value)


@dataclass(frozen=True)
class ChunkRecord:
    id: str
    content: str
    section_title: str = ""
    page: int | None = None
    chunk_index: int = 0
    total_chunks: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    document_id: str = ""
    document_version: str = ""


@dataclass(frozen=True)
class DocumentRecord:
    id: str
    domain: str
    name: str
    category: str
    owner: str
    version: str
    content_hash: str
    source: str
    updated_at: str
    status: str
    index_pending: bool
    created_at: str


@dataclass(frozen=True)
class ChunkHit:
    id: str
    document_id: str
    document_version: str
    document_name: str
    category: str
    content: str
    section_title: str
    page: int | None
    chunk_index: int
    total_chunks: int
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class DocumentStore:
    """Durable archive and FTS5 projection for one local knowledge base."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def content_hash(content: str) -> str:
        return _hash_content(content)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT '',
                    version TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'superseded', 'failed')),
                    index_pending INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_documents_domain_name
                    ON documents(domain, name, status);
                CREATE INDEX IF NOT EXISTS idx_documents_hash
                    ON documents(domain, content_hash, status);

                CREATE TABLE IF NOT EXISTS document_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    document_version TEXT NOT NULL,
                    content TEXT NOT NULL,
                    section_title TEXT NOT NULL DEFAULT '',
                    page INTEGER,
                    chunk_index INTEGER NOT NULL,
                    total_chunks INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_document_chunks_document
                    ON document_chunks(document_id, chunk_index);

                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    domain,
                    document_name,
                    category,
                    section_title,
                    content
                );
                """
            )

    def create_document(
        self,
        *,
        domain: str,
        name: str,
        category: str,
        owner: str,
        source: str,
        content: str,
        chunks: Iterable[ChunkRecord],
        version: str | None = None,
        updated_at: str | None = None,
    ) -> DocumentRecord:
        content_hash = _hash_content(content)
        existing = self.find_by_hash(domain, content_hash)
        if existing is not None:
            return existing

        active = self.find_active(domain, name)
        resolved_version = version or self.next_version(active.version if active else None)
        return self._insert_document(
            domain=domain,
            name=name,
            category=category,
            owner=owner,
            source=source,
            content_hash=content_hash,
            version=resolved_version,
            chunks=list(chunks),
            updated_at=updated_at,
        )

    def replace_document(
        self,
        document_id: str,
        *,
        domain: str,
        name: str,
        category: str,
        owner: str,
        source: str,
        content: str,
        chunks: Iterable[ChunkRecord],
        version: str | None = None,
        updated_at: str | None = None,
    ) -> DocumentRecord:
        content_hash = _hash_content(content)
        existing = self.find_by_hash(domain, content_hash)
        if existing is not None:
            return existing
        current = self.get(document_id)
        if current is None:
            raise KeyError(f"文档不存在: {document_id}")
        resolved_version = version or self.next_version(current.version)
        with self._connect() as connection:
            connection.execute(
                "UPDATE documents SET status = 'superseded', updated_at = ? WHERE id = ?",
                (_now(), document_id),
            )
        try:
            return self._insert_document(
                domain=domain,
                name=name,
                category=category,
                owner=owner,
                source=source,
                content_hash=content_hash,
                version=resolved_version,
                chunks=list(chunks),
                updated_at=updated_at,
            )
        except Exception:
            # Projection rows are transactional, and the previous active
            # version must remain usable when the replacement is malformed.
            with self._connect() as connection:
                connection.execute(
                    "UPDATE documents SET status = 'active', updated_at = ? WHERE id = ?",
                    (current.updated_at, document_id),
                )
            raise

    def _insert_document(
        self,
        *,
        domain: str,
        name: str,
        category: str,
        owner: str,
        source: str,
        content_hash: str,
        version: str,
        chunks: list[ChunkRecord],
        updated_at: str | None = None,
    ) -> DocumentRecord:
        if not chunks:
            raise ValueError("文档切片不能为空")
        document_id = str(uuid.uuid4())
        timestamp = updated_at or _now()
        total = len(chunks)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO documents (
                    id, domain, name, category, owner, version, content_hash,
                    source, updated_at, created_at, status, index_pending
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0)
                """,
                (
                    document_id,
                    domain,
                    name,
                    category,
                    owner or "",
                    str(version),
                    content_hash,
                    source,
                    timestamp,
                    timestamp,
                ),
            )
            for index, chunk in enumerate(chunks):
                chunk_id = chunk.id or f"{document_id}:{version}:{index}"
                connection.execute(
                    """
                    INSERT INTO document_chunks (
                        id, document_id, document_version, content, section_title,
                        page, chunk_index, total_chunks, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        document_id,
                        str(version),
                        chunk.content,
                        chunk.section_title or "",
                        chunk.page,
                        chunk.chunk_index if chunk.chunk_index else index,
                        chunk.total_chunks if chunk.total_chunks != 1 or total == 1 else total,
                        _json(chunk.metadata),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO document_chunks_fts (
                        chunk_id, domain, document_name, category, section_title, content
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        domain,
                        name,
                        category,
                        chunk.section_title or "",
                        chunk.content,
                    ),
                )
        return self.get_required(document_id)

    @staticmethod
    def next_version(previous: str | None) -> str:
        if not previous:
            return "1"
        match = re.fullmatch(r"(.*?)(\d+)", str(previous))
        if match:
            return f"{match.group(1)}{int(match.group(2)) + 1}"
        return f"{previous}.1"

    def find_by_hash(self, domain: str, content_hash: str) -> DocumentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE domain = ? AND content_hash = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
                (domain, content_hash),
            ).fetchone()
        return self._document_from_row(row) if row else None

    def find_active(self, domain: str, name: str) -> DocumentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE domain = ? AND name = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
                (domain, name),
            ).fetchone()
        return self._document_from_row(row) if row else None

    def get(self, document_id: str) -> DocumentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        return self._document_from_row(row) if row else None

    def get_required(self, document_id: str) -> DocumentRecord:
        document = self.get(document_id)
        if document is None:
            raise KeyError(f"文档不存在: {document_id}")
        return document

    def rollback_replacement(
        self,
        new_document_id: str,
        previous_document_id: str,
        *,
        previous_updated_at: str,
    ) -> None:
        """Remove an uncommitted replacement and reactivate its previous version."""
        with self._connect() as connection:
            new_document = connection.execute(
                "SELECT id FROM documents WHERE id = ? AND status = 'active'",
                (new_document_id,),
            ).fetchone()
            previous_document = connection.execute(
                "SELECT id FROM documents WHERE id = ? AND status = 'superseded'",
                (previous_document_id,),
            ).fetchone()
            if new_document is None or previous_document is None:
                raise RuntimeError("无法回滚文档版本替换")
            connection.execute(
                "DELETE FROM document_chunks_fts WHERE chunk_id IN "
                "(SELECT id FROM document_chunks WHERE document_id = ?)",
                (new_document_id,),
            )
            connection.execute("DELETE FROM documents WHERE id = ?", (new_document_id,))
            connection.execute(
                "UPDATE documents SET status = 'active', updated_at = ? WHERE id = ?",
                (previous_updated_at, previous_document_id),
            )

    def rollback_create(self, document_id: str) -> None:
        """Remove an uncommitted new document and its keyword projection."""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM document_chunks_fts WHERE chunk_id IN "
                "(SELECT id FROM document_chunks WHERE document_id = ?)",
                (document_id,),
            )
            connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def get_chunks(self, document_id: str, version: str | None = None) -> list[ChunkRecord]:
        sql = "SELECT * FROM document_chunks WHERE document_id = ?"
        params: list[Any] = [document_id]
        if version is not None:
            sql += " AND document_version = ?"
            params.append(str(version))
        sql += " ORDER BY chunk_index, id"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._chunk_from_row(row) for row in rows]

    def search_keyword(
        self, query: str, domain: str | None = None, limit: int = 50
    ) -> list[ChunkHit]:
        terms = [term for term in re.findall(r"[\w\u4e00-\u9fff.-]+", query or "") if term]
        if not terms:
            return []
        match_query = " AND ".join(f'"{term.replace(chr(34), chr(34) + chr(34))}"' for term in terms)
        sql = """
            SELECT f.chunk_id, c.document_id, c.document_version, d.name AS document_name,
                   d.category, c.content, c.section_title, c.page, c.chunk_index,
                   c.total_chunks, c.metadata_json, bm25(document_chunks_fts) AS rank
            FROM document_chunks_fts f
            JOIN document_chunks c ON c.id = f.chunk_id
            JOIN documents d ON d.id = c.document_id
            WHERE document_chunks_fts MATCH ? AND d.status = 'active'
        """
        params: list[Any] = [match_query]
        if domain:
            sql += " AND d.domain = ?"
            params.append(domain)
        sql += " ORDER BY rank LIMIT ?"
        params.append(int(limit))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            if not rows:
                # SQLite's default unicode61 tokenizer treats contiguous Chinese
                # text as one token. Keep FTS5 as the primary projection, then
                # use an indexed-row LIKE fallback for short Chinese terms.
                like_sql = """
                    SELECT c.id AS chunk_id, c.document_id, c.document_version,
                           d.name AS document_name, d.category, c.content,
                           c.section_title, c.page, c.chunk_index, c.total_chunks,
                           c.metadata_json, 0.0 AS rank
                    FROM document_chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE d.status = 'active'
                """
                like_params: list[Any] = []
                for term in terms:
                    like_sql += " AND (c.content LIKE ? OR c.section_title LIKE ? OR d.name LIKE ?)"
                    pattern = f"%{term}%"
                    like_params.extend([pattern, pattern, pattern])
                if domain:
                    like_sql += " AND d.domain = ?"
                    like_params.append(domain)
                like_sql += " ORDER BY d.updated_at DESC, c.chunk_index LIMIT ?"
                like_params.append(int(limit))
                rows = connection.execute(like_sql, like_params).fetchall()
        return [self._hit_from_row(row) for row in rows]

    def list_documents(
        self, domain: str | None = None, include_superseded: bool = False
    ) -> list[DocumentRecord]:
        sql = "SELECT * FROM documents WHERE 1 = 1"
        params: list[Any] = []
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        if not include_superseded:
            sql += " AND status = 'active'"
        sql += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._document_from_row(row) for row in rows]

    def mark_index_pending(self, document_id: str, pending: bool = True) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE documents SET index_pending = ? WHERE id = ?",
                (int(pending), document_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"文档不存在: {document_id}")

    def rebuild_fts(self, domains: Iterable[str] | None = None) -> int:
        """Recreate the keyword projection from active archive rows."""
        with self._connect() as connection:
            domain_list = list(domains or [])
            if domain_list:
                placeholders = ",".join("?" for _ in domain_list)
                connection.execute(
                    f"DELETE FROM document_chunks_fts WHERE domain IN ({placeholders})",
                    domain_list,
                )
            else:
                connection.execute("DELETE FROM document_chunks_fts")
            sql = """
                INSERT INTO document_chunks_fts (chunk_id, domain, document_name, category, section_title, content)
                SELECT c.id, d.domain, d.name, d.category, c.section_title, c.content
                FROM document_chunks c JOIN documents d ON d.id = c.document_id
                WHERE d.status = 'active'
            """
            params: list[Any] = []
            if domain_list:
                placeholders = ",".join("?" for _ in domain_list)
                sql += f" AND d.domain IN ({placeholders})"
                params.extend(domain_list)
            connection.execute(sql, params)
            return int(connection.execute("SELECT COUNT(*) FROM document_chunks_fts").fetchone()[0])

    @staticmethod
    def _document_from_row(row: sqlite3.Row) -> DocumentRecord:
        return DocumentRecord(
            id=row["id"],
            domain=row["domain"],
            name=row["name"],
            category=row["category"],
            owner=row["owner"],
            version=row["version"],
            content_hash=row["content_hash"],
            source=row["source"],
            updated_at=row["updated_at"],
            status=row["status"],
            index_pending=bool(row["index_pending"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _chunk_from_row(row: sqlite3.Row) -> ChunkRecord:
        return ChunkRecord(
            id=row["id"],
            document_id=row["document_id"],
            document_version=row["document_version"],
            content=row["content"],
            section_title=row["section_title"],
            page=row["page"],
            chunk_index=row["chunk_index"],
            total_chunks=row["total_chunks"],
            metadata=_from_json(row["metadata_json"]),
        )

    @staticmethod
    def _hit_from_row(row: sqlite3.Row) -> ChunkHit:
        return ChunkHit(
            id=row["chunk_id"],
            document_id=row["document_id"],
            document_version=row["document_version"],
            document_name=row["document_name"],
            category=row["category"],
            content=row["content"],
            section_title=row["section_title"],
            page=row["page"],
            chunk_index=row["chunk_index"],
            total_chunks=row["total_chunks"],
            score=max(0.0, 1.0 / (1.0 + float(row["rank"] or 0.0))),
            metadata=_from_json(row["metadata_json"]),
        )
