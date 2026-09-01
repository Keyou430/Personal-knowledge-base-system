# ADR-0005: Use Explicit Manual Semantic-Index Rebuilds

## Status

Accepted

## Context

The local edition must remain useful when the embedding model is unavailable or a vector projection is damaged. At the same time, the operator does not want a hidden background worker mutating local data. The repository already treats SQLite/FTS as the authoritative archive and exposes an explicit rebuild action in the document browser.

## Decision

Document ingestion and synchronization commit the authoritative archive and FTS projection even when semantic indexing fails. The affected record is marked as pending semantic rebuild and the UI reports it. No background reindex worker is created. The user may explicitly click “重建知识索引” to rebuild the semantic projection; until then, keyword retrieval remains available and the pending state is visible.

## Consequences

The system has a deterministic foreground-only operational model and a usable keyword fallback. Semantic quality can be temporarily degraded, so health views and answer surfaces must make the pending state visible. Rebuild must be idempotent, safe to repeat, and scoped to the selected local domain or records.
