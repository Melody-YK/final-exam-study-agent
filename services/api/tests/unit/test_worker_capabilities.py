from study_agent.modules.jobs.capabilities import claim_capabilities_are_eligible
from study_contracts import WorkerCapabilities


def _capabilities(*, profiles: list[str], supports_ocr: bool) -> WorkerCapabilities:
    return WorkerCapabilities(
        parser_profiles=profiles,
        media_types=["application/pdf"],
        supports_ocr=supports_ocr,
        max_input_bytes=1024,
        max_pages=10,
    )


def test_worker_claim_policy_accepts_only_consistent_native_or_ocr_profiles() -> None:
    assert claim_capabilities_are_eligible(
        _capabilities(profiles=["native-v1"], supports_ocr=False)
    )
    assert claim_capabilities_are_eligible(_capabilities(profiles=["ocr-v1"], supports_ocr=True))
    assert not claim_capabilities_are_eligible(
        _capabilities(profiles=["ocr-v1"], supports_ocr=False)
    )
    assert not claim_capabilities_are_eligible(
        _capabilities(profiles=["native-v1"], supports_ocr=True)
    )


def test_worker_claim_policy_accepts_mineru_without_claiming_generic_ocr() -> None:
    assert claim_capabilities_are_eligible(
        _capabilities(profiles=["mineru-v1"], supports_ocr=False)
    )


def test_worker_claim_policy_keeps_paid_and_unknown_profiles_unavailable() -> None:
    for profile in ("paid-ocr-v1", "shell-v1"):
        assert not claim_capabilities_are_eligible(
            _capabilities(profiles=[profile], supports_ocr=True)
        )
