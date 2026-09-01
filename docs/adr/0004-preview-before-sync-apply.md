# ADR-0004: Preview Before Applying Directory Sync

## Status

Accepted

## Context

Incremental synchronization can touch many files and can create new document versions. The existing batch upload flow already separates preview from confirmation, while a scheduled task needs an explicit non-interactive mode. A default write operation would make an accidental source-directory selection difficult to recover from.

## Decision

The local sync CLI defaults to dry-run/preview. It reports new, changed, unchanged, missing, unsupported, and failed files without modifying the document archive or projections. A separate explicit `--apply` flag enables writes. Scheduled execution must opt into `--apply` in its task definition and must use a run lock, structured logs, and per-file failure isolation. Source removal remains non-destructive under ADR-0003.

## Consequences

Human operators can inspect a plan before changing the knowledge base, and automation has an auditable opt-in. The implementation needs stable preview output, an apply-only path, and a lock/retry policy. Operators must intentionally configure scheduled tasks rather than assuming that invoking the scanner performs writes.
