"""Canonical three-layer packaging for one parse attempt."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from study_contracts import (
    PARSE_ATTEMPT_MEDIA_TYPE,
    PARSE_PAGE_MEDIA_TYPE,
    PARSER_RAW_MEDIA_TYPE,
    Asset,
    AssetType,
    BlockType,
    JobArtifactReceipt,
    Page,
    PageQualityStatus,
    ParseAttemptResult,
    SourceLocator,
    WorkerLease,
    canonical_json_bytes,
    canonical_sha256,
)
from study_worker.dispatcher import JobReporter, PageCheckpoint, TaskResult
from study_worker.parsers.normalize import (
    RawBlock,
    RawDocument,
    RawPage,
    normalize_bbox,
    normalize_page,
)
from study_worker.parsers.paddle_general import build_ocr_quality
from study_worker.parsers.quality import evaluate_page_quality
from study_worker.sandbox import Sandbox

RAW_PAGE_MEDIA_TYPE = PARSER_RAW_MEDIA_TYPE


@dataclass(frozen=True, slots=True)
class PackagedPage:
    page: Page
    assets: tuple[Asset, ...]
    failed: bool


async def package_parse_attempt(
    raw_document: RawDocument,
    *,
    lease: WorkerLease,
    sandbox: Sandbox,
    reporter: JobReporter,
) -> TaskResult:
    requested = tuple(sorted(lease.requested_pages)) or tuple(
        range(1, raw_document.total_page_count + 1)
    )
    covered = tuple(page.ordinal for page in raw_document.pages)
    if covered != tuple(sorted(covered)) or covered != requested:
        raise ValueError("raw parser coverage does not match the lease request")

    packaged_pages: list[PackagedPage] = []
    for raw_page in raw_document.pages:
        packaged_pages.append(
            await package_parse_page(
                raw_page,
                raw_document=raw_document,
                sandbox=sandbox,
                reporter=reporter,
            )
        )
    return await finalize_parse_attempt(
        raw_document,
        requested_pages=requested,
        packaged_pages=packaged_pages,
        sandbox=sandbox,
        reporter=reporter,
    )


async def package_parse_page(
    raw_page: RawPage,
    *,
    raw_document: RawDocument,
    sandbox: Sandbox,
    reporter: JobReporter,
) -> PackagedPage:
    """Upload and checkpoint one page before the next parser child is started."""

    if raw_page.ordinal > raw_document.total_page_count:
        raise ValueError("raw page ordinal exceeds total page count")
    raw_receipt = await _upload_raw_page(
        raw_page,
        raw_document=raw_document,
        sandbox=sandbox,
        reporter=reporter,
    )
    page_assets, asset_ids = await _package_assets(
        raw_page,
        raw_receipt=raw_receipt,
        raw_document=raw_document,
        sandbox=sandbox,
        reporter=reporter,
    )
    quality = (
        build_ocr_quality(
            raw_page.blocks,
            experimental=any(
                block.metadata.get("ocr_backend") == "pp-structure-v3" for block in raw_page.blocks
            ),
        )
        if raw_document.parser_profile == "ocr-v1"
        else evaluate_page_quality(raw_page)
    )
    page = normalize_page(
        raw_page,
        raw_result_ref=raw_receipt.artifact_ref,
        quality=quality,
        source_backend=raw_document.source_backend,
        source_version=raw_document.source_version,
        asset_ids_by_order=asset_ids,
    )
    page_receipt = await _upload_canonical(
        artifact_name=f"page-{raw_page.ordinal:06d}.json",
        payload=page,
        media_type=PARSE_PAGE_MEDIA_TYPE,
        sandbox=sandbox,
        reporter=reporter,
    )
    failed = quality.status is PageQualityStatus.FAILED
    error_code = quality.issues[0].code if failed else None
    await reporter.checkpoint(
        PageCheckpoint(
            page_ordinal=raw_page.ordinal,
            status="failed" if failed else "succeeded",
            output_ref=page_receipt.artifact_ref,
            output_sha256=page_receipt.sha256,
            output_size_bytes=page_receipt.size_bytes,
            source_backend=raw_document.source_backend,
            source_version=raw_document.source_version,
            error_code=error_code,
        )
    )
    return PackagedPage(page=page, assets=tuple(page_assets), failed=failed)


async def finalize_parse_attempt(
    raw_document: RawDocument,
    *,
    requested_pages: tuple[int, ...],
    packaged_pages: Sequence[PackagedPage],
    sandbox: Sandbox,
    reporter: JobReporter,
) -> TaskResult:
    """Upload the attempt envelope after every requested page has a checkpoint."""

    requested = tuple(sorted(requested_pages))
    if not requested:
        raise ValueError("parse attempt requires requested pages")
    pages = [packaged.page for packaged in packaged_pages]
    covered = tuple(page.ordinal for page in pages)
    if covered != tuple(sorted(covered)) or covered != requested:
        raise ValueError("packaged page coverage does not match the request")
    assets = [asset for packaged in packaged_pages for asset in packaged.assets]
    failed_pages = [packaged.page.ordinal for packaged in packaged_pages if packaged.failed]

    attempt_payload = {
        "schema_version": "1.0",
        "document_sha256": raw_document.document_sha256,
        "parser_profile": raw_document.parser_profile,
        "source_backend": raw_document.source_backend,
        "source_version": raw_document.source_version,
        "total_page_count": raw_document.total_page_count,
        "requested_page_ordinals": list(requested),
        "covered_page_ordinals": list(covered),
        "pages": [page.model_dump(mode="json") for page in pages],
        "assets": [asset.model_dump(mode="json") for asset in assets],
    }
    attempt = ParseAttemptResult(
        **attempt_payload,
        canonical_sha256=canonical_sha256(attempt_payload),
    )
    attempt_receipt = await _upload_canonical(
        artifact_name="parse-result.json",
        payload=attempt,
        media_type=PARSE_ATTEMPT_MEDIA_TYPE,
        sandbox=sandbox,
        reporter=reporter,
    )
    return TaskResult(
        result_manifest_ref=attempt_receipt.artifact_ref,
        result_sha256=attempt_receipt.sha256,
        result_size_bytes=attempt_receipt.size_bytes,
        page_count=raw_document.total_page_count,
        failed_pages=tuple(failed_pages),
    )


async def _upload_raw_page(
    raw_page: RawPage,
    *,
    raw_document: RawDocument,
    sandbox: Sandbox,
    reporter: JobReporter,
) -> JobArtifactReceipt:
    payload = {
        "schema_version": "1.0",
        "source_backend": raw_document.source_backend,
        "source_version": raw_document.source_version,
        "page": raw_page.model_dump(mode="json"),
    }
    return await _upload_canonical(
        artifact_name=f"raw-page-{raw_page.ordinal:06d}.json",
        payload=payload,
        media_type=RAW_PAGE_MEDIA_TYPE,
        sandbox=sandbox,
        reporter=reporter,
    )


async def _package_assets(
    raw_page: RawPage,
    *,
    raw_receipt: JobArtifactReceipt,
    raw_document: RawDocument,
    sandbox: Sandbox,
    reporter: JobReporter,
) -> tuple[list[Asset], dict[int, str]]:
    assets: list[Asset] = []
    asset_ids: dict[int, str] = {}
    for block in raw_page.blocks:
        asset_type = _asset_type(block)
        if asset_type is None:
            continue
        asset_id = f"asset-{raw_page.ordinal}-{block.reading_order}"
        asset_ids[block.reading_order] = asset_id
        if block.artifact is None:
            receipt = raw_receipt
            metadata_only = True
        else:
            source = _resolve_output_artifact(sandbox, block.artifact.relative_path)
            receipt = await reporter.upload_artifact(
                artifact_name=_asset_name(raw_page.ordinal, block),
                source=source,
                media_type=block.artifact.media_type,
            )
            metadata_only = False
        assets.append(
            Asset(
                id=asset_id,
                type=asset_type,
                locator=SourceLocator(kind=raw_page.source_kind, ordinal=raw_page.ordinal),
                bbox_norm=normalize_bbox(
                    block.bbox,
                    page_width=raw_page.width,
                    page_height=raw_page.height,
                ),
                object_ref=receipt.artifact_ref,
                media_type=receipt.media_type,
                sha256=receipt.sha256,
                size_bytes=receipt.size_bytes,
                source_backend=raw_document.source_backend,
                source_version=raw_document.source_version,
                raw_result_ref=raw_receipt.artifact_ref,
                metadata={**block.metadata, "metadata_only": metadata_only},
            )
        )
    return assets, asset_ids


async def _upload_canonical(
    *,
    artifact_name: str,
    payload: object,
    media_type: str,
    sandbox: Sandbox,
    reporter: JobReporter,
) -> JobArtifactReceipt:
    path = sandbox.output_dir / artifact_name
    _write_private(path, canonical_json_bytes(payload))
    return await reporter.upload_artifact(
        artifact_name=artifact_name,
        source=path,
        media_type=media_type,
    )


def _asset_type(block: RawBlock) -> AssetType | None:
    return {
        BlockType.IMAGE: AssetType.IMAGE,
        BlockType.TABLE: AssetType.TABLE,
        BlockType.FORMULA: AssetType.FORMULA,
    }.get(block.type)


def _asset_name(ordinal: int, block: RawBlock) -> str:
    if block.artifact is None:
        raise ValueError("binary asset name requested for metadata-only block")
    suffix = Path(block.artifact.relative_path).suffix.lower() or ".bin"
    return f"asset-{ordinal:06d}-{block.reading_order:06d}{suffix}"


def _resolve_output_artifact(sandbox: Sandbox, relative_path: str) -> Path:
    root = sandbox.output_dir.resolve(strict=True)
    candidate = (root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError("parser artifact escapes output directory") from None
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("parser artifact is missing")
    return candidate


def _write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    os.chmod(path, 0o600)
