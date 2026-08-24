# -*- coding: utf-8 -*-
"""Business workflow for reviewing and saving experience cards."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from core.experience_store import ExperienceCard, ExperienceDraft, ExperienceStore


@dataclass(frozen=True)
class DedupResult:
    kind: str
    matches: list[ExperienceCard]


def prepare_save(
    store: ExperienceStore,
    draft: ExperienceDraft,
    semantic_matches: Iterable[ExperienceCard] = (),
) -> DedupResult:
    """Return a user-facing decision without mutating the store."""
    exact = store.find_exact_duplicate(draft)
    if exact is not None:
        return DedupResult(kind="exact_duplicate", matches=[exact])

    matches = list(semantic_matches)
    if matches:
        return DedupResult(kind="needs_review", matches=matches)
    return DedupResult(kind="ready", matches=[])


def save_new_experience(
    store: ExperienceStore,
    draft: ExperienceDraft,
) -> ExperienceCard:
    return store.create(draft)


def merge_experience(
    store: ExperienceStore,
    experience_id: str,
    draft: ExperienceDraft,
) -> ExperienceCard:
    """Apply the user-approved draft while preserving the old card's tags."""
    existing = store.get_required(experience_id)
    merged_tags = _unique_values([*existing.tags, *draft.tags])
    merged_draft = replace(draft, tags=merged_tags)
    return store.update(experience_id, merged_draft, change_type="merged")


def summarize_field_diff(
    existing: ExperienceCard,
    draft: ExperienceDraft,
) -> dict[str, dict[str, Any]]:
    """Return simple field-level before/after values for review UI."""
    values = {
        "title": (existing.title, draft.title),
        "scenario": (existing.scenario, draft.scenario),
        "conclusion": (existing.conclusion, draft.conclusion),
        "steps": (existing.steps, draft.steps),
        "tags": (existing.tags, draft.tags),
    }
    return {
        field: {"before": before, "after": after, "changed": before != after}
        for field, (before, after) in values.items()
    }


def _unique_values(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
