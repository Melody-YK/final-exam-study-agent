from datetime import UTC, datetime, timedelta

import pytest

from study_agent.modules.jobs.presence import WorkerPresenceRegistry
from study_contracts import JobClaimRequest, WorkerCapabilities


def _request(*, worker_id: str, ocr: bool) -> JobClaimRequest:
    profiles = ["native-v1", "ocr-v1"] if ocr else ["native-v1"]
    return JobClaimRequest(
        worker_id=worker_id,
        capabilities=WorkerCapabilities(
            parser_profiles=profiles,
            media_types=["application/pdf"],
            supports_ocr=ocr,
            supports_rendering=False,
            max_input_bytes=1_000,
            max_pages=10,
        ),
    )


@pytest.mark.asyncio
async def test_presence_reports_only_recent_authenticated_claim_capabilities() -> None:
    registry = WorkerPresenceRegistry()
    now = datetime(2026, 7, 19, 6, 0, tzinfo=UTC)

    await registry.record(_request(worker_id="native-worker", ocr=False), now=now)
    native = await registry.availability(now=now, max_age=timedelta(seconds=45))
    await registry.record(_request(worker_id="ocr-worker", ocr=True), now=now)
    ocr = await registry.availability(now=now, max_age=timedelta(seconds=45))
    expired = await registry.availability(
        now=now + timedelta(seconds=46),
        max_age=timedelta(seconds=45),
    )

    assert native.native_parser is True
    assert native.ocr_parser is False
    assert ocr.native_parser is True
    assert ocr.ocr_parser is True
    assert expired.native_parser is False
    assert expired.ocr_parser is False
