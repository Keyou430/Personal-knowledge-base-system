# Agent Managed Experience Knowledge Base Implementation Plan

**Goal:** Add user-confirmed experience capture, duplicate review, version history, and library management to the current knowledge base.

**Architecture:** Structured experience cards are persisted independently from uploaded documents. A service layer produces and validates drafts, compares them with saved cards, and executes the user's save or merge choice. The UI holds temporary review state only.

## Task 1: Experience Store

Files: create `core/experience_store.py` and `tests/test_experience_store.py`; modify `config.py`.

- [ ] Write a failing test that creates an `ExperienceDraft`, saves it through `ExperienceStore`, and asserts card fields, a source record, and a `created` version exist.
- [ ] Run `.\\.venv\\Scripts\\python.exe -m pytest tests/test_experience_store.py -v` and verify it fails because the module is missing.
- [ ] Implement `ExperienceDraft`, `ExperienceCard`, and `ExperienceStore`. The store initializes card, source, and version tables; supports `create`, `get`, `list`, `search`, `get_sources`, and `get_versions`; serializes lists with UTF-8 JSON; and writes the initial snapshot in a transaction.
- [ ] Run the focused test and verify it passes.
- [ ] Add failing tests for normalized exact duplicates, update history, archive, and restore. Implement `find_exact_duplicate`, `update`, `archive`, and `restore`; each mutation must append a complete history snapshot in the same transaction.
- [ ] Run the focused test suite, then commit `config.py`, the store, and its tests with message `feat: add sqlite experience store`.

## Task 2: Cloud Drafting

Files: create `core/agent.py` and `tests/test_agent.py`.

- [ ] Write a failing test for parsing a valid card JSON response into `ExperienceDraft`, plus a failing test for missing `conclusion`.
- [ ] Run `.\\.venv\\Scripts\\python.exe -m pytest tests/test_agent.py -v` and verify the module-missing failure.
- [ ] Implement `AgentError`, `AgentOutputError`, `parse_experience_draft`, and `draft_experience`. The parser accepts JSON or fenced JSON, requires title/scenario/conclusion, and validates bounded steps/tags/sources lists.
- [ ] Add a fake-client test proving the request contains only the supplied question, answer, and selected source excerpts. Add missing-credential and malformed-response tests.
- [ ] Run focused tests and commit with message `feat: add validated experience drafting`.

## Task 3: Duplicate Review Pipeline

Files: create `core/experience_pipeline.py` and `tests/test_experience_pipeline.py`.

- [ ] Write a failing test showing exact duplicate input returns `exact_duplicate` without a write; write another showing a merge replaces approved fields, unions tags, and appends a `merged` version.
- [ ] Run `.\\.venv\\Scripts\\python.exe -m pytest tests/test_experience_pipeline.py -v` and verify the module-missing failure.
- [ ] Implement `DedupResult`, `prepare_save`, `save_new_experience`, `merge_experience`, and `summarize_field_diff`. Exact hash matches take priority; provided semantic matches require review; otherwise a card is ready to save. The pipeline never triggers a second cloud request.
- [ ] Run focused tests and commit with message `feat: add experience duplicate review workflow`.

## Task 4: Streamlit Capture and Library

Files: modify `app.py`, `README.md`, and `使用指南.md`; create `core/domains.py`, `tests/test_app_experience_helpers.py`, and `tests/test_app_startup.py`.

- [ ] Write failing tests for deterministic chat-entry IDs and dispatching exactly one selected view.
- [ ] Run both app test files and verify they fail because the helpers and dispatcher do not exist.
- [ ] Move directory-only operations to `core/domains.py`; defer heavy feature imports in `app.py`; add view dispatch for question-answering, browsing, upload, and the experience library.
- [ ] Under each stored answer, add capture action, explicit sharing notice, editable candidate fields, duplicate comparison, and new/merge/abandon actions. Keep draft and failure state in the session for retry.
- [ ] Add the experience library: text/tag search, details, source display, history, archive, restore, and index rebuild action.
- [ ] Run focused app tests, update both user documents, and commit with message `feat: add experience capture and library views`.

## Task 5: Semantic Index and Operational Verification

Files: create `core/experience_index.py` and `tests/test_experience_index.py`; modify the store and pipeline only as needed.

- [ ] Write a failing test with a fake failing index: saving persists the card and marks it pending instead of losing it.
- [ ] Implement independent experience-index `add`, `delete`, `search`, and `rebuild`; archived cards cannot appear as candidates. On index failure, preserve the saved card and expose rebuildable pending state.
- [ ] Run all tests with `.\\.venv\\Scripts\\python.exe -m pytest -v`, compile with `.\\.venv\\Scripts\\python.exe -m compileall -q app.py config.py core`, and check `git diff --check`.
- [ ] Start Streamlit on port 8501 and verify `http://127.0.0.1:8501` returns HTTP 200 before retrieval or capture operations.
- [ ] Commit with message `feat: add rebuildable experience index`.
