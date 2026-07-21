"""Aggregate local 2 GiB-equivalent observations; never infer production capacity."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from evals.resources.preflight import (
        ResourcePreflightObservation,
        build_resource_report,
        write_resource_report,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--observations",
        type=Path,
        default=_ROOT / ".local" / "evals" / "resource-observations.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_ROOT / ".local" / "evals" / "resource-preflight.json",
    )
    arguments = parser.parse_args()
    observations_path = arguments.observations.expanduser().absolute()
    if not observations_path.is_file() or observations_path.is_symlink():
        print(
            "external-blocked: local RC resource observations are unavailable; "
            "no production capacity conclusion was made"
        )
        return 77
    payload = json.loads(observations_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("resource observations must be a JSON array")
    observations = [ResourcePreflightObservation.model_validate(item) for item in payload]
    report = build_resource_report(observations)
    write_resource_report(report, arguments.output)
    print(
        f"resource preflight status={report.status} local_equivalent_only=true "
        "production_capacity_verified=false"
    )
    return 0 if report.status == "passed-local-preflight" else 1


if __name__ == "__main__":
    raise SystemExit(main())
