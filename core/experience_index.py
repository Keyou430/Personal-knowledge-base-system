# -*- coding: utf-8 -*-
"""Rebuildable semantic projection for experience cards."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.experience_store import ExperienceCard, ExperienceStore


class ExperienceIndex:
    def __init__(
        self,
        store: ExperienceStore,
        *,
        backend: str = "chroma",
        persist_directory: str | Path | None = None,
    ):
        self.store = store
        self.backend = backend
        self._memory: dict[str, ExperienceCard] = {}
        self._vectorstore: Any | None = None
        if backend == "chroma":
            from config import DATA_DIR
            from core.embedder import get_embedding_model
            from langchain_chroma import Chroma

            path = Path(persist_directory or Path(DATA_DIR) / "experience_index")
            path.mkdir(parents=True, exist_ok=True)
            self._vectorstore = Chroma(
                persist_directory=str(path),
                collection_name="experiences",
                embedding_function=get_embedding_model(),
            )
        elif backend != "memory":
            raise ValueError(f"不支持的经验索引后端: {backend}")

    def add(self, card: ExperienceCard) -> None:
        if self.backend == "memory":
            self._memory[card.id] = card
            return
        from langchain_core.documents import Document

        # Chroma versions differ on whether duplicate IDs are upserted or rejected.
        # Delete first so edits and merges always replace the indexed card.
        self._vectorstore.delete(ids=[card.id])
        self._vectorstore.add_documents(
            [
                Document(
                    page_content=self._card_text(card),
                    metadata={"experience_id": card.id},
                )
            ],
            ids=[card.id],
        )

    def delete(self, experience_id: str) -> None:
        if self.backend == "memory":
            self._memory.pop(experience_id, None)
            return
        self._vectorstore.delete(ids=[experience_id])

    def search(self, query: str, top_k: int = 5) -> list[ExperienceCard]:
        if self.backend == "memory":
            normalized_query = query.casefold().strip()
            scored = []
            for card in self._memory.values():
                searchable = self._card_text(card).casefold()
                score = int(bool(normalized_query) and normalized_query in searchable)
                if score:
                    scored.append((score, card))
            scored.sort(key=lambda pair: (-pair[0], pair[1].updated_at), reverse=False)
            return [card for _, card in scored[:top_k]]

        results = self._vectorstore.similarity_search(query, k=top_k)
        cards = []
        for document in results:
            experience_id = document.metadata.get("experience_id")
            card = self.store.get(experience_id) if experience_id else None
            if card and card.status == "active":
                cards.append(card)
        return cards

    def rebuild(self) -> None:
        if self.backend == "memory":
            self._memory.clear()
            for card in self.store.list():
                self.add(card)
                self.store.set_index_pending(card.id, False)
            return

        ids = [card.id for card in self.store.list(include_archived=True)]
        if ids:
            self._vectorstore.delete(ids=ids)
        for card in self.store.list():
            self.add(card)
            self.store.set_index_pending(card.id, False)

    @staticmethod
    def _card_text(card: ExperienceCard) -> str:
        return "\n".join(
            [card.title, card.scenario, card.conclusion, *card.steps, *card.tags]
        )
