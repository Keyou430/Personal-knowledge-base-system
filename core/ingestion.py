"""Version-aware document ingestion and rebuildable vector projection."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from core.document_store import ChunkRecord, DocumentRecord, DocumentStore
from core.loader import load_document
from core.splitter import split_documents


@dataclass(frozen=True)
class IngestionResult:
    document: DocumentRecord
    chunk_count: int
    replaced: bool
    index_pending: bool
    self_check_passed: bool


def _to_chunk_records(documents: list[Document]) -> list[ChunkRecord]:
    chunks: list[ChunkRecord] = []
    for index, document in enumerate(documents):
        metadata = dict(document.metadata)
        chunks.append(
            ChunkRecord(
                id="",
                content=document.page_content,
                section_title=str(metadata.get("heading_path") or metadata.get("section_title") or ""),
                page=metadata.get("page"),
                chunk_index=int(metadata.get("chunk_index", index)),
                total_chunks=int(metadata.get("total_chunks", len(documents))),
                metadata=metadata,
            )
        )
    return chunks


def _projection_documents(
    chunks: list[ChunkRecord], document: DocumentRecord
) -> list[Document]:
    documents = []
    for chunk in chunks:
        metadata = dict(chunk.metadata)
        metadata.update(
            {
                "document_id": document.id,
                "document_version": document.version,
                "document_name": document.name,
                "source": document.source,
                "chunk_id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "total_chunks": chunk.total_chunks,
            }
        )
        documents.append(Document(page_content=chunk.content, metadata=metadata))
    return documents


def ingest_file(
    path: str | Path,
    *,
    domain: str,
    store: DocumentStore,
    vectorstore: Any = None,
    category: str = "其他",
    owner: str = "",
    source: str = "upload",
) -> IngestionResult:
    """Load, archive, and project one file; SQLite remains readable on projection failure."""
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"文件不存在: {source_path}")

    raw_path = store.database_path.parent / "raw" / domain / source_path.name
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() != raw_path.resolve():
        shutil.copy2(source_path, raw_path)

    loaded = load_document(str(source_path))
    if not loaded:
        raise ValueError("文档内容为空或无法提取")
    split = [doc for doc in split_documents(loaded) if doc.page_content.strip()]
    if not split:
        raise ValueError("文档切分后无有效内容")

    content = "\n".join(doc.page_content for doc in split)
    name = source_path.name
    existing = store.find_active(domain, name)
    content_hash = store.content_hash(content)
    same = store.find_by_hash(domain, content_hash)
    if same is not None:
        return IngestionResult(
            document=same,
            chunk_count=len(store.get_chunks(same.id)),
            replaced=False,
            index_pending=same.index_pending,
            self_check_passed=True,
        )

    old_chunks = store.get_chunks(existing.id) if existing else []
    chunk_records = _to_chunk_records(split)
    if existing:
        document = store.replace_document(
            existing.id,
            domain=domain,
            name=name,
            category=category,
            owner=owner,
            source=source,
            content=content,
            chunks=chunk_records,
        )
    else:
        document = store.create_document(
            domain=domain,
            name=name,
            category=category,
            owner=owner,
            source=source,
            content=content,
            chunks=chunk_records,
        )

    stored_chunks = store.get_chunks(document.id)
    pending = vectorstore is None
    try:
        if vectorstore is not None:
            vector_documents = _projection_documents(stored_chunks, document)
            ids = [chunk.id for chunk in stored_chunks]
            vectorstore.add_documents(vector_documents, ids=ids)
            if old_chunks and hasattr(vectorstore, "delete"):
                vectorstore.delete(ids=[chunk.id for chunk in old_chunks])
        self_check_passed = bool(store.search_keyword(Path(name).stem, domain=domain))
        if not self_check_passed:
            pending = True
    except Exception:
        pending = True
        self_check_passed = False

    store.mark_index_pending(document.id, pending)
    document = store.get_required(document.id)
    return IngestionResult(
        document=document,
        chunk_count=len(stored_chunks),
        replaced=existing is not None,
        index_pending=pending,
        self_check_passed=self_check_passed,
    )
