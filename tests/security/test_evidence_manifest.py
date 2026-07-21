from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.generate_implementation_manifest import GateSpec, run_gate_specs


def test_evidence_manifest_hashes_output_without_persisting_text_or_secrets(
    workspace_root: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "implementation-manifest.json"
    secret = "unit-test-sensitive-value"
    result = run_gate_specs(
        workspace_root,
        [
            GateSpec(
                name="redaction-probe",
                command=(
                    sys.executable,
                    "-c",
                    "import os; assert 'EMBEDDING_API_KEY' not in os.environ; "
                    "print('raw-private-output')",
                ),
                required=True,
            )
        ],
        output=output,
        source_environment={"EMBEDDING_API_KEY": secret, "PATH": ""},
        artifact_paths=(Path("evals/manifests/public.json"),),
    )
    serialized = output.read_text(encoding="utf-8")
    manifest = json.loads(serialized)

    assert result == 0
    assert manifest["status"] in {"passed", "partial-external-blockers"}
    assert manifest["gates"][0]["status"] == "passed"
    assert len(manifest["gates"][0]["output_sha256"]) == 64
    assert "raw-private-output" not in serialized
    assert secret not in serialized
    assert str(workspace_root) not in serialized
    assert output.stat().st_mode & 0o777 == 0o600


def test_browser_gate_removes_only_the_browser_e2e_blocker(
    workspace_root: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "implementation-manifest.json"

    result = run_gate_specs(
        workspace_root,
        [
            GateSpec(
                name="browser-e2e",
                command=(sys.executable, "-c", "raise SystemExit(0)"),
                required=True,
            )
        ],
        output=output,
        source_environment={"PATH": ""},
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))

    assert result == 0
    assert "browser-e2e-not-run" not in manifest["external_blockers"]
    assert "live-provider-not-run" in manifest["external_blockers"]
