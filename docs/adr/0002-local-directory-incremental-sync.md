# ADR-0002: Use Local Directory Incremental Sync First

## Status

Accepted

## Context

The reference design lists event-triggered updates, scheduled source scans, and connectors for Feishu, group messages, web pages, and recordings. The current application already has local raw directories, explicit migration, content-hash idempotency, and version-aware ingestion, but it does not have a scheduler, file watcher, or external connector contract.

## Decision

The local sustainable edition will first support incremental scanning of `data/raw/<domain>` (or an explicitly configured local source directory). The sync operation will be invokable through a deterministic CLI and may be run by Windows Task Scheduler. A long-running watcher and external source connectors remain out of scope until their credentials, retry semantics, privacy policy, and operational ownership are specified.

## Consequences

This reuses the existing migration and ingestion primitives and keeps updates offline, reproducible, and easy to inspect. The scan must compare source fingerprints with the document archive, avoid reprocessing unchanged files, and report every action. Scheduling is an operational wrapper around the CLI rather than logic embedded in the Streamlit process. External-source and watcher designs will require separate ADRs.
