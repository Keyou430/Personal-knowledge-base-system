# ADR-0001: Lock the Local-First Sustainable Edition Scope

## Status

Accepted

## Context

The current product is a single-user local Streamlit application with SQLite-backed document and experience stores. The reference development document also describes a broader enterprise platform with tenant isolation, external connectors, APIs, authentication, and an administration console. Those capabilities require separate deployment, identity, security, and data-isolation decisions that are not yet specified in the repository.

## Decision

The current delivery scope is the single-user local sustainable edition. The next iterations will strengthen local ingestion, incremental updates, feedback/experience workflows, quality evaluation, backup/rebuild, and operational self-checks. Enterprise multi-tenancy, external APIs/connectors, authentication, and an administration console remain out of scope for this edition and must be re-evaluated as a separate architecture phase.

## Consequences

The team can improve reliability and daily usefulness without prematurely introducing an incomplete tenant or identity model. Local paths and SQLite remain valid boundaries for this phase, but they must not be presented as a production multi-tenant security boundary. Any future multi-tenant work will need a new ADR covering deployment topology, identity, authorization, tenant-scoped storage, and migration.
