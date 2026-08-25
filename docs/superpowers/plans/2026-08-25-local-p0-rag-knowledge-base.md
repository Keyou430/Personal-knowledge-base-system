# Local P0 RAG Knowledge Base Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the local Streamlit RAG app with document archives, structured chunks, version replacement, SQLite FTS5 + Chroma hybrid retrieval, refusal on low relevance, auditable citations, and explicit migration/rebuild.

**Architecture:** SQLite is the source of truth for document records, versions, chunks, and FTS5 projection metadata. Chroma remains a rebuildable semantic projection. A document-ingestion service owns file hashing, structured loading, version replacement, and index synchronization; a hybrid retriever owns two-way recall, fusion, and threshold filtering; `app.py` stays a thin UI boundary.

**Tech Stack:** Python 3.12, Streamlit, SQLite 3.53 FTS5, LangChain `Document`, Chroma, existing document loaders, pytest.

---

## Task 1: Document Archive and FTS5 Store

**Files:**
- Create: `core/document_store.py`
- Create: `tests/test_document_store.py`
- Modify: `config.py` only if a document database path constant is needed

- [ ] **Step 1: Write failing archive tests**

Add tests for creating an active document record, storing its chunks, returning a stable content hash, and searching the FTS5 projection:

```python
def test_document_store_persists_active_document_and_fts_results(tmp_path):
    store = DocumentStore(tmp_path / "documents.db")
    document = store.create_document(
        domain="制度",
        name="报销制度.md",
        category="制度",
        owner="财务部",
        source="upload",
        content="报销流程需要提交发票和审批单。",
        chunks=[ChunkRecord(id="chunk-1", content="报销流程需要提交发票和审批单。", section_title="报销流程")],
    )

    assert document.status == "active"
    assert document.version == "1"
    assert store.search_keyword("报销")[0].id == "chunk-1"
    assert store.get_chunks(document.id)[0].section_title == "报销流程"
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_document_store.py -v
```

Expected: collection error because `core.document_store` and `DocumentStore` do not exist.

- [ ] **Step 3: Implement the minimal SQLite/FTS5 store**

Implement `DocumentRecord`, `ChunkRecord`, and `DocumentStore` with these public methods:

```python
DocumentStore(path)
create_document(domain, name, category, owner, source, content, chunks, version=None) -> DocumentRecord
find_by_hash(domain, content_hash) -> DocumentRecord | None
find_active(domain, name) -> DocumentRecord | None
replace_document(document_id, ..., chunks, version) -> DocumentRecord
get(document_id) -> DocumentRecord | None
get_chunks(document_id, version=None) -> list[ChunkRecord]
search_keyword(query, domain=None, limit=50) -> list[ChunkHit]
list_documents(domain=None, include_superseded=False) -> list[DocumentRecord]
mark_index_pending(document_id, pending=True) -> None
```

Create `documents`, `document_chunks`, and an FTS5 virtual table indexing chunk content, file name, section title, and category. FTS results must join only `documents.status = 'active'`.

- [ ] **Step 4: Add version and failure tests**

Test same-hash idempotency, changed-content version replacement, old-version filtering, and transaction rollback when chunk insertion fails.

- [ ] **Step 5: Run focused tests and commit**

Run the focused file and then commit:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_document_store.py -v
git add core/document_store.py tests/test_document_store.py config.py
git commit -m "feat: add document archive and FTS5 store"
```

## Task 2: Structured Loading and Chunking

**Files:**
- Modify: `core/loader.py`
- Modify: `core/splitter.py`
- Create: `tests/test_loader_structure.py`
- Create: `tests/test_splitter_structure.py`

- [ ] **Step 1: Write failing structured-loading tests**

Use a temporary DOCX created with `python-docx` and assert that `load_docx` returns paragraph/title and table metadata rather than one flattened string:

```python
def test_load_docx_preserves_heading_and_table(tmp_path):
    path = make_docx(tmp_path / "制度.docx")
    docs = load_docx(str(path))

    assert any(doc.metadata["block_type"] == "heading" for doc in docs)
    assert any(doc.metadata["block_type"] == "table" for doc in docs)
    assert "审批人" in next(doc.page_content for doc in docs if doc.metadata["block_type"] == "table")
