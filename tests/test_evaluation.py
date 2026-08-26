import json
from pathlib import Path

import pytest

from core.hybrid_retriever import RetrievedChunk
from core.evaluation import (
    EvaluationConfigError,
    evaluate_cases,
    load_cases,
    load_cases_from_payload,
    report_to_dict,
    cases_sha256,
    compare_baseline,
    main,
    run_fixture_evaluation,
    write_baseline,
)


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


def chunk(name="制度.md", version="2", section="生效日期", page=3, content="2026-01-01"):
    return RetrievedChunk(
        id=f"{name}:{version}",
        document_id="doc-1",
        document_version=version,
        document_name=name,
        category="制度",
        content=content,
        section_title=section,
        page=page,
        score=0.9,
        keyword_score=0.9,
        semantic_score=0.9,
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


def test_report_is_safe_and_failed_threshold_is_observable():
    payload = valid_payload()
    payload["thresholds"] = {key: 1.0 for key in payload["thresholds"]}
    config = load_cases_from_payload(payload)
    report = evaluate_cases(config, lambda case, top_k: [])
    assert report.status == "failed"
    rendered = json.dumps(report_to_dict(report), ensure_ascii=False)
    assert "制度何时生效" not in rendered
    assert "2026-01-01" not in rendered
    assert "case-001" in rendered


def test_malformed_retriever_result_is_error():
    config = load_cases_from_payload(valid_payload())
    report = evaluate_cases(config, lambda case, top_k: [object()])
    assert report.status == "error"
    assert report.error_code == "malformed_retriever_result"


def test_inactive_version_is_reported_without_exposing_content():
    config = load_cases_from_payload(valid_payload())
    report = evaluate_cases(
        config,
        lambda case, top_k: [chunk(version="1")],
        active_version_lookup=lambda domain, name: "2",
    )
    assert report.status == "failed"
    assert "inactive_version_recalled" in report.case_results[0].reason_codes
    rendered = json.dumps(report_to_dict(report), ensure_ascii=False)
    assert "2026-01-01" not in rendered


def test_fixture_files_are_present_and_public():
    cases_path = Path("tests/fixtures/evaluation_cases.json")
    corpus_path = Path("tests/fixtures/evaluation_corpus.json")
    assert cases_path.exists()
    assert corpus_path.exists()


def test_fixture_runner_is_deterministic_and_does_not_create_llm_client(monkeypatch):
    def fail_client(*args, **kwargs):
        raise AssertionError("fixture evaluation must not create an OpenAI client")

    monkeypatch.setattr("openai.OpenAI", fail_client)
    cases_path = Path("tests/fixtures/evaluation_cases.json")
    corpus_path = Path("tests/fixtures/evaluation_corpus.json")
    first = run_fixture_evaluation(cases_path, corpus_path)
    second = run_fixture_evaluation(cases_path, corpus_path)
    assert first.status == "passed"
    assert first.metrics == second.metrics
    assert len(first.case_results) >= 20


def test_baseline_round_trip_and_regression_does_not_overwrite(tmp_path):
    cases_path = write_cases(tmp_path, valid_payload())
    config = load_cases(cases_path)
    report = evaluate_cases(config, lambda case, top_k: [chunk()])
    baseline_path = tmp_path / "baseline.json"
    write_baseline(baseline_path, cases_path, report)
    original = baseline_path.read_text(encoding="utf-8")
    assert json.loads(original)["cases_sha256"] == cases_sha256(cases_path)

    degraded = evaluate_cases(config, lambda case, top_k: [])
    compared = compare_baseline(baseline_path, cases_path, degraded)
    assert compared.status == "failed"
    assert compared.error_code == "baseline_regression"
    assert baseline_path.read_text(encoding="utf-8") == original


def test_missing_baseline_is_configuration_error(tmp_path):
    cases_path = write_cases(tmp_path, valid_payload())
    config = load_cases(cases_path)
    report = evaluate_cases(config, lambda case, top_k: [chunk()])
    compared = compare_baseline(tmp_path / "missing.json", cases_path, report)
    assert compared.status == "error"
    assert compared.error_code == "missing_or_invalid_baseline"


def test_cli_fixture_exit_codes_and_json_output(tmp_path):
    output = tmp_path / "report.json"
    assert main([
        "--mode", "fixture", "--cases", "tests/fixtures/evaluation_cases.json",
        "--corpus", "tests/fixtures/evaluation_corpus.json", "--json-out", str(output),
    ]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert "metrics" in payload and "thresholds" in payload

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    assert main([
        "--mode", "fixture", "--cases", str(invalid),
        "--corpus", "tests/fixtures/evaluation_corpus.json",
    ]) == 2
