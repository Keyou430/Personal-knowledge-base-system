"""Metadata governance and review-first batch ingestion."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from config import SUPPORTED_EXTENSIONS
from core.document_store import DocumentStore


class MetadataValidationError(ValueError):
    """A metadata field cannot be safely persisted."""


@dataclass(frozen=True)
class NormalizedMetadata:
    category: str
    owner: str
    source: str
    version: str | None
    updated_at: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class BatchPreviewItem:
    path: Path
    name: str
    action: str
    reason: str
    content_sha256: str | None
    size: int
    category: str
    owner: str
    source: str
    proposed_version: str | None
    updated_at: str
    existing_document_id: str | None = None
    duplicate_of: str | None = None

    @property
    def accepted(self) -> bool:
        return self.action in {"new", "replace"}


@dataclass(frozen=True)
class BatchPreview:
    domain: str
    items: tuple[BatchPreviewItem, ...]
    metadata: NormalizedMetadata

    @property
    def accepted_count(self) -> int:
        return sum(item.accepted for item in self.items)


@dataclass
class BatchExecutionReport:
    successes: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    retry_needed: list[str] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: str | None, *, field_name: str, default: str = "") -> str:
    cleaned = " ".join((value or "").strip().split())
    if any(character in (value or "") for character in "\r\n"):
        raise MetadataValidationError(f"{field_name}不能包含换行")
    if len(cleaned) > 200:
        raise MetadataValidationError(f"{field_name}不能超过 200 个字符")
    return cleaned or default


def normalize_metadata(
    *,
    category: str | None,
    owner: str | None,
    source: str | None,
    version: str | None = None,
    updated_at: str | None = None,
) -> NormalizedMetadata:
    warnings: list[str] = []
    clean_category = _clean(category, field_name="分类")
    clean_owner = _clean(owner, field_name="责任人")
    clean_source = _clean(source, field_name="来源")
    if not clean_category:
        clean_category = "其他"
        warnings.append("分类为空，使用默认值“其他”")
    if not clean_owner:
        warnings.append("责任人未填写")
    if not clean_source:
        clean_source = "upload"
        warnings.append("来源为空，使用默认值“upload”")
    timestamp = updated_at or _now()
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise MetadataValidationError("更新时间必须是 ISO 8601 格式") from error
    if parsed.tzinfo is None:
        raise MetadataValidationError("更新时间必须包含时区")
    return NormalizedMetadata(
        category=clean_category,
        owner=clean_owner,
        source=clean_source,
        version=_clean(version, field_name="版本") or None,
        updated_at=parsed.astimezone(UTC).isoformat(),
        warnings=tuple(warnings),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def preview_batch(
    paths: Iterable[str | Path],
    *,
    domain: str,
    store: DocumentStore,
    category: str | None,
    owner: str | None,
    source: str | None,
) -> BatchPreview:
    metadata = normalize_metadata(category=category, owner=owner, source=source)
    from core.ingestion import prepare_document

    items: list[BatchPreviewItem] = []
    batch_hashes: dict[str, str] = {}
    for value in paths:
        path = Path(value)
        common = {
            "path": path,
            "name": path.name,
            "category": metadata.category,
            "owner": metadata.owner,
            "source": metadata.source,
            "updated_at": metadata.updated_at,
        }
        if not path.is_file():
            items.append(
                BatchPreviewItem(
                    **common,
                    action="missing",
                    reason="文件不存在",
                    content_sha256=None,
                    size=0,
                    proposed_version=None,
                )
            )
            continue
        digest = _sha256(path)
        size = path.stat().st_size
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            items.append(
                BatchPreviewItem(
                    **common,
                    action="unsupported",
                    reason="不支持的文件格式",
                    content_sha256=digest,
                    size=size,
                    proposed_version=None,
                )
            )
            continue
        try:
            content, _ = prepare_document(path)
        except Exception as error:
            items.append(
                BatchPreviewItem(
                    **common,
                    action="invalid",
                    reason=str(error),
                    content_sha256=digest,
                    size=size,
                    proposed_version=None,
                )
            )
            continue
        content_hash = DocumentStore.content_hash(content)
        duplicate_name = batch_hashes.get(content_hash)
        if duplicate_name is not None:
            items.append(
                BatchPreviewItem(
                    **common,
                    action="duplicate",
                    reason=f"与批次内{duplicate_name}内容重复",
                    content_sha256=digest,
                    size=size,
                    proposed_version=None,
                    duplicate_of=duplicate_name,
                )
            )
            continue
        batch_hashes[content_hash] = path.name
        existing_hash = store.find_by_hash(domain, content_hash)
        active = store.find_active(domain, path.name)
        if existing_hash is not None:
            items.append(
                BatchPreviewItem(
                    **common,
                    action="duplicate",
                    reason="内容已入库",
                    content_sha256=digest,
                    size=size,
                    proposed_version=existing_hash.version,
                    existing_document_id=existing_hash.id,
                )
            )
        elif active is not None:
            items.append(
                BatchPreviewItem(
                    **common,
                    action="replace",
                    reason="同名内容变化，将生成新版本",
                    content_sha256=digest,
                    size=size,
                    proposed_version=DocumentStore.next_version(active.version),
                    existing_document_id=active.id,
                )
            )
        else:
            items.append(
                BatchPreviewItem(
                    **common,
                    action="new",
                    reason="新文档",
                    content_sha256=digest,
                    size=size,
                    proposed_version="1",
                )
            )
    return BatchPreview(domain=domain, items=tuple(items), metadata=metadata)


def execute_batch(
    preview: BatchPreview,
    *,
    store: DocumentStore,
    vectorstore: Any = None,
) -> BatchExecutionReport:
    from core.ingestion import ingest_file

    report = BatchExecutionReport()
    for item in preview.items:
        if item.action in {"new", "replace", "duplicate"} and (
            not item.path.is_file() or _sha256(item.path) != item.content_sha256
        ):
            report.retry_needed.append(item.name)
            report.failures.append(
                {"file": item.name, "reason": "文件在预览后发生变化"}
            )
            continue
        if item.action == "duplicate":
            if item.duplicate_of and item.duplicate_of not in {
                *report.successes,
                *report.duplicates,
            }:
                report.retry_needed.append(item.name)
                report.failures.append(
                    {
                        "file": item.name,
                        "reason": f"依赖的批次文件未成功入库: {item.duplicate_of}",
                    }
                )
                continue
            if item.existing_document_id:
                existing = store.get(item.existing_document_id)
                if existing is None or existing.status != "active":
                    report.retry_needed.append(item.name)
                    report.failures.append(
                        {"file": item.name, "reason": "重复项状态在预览后发生变化"}
                    )
                    continue
            report.duplicates.append(item.name)
            continue
        if not item.accepted:
            continue
        try:
            current = store.find_active(preview.domain, item.name)
            current_id = current.id if current else None
            if current_id != item.existing_document_id:
                raise RuntimeError("文档状态在预览后发生变化")
            result = ingest_file(
                item.path,
                domain=preview.domain,
                store=store,
                vectorstore=vectorstore,
                category=item.category,
                owner=item.owner,
                source=item.source,
                version=item.proposed_version,
                updated_at=item.updated_at,
            )
            report.successes.append(item.name)
            if result.index_pending:
                report.pending.append(item.name)
        except Exception as error:
            report.retry_needed.append(item.name)
            report.failures.append({"file": item.name, "reason": str(error)})
    return report
