# ADR-0006: Minimize Cloud LLM Data Egress

## Status

Accepted

## Context

The current local application uses a configured OpenAI-compatible LLM endpoint for answer generation. The reference design permits a cloud DeepSeek default and a local open-source model for private deployments. Sending full documents, database contents, or unlimited conversation history would exceed the minimum data needed to answer a question.

## Decision

Cloud LLM generation remains the default for the local sustainable edition, but each request may contain only the current user question and the final Top-K retrieved evidence needed for that answer. Raw file paths, unrelated documents, the full local database, and unbounded chat history are excluded. The UI must disclose that selected content is sent to the configured endpoint. A local privacy mode disables cloud calls and returns local retrieval evidence or an explicit refusal when no local generator is available.

## Consequences

The product stays practical on ordinary hardware while defining a bounded egress contract. Prompt assembly and privacy filtering become part of the generation boundary and need regression tests. Local privacy mode may provide less fluent answers until a local model is configured; it must never silently fall back to cloud transmission.
