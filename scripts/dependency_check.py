"""Offline dependency lock integrity checks; not a vulnerability database scan."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_HASH = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class DependencyFinding:
    code: str
    subject: str


def check_dependency_locks(root: Path) -> list[DependencyFinding]:
    findings: list[DependencyFinding] = []
    for relative in (Path("uv.lock"), Path("services/worker/profiles/paddle/uv.lock")):
        path = root / relative
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            findings.append(DependencyFinding("UV_LOCK_INVALID", relative.as_posix()))
            continue
        if payload.get("version") != 1 or not isinstance(payload.get("package"), list):
            findings.append(DependencyFinding("UV_LOCK_INVALID", relative.as_posix()))
            continue
        for package in payload["package"]:
            if not isinstance(package, dict):
                findings.append(DependencyFinding("UV_LOCK_INVALID", relative.as_posix()))
                continue
            source = package.get("source")
            if not isinstance(source, dict) or "registry" not in source:
                continue
            name = str(package.get("name", "unknown"))
            registry = source.get("registry")
            if not isinstance(registry, str) or urlsplit(registry).scheme != "https":
                findings.append(DependencyFinding("UV_REGISTRY_INSECURE", name))
            archives: list[object] = []
            if "sdist" in package:
                archives.append(package["sdist"])
            wheels = package.get("wheels", [])
            if isinstance(wheels, list):
                archives.extend(wheels)
            if not archives:
                findings.append(DependencyFinding("UV_ARCHIVE_MISSING", name))
            for archive in archives:
                if not isinstance(archive, dict):
                    findings.append(DependencyFinding("UV_ARCHIVE_INVALID", name))
                    continue
                url = archive.get("url")
                digest = archive.get("hash")
                if not isinstance(url, str) or urlsplit(url).scheme != "https":
                    findings.append(DependencyFinding("UV_ARCHIVE_INSECURE", name))
                if not isinstance(digest, str) or _HASH.fullmatch(digest) is None:
                    findings.append(DependencyFinding("UV_INTEGRITY_MISSING", name))

    npm_path = root / "package-lock.json"
    try:
        npm = json.loads(npm_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        findings.append(DependencyFinding("NPM_LOCK_INVALID", "package-lock.json"))
    else:
        packages = npm.get("packages")
        if npm.get("lockfileVersion") != 3 or not isinstance(packages, dict):
            findings.append(DependencyFinding("NPM_LOCK_INVALID", "package-lock.json"))
        else:
            for package_path, metadata in packages.items():
                if not str(package_path).startswith("node_modules/"):
                    continue
                if not isinstance(metadata, dict):
                    findings.append(DependencyFinding("NPM_LOCK_INVALID", str(package_path)))
                    continue
                if metadata.get("link") is True:
                    continue
                resolved = metadata.get("resolved")
                integrity = metadata.get("integrity")
                license_id = metadata.get("license")
                if not isinstance(resolved, str) or urlsplit(resolved).scheme != "https":
                    findings.append(DependencyFinding("NPM_SOURCE_INSECURE", str(package_path)))
                if not isinstance(integrity, str) or not integrity.startswith(
                    ("sha256-", "sha384-", "sha512-")
                ):
                    findings.append(DependencyFinding("NPM_INTEGRITY_MISSING", str(package_path)))
                if not isinstance(license_id, str) or not license_id.strip():
                    findings.append(DependencyFinding("NPM_LICENSE_MISSING", str(package_path)))
    return sorted(set(findings), key=lambda item: (item.code, item.subject))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    findings = check_dependency_locks(arguments.root.expanduser().resolve(strict=True))
    if findings:
        for finding in findings:
            print(f"{finding.code}: {finding.subject}")
        return 1
    print("dependency lock integrity passed (offline-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
