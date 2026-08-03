"""Fail-closed policy for Worker capability claims."""

from study_contracts import WorkerCapabilities

NATIVE_PROFILE = "native-v1"
OCR_PROFILE = "ocr-v1"
MINERU_PROFILE = "mineru-v1"
ALLOWED_PARSER_PROFILES = frozenset({NATIVE_PROFILE, OCR_PROFILE, MINERU_PROFILE})
DISABLED_PARSER_PROFILES = frozenset({"paid-ocr-v1"})


def claim_capabilities_are_eligible(capabilities: WorkerCapabilities) -> bool:
    """Reject empty, unknown, disabled, or internally inconsistent advertisements."""

    profiles = set(capabilities.parser_profiles)
    if not profiles or not capabilities.media_types:
        return False
    if profiles & DISABLED_PARSER_PROFILES or not profiles <= ALLOWED_PARSER_PROFILES:
        return False
    advertises_ocr_profile = OCR_PROFILE in profiles
    return advertises_ocr_profile == capabilities.supports_ocr
