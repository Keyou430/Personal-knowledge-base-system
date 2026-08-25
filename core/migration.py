"""Explicit migration and projection rebuild operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import SUPPORTED_EXTENSIONS
from core.document_store import DocumentStore
from core.ingestion import IngestionResult, _projection_documents
from core.retriever import stable_chunk_id


@dataclass
class MigrationReport:
    success_count: int = 0
    failure_count: int = 0
    pending_count: int = 0
    successes: list[str] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)


@dataclass
class RebuildReport:
    success_count: int = 0
    failure_count: int = 0
    pending_count: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)


def migrate_domain(
    domain: str,
    *,
    raw_dir: str | Path,
    store: DocumentStore,
    vectorstore: Any = None,
) -> MigrationReport:
    report = MigrationReport()
    directory = Path(raw_dir)
    if not directory.exists():
        return report
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            result = _migrate_one(path, domain=domain, store=store, vectorstore=vectorstore)
            report.success_count += 1
            report.successes.append(path.name)
            report.pending_count += int(result.index_pending)
        except Exception as error:
            report.failure_count += 1
            report.failures.append({"file": path.name, "reason": str(error)})
    return report


def _migrate_one(path: Path, *, domain: str, store: DocumentStore, vectorstore: Any) -> IngestionResult:
    from core.ingestion import ingest_file

    return ingest_file(
        path,
        domain=domain,
        store=store,
        vectorstore=vectorstore,
        source="migration",
    )


def rebuild_indexes(
    *,
    store: DocumentStore,
    vectorstore: Any = None,
    domains: list[str] | None = None,
) -> RebuildReport:
    report = RebuildReport()
    store.rebuild_fts(domains)
    documents = store.list_documents(domain=None, include_superseded=False)
    if domains:
        documents = [document for document in documents if document.domain in domains]
    for document in documents:
        try:
            chunks = store.get_chunks(document.id)
            if vectorstore is not None:
                vector_documents = _projection_documents(chunks, document)
                ids = [chunk.id for chunk in chunks]
                vectorstore.add_documents(vector_documents, ids=ids)
            if not store.search_keyword(Path(document.name).stem, domain=document.domain):
                raise RuntimeError("关键词索引自检未命中")
            store.mark_index_pending(document.id, vectorstore is None)
            report.success_count += 1
            report.pending_count += int(vectorstore is None)
        except Exception as error:
            store.mark_index_pending(document.id, True)
            report.pending_count += 1
            report.failure_count += 1
            report.failures.append({"file": document.name, "reason": str(error)})
    return report
