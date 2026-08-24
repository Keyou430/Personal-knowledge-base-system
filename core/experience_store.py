# -*- coding: utf-8 -*-
"""SQLite persistence for user-confirmed experience cards."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _from_json(value: str) -> Any:
    return json.loads(value)


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


@dataclass(frozen=True)
class ExperienceDraft:
    title: str
    scenario: str
    conclusion: str
    steps: list[str]
    tags: list[str]
    sources: list[dict[str, Any]]
    question: str
    answer_excerpt: str


@dataclass(frozen=True)
class ExperienceCard:
    id: str
    title: str
    scenario: str
    conclusion: str
    steps: list[str]
    tags: list[str]
    status: str
    content_hash: str
    index_pending: bool
    created_at: str
    updated_at: str


class ExperienceStore:
    """Source of truth for cards, their citations, and change snapshots."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS experiences (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    scenario TEXT NOT NULL,
                    conclusion TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'archived')),
                    content_hash TEXT NOT NULL,
                    index_pending INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_experiences_hash ON experiences(content_hash);
                CREATE INDEX IF NOT EXISTS idx_experiences_status ON experiences(status);

                CREATE TABLE IF NOT EXISTS experience_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experience_id TEXT NOT NULL REFERENCES experiences(id) ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer_excerpt TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_experience_sources_card ON experience_sources(experience_id);

                CREATE TABLE IF NOT EXISTS experience_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experience_id TEXT NOT NULL REFERENCES experiences(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(experience_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_experience_versions_card ON experience_versions(experience_id, version);
                """
            )

    @staticmethod
    def content_hash(draft: ExperienceDraft) -> str:
        payload = {
            "title": _normalized_text(draft.title),
            "scenario": _normalized_text(draft.scenario),
            "conclusion": _normalized_text(draft.conclusion),
            "steps": [_normalized_text(step) for step in draft.steps],
            "tags": sorted(_normalized_text(tag) for tag in draft.tags),
        }
        return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()

    def create(self, draft: ExperienceDraft) -> ExperienceCard:
        card_id = str(uuid.uuid4())
        timestamp = _now()
        content_hash = self.content_hash(draft)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO experiences (
                    id, title, scenario, conclusion, steps_json, tags_json, status,
                    content_hash, index_pending, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, 0, ?, ?)
                """,
                (
                    card_id,
                    draft.title.strip(),
                    draft.scenario.strip(),
                    draft.conclusion.strip(),
                    _json(list(draft.steps)),
                    _json(list(draft.tags)),
                    content_hash,
                    timestamp,
                    timestamp,
                ),
            )
            self._replace_sources(connection, card_id, draft, timestamp)
            self._append_version(connection, card_id, "created", timestamp)

        return self.get(card_id)

    def get(self, experience_id: str) -> ExperienceCard | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiences WHERE id = ?", (experience_id,)
            ).fetchone()
        return self._card_from_row(row) if row else None

    def list(self, include_archived: bool = False) -> list[ExperienceCard]:
        sql = "SELECT * FROM experiences"
        parameters: tuple[Any, ...] = ()
        if not include_archived:
            sql += " WHERE status = 'active'"
        sql += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._card_from_row(row) for row in rows]

    def search(self, query: str, include_archived: bool = False) -> list[ExperienceCard]:
        normalized_query = _normalized_text(query)
        if not normalized_query:
            return self.list(include_archived=include_archived)

        pattern = f"%{normalized_query}%"
        sql = """
            SELECT * FROM experiences
            WHERE (
                lower(title) LIKE ? OR lower(scenario) LIKE ? OR
                lower(conclusion) LIKE ? OR lower(tags_json) LIKE ?
            )
        """
        parameters: list[Any] = [pattern, pattern, pattern, pattern]
        if not include_archived:
            sql += " AND status = 'active'"
        sql += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._card_from_row(row) for row in rows]

    def find_exact_duplicate(
        self, draft: ExperienceDraft, include_archived: bool = False
    ) -> ExperienceCard | None:
        sql = "SELECT * FROM experiences WHERE content_hash = ?"
        parameters: list[Any] = [self.content_hash(draft)]
        if not include_archived:
            sql += " AND status = 'active'"
        sql += " ORDER BY updated_at DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
        return self._card_from_row(row) if row else None

    def update(
        self,
        experience_id: str,
        draft: ExperienceDraft,
        change_type: str = "edited",
    ) -> ExperienceCard:
        timestamp = _now()
        with self._connect() as connection:
            current = connection.execute(
                "SELECT id FROM experiences WHERE id = ?", (experience_id,)
            ).fetchone()
            if not current:
                raise KeyError(f"经验不存在: {experience_id}")

            connection.execute(
                """
                UPDATE experiences
                SET title = ?, scenario = ?, conclusion = ?, steps_json = ?, tags_json = ?,
                    content_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    draft.title.strip(),
                    draft.scenario.strip(),
                    draft.conclusion.strip(),
                    _json(list(draft.steps)),
                    _json(list(draft.tags)),
                    self.content_hash(draft),
                    timestamp,
                    experience_id,
                ),
            )
            self._replace_sources(connection, experience_id, draft, timestamp)
            self._append_version(connection, experience_id, change_type, timestamp)

        return self.get_required(experience_id)

    def archive(self, experience_id: str) -> ExperienceCard:
        return self._set_status(experience_id, "archived", "archived")

    def restore(self, experience_id: str) -> ExperienceCard:
        return self._set_status(experience_id, "active", "restored")

    def set_index_pending(self, experience_id: str, pending: bool) -> ExperienceCard:
        timestamp = _now()
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE experiences SET index_pending = ?, updated_at = ? WHERE id = ?",
                (int(pending), timestamp, experience_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"经验不存在: {experience_id}")
        return self.get_required(experience_id)

    def get_sources(self, experience_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source, excerpt, metadata_json, question, answer_excerpt, created_at
                FROM experience_sources WHERE experience_id = ? ORDER BY id
                """,
                (experience_id,),
            ).fetchall()
        sources: list[dict[str, Any]] = []
        for row in rows:
            source = _from_json(row["metadata_json"])
            source.update(
                {
                    "source": row["source"],
                    "excerpt": row["excerpt"],
                    "question": row["question"],
                    "answer_excerpt": row["answer_excerpt"],
                    "created_at": row["created_at"],
                }
            )
            sources.append(source)
        return sources

    def get_versions(self, experience_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT version, snapshot_json, change_type, created_at
                FROM experience_versions WHERE experience_id = ? ORDER BY version
                """,
                (experience_id,),
            ).fetchall()
        return [
            {
                "version": row["version"],
                "snapshot": _from_json(row["snapshot_json"]),
                "change_type": row["change_type"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_required(self, experience_id: str) -> ExperienceCard:
        card = self.get(experience_id)
        if card is None:
            raise KeyError(f"经验不存在: {experience_id}")
        return card

    def _set_status(
        self, experience_id: str, status: str, change_type: str
    ) -> ExperienceCard:
        timestamp = _now()
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE experiences SET status = ?, updated_at = ? WHERE id = ?",
                (status, timestamp, experience_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"经验不存在: {experience_id}")
            self._append_version(connection, experience_id, change_type, timestamp)
        return self.get_required(experience_id)

    def _replace_sources(
        self,
        connection: sqlite3.Connection,
        experience_id: str,
        draft: ExperienceDraft,
        timestamp: str,
    ) -> None:
        connection.execute(
            "DELETE FROM experience_sources WHERE experience_id = ?", (experience_id,)
        )
        for source in draft.sources:
            source_data = dict(source)
            name = str(source_data.pop("source", "未知来源"))
            excerpt = str(source_data.pop("excerpt", ""))
            connection.execute(
                """
                INSERT INTO experience_sources (
                    experience_id, source, excerpt, metadata_json, question, answer_excerpt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experience_id,
                    name,
                    excerpt,
                    _json(source_data),
                    draft.question,
                    draft.answer_excerpt,
                    timestamp,
                ),
            )

    def _append_version(
        self,
        connection: sqlite3.Connection,
        experience_id: str,
        change_type: str,
        timestamp: str,
    ) -> None:
        row = connection.execute(
            "SELECT * FROM experiences WHERE id = ?", (experience_id,)
        ).fetchone()
        version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM experience_versions WHERE experience_id = ?",
            (experience_id,),
        ).fetchone()[0]
        card = self._card_from_row(row)
        snapshot = {
            "id": card.id,
            "title": card.title,
            "scenario": card.scenario,
            "conclusion": card.conclusion,
            "steps": card.steps,
            "tags": card.tags,
            "status": card.status,
            "content_hash": card.content_hash,
            "index_pending": card.index_pending,
            "sources": self._sources_for_connection(connection, experience_id),
        }
        connection.execute(
            """
            INSERT INTO experience_versions (
                experience_id, version, snapshot_json, change_type, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (experience_id, version, _json(snapshot), change_type, timestamp),
        )

    @staticmethod
    def _sources_for_connection(
        connection: sqlite3.Connection, experience_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT source, excerpt, metadata_json FROM experience_sources WHERE experience_id = ? ORDER BY id",
            (experience_id,),
        ).fetchall()
        sources = []
        for row in rows:
            source = _from_json(row["metadata_json"])
            source.update({"source": row["source"], "excerpt": row["excerpt"]})
            sources.append(source)
        return sources

    @staticmethod
    def _card_from_row(row: sqlite3.Row) -> ExperienceCard:
        return ExperienceCard(
            id=row["id"],
            title=row["title"],
            scenario=row["scenario"],
            conclusion=row["conclusion"],
            steps=_from_json(row["steps_json"]),
            tags=_from_json(row["tags_json"]),
            status=row["status"],
            content_hash=row["content_hash"],
            index_pending=bool(row["index_pending"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
