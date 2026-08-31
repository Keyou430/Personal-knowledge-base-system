import json
from types import SimpleNamespace

from core.acceptance import AcceptanceCheck, AcceptanceReport, main, run_acceptance_gate, run_restore_rehearsal


def _fake_result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_acceptance_gate_runs_required_checks_and_writes_safe_report(tmp_path):
    cases = tmp_path / "cases.json"
    corpus = tmp_path / "corpus.json"
    cases.write_text("{}", encoding="utf-8")
    corpus.write_text("{}", encoding="utf-8")

    def fake_runner(command, cwd, env):
        if "core.evaluation" in command:
            output_path = command[command.index("--json-out") + 1]
            output_path.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "metrics": {
                            "hit_at_k": 1.0,
                            "active_version_hit_rate": 1.0,
                            "refusal_accuracy": 1.0,
                            "citation_coverage": 1.0,
                        },
                        "thresholds": {
                            "hit_at_k": 1.0,
                            "active_version_hit_rate": 1.0,
                            "refusal_accuracy": 1.0,
                            "citation_coverage": 1.0,
                        },
                    }
                ),
                encoding="utf-8",
            )
        return _fake_result(stdout="67 passed")

    report = run_acceptance_gate(
        project_root=tmp_path,
        cases_path=cases,
        corpus_path=corpus,
        command_runner=fake_runner,
    )

    assert report.status == "passed"
    assert {check.name for check in report.checks} >= {
        "pytest",
        "offline_evaluation",
        "compileall",
        "restore_rehearsal",
    }
    rendered = json.dumps(report.as_dict(), ensure_ascii=False)
    assert "67 passed" not in rendered
    assert "LLM_API_KEY" not in rendered


def test_acceptance_gate_fails_on_quality_regression_without_exposing_source_text(tmp_path):
    cases = tmp_path / "cases.json"
    corpus = tmp_path / "corpus.json"
    cases.write_text("{}", encoding="utf-8")
    corpus.write_text("{}", encoding="utf-8")
    secret_question = "绝不应出现在验收报告的私有问题"

    def fake_runner(command, cwd, env):
        if "core.evaluation" in command:
            output_path = command[command.index("--json-out") + 1]
            output_path.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "error_code": "quality_regression",
                        "case_results": [{"id": "case-001", "reason_codes": ["hit_missed"]}],
                    }
                ),
                encoding="utf-8",
            )
            return _fake_result(returncode=1, stdout=secret_question)
        return _fake_result()

    report = run_acceptance_gate(
        project_root=tmp_path,
        cases_path=cases,
        corpus_path=corpus,
        command_runner=fake_runner,
    )

    assert report.status == "failed"
    rendered = json.dumps(report.as_dict(), ensure_ascii=False)
    assert secret_question not in rendered
    assert "quality_regression" in rendered


def test_restore_rehearsal_recovers_documents_and_rebuilds_keyword_index(tmp_path):
    check = run_restore_rehearsal(tmp_path)

    assert check.status == "passed"
    assert check.retry_needed == ()


def test_acceptance_cli_writes_safe_report_and_maps_status_to_exit_code(tmp_path, monkeypatch):
    report = AcceptanceReport(
        status="passed",
        started_at="2026-08-31T00:00:00+00:00",
        finished_at="2026-08-31T00:00:01+00:00",
        checks=(AcceptanceCheck("pytest", "passed", "自动化测试通过", exit_code=0),),
    )
    monkeypatch.setattr("core.acceptance.run_acceptance_gate", lambda **kwargs: report)
    output = tmp_path / "evidence.json"

    exit_code = main(["--project-root", str(tmp_path), "--json-out", str(output)])

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "passed"
