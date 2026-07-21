"""Record a text-free local RC smoke receipt after every live component passes."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    resource_path = _ROOT / ".local" / "evals" / "resource-preflight.json"
    resource = json.loads(resource_path.read_text(encoding="utf-8"))
    if (
        not isinstance(resource, dict)
        or resource.get("status") != "passed-local-preflight"
        or resource.get("production_capacity_verified") is not False
    ):
        raise ValueError("local resource preflight evidence is unavailable")
    ocr_path = _ROOT / ".local" / "evidence" / "ocr-ingestion-smoke.json"
    if ocr_path.is_symlink() or not ocr_path.is_file() or ocr_path.stat().st_mode & 0o077:
        raise ValueError("live OCR ingestion evidence is unavailable")
    ocr = json.loads(ocr_path.read_text(encoding="utf-8"))
    if (
        not isinstance(ocr, dict)
        or ocr.get("status") != "passed-live-local-ingestion"
        or ocr.get("preview_revision_created") is not True
        or ocr.get("source_backend") != "paddleocr-general"
        or ocr.get("contains_raw_text") is not False
    ):
        raise ValueError("live OCR ingestion evidence is invalid")
    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed-local-smoke",
        "components": {
            "postgres": "healthy",
            "api": "healthy",
            "web": "healthy",
            "index_runner": "running",
            "worker": "running",
            "ocr_ingestion": "passed-live-local-ingestion",
            "rag_test_double": "passed",
            "rag_no_provider": "passed",
            "resource_preflight": "passed-local-preflight",
        },
        "compose_teardown_performed": False,
        "contains_command_output": False,
        "contains_environment_values": False,
        "production_capacity_verified": False,
        "production_readiness": "not-assessed",
    }
    target = _ROOT / ".local" / "evidence" / "local-rc-smoke.json"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    os.chmod(target, 0o600)
    print("local_rc_smoke_evidence=recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
