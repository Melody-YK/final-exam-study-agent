#!/usr/bin/env python3
import json
from pathlib import Path

from study_agent.openapi import build_openapi_document


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / "packages" / "contracts" / "openapi" / "openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_openapi_document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