```

Add splitter tests proving parent heading context, intact table chunks, FAQ boundaries, and PDF page metadata preservation.

- [ ] **Step 2: Run the focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_loader_structure.py tests/test_splitter_structure.py -v
```

Expected: failures because current DOCX loading flattens paragraphs and the splitter has no structural strategy.

- [ ] **Step 3: Implement structured loader output**

Change `load_docx` to emit one `Document` per meaningful paragraph/table block with `block_type`, `heading_level`, `heading_path`, `table_index`, and `source`. Keep the existing `load_document` dispatch and non-DOCX loaders compatible. Ensure all text is UTF-8 and empty blocks are skipped.

- [ ] **Step 4: Implement structural splitting**

Add a structure-aware path in `split_documents` that:

1. Carries heading paths into each chunk.
2. Emits a table as one atomic chunk unless it exceeds the maximum, then splits by row groups.
3. Detects FAQ question boundaries and keeps each answer with its question.
4. Keeps PDF `page` metadata on every derived chunk.
5. Uses an approximately 800-character target and 1000-character maximum for ordinary prose.

- [ ] **Step 5: Run focused tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_loader_structure.py tests/test_splitter_structure.py -v
git add core/loader.py core/splitter.py tests/test_loader_structure.py tests/test_splitter_structure.py
git commit -m "feat: preserve document structure during chunking"
```

## Task 3: Versioned Ingestion and Chroma Synchronization

**Files:**
- Create: `core/ingestion.py`
- Create: `tests/test_ingestion.py`
- Modify: `core/retriever.py`
- Modify: `config.py` if a document index path is required

- [ ] **Step 1: Write failing ingestion tests**

Cover upload of a new document, same-hash idempotency, changed-content replacement, stable chunk IDs, deletion of superseded Chroma IDs, and index failure leaving `index_pending=True` while SQLite remains readable.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ingestion.py -v
```

Expected: collection error because `core.ingestion` and stable-ID retriever operations do not exist.

- [ ] **Step 3: Implement `ingest_file` and projection synchronization**

Expose:

```python
ingest_file(path, *, domain, store, vectorstore=None, category="其他", owner="", source="upload") -> IngestionResult
```

The function computes a content hash, loads and splits the file, writes the raw file before processing, uses the document store for idempotency/version selection, writes Chroma IDs as `{document_id}:{version}:{chunk_index}`, removes superseded IDs, and marks pending state on projection failure.

- [ ] **Step 4: Run tests, then commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ingestion.py -v
git add core/ingestion.py core/retriever.py tests/test_ingestion.py config.py
git commit -m "feat: add versioned document ingestion"
```

## Task 4: Hybrid Retrieval and Low-Relevance Gate

**Files:**
- Create: `core/hybrid_retriever.py`
- Create: `tests/test_hybrid_retriever.py`
- Modify: `config.py` for explicit retrieval limits and threshold constants

- [ ] **Step 1: Write failing hybrid-retrieval tests**

Use a fake semantic backend and the real temporary SQLite FTS5 store to prove keyword and semantic results merge by chunk ID, numeric/date queries prefer keyword matches, inactive versions are excluded, and low scores return an empty list.

- [ ] **Step 2: Run the focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_hybrid_retriever.py -v
```

Expected: collection error because `core.hybrid_retriever` does not exist.

- [ ] **Step 3: Implement the retriever**

Expose:

```python
HybridRetriever(store, semantic_search, *, top_k=8, keyword_weight=0.55, semantic_weight=0.45, min_score=0.18)
search(query, domain, top_k=None) -> list[RetrievedChunk]
```

Normalize scores, detect exact identifiers/dates/numbers, apply latest-version preference for policy queries, merge duplicates, and return source-rich objects containing document name, version, section, page, content, and score breakdown.

- [ ] **Step 4: Run focused tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_hybrid_retriever.py -v
git add core/hybrid_retriever.py tests/test_hybrid_retriever.py config.py
git commit -m "feat: add hybrid retrieval and relevance gate"
```

## Task 5: Auditable Answer Contract and UI Integration

**Files:**
- Modify: `core/generator.py`
- Modify: `app.py`
- Create: `tests/test_generator_contract.py`
- Modify: `tests/test_app_startup.py`
- Modify: `tests/test_app_experience_helpers.py` only if stable chat source shape changes

- [ ] **Step 1: Write failing generator-contract tests**

Test that context includes document name, version, section, page, and excerpt; empty retrieval returns the refusal string without constructing an OpenAI client; and the prompt requires conclusion, evidence, and citations.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_generator_contract.py -v
```

