# ADR-0007: Defer Authorization in the Local Edition

## Status

Accepted

## Context

The reference document defines tenant administrators, editors, read-only users, and permission scopes. The current product is explicitly single-user and has no identity provider, session authentication, or authorization middleware. Adding a permission field without enforcement would create a false sense of protection.

## Decision

The single-user local edition does not implement authentication, role-based access control, tenant filtering, or permission enforcement. Existing metadata such as owner, source, category, and version remains descriptive governance data. A future `permission` field may be reserved for migration compatibility, but it is not a security boundary and must not alter retrieval authorization in this edition.

## Consequences

The implementation remains honest about its threat model and avoids partial authorization. Local filesystem and process access remain the actual security boundary. Multi-user deployment requires a new architecture phase and ADR covering identity, roles, tenant-scoped storage, request filtering, and audit guarantees.
