# Offline RAG Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为本地知识库增加一个不调用云端模型的离线 RAG 评测和回归门禁，报告检索命中、active 版本、拒答和引用覆盖率。

**Architecture:** 新增 `core.evaluation` 作为纯评测边界，使用严格校验后的 JSON 案例驱动一个可注入的检索器。公开 fixture 模式使用合成脱敏语料、临时 SQLite/FTS5 和确定性语义回调；live 模式读取本地文档档案和 Chroma，使用显式基线比较。评测不会改变 `HybridRetriever` 或 `generator` 的业务行为，也不会创建 OpenAI client。

**Tech Stack:** Python 3.12, dataclasses, JSON, SQLite/FTS5, existing `HybridRetriever`, pytest, argparse.

---

## 文件职责

- Create: `core/evaluation.py` — 案例 schema、校验、评测指标、报告、基线比较、fixture/live runner 和 CLI。
- Create: `tests/test_evaluation.py` — schema、指标、基线、隐私和退出码测试。
- Create: `tests/fixtures/evaluation_cases.json` — 至少 20 条公开脱敏案例和公开阈值。
- Create: `tests/fixtures/evaluation_corpus.json` — fixture 模式使用的合成文档、版本、章节、页码和正文。
- Modify: `.gitignore` — 忽略 `data/evaluation/` 私有案例、基线和报告，但保留目录说明文件（若需要）。
- Modify: `README.md`、`使用指南.md` — 记录公开 fixture、live 私有评测和失败码。

## Task 1: 案例模型与严格校验

**Files:**
- Create: `core/evaluation.py`
- Create: `tests/test_evaluation.py`

- [ ] **Step 1: Write the failing tests for valid cases and schema errors**

```python
import json
import pytest

from core.evaluation import EvaluationConfigError, load_cases


def write_cases(tmp_path, payload):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def valid_payload():
    return {
        "version": 1,
        "thresholds": {
            "hit_at_k": 1.0,
            "active_version_hit_rate": 1.0,
            "refusal_accuracy": 1.0,
            "citation_coverage": 1.0,
        },
        "cases": [{
            "id": "case-001",
            "question": "制度何时生效？",
            "domain": "制度",
            "expected_documents": ["制度.md"],
            "expected_versions": ["2"],
            "expected_sections": ["生效日期"],
            "expected_pages": [],
            "required_terms": ["2026"],
            "should_refuse": False,
        }],
    }


def test_load_cases_returns_typed_cases(tmp_path):
    config = load_cases(write_cases(tmp_path, valid_payload()))
    assert config.version == 1
    assert config.cases[0].id == "case-001"
    assert config.thresholds.hit_at_k == 1.0


@pytest.mark.parametrize("change", [
    {"version": 2},
    {"cases": []},
    {"duplicate_ids": True},
    {"refusal_with_expectations": True},
])
def test_load_cases_rejects_invalid_payload(tmp_path, change):
    payload = valid_payload()
    if "version" in change:
        payload["version"] = change["version"]
    elif "cases" in change:
        payload["cases"] = change["cases"]
    elif change.get("duplicate_ids"):
        payload["cases"].append(dict(payload["cases"][0]))
    else:
        payload["cases"][0]["should_refuse"] = True
    with pytest.raises(EvaluationConfigError):
        load_cases(write_cases(tmp_path, payload))
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
New-Item -ItemType Directory -Force .pytest-tmp | Out-Null
$env:TEMP = (Resolve-Path .pytest-tmp).Path
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation.py -k "load_cases" -v
```

Expected: collection failure because `core.evaluation` and `load_cases` do not exist.

- [ ] **Step 3: Implement the typed schema and validator**

Add frozen dataclasses `EvaluationThresholds`, `EvaluationCase`, `EvaluationConfig`, and `EvaluationConfigError`. Implement `load_cases(path, *, require_thresholds=False)` with UTF-8 JSON loading, top-level object validation, schema version `1`, unique safe IDs, non-empty question/domain, array type checks, refusal/expectation consistency, positive page checks, bounded string lengths, and threshold range checks. Do not include question text in exception messages.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the same focused pytest command. Expected: all schema tests pass.

- [ ] **Step 5: Commit the schema boundary**

```powershell
git add core/evaluation.py tests/test_evaluation.py
git commit -m "feat: add offline evaluation case schema"
```

## Task 2: Metric engine and safe reports

**Files:**
- Modify: `core/evaluation.py`
- Modify: `tests/test_evaluation.py`

- [ ] **Step 1: Write failing metric tests**

Use `RetrievedChunk` instances and a small injected retriever:

