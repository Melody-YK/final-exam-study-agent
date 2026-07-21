"""Build a local-only OCR manifest without copying private source material."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from evals.ocr.manifest import OcrEvalEntry, OcrEvalManifest

_ALLOWED_MEDIA_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def build_manifest(source: Path, *, split: str) -> OcrEvalManifest:
    root = source.expanduser().resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("source must be a non-symlink directory")
    entries: list[OcrEvalEntry] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        media_type = _ALLOWED_MEDIA_TYPES.get(path.suffix.casefold())
        if media_type is None:
            continue
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(root).as_posix()
        entries.append(
            OcrEvalEntry(
                id=_entry_id(relative),
                source_path=str(resolved),
                sha256=_sha256(resolved),
                size_bytes=resolved.stat().st_size,
                media_type=media_type,
                split=split,
                privacy="private-authorized",
                license_status="private-use-only",
            )
        )
    return OcrEvalManifest(generated_at=datetime.now(UTC), entries=entries)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".local/evals/ocr/private-manifest.json"),
    )
    parser.add_argument("--split", choices=("development", "validation", "test"), default="test")
    arguments = parser.parse_args()
    manifest = build_manifest(arguments.source, split=arguments.split)
    output = arguments.output.expanduser().resolve()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=True, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    output.chmod(0o600)
    print(f"wrote {len(manifest.entries)} entries to {output}")
    return 0


def _entry_id(relative_path: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", relative_path.casefold()).strip("-.")
    digest = hashlib.sha256(relative_path.encode()).hexdigest()[:12]
    return f"{slug[:80] or 'document'}-{digest}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
