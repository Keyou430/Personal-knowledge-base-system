# ADR-0003: Preserve Knowledge When a Source File Is Removed

## Status

Accepted

## Context

Local directory synchronization must cope with accidental deletes, moves, and temporary mount failures. The current document archive tracks active and superseded versions but has no source-presence state. Treating every missing path as a delete would make a transient filesystem problem destructive and would erase audit evidence.

## Decision

An incremental scan will never delete document records, chunks, historical versions, or backups merely because a source path is absent or renamed. It will record a source-missing/tombstone state for the affected record, exclude that record from default retrieval, and report it for review. A rename is initially represented as a new source observation plus the original source becoming missing; any consolidation based on content identity requires an explicit, separately tested rule. Physical purge requires a user-confirmed cleanup operation.

## Consequences

Accidental deletion and temporary unavailability are recoverable, and audit history remains intact. The archive needs a source-presence field/state and the UI/CLI must surface pending review items. Storage can retain stale records until a deliberate cleanup, so retention and purge policies must be specified later.
