"""Generate a local CycloneDX inventory from the committed dependency locks."""

from __future__ import annotations

import argparse
import json
import os
import tomllib
from pathlib import Path
from typing import Any


def build_sbom(root: Path) -> dict[str, object]:
    components: dict[str, dict[str, object]] = {}
    for relative in (
        Path("uv.lock"),
        Path("services/worker/profiles/paddle/uv.lock"),
        Path("services/worker/profiles/docling/uv.lock"),
    ):
        uv_payload = tomllib.loads((root / relative).read_text(encoding="utf-8"))
        for package in uv_payload.get("package", []):
            if not isinstance(package, dict):
                continue
            name = package.get("name")
            version = package.get("version")
            if isinstance(name, str) and isinstance(version, str):
                purl = f"pkg:pypi/{name}@{version}"
                components[purl] = {
                    "type": "library",
                    "name": name,
                    "version": version,
                    "purl": purl,
                }

    npm_payload = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
    packages = npm_payload.get("packages", {})
    if not isinstance(packages, dict):
        raise ValueError("package-lock packages must be an object")
    for package_path, package in packages.items():
        if not isinstance(package_path, str) or not isinstance(package, dict):
            continue
        name = _npm_package_name(package_path, package)
        version = package.get("version")
        if name is None or not isinstance(version, str):
            continue
        encoded_name = name.replace("@", "%40", 1) if name.startswith("@") else name
        purl = f"pkg:npm/{encoded_name}@{version}"
        component = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
        }
        license_name = package.get("license")
        if isinstance(license_name, str) and license_name.strip():
            component["licenses"] = [{"license": {"name": license_name.strip()}}]
        components[purl] = component

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "final-exam-study-agent",
                "version": "0.1.0",
            },
            "properties": [
                {"name": "study-agent:scope", "value": "local-lock-inventory"},
                {"name": "study-agent:advisory-audit", "value": "not-queried"},
            ],
        },
        "components": [components[purl] for purl in sorted(components)],
    }


def write_sbom(payload: dict[str, object], output: Path) -> None:
    destination = output.expanduser().absolute()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    os.chmod(destination, 0o600)


def _npm_package_name(package_path: str, package: dict[str, Any]) -> str | None:
    declared = package.get("name")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    marker = "node_modules/"
    if marker not in package_path:
        return None
    inferred = package_path.rsplit(marker, 1)[-1]
    return inferred or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path(".local/evidence/sbom.cdx.json"))
    arguments = parser.parse_args()
    root = arguments.root.expanduser().resolve(strict=True)
    write_sbom(build_sbom(root), arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