Expected: failures because current context only includes `source` and content, and empty retrieval still reaches the generic API path.

- [ ] **Step 3: Implement generator changes**

Add a `RetrievedChunk`-compatible context formatter and a single refusal constant. Update both streaming and non-streaming paths to refuse before API construction when no reliable chunks exist. Keep the existing OpenAI-compatible configuration and error messages.

- [ ] **Step 4: Integrate the UI with ingestion and hybrid retrieval**

Replace direct `load_document` → `split_documents` → `add_documents` calls in `render_upload_section` with `ingest_file`. Replace `search_with_score` in `render_qa_section` with `HybridRetriever.search`. Display source name, version, section/page, keyword/semantic contribution, and refusal state. Keep experience capture source payloads limited to the selected citations.

- [ ] **Step 5: Run focused tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_generator_contract.py tests/test_app_startup.py tests/test_app_experience_helpers.py -v
git add core/generator.py app.py tests/test_generator_contract.py tests/test_app_startup.py tests/test_app_experience_helpers.py
git commit -m "feat: add auditable answers and hybrid QA flow"
```

## Task 6: Explicit Migration and Rebuild UI

**Files:**
- Create: `core/migration.py`
- Create: `tests/test_migration.py`
- Modify: `app.py`

- [ ] **Step 1: Write failing migration tests**

Test scanning `data/raw/{domain}`, creating archive records for existing files, reusing same-hash records, continuing after one malformed file, and rebuilding projections without deleting raw files.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_migration.py -v
```

Expected: collection error because `core.migration` does not exist.

- [ ] **Step 3: Implement migration and rebuild services**

Expose:

```python
migrate_domain(domain, *, raw_dir, store, vectorstore=None) -> MigrationReport
rebuild_indexes(*, store, vectorstore=None, domains=None) -> RebuildReport
```

Process files independently, preserve originals, reuse hashes, report successes/failures/pending items, and never run automatically during app import.

- [ ] **Step 4: Add explicit UI actions**

Add a maintenance section to the document browser or upload view with “迁移现有资料” and “重建知识索引” actions. Show progress and per-file results. Do not instantiate Embedding or Chroma until the user starts migration, rebuild, upload, or retrieval.

- [ ] **Step 5: Run focused tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_migration.py tests/test_app_startup.py -v
git add core/migration.py tests/test_migration.py app.py
git commit -m "feat: add explicit document migration and rebuild"
```

## Task 7: Full Verification and Documentation

**Files:**
- Modify: `README.md`
- Modify: `使用指南.md`
- Modify or create: focused test files only when a verified regression is found

- [ ] **Step 1: Document the P0 workflow**

Document document categories, versions, structured citations, hybrid retrieval behavior, refusal behavior, upload replacement, migration, rebuild, and index-pending recovery. State that cloud experience capture remains user-triggered and separate from document ingestion.

- [ ] **Step 2: Run the complete verification suite**

Use the project-local temporary directory on Windows:

```powershell
New-Item -ItemType Directory -Force .pytest-tmp | Out-Null
$env:TEMP=(Resolve-Path .pytest-tmp).Path
$env:TMP=$env:TEMP
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\python.exe -m compileall -q app.py config.py core
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Expected: all tests pass, compileall exits 0, pip check reports no broken requirements, and git diff --check is empty.

- [ ] **Step 3: Verify the current app on a fresh port**

Confirm any existing listener before using a port. Start the current app on an unused port such as 8502, request `/`, and verify HTTP 200 without triggering retrieval or migration. Keep any pre-existing user-owned listener untouched.

- [ ] **Step 4: Review the requirement checklist and commit documentation**

```powershell
git add README.md 使用指南.md
git commit -m "docs: document local P0 knowledge base workflow"
git status --short
```

The final status must distinguish committed P0 changes from any unrelated pre-existing worktree edits.
