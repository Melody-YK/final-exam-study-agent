"""Validate local, redacted external-gate evidence without reading secrets or raw text."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.environment_fingerprint import installed_environment_sha256  # noqa: E402


def _load(relative: str) -> dict[str, Any]:
    path = _ROOT / relative
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("external evidence must be a private regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("external evidence must be a JSON object")
    return payload


def _sha256(relative: str) -> str:
    return hashlib.sha256((_ROOT / relative).read_bytes()).hexdigest()


def _verify_advisory() -> None:
    report = _load(".local/evidence/advisory-audit.json")
    expected_locks = {
        "uv": _sha256("uv.lock"),
        "paddle_uv": _sha256("services/worker/profiles/paddle/uv.lock"),
        "docling_uv": _sha256("services/worker/profiles/docling/uv.lock"),
        "npm": _sha256("package-lock.json"),
    }
    counts = report.get("vulnerability_counts")
    expected_environments = {
        "workspace": installed_environment_sha256(_ROOT / ".venv"),
        "paddle": installed_environment_sha256(
            _ROOT / "services" / "worker" / "profiles" / "paddle" / ".venv"
        ),
        "docling": installed_environment_sha256(
            _ROOT / "services" / "worker" / "profiles" / "docling" / ".venv"
        ),
    }
    if (
        report.get("status") != "passed"
        or report.get("query_mode") != "live-advisory-databases"
        or report.get("sync_mode") != "locked-before-audit"
        or report.get("lock_sha256") != expected_locks
        or report.get("installed_environment_sha256") != expected_environments
        or not isinstance(counts, dict)
        or any(value != 0 for value in counts.values())
    ):
        raise ValueError("advisory evidence is stale or contains findings")


def _verify_provider() -> None:
    report = _load(".local/evidence/provider-live-rag-smoke.json")
    if (
        report.get("synthetic_retrieval_selected_expected") is not True
        or report.get("answer_status") != "answered"
        or report.get("citation_count") != 1
        or report.get("contains_raw_text") is not False
        or report.get("contains_secret_values") is not False
    ):
        raise ValueError("live Provider evidence is invalid")


def _verify_ocr() -> None:
    report = _load(".local/evals/ocr/live-benchmark.json")
    private_smoke = _load(".local/evals/ocr/private-authorized-smoke.json")
    ingestion = _load(".local/evidence/ocr-ingestion-smoke.json")
    if (
        report.get("live_ocr_verified") is not True
        or report.get("contains_raw_text") is not False
        or not report.get("cases")
        or private_smoke.get("executed") is not True
        or private_smoke.get("succeeded_cases") != 1
        or private_smoke.get("contains_raw_text") is not False
        or private_smoke.get("contains_source_paths") is not False
        or ingestion.get("status") != "passed-live-local-ingestion"
        or ingestion.get("document_status") != "parsed_index_blocked"
        or ingestion.get("job_status") != "succeeded"
        or ingestion.get("parser_profile") != "ocr-v1"
        or ingestion.get("source_backend") != "paddleocr-general"
        or ingestion.get("preview_revision_created") is not True
        or ingestion.get("runtime_model_version_recorded") is not True
        or ingestion.get("raw_result_ref_recorded") is not True
        or ingestion.get("contains_raw_text") is not False
        or ingestion.get("contains_source_paths") is not False
        or ingestion.get("contains_object_keys") is not False
        or ingestion.get("contains_secret_values") is not False
    ):
        raise ValueError("live OCR evidence is invalid")
    executable = (
        _ROOT
        / "services"
        / "worker"
        / "profiles"
        / "paddle"
        / ".venv"
        / "bin"
        / "study-agent-paddle-profile"
    )
    cache = _ROOT / ".local" / "models" / "paddlex"
    if executable.is_symlink() or not os.access(executable, os.X_OK):
        raise ValueError("live OCR profile is unavailable")
    result = subprocess.run(
        [str(executable), "capabilities", "--cache-root", str(cache)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    capability = json.loads(result.stdout) if result.stdout else {}
    if result.returncode != 0 or capability.get("ready") is not True:
        raise ValueError("live OCR profile is not ready")


def _verify_libreoffice() -> None:
    report = _load(".local/evidence/libreoffice-visual-baseline.json")
    if (
        report.get("renderer_status") != "stable"
        or report.get("self_authored_pixel_reproducible") is not True
        or report.get("private_authorized_smoke_executed") is not True
        or report.get("contains_raw_text") is not False
        or report.get("contains_source_paths") is not False
    ):
        raise ValueError("LibreOffice baseline evidence is invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("category", choices=("advisory", "provider", "ocr", "libreoffice"))
    category = parser.parse_args().category
    {
        "advisory": _verify_advisory,
        "provider": _verify_provider,
        "ocr": _verify_ocr,
        "libreoffice": _verify_libreoffice,
    }[category]()
    print(f"external_evidence_{category}=verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
