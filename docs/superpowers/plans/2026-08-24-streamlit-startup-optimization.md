# Streamlit Startup Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Streamlit interface open without importing retrieval/loading dependencies or constructing the embedding model, then initialize those resources only for an action that needs them.

**Architecture:** Move filesystem-only domain functions into `core/domains.py`, so navigation and file browsing stay light. `app.py` will select and render exactly one view per rerun, with local imports at retrieval, upload, and explicit-statistics boundaries. Existing process-level model/vector-store caching remains in `core.retriever`.

**Tech Stack:** Python 3.12, Streamlit, pytest, Chroma, LangChain, sentence-transformers.

---

### Task 1: Add a lightweight domain filesystem module

**Files:**
- Create: `core/domains.py`
- Create: `tests/test_domains.py`

- [ ] **Step 1: Write failing tests for non-vector domain operations**

```python
def test_create_domain_does_not_import_retriever(tmp_path, monkeypatch):
    monkeypatch.setattr(domains, "DOMAINS_DIR", str(tmp_path / "domains"))

    assert domains.create_domain("默认") is True
    assert "core.retriever" not in sys.modules
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/test_domains.py -v`

Expected: FAIL because `core.domains` does not exist.

- [ ] **Step 3: Implement only directory, listing, and raw-file helpers**

```python
def create_domain(domain: str) -> bool:
    domain_path = os.path.join(DOMAINS_DIR, domain)
    if os.path.exists(domain_path):
        return False
    os.makedirs(domain_path, exist_ok=True)
    return True
```

Keep `list_domains`, `delete_domain`, and `list_domain_files` free of Chroma,
LangChain, and embedding imports.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/test_domains.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the completed task**

```powershell
git add core/domains.py tests/test_domains.py
git commit -m "feat: add lightweight domain storage helpers"
```

### Task 2: Add regression tests for lazy application boundaries

**Files:**
- Create: `tests/test_app_startup.py`
- Modify: `app.py`

- [ ] **Step 1: Write a failing import isolation test**

```python
def test_importing_app_does_not_import_retriever_or_loader():
    code = "import app, sys; assert 'core.retriever' not in sys.modules; assert 'core.loader' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/test_app_startup.py::test_importing_app_does_not_import_retriever_or_loader -v`

Expected: FAIL because `app.py` imports both modules at module load.

- [ ] **Step 3: Implement view dispatch and lazy feature imports**

```python
def render_active_view(view: str) -> None:
    if view == VIEW_QA:
        render_qa_section()
    elif view == VIEW_BROWSE:
        render_browse_section()
    else:
        render_upload_section()
```

Use the new `core.domains` module for sidebar and browse filesystem operations.
Import loader, retriever, and generator functions inside the click/action paths
that require them.

- [ ] **Step 4: Run the import-isolation test to verify it passes**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/test_app_startup.py::test_importing_app_does_not_import_retriever_or_loader -v`

Expected: PASS.

- [ ] **Step 5: Commit the completed task**

```powershell
git add app.py tests/test_app_startup.py
git commit -m "perf: defer heavy knowledge base imports"
```

### Task 3: Render only the selected view and defer statistics

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app_startup.py`

- [ ] **Step 1: Write failing tests for view dispatch and deferred loading**

```python
def test_render_active_view_calls_only_requested_view(monkeypatch):
    called = []
    monkeypatch.setattr(app, "render_qa_section", lambda: called.append("qa"))
    monkeypatch.setattr(app, "render_browse_section", lambda: called.append("browse"))
    monkeypatch.setattr(app, "render_upload_section", lambda: called.append("upload"))

    app.render_active_view(app.VIEW_QA)

    assert called == ["qa"]
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/test_app_startup.py -v`

Expected: FAIL because the view constants/dispatcher do not yet exist or tabs run every view.

- [ ] **Step 3: Implement a single-view navigation control and explicit statistics action**

```python
if st.button("加载文本片段统计", key=f"stats_{current_domain}"):
    from core.retriever import get_domain_stats
    st.session_state[stats_key] = get_domain_stats(current_domain)
```

Replace `st.tabs` with a navigation selection whose value is passed to
`render_active_view`. Do not call `get_domain_stats` from the sidebar or during
initial view rendering.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/test_app_startup.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the completed task**

```powershell
git add app.py tests/test_app_startup.py
git commit -m "perf: render one view and defer domain stats"
```

### Task 4: Verify the end-to-end startup contract

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document first-use initialization behavior**

```markdown
首次提问、处理文档或加载片段统计时，系统会初始化本地嵌入模型；之后会在当前服务进程中复用。
```

- [ ] **Step 2: Run all automated checks**

Run: `.\\.venv\\Scripts\\python.exe -m pytest -v`

Expected: PASS with no test failures.

Run: `.\\.venv\\Scripts\\python.exe -m compileall -q app.py config.py core`

Expected: exit code 0.

- [ ] **Step 3: Start the application and verify the page is available without model initialization**

Run: `.\\.venv\\Scripts\\python.exe -m streamlit run app.py --server.headless true`

Expected: `http://127.0.0.1:8501` responds with HTTP 200 and the startup log has no `正在加载 Embedding 模型` entry before a first-use action.

- [ ] **Step 4: Commit the documentation update**

```powershell
git add README.md
git commit -m "docs: explain deferred model initialization"
```
