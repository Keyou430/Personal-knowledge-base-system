# ADR-0008: Make Local Incremental Sync the Next Vertical Slice

## Status

Accepted

## Context

The local sustainable edition still has several possible enhancements: directory synchronization, external connectors, feedback workflows, authorization, background services, and multi-tenant deployment. Implementing several of these together would mix unrelated operational and security concerns and make acceptance evidence harder to isolate.

## Decision

The next implementation slice is limited to local directory incremental synchronization. It includes source fingerprinting; dry-run and explicit apply modes; new, changed, unchanged, missing, unsupported, and failed outcomes; non-destructive source-missing state; per-file failure isolation; a single-run lock; privacy-safe structured logs; CLI documentation; and focused regression/acceptance tests. External connectors, authorization, background workers, and multi-tenancy are excluded from this slice.

## Consequences

The next delivery has a testable end-to-end boundary and directly implements ADR-0002 through ADR-0005. Existing migration, ingestion, document-store, metadata, observability, and acceptance modules can be extended without redesigning the whole product. Other enhancements remain available as later slices after this one passes its acceptance criteria.
