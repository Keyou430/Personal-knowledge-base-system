# Streamlit Startup Optimization Design

## Goal

Make the application interface available without initializing Chroma or the
embedding model. The first retrieval or ingestion action may pay the model-load
cost.

## Scope

- Keep Streamlit, local Chroma persistence, and the current single-user Windows
  deployment model.
- Keep the existing upload, browse, domain management, and question-answering
  capabilities.
- Do not change models, document formats, data layout, or introduce a service
  backend.

## Design

The application entry point will import only light UI/configuration code. It
will render one active view at a time rather than executing all tab bodies on
every Streamlit rerun.

The vector-store and document-loader dependencies will be imported by small
feature helpers only when an operation needs them. The embedding model and
per-domain vector stores remain process-local cached resources once requested.

Document-count statistics will be a deferred UI operation. The normal sidebar
and initial page do not call `get_domain_stats`; a user action can request the
count and trigger initialization when needed.

## Data Flow

1. `streamlit run app.py` imports and starts the UI without embedding-model
   construction.
2. Opening the page renders the selected view and file/domain names only.
3. Asking a question, adding documents, or explicitly loading statistics calls
   the corresponding lazy helper.
4. The helper creates the cached embedding/vector-store resource on its first
   use; later calls reuse it.

## Error Handling

- Existing user-facing ingestion and LLM API errors remain intact.
- Deferred statistics display an explicit unavailable state if the data store
  cannot be opened.
- Lazy imports preserve the original exception behavior at the feature boundary.

## Verification

- Unit tests prove that importing `app.py` does not import the retriever or
  document loader.
- Unit tests prove navigation renders only the selected view.
- Unit tests prove the sidebar does not request vector-store statistics until
  explicitly requested.
- Run the full test suite and compile checks.
- Start Streamlit and confirm the root page returns HTTP 200 without an
  embedding-model load message in the startup log.
