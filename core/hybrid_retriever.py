"""Explainable local keyword + semantic retrieval fusion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.documents import Document

from core.document_store import DocumentStore


@dataclass(frozen=True)
class RetrievedChunk:
    id: str
    document_id: str
    document_version: str
    document_name: str
    category: str
    content: str
    section_title: str
    page: int | None
    score: float
    keyword_score: float
    semantic_score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class HybridRetriever:
    def __init__(
        self,
        store: DocumentStore,
        semantic_search: Callable[..., Any],
        *,
        top_k: int = 8,
        keyword_weight: float = 0.55,
        semantic_weight: float = 0.45,
        min_score: float = 0.18,
    ):
        self.store = store
        self.semantic_search = semantic_search
        self.top_k = top_k
        self.keyword_weight = keyword_weight
        self.semantic_weight = semantic_weight
        self.min_score = min_score

    def search(self, query: str, domain: str, top_k: int | None = None) -> list[RetrievedChunk]:
        limit = top_k or self.top_k
        keyword_hits = self.store.search_keyword(query, domain=domain, limit=max(limit * 2, 10))
        exact_query = bool(re.search(r"\d|[A-Z]{2,}[-_/]?\d|\d{4}[-/.]\d", query or ""))
        keyword_weight = 0.75 if exact_query else self.keyword_weight
        semantic_weight = 1.0 - keyword_weight if exact_query else self.semantic_weight

        merged: dict[str, dict[str, Any]] = {}
        for hit in keyword_hits:
            merged[hit.id] = {
                "id": hit.id,
                "document_id": hit.document_id,
                "document_version": hit.document_version,
                "document_name": hit.document_name,
                "category": hit.category,
                "content": hit.content,
                "section_title": hit.section_title,
                "page": hit.page,
                "keyword_score": min(1.0, max(0.0, hit.score)),
                "semantic_score": 0.0,
                "metadata": dict(hit.metadata),
            }

        semantic_hits = self._semantic_hits(query, domain, limit)
        for item in semantic_hits:
            chunk_id = item["id"]
            document_id = item["document_id"]
            if document_id:
                document = self.store.get(document_id)
                if document is None or document.status != "active":
                    continue
            current = merged.setdefault(
                chunk_id,
                {
                    "id": chunk_id,
                    "document_id": document_id,
                    "document_version": item["document_version"],
                    "document_name": item["document_name"],
                    "category": item["category"],
                    "content": item["content"],
                    "section_title": item["section_title"],
                    "page": item["page"],
                    "keyword_score": 0.0,
                    "semantic_score": 0.0,
                    "metadata": {},
                },
            )
            current["semantic_score"] = max(current["semantic_score"], item["score"])
            for key, value in item["metadata"].items():
                current["metadata"].setdefault(key, value)

        results = []
        for values in merged.values():
            score = keyword_weight * values["keyword_score"] + semantic_weight * values["semantic_score"]
            if score < self.min_score:
                continue
            results.append(
                RetrievedChunk(
                    id=values["id"],
                    document_id=values["document_id"],
                    document_version=values["document_version"],
                    document_name=values["document_name"],
                    category=values["category"],
                    content=values["content"],
                    section_title=values["section_title"],
                    page=values["page"],
                    score=score,
                    keyword_score=values["keyword_score"],
                    semantic_score=values["semantic_score"],
                    metadata=values["metadata"],
                )
            )
        results.sort(key=lambda item: (-item.score, item.document_name, item.id))
        return results[:limit]

    def _semantic_hits(self, query: str, domain: str, limit: int) -> list[dict[str, Any]]:
        try:
            raw = self.semantic_search(query, domain, limit)
        except TypeError:
            raw = self.semantic_search(query, limit)
        results: list[dict[str, Any]] = []
        for index, value in enumerate(raw or []):
            if isinstance(value, tuple):
                document, distance = value
                semantic_score = 1.0 / (1.0 + max(0.0, float(distance)))
            else:
                document = value
                semantic_score = 1.0
            if not isinstance(document, Document):
                continue
            metadata = dict(document.metadata)
            chunk_id = metadata.get("chunk_id") or metadata.get("id")
            document_id = str(metadata.get("document_id", ""))
            if not chunk_id and document_id:
                chunk_id = f"{document_id}:{metadata.get('document_version', '1')}:{metadata.get('chunk_index', index)}"
            if not chunk_id:
                continue
            results.append(
                {
                    "id": str(chunk_id),
                    "document_id": document_id,
                    "document_version": str(metadata.get("document_version", "")),
                    "document_name": str(metadata.get("document_name") or metadata.get("source", "未知来源")),
                    "category": str(metadata.get("category", "其他")),
                    "content": document.page_content,
                    "section_title": str(metadata.get("section_title") or metadata.get("heading_path", "")),
                    "page": metadata.get("page"),
                    "score": semantic_score,
                    "metadata": metadata,
                }
            )
        return results
