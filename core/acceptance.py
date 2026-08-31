"""Repeatable local acceptance gate for the P1 knowledge-base release."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, build_opener

from core.backup import create_backup, restore_backup
from core.document_store import DocumentStore
from core.ingestion import ingest_file
from core.migration import rebuild_indexes


class AcceptanceConfigError(ValueError):
    """Raised when the acceptance gate inputs are not safe or complete."""


@dataclass(frozen=True)
class AcceptanceCheck:
    name: str
    status: str
    summary: str
    exit_code: int | None = None
    retry_needed: tuple[str, ...] = ()
    duration_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "exit_code": self.exit_code,
            "retry_needed": list(self.retry_needed),
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class AcceptanceReport:
    status: str
    started_at: str
    finished_at: str
    checks: tuple[AcceptanceCheck, ...]

    @property
    def retry_needed(self) -> tuple[str, ...]:
        return tuple(
            item
            for check in self.checks
            for item in check.retry_needed
        )

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(
            check.name
            for check in self.checks
            if check.status in {"failed", "error"}
        )

    @property
    def exit_code(self) -> int:
        """Return the documented process code for the aggregate report."""
        errors = [check for check in self.checks if check.status == "error"]
        if errors:
            return 3 if any((check.exit_code or 3) != 2 for check in errors) else 2
        if any(check.status == "failed" for check in self.checks):
            return 1
        return 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "checks": [check.as_dict() for check in self.checks],
            "retry_needed": list(self.retry_needed),
            "failures": list(self.failures),
            "exit_code": self.exit_code,
        }


CommandRunner = Callable[[Sequence[str | Path], Path, Mapping[str, str]], Any]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _run_command(
    command: Sequence[str | Path],
    cwd: Path,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(item) for item in command],
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return subprocess.CompletedProcess(
            [str(item) for item in command],
            124,
            stdout=error.stdout or "",
            stderr=error.stderr or "验收命令超时",
        )


def _exit_code(value: Any) -> int:
    try:
        code = int(value)
    except (TypeError, ValueError):
        return 3
    return code if code in {0, 1, 2, 3} else 3


def _command_check(
    name: str,
    command: Sequence[str | Path],
    *,
    cwd: Path,
    env: Mapping[str, str],
    runner: CommandRunner,
    summary_on_success: str,
    advisory: bool = False,
) -> AcceptanceCheck:
    started = time.perf_counter()
    result = runner(command, cwd, env)
    duration_ms = int((time.perf_counter() - started) * 1000)
    exit_code = _exit_code(getattr(result, "returncode", 3))
    if exit_code == 0:
        return AcceptanceCheck(
            name=name,
            status="passed",
            summary=summary_on_success,
            exit_code=0,
            duration_ms=duration_ms,
        )
    if advisory:
        status = "warning"
    elif exit_code == 1:
        status = "failed"
    else:
        status = "error"
    return AcceptanceCheck(
        name=name,
        status=status,
        summary=("环境依赖检查存在未满足项" if advisory else "命令未通过"),
        exit_code=exit_code,
        duration_ms=duration_ms,
    )


_METRIC_NAMES = (
    "hit_at_k",
    "active_version_hit_rate",
    "refusal_accuracy",
    "citation_coverage",
)
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _safe_error_code(value: Any) -> str:
    if isinstance(value, str) and _SAFE_ERROR_CODE.fullmatch(value):
        return value
    return "invalid_evaluation_report"


def _metric_values(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, float] = {}
    for name in _METRIC_NAMES:
        raw = value.get(name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        try:
            number = float(raw)
        except (OverflowError, TypeError, ValueError):
            return None
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            return None
        result[name] = number
    return result


def _evaluation_check(report_path: Path, *, evaluator_exit_code: int | None = None) -> AcceptanceCheck:
    report_exists = report_path.is_file()
    process_code = 0 if evaluator_exit_code is None and report_exists else _exit_code(evaluator_exit_code)
    if not report_exists:
        if process_code == 1:
            status, summary, exit_code = "failed", "评测命令未生成质量报告", 1
        elif process_code == 2:
            status, summary, exit_code = "error", "评测输入配置或索引无效，未生成报告", 2
        else:
            status, summary, exit_code = "error", "评测命令异常，未生成报告", 3
        return AcceptanceCheck(
            name="offline_evaluation",
            status=status,
            summary=summary,
            exit_code=exit_code,
        )
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return AcceptanceCheck(
            name="offline_evaluation",
            status="error",
            summary="评测报告无法读取",
            exit_code=process_code if process_code in {2, 3} else 3,
        )
    if not isinstance(payload, dict):
        return AcceptanceCheck(
            name="offline_evaluation",
            status="error",
            summary="评测报告格式无效",
            exit_code=process_code if process_code in {2, 3} else 3,
        )
    status = payload.get("status")
    if status == "failed":
        if process_code in {2, 3}:
            return AcceptanceCheck(
                name="offline_evaluation",
                status="error",
                summary="评测命令异常退出",
                exit_code=process_code,
            )
        error_code = _safe_error_code(payload.get("error_code") or "quality_gate_failed")
        return AcceptanceCheck(
            name="offline_evaluation",
            status="failed",
            summary=f"离线评测未通过: {error_code}",
            exit_code=1,
        )
    if status == "error":
        error_code = _safe_error_code(payload.get("error_code"))
        return AcceptanceCheck(
            name="offline_evaluation",
            status="error",
            summary=f"离线评测配置或运行错误: {error_code}",
            exit_code=process_code if process_code in {2, 3} else 2,
        )
    if status != "passed":
        return AcceptanceCheck(
            name="offline_evaluation",
            status="error",
            summary="评测报告状态无效",
            exit_code=3,
        )
    metrics = _metric_values(payload.get("metrics"))
    thresholds = _metric_values(payload.get("thresholds"))
    if metrics is None or thresholds is None:
        return AcceptanceCheck(
            name="offline_evaluation",
            status="error",
            summary="评测报告缺少完整且有限的指标或阈值",
            exit_code=process_code if process_code in {2, 3} else 3,
        )
    if process_code != 0:
        return AcceptanceCheck(
            name="offline_evaluation",
            status="failed" if process_code == 1 else "error",
            summary="离线评测命令退出码异常",
            exit_code=process_code,
        )
    regressions = [name for name in _METRIC_NAMES if metrics[name] + 1e-12 < thresholds[name]]
    metric_summary = ", ".join(f"{key}={metrics[key]:.3f}" for key in _METRIC_NAMES)
    if regressions:
        return AcceptanceCheck(
            name="offline_evaluation",
            status="failed",
            summary=f"离线评测指标未达到阈值: {', '.join(regressions)} ({metric_summary})",
            exit_code=1,
        )
    return AcceptanceCheck(
        name="offline_evaluation",
        status="passed",
        summary=f"离线评测通过: {metric_summary}",
        exit_code=0,
    )


def run_restore_rehearsal(root: str | Path) -> AcceptanceCheck:
    """Exercise backup, restore, and keyword-index rebuild on disposable data."""
    started = time.perf_counter()
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="p1-rehearsal-", dir=base))
    try:
        source_root = workspace / "source"
        raw_dir = source_root / "raw"
        source_file = raw_dir / "默认" / "验收样例.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("# 验收样例\n本地恢复演练内容", encoding="utf-8")
        document_db = source_root / "documents.db"
        experience_db = source_root / "experiences.db"
        experience_db.write_bytes(b"disposable-experience-db")
        source_store = DocumentStore(document_db)
        ingest_file(
            source_file,
            domain="默认",
            store=source_store,
            vectorstore=None,
            category="验收",
            owner="",
            source="rehearsal",
        )
        backup_dir = workspace / "backup"
        created = create_backup(
            backup_dir,
            document_db_path=document_db,
            experience_db_path=experience_db,
            raw_dir=raw_dir,
        )
        if not created.valid:
            raise RuntimeError("备份完整性校验未通过")
        destination = workspace / "restored"
        restored = restore_backup(backup_dir, destination)
        if not restored.valid:
            raise RuntimeError("恢复报告未通过")
        restored_store = DocumentStore(destination / "documents.db")
        rebuild = rebuild_indexes(store=restored_store, vectorstore=None)
        if rebuild.missing or rebuild.retry_needed:
            raise RuntimeError("重建自检存在缺失或待重试项")
        restored_raw = destination / "raw" / "默认" / source_file.name
        if restored_raw.read_text(encoding="utf-8") != source_file.read_text(encoding="utf-8"):
            raise RuntimeError("恢复后的原始资料校验不一致")
        return AcceptanceCheck(
            name="restore_rehearsal",
            status="passed",
            summary="临时数据备份、恢复和关键词索引重建通过",
            exit_code=0,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception as error:
        return AcceptanceCheck(
            name="restore_rehearsal",
            status="failed",
            summary=f"恢复演练失败: {type(error).__name__}",
            exit_code=1,
            retry_needed=("验收样例.md",),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        import shutil

        shutil.rmtree(workspace, ignore_errors=True)


_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


def _validate_streamlit_url(url: str) -> None:
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("invalid URL")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Streamlit 地址必须是 HTTP(S) 本地地址")
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("invalid URL") from error
    if not hostname:
        raise ValueError("Streamlit 地址缺少主机名")
    normalized = hostname.rstrip(".").lower()
    if normalized in _LOCAL_HOSTNAMES:
        return
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        try:
            infos = socket.getaddrinfo(normalized, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except OSError as error:
            raise ValueError("Streamlit 地址必须解析到本地回环地址") from error
        try:
            loopback = bool(infos) and all(ipaddress.ip_address(info[4][0]).is_loopback for info in infos)
        except ValueError as error:
            raise ValueError("Streamlit 地址必须解析到本地回环地址") from error
        if not loopback:
            raise ValueError("Streamlit 地址必须解析到本地回环地址")
        return
    if not address.is_loopback:
        raise ValueError("Streamlit 地址必须是本地回环地址")


class _LocalOnlyRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_streamlit_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _streamlit_check(url: str) -> AcceptanceCheck:
    started = time.perf_counter()
    try:
        _validate_streamlit_url(url)
    except (ValueError, TypeError):
        return AcceptanceCheck(
            name="streamlit_health",
            status="error",
            summary="Streamlit 地址必须是本地 HTTP(S) 回环地址",
            exit_code=2,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    try:
        opener = build_opener(_LocalOnlyRedirectHandler())
        with opener.open(url, timeout=10) as response:
            status = int(response.status)
        passed = status == 200
        return AcceptanceCheck(
            name="streamlit_health",
            status="passed" if passed else "failed",
            summary="本地 Streamlit 健康检查通过" if passed else "本地 Streamlit 返回非 200",
            exit_code=0 if passed else 1,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except (ValueError, TypeError):
        return AcceptanceCheck(
            name="streamlit_health",
            status="error",
            summary="Streamlit 重定向目标必须是本地 HTTP(S) 回环地址",
            exit_code=2,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except (OSError, URLError):
        return AcceptanceCheck(
            name="streamlit_health",
            status="failed",
            summary="无法连接指定的本地 Streamlit 地址",
            exit_code=1,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def run_acceptance_gate(
    *,
    project_root: str | Path,
    cases_path: str | Path,
    corpus_path: str | Path,
    streamlit_url: str | None = None,
    command_runner: CommandRunner = _run_command,
    include_pytest: bool = True,
    include_dependency_check: bool = True,
) -> AcceptanceReport:
    root = Path(project_root).resolve()
    cases = Path(cases_path)
    corpus = Path(corpus_path)
    if not cases.is_absolute():
        cases = root / cases
    if not corpus.is_absolute():
        corpus = root / corpus
    if not root.is_dir() or not cases.is_file() or not corpus.is_file():
        raise AcceptanceConfigError("项目根目录、案例文件和语料文件必须存在")

    started_at = _now()
    checks: list[AcceptanceCheck] = []
    temp_root = root / ".pytest-tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    allowed_env = (
        "PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "WINDIR", "ComSpec",
        "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
        "PROGRAMDATA", "ALLUSERSPROFILE", "PUBLIC",
        "PYTHONHOME", "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONHASHSEED",
        "VIRTUAL_ENV", "LANG", "LC_ALL",
    )
    env = {key: os.environ[key] for key in allowed_env if key in os.environ}
    env.update({
        "TEMP": str(temp_root),
        "TMP": str(temp_root),
        "PYTHONPATH": str(root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    })
    if include_pytest:
        checks.append(
            _command_check(
                "pytest",
                [sys.executable, "-m", "pytest", "-q", "--basetemp", str(temp_root / "pytest-basetemp")],
                cwd=root,
                env=env,
                runner=command_runner,
                summary_on_success="自动化测试通过",
            )
        )

    with tempfile.TemporaryDirectory(prefix="p1-acceptance-", dir=temp_root) as temp_dir:
        evaluation_path = Path(temp_dir) / "evaluation.json"
        evaluation_command = [
            sys.executable,
            "-m",
            "core.evaluation",
            "--mode",
            "fixture",
            "--cases",
            cases,
            "--corpus",
            corpus,
            "--json-out",
            evaluation_path,
        ]
        evaluation_result = command_runner(evaluation_command, root, env)
        evaluation_check = _evaluation_check(
            evaluation_path,
            evaluator_exit_code=getattr(evaluation_result, "returncode", 3),
        )
        checks.append(evaluation_check)

    checks.append(
        _command_check(
            "compileall",
            [sys.executable, "-m", "compileall", "-q", "app.py", "config.py", "core"],
            cwd=root,
            env=env,
            runner=command_runner,
            summary_on_success="Python 模块编译检查通过",
        )
    )
    if include_dependency_check:
        checks.append(
            _command_check(
                "dependency_check",
                [sys.executable, "-m", "pip", "check"],
                cwd=root,
                env=env,
                runner=command_runner,
                summary_on_success="项目环境依赖检查通过",
                advisory=True,
            )
        )
    checks.append(run_restore_rehearsal(temp_root))
    if streamlit_url:
        checks.append(_streamlit_check(streamlit_url))

    status = "error" if any(check.status == "error" for check in checks) else (
        "failed" if any(check.status == "failed" for check in checks) else "passed"
    )
    return AcceptanceReport(
        status=status,
        started_at=started_at,
        finished_at=_now(),
        checks=tuple(checks),
    )


def render_text_report(report: AcceptanceReport) -> str:
    lines = [f"P1 验收门禁: {report.status} (exit_code={report.exit_code})"]
    for check in report.checks:
        suffix = f"; retry_needed={','.join(check.retry_needed)}" if check.retry_needed else ""
        lines.append(f"- {check.name}: {check.status} - {check.summary}{suffix}")
    return "\n".join(lines)


def _write_json(path: str | Path | None, report: AcceptanceReport) -> None:
    if path is None:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local P1 acceptance gate")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--cases", default="tests/fixtures/evaluation_cases.json")
    parser.add_argument("--corpus", default="tests/fixtures/evaluation_corpus.json")
    parser.add_argument("--streamlit-url")
    parser.add_argument("--json-out")
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--skip-dependency-check", action="store_true")
    try:
        args = parser.parse_args(argv)
        report = run_acceptance_gate(
            project_root=args.project_root,
            cases_path=args.cases,
            corpus_path=args.corpus,
            streamlit_url=args.streamlit_url,
            include_pytest=not args.skip_pytest,
            include_dependency_check=not args.skip_dependency_check,
        )
        _write_json(args.json_out, report)
        print(render_text_report(report))
        return report.exit_code
    except AcceptanceConfigError:
        return 2
    except Exception:
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
