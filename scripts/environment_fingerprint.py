"""Stable, path-free fingerprints for installed Python distributions."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from pathlib import Path

_NORMALIZE_NAME = re.compile(r"[-_.]+")


def site_packages(environment: Path) -> Path:
    candidates = sorted((environment / "lib").glob("python3.*/site-packages"))
    if len(candidates) != 1 or not candidates[0].is_dir():
        raise RuntimeError("Python site-packages directory is unavailable")
    return candidates[0]


def installed_environment_sha256(environment: Path) -> str:
    """Hash normalized distribution names and versions without recording local paths."""

    distributions: set[tuple[str, str]] = set()
    for distribution in importlib.metadata.distributions(path=[str(site_packages(environment))]):
        name = distribution.metadata.get("Name")
        version = distribution.version
        if not isinstance(name, str) or not name.strip() or not version:
            raise RuntimeError("installed Python distribution metadata is invalid")
        distributions.add((_NORMALIZE_NAME.sub("-", name).lower(), version))
    if not distributions:
        raise RuntimeError("installed Python environment is empty")
    canonical = json.dumps(
        sorted(f"{name}=={version}" for name, version in distributions),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()