```python
from core.hybrid_retriever import RetrievedChunk
from core.evaluation import evaluate_cases


def chunk(name="制度.md", version="2", section="生效日期", page=3, content="2026-01-01"):
    return RetrievedChunk(
        id=f"{name}:{version}", document_id="doc-1",
        document_version=version, document_name=name, category="制度",
        content=content, section_title=section, page=page,
        score=0.9, keyword_score=0.9, semantic_score=0.9,
    )


def test_evaluate_cases_reports_hit_version_refusal_and_citation_rates():
    cases = load_cases_from_payload({
        "version": 1,
        "thresholds": {k: 1.0 for k in (
            "hit_at_k", "active_version_hit_rate",
            "refusal_accuracy", "citation_coverage",
        )},
        "cases": [
            {"id": "hit", "question": "q", "domain": "制度",
             "expected_documents": ["制度.md"], "expected_versions": ["2"],
             "expected_sections": ["生效"], "expected_pages": [3],
             "required_terms": ["2026"], "should_refuse": False},
            {"id": "refuse", "question": "q2", "domain": "制度",
             "expected_documents": [], "expected_versions": [],
             "expected_sections": [], "expected_pages": [],
             "required_terms": [], "should_refuse": True},
        ],
    })
    report = evaluate_cases(cases, lambda case, top_k: [chunk()] if case.id == "hit" else [])
    assert report.metrics.hit_at_k == 1.0
    assert report.metrics.active_version_hit_rate == 1.0
    assert report.metrics.refusal_accuracy == 1.0
    assert report.metrics.citation_coverage == 1.0
    assert report.status == "passed"
```

The test helper may construct typed cases directly if the production API keeps file loading separate; it must not bypass the same validation rules in the public tests.

- [ ] **Step 2: Run the focused metric test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation.py -k "metric or evaluate" -v
```

Expected: failure because `evaluate_cases`, report types, and metric computation do not exist.

- [ ] **Step 3: Implement deterministic metric evaluation**

Add `EvaluationMetrics`, `CaseResult`, and `EvaluationReport`. Implement `evaluate_cases(config, retriever, *, top_k=5, active_version_lookup=None)` where `retriever(case, top_k)` returns a list of source-rich chunks. Apply the exact design rules: all required terms must occur in one matching result; section/page constraints are substring/exact matches; expected version is checked on a matching document; refusal is based on empty results; citation readiness requires name, version, section/page, and content. Record only case ID and reason codes such as `document_mismatch`, `inactive_version_recalled`, `missing_citation_metadata`, and `unexpected_results`.

- [ ] **Step 4: Add threshold and report serialization tests**

Test a below-threshold report has status `failed`, a malformed retriever result has status `error`, JSON serialization contains metrics and case IDs but not question text or content, and division-by-zero is avoided when no cases participate in a version-specific metric.

- [ ] **Step 5: Implement safe human/JSON reports and commit**

Implement `report_to_dict(report)`, `render_text_report(report)`, and threshold status evaluation. Reports must include current metric values, thresholds, status, case IDs and reason codes only. Then run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation.py -v
git add core/evaluation.py tests/test_evaluation.py
git commit -m "feat: evaluate RAG retrieval metrics safely"
```

## Task 3: Public deterministic fixture corpus and runner

**Files:**
- Create: `tests/fixtures/evaluation_cases.json`
- Create: `tests/fixtures/evaluation_corpus.json`
- Modify: `core/evaluation.py`
- Modify: `tests/test_evaluation.py`

- [ ] **Step 1: Write fixture runner tests before adding the corpus**

```python
def test_fixture_runner_is_deterministic_and_does_not_create_llm_client(monkeypatch):
    def fail_client(*args, **kwargs):
        raise AssertionError("fixture evaluation must not create an OpenAI client")
    monkeypatch.setattr("openai.OpenAI", fail_client)
    first = run_fixture_evaluation(
        Path("tests/fixtures/evaluation_cases.json"),
        Path("tests/fixtures/evaluation_corpus.json"),
    )
    second = run_fixture_evaluation(
        Path("tests/fixtures/evaluation_cases.json"),
        Path("tests/fixtures/evaluation_corpus.json"),
    )
    assert first.status == "passed"
    assert first.metrics == second.metrics
    assert len(first.case_results) >= 20
```

- [ ] **Step 2: Run the fixture test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation.py -k fixture -v
```

Expected: failure because fixture files and `run_fixture_evaluation` do not exist.

- [ ] **Step 3: Add synthetic public corpus and 20+ cases**

Create a small UTF-8 corpus containing synthetic documents for ordinary sections, numbered rules, dates, FAQ, tables, page metadata, and two versions of one same-name document. Create at least 20 cases with no real customer names, paths, secrets, or copied source paragraphs. Set all public thresholds to `1.0`; each case must have stable IDs and cover the required categories.

- [ ] **Step 4: Implement deterministic fixture construction**

Implement `run_fixture_evaluation(cases_path, corpus_path, *, top_k=5)` to create a temporary `DocumentStore`, load the synthetic corpus into it, expose a deterministic semantic callback that returns corpus chunks in a stable order, and call the same metric engine used by live mode. The fixture runner must never call `get_embedding_model`, Chroma, or OpenAI.

- [ ] **Step 5: Run fixture tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation.py -k fixture -v
```

Expected: fixture cases pass deterministically. Commit:

```powershell
git add core/evaluation.py tests/test_evaluation.py tests/fixtures/evaluation_cases.json tests/fixtures/evaluation_corpus.json
git commit -m "feat: add deterministic public RAG evaluation fixtures"
```

## Task 4: Live mode and explicit baseline comparison

