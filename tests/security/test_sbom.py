from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_sbom import build_sbom, write_sbom


def test_sbom_uses_committed_locks_without_claiming_an_advisory_audit(
    workspace_root: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "sbom.cdx.json"
    payload = build_sbom(workspace_root)

    write_sbom(payload, output)
    serialized = json.loads(output.read_text(encoding="utf-8"))
    purls = {component["purl"] for component in serialized["components"]}

    assert serialized["bomFormat"] == "CycloneDX"
    assert serialized["specVersion"] == "1.5"
    assert any(purl.startswith("pkg:pypi/fastapi@") for purl in purls)
    assert any(purl.startswith("pkg:npm/react@") for purl in purls)
    assert serialized["metadata"]["properties"] == [
        {"name": "study-agent:scope", "value": "local-lock-inventory"},
        {"name": "study-agent:advisory-audit", "value": "not-queried"},
    ]
    assert output.stat().st_mode & 0o777 == 0o600
