"""Offline retrieval evaluation with safe, deterministic reports.

This module deliberately does not import the answer generator or create an
LLM client.  SQLite remains the authoritative archive in live mode; vector
stores are treated as injectable/rebuildable projections.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from langchain_core.documents import Document

from core.document_store import ChunkRecord, DocumentStore
from core.hybrid_retriever import HybridRetriever, RetrievedChunk


EVALUATOR_VERSION = "1"
_THRESHOLD_NAMES = (
    "hit_at_k",
    "active_version_hit_rate",
    "refusal_accuracy",
    "citation_coverage",
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class EvaluationConfigError(ValueError):
    """Raised when an evaluation case/configuration is unsafe or malformed."""


@dataclass(frozen=True)
class EvaluationThresholds:
    hit_at_k: float = 1.0
    active_version_hit_rate: float = 1.0
    refusal_accuracy: float = 1.0
    citation_coverage: float = 1.0


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    question: str
    domain: str
    expected_documents: tuple[str, ...] = ()
    expected_versions: tuple[str, ...] = ()
    expected_sections: tuple[str, ...] = ()
    expected_pages: tuple[int, ...] = ()
    required_terms: tuple[str, ...] = ()
    should_refuse: bool = False


@dataclass(frozen=True)
class EvaluationConfig:
    version: int
    thresholds: EvaluationThresholds
    cases: tuple[EvaluationCase, ...]


@dataclass(frozen=True)
class EvaluationMetrics:
    hit_at_k: float
    active_version_hit_rate: float
    refusal_accuracy: float
    citation_coverage: float


@dataclass(frozen=True)
class CaseResult:
    id: str
    hit: bool
    active_version_hit: bool
    refusal_correct: bool
    citation_covered: bool
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationReport:
    status: str
    metrics: EvaluationMetrics
    thresholds: EvaluationThresholds
    case_results: tuple[CaseResult, ...] = ()
    error_code: str | None = None


def _fail(path: str, reason: str) -> EvaluationConfigError:
    return EvaluationConfigError(f"invalid evaluation config: {path}: {reason}")


def _string_list(value: Any, path: str, *, max_items: int = 32) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail(path, "must be an array")
    if len(value) > max_items:
        raise _fail(path, "too many items")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise _fail(f"{path}[{index}]", "must be a non-empty string")
        if len(item) > 512:
            raise _fail(f"{path}[{index}]", "string is too long")
        result.append(item.strip())
    return tuple(result)


def _page_list(value: Any, path: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise _fail(path, "must be an array")
    if len(value) > 32:
        raise _fail(path, "too many items")
    pages: list[int] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise _fail(f"{path}[{index}]", "must be a positive integer")
        pages.append(item)
    return tuple(pages)


def _thresholds(payload: Any) -> EvaluationThresholds:
    if not isinstance(payload, dict):
        raise _fail("thresholds", "must be an object")
    values: dict[str, float] = {}
    for name in _THRESHOLD_NAMES:
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _fail(f"thresholds.{name}", "must be a number")
        number = float(value)
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise _fail(f"thresholds.{name}", "must be between 0 and 1")
        values[name] = number
    return EvaluationThresholds(**values)


def _config_from_payload(payload: Any, *, require_thresholds: bool = False) -> EvaluationConfig:
    if not isinstance(payload, dict):
        raise _fail("root", "must be an object")
    if payload.get("version") != 1:
        raise _fail("version", "must equal 1")
    if require_thresholds and "thresholds" not in payload:
        raise _fail("thresholds", "is required")
    thresholds = _thresholds(payload.get("thresholds", {name: 1.0 for name in _THRESHOLD_NAMES}))
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise _fail("cases", "must be a non-empty array")
    if len(raw_cases) > 10000:
        raise _fail("cases", "too many cases")

    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_cases):
        path = f"cases[{index}]"
        if not isinstance(raw, dict):
            raise _fail(path, "must be an object")
        case_id = raw.get("id")
        if not isinstance(case_id, str) or not _ID_RE.fullmatch(case_id):
            raise _fail(f"{path}.id", "must be a safe identifier")
        if case_id in seen:
            raise _fail(f"{path}.id", "must be unique")
        seen.add(case_id)
        question = raw.get("question")
        domain = raw.get("domain")
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise _fail(f"{path}.question", "must be a non-empty string")
        if not isinstance(domain, str) or not domain.strip() or len(domain) > 128:
            raise _fail(f"{path}.domain", "must be a non-empty string")

        expected_documents = _string_list(raw.get("expected_documents", []), f"{path}.expected_documents")
        expected_versions = _string_list(raw.get("expected_versions", []), f"{path}.expected_versions")
        expected_sections = _string_list(raw.get("expected_sections", []), f"{path}.expected_sections")
        expected_pages = _page_list(raw.get("expected_pages", []), f"{path}.expected_pages")
        required_terms = _string_list(raw.get("required_terms", []), f"{path}.required_terms")
        should_refuse = raw.get("should_refuse")
        if not isinstance(should_refuse, bool):
            raise _fail(f"{path}.should_refuse", "must be boolean")
        expectations = expected_documents + expected_versions + expected_sections + tuple(map(str, expected_pages)) + required_terms
        if should_refuse and expectations:
            raise _fail(path, "refusal cases cannot define expectations")
        if not should_refuse and not expectations:
            raise _fail(path, "non-refusal cases need an expectation")
        cases.append(EvaluationCase(
            id=case_id,
            question=question.strip(),
            domain=domain.strip(),
            expected_documents=expected_documents,
            expected_versions=expected_versions,
            expected_sections=expected_sections,
            expected_pages=expected_pages,
            required_terms=required_terms,
            should_refuse=should_refuse,
        ))
    return EvaluationConfig(version=1, thresholds=thresholds, cases=tuple(cases))


def load_cases(path: str | Path, *, require_thresholds: bool = False) -> EvaluationConfig:
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationConfigError(f"cannot read evaluation config: {type(exc).__name__}") from exc
    return _config_from_payload(payload, require_thresholds=require_thresholds)


def load_cases_from_payload(payload: Any, *, require_thresholds: bool = False) -> EvaluationConfig:
    return _config_from_payload(payload, require_thresholds=require_thresholds)


def _coerce_chunk(value: Any) -> RetrievedChunk:
    if isinstance(value, RetrievedChunk):
        return value
    required = ("id", "document_id", "document_version", "document_name", "category", "content", "section_title", "score")
    if isinstance(value, dict):
        source = value
        get = source.get
    else:
        get = lambda key, default=None: getattr(value, key, default)
    if any(get(key, None) is None for key in required):
        raise TypeError("malformed retriever result")
    return RetrievedChunk(
        id=str(get("id")), document_id=str(get("document_id")), document_version=str(get("document_version")),
        document_name=str(get("document_name")), category=str(get("category")), content=str(get("content")),
        section_title=str(get("section_title")), page=get("page"), score=float(get("score")),
        keyword_score=float(get("keyword_score", 0.0) or 0.0), semantic_score=float(get("semantic_score", 0.0) or 0.0),
        metadata=dict(get("metadata", {}) or {}),
    )


def _matches(case: EvaluationCase, chunk: RetrievedChunk, *, include_version: bool = True) -> bool:
    if case.expected_documents and chunk.document_name not in case.expected_documents:
        return False
    if include_version and case.expected_versions and chunk.document_version not in case.expected_versions:
        return False
    if case.expected_sections and not any(section in chunk.section_title for section in case.expected_sections):
        return False
    if case.expected_pages and chunk.page not in case.expected_pages:
        return False
    return all(term in chunk.content for term in case.required_terms)


def _active_for_chunk(case: EvaluationCase, chunk: RetrievedChunk, lookup: Callable[..., Any] | None) -> bool:
    if lookup is None:
        return True
    try:
        active = lookup(case.domain, chunk.document_name)
    except TypeError:
        active = lookup(chunk.document_name)
    if active is None:
        return True
    if hasattr(active, "version"):
        active = active.version
    return str(active) == str(chunk.document_version)


def _citation_ready(chunk: RetrievedChunk) -> bool:
    return bool(
        chunk.document_name.strip()
        and chunk.document_version.strip()
        and (chunk.section_title.strip() or chunk.page is not None)
        and chunk.content.strip()
    )


def _empty_metrics() -> EvaluationMetrics:
    return EvaluationMetrics(0.0, 0.0, 0.0, 0.0)


def _error_report(config: EvaluationConfig, code: str) -> EvaluationReport:
    return EvaluationReport("error", _empty_metrics(), config.thresholds, error_code=code)


def evaluate_cases(
    config: EvaluationConfig,
    retriever: Callable[[EvaluationCase, int], Sequence[Any]],
    *,
    top_k: int = 5,
    active_version_lookup: Callable[..., Any] | None = None,
) -> EvaluationReport:
    if not isinstance(config, EvaluationConfig) or top_k <= 0:
        return _error_report(config, "invalid_evaluation_input")
    results: list[CaseResult] = []
    try:
        for case in config.cases:
            try:
                raw_results = retriever(case, top_k)
            except Exception:
                return _error_report(config, "retriever_error")
            if raw_results is None:
                raw_results = []
            try:
                chunks = tuple(_coerce_chunk(value) for value in list(raw_results)[:top_k])
            except Exception:
                return _error_report(config, "malformed_retriever_result")
            if case.should_refuse:
                refusal_correct = not chunks
                results.append(CaseResult(
                    id=case.id, hit=False, active_version_hit=True, refusal_correct=refusal_correct,
                    citation_covered=True, reason_codes=() if refusal_correct else ("unexpected_results",),
                ))
                continue

            matching = tuple(chunk for chunk in chunks if _matches(case, chunk))
            hit = bool(matching)
            active_hit = any(_active_for_chunk(case, chunk, active_version_lookup) for chunk in matching)
            version_candidates = tuple(chunk for chunk in chunks if _matches(case, chunk, include_version=False))
            citation = bool(matching) and any(_citation_ready(chunk) for chunk in matching)
            reasons: list[str] = []
            if not hit:
                if case.expected_documents and not any(chunk.document_name in case.expected_documents for chunk in chunks):
                    reasons.append("document_mismatch")
                if case.expected_versions and not any(chunk.document_version in case.expected_versions for chunk in chunks):
                    reasons.append("version_mismatch")
                if case.expected_sections and not any(any(section in chunk.section_title for section in case.expected_sections) for chunk in chunks):
                    reasons.append("section_mismatch")
                if case.expected_pages and not any(chunk.page in case.expected_pages for chunk in chunks):
                    reasons.append("page_mismatch")
                if case.required_terms and not any(all(term in chunk.content for term in case.required_terms) for chunk in chunks):
                    reasons.append("required_terms_missing")
                if not reasons:
                    reasons.append("no_matching_result")
            if matching and not active_hit:
                reasons.append("inactive_version_recalled")
            elif version_candidates and not any(_active_for_chunk(case, chunk, active_version_lookup) for chunk in version_candidates):
                reasons.append("inactive_version_recalled")
            if hit and not citation:
                reasons.append("missing_citation_metadata")
            results.append(CaseResult(
                id=case.id, hit=hit, active_version_hit=active_hit, refusal_correct=True,
                citation_covered=citation, reason_codes=tuple(dict.fromkeys(reasons)),
            ))
    except Exception:
        return _error_report(config, "malformed_retriever_result")

    non_refusal = [result for result, case in zip(results, config.cases) if not case.should_refuse]
    version_cases = [result for result, case in zip(results, config.cases) if not case.should_refuse and case.expected_versions]
    metrics = EvaluationMetrics(
        hit_at_k=sum(result.hit for result in non_refusal) / len(non_refusal) if non_refusal else 1.0,
        active_version_hit_rate=sum(result.active_version_hit for result in version_cases) / len(version_cases) if version_cases else 1.0,
        refusal_accuracy=sum(result.refusal_correct for result in results) / len(results) if results else 1.0,
        citation_coverage=sum(result.citation_covered for result in non_refusal) / len(non_refusal) if non_refusal else 1.0,
    )
    passed = all(getattr(metrics, name) + 1e-12 >= getattr(config.thresholds, name) for name in _THRESHOLD_NAMES)
    return EvaluationReport("passed" if passed else "failed", metrics, config.thresholds, tuple(results))


def report_to_dict(report: EvaluationReport) -> dict[str, Any]:
    return {
        "status": report.status,
        "error_code": report.error_code,
        "metrics": asdict(report.metrics),
        "thresholds": asdict(report.thresholds),
        "case_results": [
            {
                "id": item.id,
                "hit": item.hit,
                "active_version_hit": item.active_version_hit,
                "refusal_correct": item.refusal_correct,
                "citation_covered": item.citation_covered,
                "reason_codes": list(item.reason_codes),
            }
            for item in report.case_results
        ],
    }


def render_text_report(report: EvaluationReport) -> str:
    lines = [
        f"status: {report.status}",
        *(f"{name}: {getattr(report.metrics, name):.4f} (threshold {getattr(report.thresholds, name):.4f})" for name in _THRESHOLD_NAMES),
    ]
    if report.error_code:
        lines.append(f"error_code: {report.error_code}")
    for item in report.case_results:
        if item.reason_codes:
            lines.append(f"case {item.id}: {','.join(item.reason_codes)}")
    return "\n".join(lines)


def cases_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_baseline(path: str | Path, cases_path: str | Path, report: EvaluationReport) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "evaluator_version": EVALUATOR_VERSION,
        "cases_sha256": cases_sha256(cases_path),
        "metrics": asdict(report.metrics),
        "thresholds": asdict(report.thresholds),
        "created_at": datetime.now(UTC).isoformat(),
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compare_baseline(path: str | Path, cases_path: str | Path, report: EvaluationReport) -> EvaluationReport:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("evaluator_version") != EVALUATOR_VERSION:
            return replace(report, status="error", error_code="invalid_baseline")
        if payload.get("cases_sha256") != cases_sha256(cases_path):
            return replace(report, status="error", error_code="baseline_cases_mismatch")
        baseline_metrics = payload.get("metrics")
        if not isinstance(baseline_metrics, dict) or any(name not in baseline_metrics for name in _THRESHOLD_NAMES):
            return replace(report, status="error", error_code="invalid_baseline")
        regressions = [
            name for name in _THRESHOLD_NAMES
            if float(getattr(report.metrics, name)) < float(baseline_metrics[name]) - 0.0001
        ]
        if regressions:
            return replace(report, status="failed", error_code="baseline_regression")
        return report
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        return replace(report, status="error", error_code="missing_or_invalid_baseline")


def _load_fixture_corpus(path: str | Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationConfigError(f"cannot read evaluation corpus: {type(exc).__name__}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("documents"), list):
        raise EvaluationConfigError("invalid evaluation corpus")
    return payload["documents"]


def run_fixture_evaluation(cases_path: str | Path, corpus_path: str | Path, *, top_k: int = 5) -> EvaluationReport:
    config = load_cases(cases_path, require_thresholds=True)
    corpus = _load_fixture_corpus(corpus_path)
    with tempfile.TemporaryDirectory(prefix="rag-eval-", ignore_cleanup_errors=True) as temp_dir:
        store = DocumentStore(Path(temp_dir) / "documents.db")
        semantic_documents: list[Document] = []
        for raw in corpus:
            if not isinstance(raw, dict):
                raise EvaluationConfigError("invalid evaluation corpus document")
            chunks = raw.get("chunks")
            if not isinstance(chunks, list) or not chunks:
                raise EvaluationConfigError("invalid evaluation corpus chunks")
            records = [ChunkRecord(
                id=str(chunk.get("id", "")), content=str(chunk.get("content", "")),
                section_title=str(chunk.get("section_title", "")), page=chunk.get("page"),
                chunk_index=int(chunk.get("chunk_index", index)), total_chunks=len(chunks),
                metadata=dict(chunk.get("metadata", {}) or {}),
            ) for index, chunk in enumerate(chunks)]
            domain = str(raw.get("domain", ""))
            name = str(raw.get("name", ""))
            existing = store.find_active(domain, name)
            document = (store.replace_document(
                existing.id,
                domain=domain, name=name,
                category=str(raw.get("category", "其他")), owner="", source="fixture",
                content=str(raw.get("content", "\n".join(record.content for record in records))),
                chunks=records, version=str(raw.get("version", "1")),
            ) if existing else store.create_document(
                domain=domain, name=name,
                category=str(raw.get("category", "其他")), owner="", source="fixture",
                content=str(raw.get("content", "\n".join(record.content for record in records))),
                chunks=records, version=str(raw.get("version", "1")),
            ))
            for record in store.get_chunks(document.id):
                metadata = dict(record.metadata)
                metadata.update({
                    "chunk_id": record.id, "document_id": document.id,
                    "document_version": document.version, "document_name": document.name,
                    "category": document.category, "domain": domain, "page": record.page,
                    "section_title": record.section_title,
                })
                semantic_documents.append(Document(page_content=record.content, metadata=metadata))

        def semantic_search(query: str, domain: str, limit: int):
            # Public fixture keys are ASCII identifiers, so common Chinese
            # question words cannot accidentally turn a refusal case into a hit.
            terms = {term.lower() for term in re.findall(r"[A-Za-z0-9_\-]+", query or "") if term.strip()}
            ranked: list[tuple[Document, float]] = []
            for document in semantic_documents:
                if document.metadata.get("domain", domain) != domain and document.metadata.get("category") != domain:
                    # Corpus metadata may omit domain; the store check below keeps the public fixture deterministic.
                    record = store.get(str(document.metadata.get("document_id", "")))
                    if record is None or record.domain != domain:
                        continue
                text = document.page_content.lower()
                overlap = sum(1 for term in terms if term in text)
                if overlap:
                    ranked.append((document, 1.0 / (1.0 + overlap)))
            ranked.sort(key=lambda item: (item[1], str(item[0].metadata.get("chunk_id", ""))))
            return ranked[:limit]

        hybrid = HybridRetriever(store, semantic_search, top_k=top_k, min_score=0.0)
        return evaluate_cases(
            config,
            lambda case, limit: hybrid.search(case.question, case.domain, limit),
            top_k=top_k,
            active_version_lookup=lambda domain, name: (store.find_active(domain, name).version if store.find_active(domain, name) else None),
        )


def run_live_evaluation(
    cases_path: str | Path,
    *,
    database_path: str | Path,
    domain: str,
    top_k: int = 5,
    baseline_path: str | Path | None = None,
    accept_baseline: bool = False,
    vectorstore: Any = None,
) -> EvaluationReport:
    config = load_cases(cases_path, require_thresholds=True)
    if not domain.strip():
        return _error_report(config, "invalid_domain")
    if any(case.domain != domain for case in config.cases):
        return _error_report(config, "case_domain_mismatch")
    store = DocumentStore(database_path)
    if not store.list_documents(domain=domain):
        return _error_report(config, "index_not_ready")
    try:
        if vectorstore is None:
            from core.retriever import get_vectorstore
            vectorstore = get_vectorstore(domain)
        if vectorstore is None or not hasattr(vectorstore, "similarity_search_with_score"):
            return _error_report(config, "index_unavailable")

        def semantic_search(query: str, requested_domain: str, limit: int):
            return vectorstore.similarity_search_with_score(query, k=limit)

        hybrid = HybridRetriever(store, semantic_search, top_k=top_k)
        report = evaluate_cases(
            config,
            lambda case, limit: hybrid.search(case.question, case.domain, limit),
            top_k=top_k,
            active_version_lookup=lambda requested_domain, name: (store.find_active(requested_domain, name).version if store.find_active(requested_domain, name) else None),
        )
    except Exception:
        return _error_report(config, "index_unavailable")
    if baseline_path is None:
        return replace(report, status="error", error_code="baseline_required")
    if accept_baseline:
        write_baseline(baseline_path, cases_path, report)
        return report
    return compare_baseline(baseline_path, cases_path, report)


def _write_json_output(path: str | Path | None, report: EvaluationReport) -> None:
    if path is not None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _exit_code(report: EvaluationReport) -> int:
    if report.status == "passed":
        return 0
    if report.status == "failed":
        return 1
    if report.status == "error":
        return 2
    return 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline RAG retrieval evaluation")
    parser.add_argument("--mode", choices=("fixture", "live"), required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--corpus")
    parser.add_argument("--database")
    parser.add_argument("--domain", default="")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--baseline")
    parser.add_argument("--accept-baseline", action="store_true")
    parser.add_argument("--json-out")
    try:
        args = parser.parse_args(argv)
        load_cases(args.cases, require_thresholds=True)
        if args.mode == "fixture":
            if not args.corpus:
                raise EvaluationConfigError("fixture mode requires corpus")
            report = run_fixture_evaluation(args.cases, args.corpus, top_k=args.top_k)
        else:
            if not args.database or not args.domain:
                raise EvaluationConfigError("live mode requires database and domain")
            report = run_live_evaluation(
                args.cases, database_path=args.database, domain=args.domain, top_k=args.top_k,
                baseline_path=args.baseline, accept_baseline=args.accept_baseline,
            )
        _write_json_output(args.json_out, report)
        print(render_text_report(report))
        return _exit_code(report)
    except EvaluationConfigError:
        return 2
    except Exception:
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