**Files:**
- Modify: `core/evaluation.py`
- Modify: `tests/test_evaluation.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing baseline tests**

Test that `write_baseline` creates a file containing evaluator version, cases SHA-256, metrics and timestamp; a later run with the same cases passes; a metric decrease greater than `0.0001`, missing baseline, malformed baseline, or changed cases hash returns the documented error/failure status without overwriting the old baseline.

- [ ] **Step 2: Run baseline tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation.py -k baseline -v
```

Expected: failure because baseline functions do not exist.

- [ ] **Step 3: Implement baseline and live runner**

Implement `cases_sha256(path)`, `write_baseline(path, cases_path, report)`, and `compare_baseline(path, cases_path, report)`. Use exit-status semantics in the returned report: quality regression is `failed`, missing/invalid baseline or index configuration is `error`; never auto-accept or overwrite a baseline. Implement `run_live_evaluation(cases_path, *, database_path, domain, top_k, baseline_path, accept_baseline=False)` using `DocumentStore` and `HybridRetriever`; semantic search must be injected from the existing local Chroma path and failures must be surfaced as configuration errors. The runner must not instantiate an LLM client.

- [ ] **Step 4: Add private-data ignore rules and privacy tests**

Add `data/evaluation/` to `.gitignore`. Test that reports contain case IDs and reason codes but not question text, returned chunk content, API keys, or absolute private paths. Test that `--accept-baseline` is the only path that writes a new baseline.

- [ ] **Step 5: Run focused tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation.py -k "baseline or live or privacy" -v
git add core/evaluation.py tests/test_evaluation.py .gitignore
git commit -m "feat: add private live evaluation baselines"
```

## Task 5: CLI, documentation, and full verification

**Files:**
- Modify: `core/evaluation.py`
- Modify: `tests/test_evaluation.py`
- Modify: `README.md`
- Modify: `使用指南.md`

- [ ] **Step 1: Write CLI exit-code tests**

Use subprocess or the module `main(argv)` entry point to verify:

```python
assert main(["--mode", "fixture", "--cases", str(case_path)]) == 0
assert main(["--mode", "fixture", "--cases", str(invalid_path)]) == 2
assert main(["--mode", "fixture", "--cases", str(failing_path)]) == 1
```

Also verify `--json-out` writes a report with `status`, `metrics`, `thresholds`, and safe failure identifiers.

- [ ] **Step 2: Run CLI tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation.py -k cli -v
```

Expected: failure because the argparse entry point and output writer do not exist.

- [ ] **Step 3: Implement CLI and safe output**

Implement `main(argv=None)` with `--mode fixture|live`, `--cases`, `--corpus`, `--database`, `--domain`, `--top-k`, `--baseline`, `--accept-baseline`, and `--json-out`. Validate the case file before constructing any runner. Map report status to exit codes `0` passed, `1` failed quality gate, `2` invalid configuration/index/baseline, `3` unexpected exception. Use `if __name__ == "__main__": raise SystemExit(main())`.

- [ ] **Step 4: Document commands and operational boundary**

Add a short section to `README.md` and `使用指南.md` covering the public fixture command, private live command, explicit baseline acceptance, exit codes, and the fact that private cases and reports under `data/evaluation/` are not committed or uploaded.

- [ ] **Step 5: Run complete verification**

Run from a PowerShell session configured for the project-local temporary directory:

```powershell
New-Item -ItemType Directory -Force .pytest-tmp | Out-Null
$env:TEMP = (Resolve-Path .pytest-tmp).Path
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\python.exe -m core.evaluation --mode fixture --cases tests/fixtures/evaluation_cases.json --corpus tests/fixtures/evaluation_corpus.json
.\.venv\Scripts\python.exe -m compileall -q app.py config.py core
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Expected: the existing suite and new evaluation tests pass, fixture CLI exits `0`, compileall and pip check are clean, and no private data is tracked.

- [ ] **Step 6: Commit implementation and update Issue #1**

```powershell
git add core/evaluation.py tests/test_evaluation.py tests/fixtures/evaluation_cases.json tests/fixtures/evaluation_corpus.json README.md 使用指南.md .gitignore
git commit -m "feat: deliver offline RAG evaluation gate"
gh issue comment 1 --repo Keyou430/Personal-knowledge-base-system --body "Implemented offline evaluation schema, deterministic public fixtures, live baseline comparison, safe reports, CLI exit codes, and documentation. Verification: pytest, fixture CLI, compileall, pip check, and git diff --check all pass."
```

## Self-review checklist

- Spec coverage: schema, public/private data boundary, four metrics, thresholds, baselines, CLI, failure codes, privacy, tests, and documentation each map to a task above.
- Placeholder scan: no `TBD`, `TODO`, or unspecified implementation step is used.
- Type consistency: all tasks use `EvaluationConfig`, `EvaluationCase`, `EvaluationReport`, `load_cases`, `evaluate_cases`, `run_fixture_evaluation`, `run_live_evaluation`, `write_baseline`, `compare_baseline`, and `main` consistently.
- Scope: this plan does not change retrieval behavior, add LLM judging, or implement P2 feedback/Rerank work.
