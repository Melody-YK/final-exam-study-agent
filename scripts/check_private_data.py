"""Reject private material, credential shapes, and public-fixture contamination."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

_FORBIDDEN_PATH_PARTS = frozenset({"学校", "操作系统", "private", "secrets"})
_DOCUMENT_SUFFIXES = frozenset(
    {".doc", ".docx", ".jpeg", ".jpg", ".pdf", ".png", ".ppt", ".pptx", ".tif", ".tiff"}
)
_ALLOWED_DOCUMENT_ROOTS = (
    Path("evals/fixtures/public"),
    Path("tests/fixtures/public"),
)
_ANSWER_NAME = re.compile(r"(?:^|[-_.])(answers?|solutions?|gold[-_]?answers?)(?:[-_.]|$)", re.I)
_CREDENTIAL_PATTERNS = (
    ("OPENAI_STYLE_KEY", re.compile(rb"\bsk-[A-Za-z0-9_-]{24,}\b")),
    ("BEARER_TOKEN", re.compile(rb"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._~-]{20,}")),
    ("PRIVATE_KEY", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)
_MAX_SCAN_BYTES = 8 * 1024 * 1024
_PUBLIC_FIXTURE_ROOT = Path("evals/fixtures/public")
_FORBIDDEN_CORPUS_FIELDS = frozenset(
    {
        "answer",
        "expected_answer",
        "gold_answer",
        "reference_answer",
        "solution",
    }
)


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    path: str


def scan_workspace(root: Path, paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for relative in paths:
        if relative.is_absolute() or ".." in relative.parts:
            findings.append(Finding("PATH_OUTSIDE_WORKSPACE", relative.as_posix()))
            continue
        if any(part.casefold() in _FORBIDDEN_PATH_PARTS for part in relative.parts):
            findings.append(Finding("PRIVATE_PATH", relative.as_posix()))
        suffix = relative.suffix.casefold()
        if suffix in _DOCUMENT_SUFFIXES and not _under_allowed_document_root(relative):
            findings.append(Finding("UNDECLARED_DOCUMENT_FIXTURE", relative.as_posix()))
        if suffix in _DOCUMENT_SUFFIXES and _ANSWER_NAME.search(relative.name):
            findings.append(Finding("ANSWER_CONTAMINATION", relative.as_posix()))

        candidate = root / relative
        if candidate.is_symlink():
            findings.append(Finding("SYMLINK_NOT_SCANNED", relative.as_posix()))
            continue
        try:
            if not candidate.is_file() or candidate.stat().st_size > _MAX_SCAN_BYTES:
                continue
            payload = candidate.read_bytes()
        except OSError:
            findings.append(Finding("FILE_UNREADABLE", relative.as_posix()))
            continue
        for code, pattern in _CREDENTIAL_PATTERNS:
            if pattern.search(payload):
                findings.append(Finding(code, relative.as_posix()))
    return sorted(set(findings), key=lambda item: (item.path, item.code))


def verify_public_manifest(root: Path) -> list[Finding]:
    manifest_path = root / "evals/manifests/public.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [Finding("PUBLIC_MANIFEST_INVALID", "evals/manifests/public.json")]
    if (
        manifest.get("schema_version") != "1.0"
        or manifest.get("contains_private_course_material") is not False
        or manifest.get("generator") != "tests/fixtures/build_documents.py"
    ):
        return [Finding("PUBLIC_MANIFEST_INVALID", "evals/manifests/public.json")]
    licenses = manifest.get("licenses")
    if not isinstance(licenses, dict) or not licenses:
        return [Finding("PUBLIC_MANIFEST_LICENSE_INVALID", "evals/manifests/public.json")]
    for license_id, metadata in licenses.items():
        if (
            not isinstance(license_id, str)
            or not license_id.strip()
            or not isinstance(metadata, dict)
            or not isinstance(metadata.get("name"), str)
            or not isinstance(metadata.get("attribution"), str)
            or not isinstance(metadata.get("url"), str)
            or not metadata["url"].startswith("https://")
        ):
            return [Finding("PUBLIC_MANIFEST_LICENSE_INVALID", "evals/manifests/public.json")]
    entries = manifest.get("files")
    if not isinstance(entries, list):
        return [Finding("PUBLIC_MANIFEST_INVALID", "evals/manifests/public.json")]
    expected: dict[str, str] = {}
    generated_expected: dict[str, str] = {}
    tracked_paths: set[Path] = set()
    findings: list[Finding] = []
    for entry in entries:
        if not isinstance(entry, dict):
            findings.append(Finding("PUBLIC_MANIFEST_INVALID", "evals/manifests/public.json"))
            continue
        name = entry.get("name")
        digest = entry.get("sha256")
        fixture_kind = entry.get("fixture_kind")
        license_id = entry.get("license_id")
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or fixture_kind not in {"generated", "tracked"}
            or license_id not in licenses
            or entry.get("contains_reference_answers") is not False
            or not isinstance(entry.get("corpus_role"), str)
            or not isinstance(entry.get("purpose"), str)
        ):
            findings.append(Finding("PUBLIC_MANIFEST_INVALID", "evals/manifests/public.json"))
            continue
        expected[name] = digest
        if fixture_kind == "generated":
            generated_expected[name] = digest
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            findings.append(Finding("PUBLIC_MANIFEST_INVALID", "evals/manifests/public.json"))
            continue
        relative = Path(raw_path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.is_relative_to(_PUBLIC_FIXTURE_ROOT)
        ):
            findings.append(Finding("PUBLIC_MANIFEST_PATH_INVALID", relative.as_posix()))
            continue
        tracked_paths.add(relative)
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file():
            findings.append(Finding("PUBLIC_FIXTURE_MISSING", relative.as_posix()))
            continue
        if _sha256(candidate) != digest:
            findings.append(Finding("PUBLIC_FIXTURE_HASH_DRIFT", relative.as_posix()))
        if candidate.suffix.casefold() == ".jsonl":
            findings.extend(verify_rag_seed(candidate, relative.as_posix()))
    if len(expected) != len(entries) or len(set(expected.values())) != len(expected):
        findings.append(Finding("PUBLIC_MANIFEST_DUPLICATE", "evals/manifests/public.json"))

    try:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tests.fixtures.build_documents import build_documents

        with TemporaryDirectory() as temporary_directory:
            fixtures = build_documents(Path(temporary_directory))
            generated = {
                path.name: _sha256(path)
                for path in (fixtures.pdf, fixtures.pptx, fixtures.macro_pptx)
            }
    except Exception:
        findings.append(Finding("PUBLIC_FIXTURE_BUILD_FAILED", "tests/fixtures/build_documents.py"))
    else:
        if generated != generated_expected:
            findings.append(Finding("PUBLIC_FIXTURE_HASH_DRIFT", "evals/manifests/public.json"))
    unmanifested = {
        path.relative_to(root)
        for path in (root / _PUBLIC_FIXTURE_ROOT).glob("*")
        if path.is_file() and path.suffix.casefold() in _DOCUMENT_SUFFIXES | {".json", ".jsonl"}
    } - tracked_paths
    findings.extend(
        Finding("PUBLIC_FIXTURE_UNMANIFESTED", path.as_posix()) for path in unmanifested
    )
    return sorted(set(findings), key=lambda item: (item.path, item.code))


def verify_rag_seed(path: Path, display_path: str) -> list[Finding]:
    """Validate a public seed without treating relevance IDs as answer text."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [Finding("PUBLIC_SEED_INVALID", display_path)]
    records: list[dict[str, object]] = []
    findings: list[Finding] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            findings.append(Finding("PUBLIC_SEED_INVALID", display_path))
            continue
        if not isinstance(payload, dict):
            findings.append(Finding("PUBLIC_SEED_INVALID", display_path))
            continue
        records.append(payload)
    ids = [record.get("id") for record in records]
    if any(not isinstance(record_id, str) or not record_id for record_id in ids):
        findings.append(Finding("PUBLIC_SEED_INVALID", display_path))
    if len(ids) != len(set(ids)):
        findings.append(Finding("PUBLIC_SEED_DUPLICATE_ID", display_path))

    chunks: dict[str, dict[str, object]] = {}
    content_hashes: set[str] = set()
    for record in records:
        if record.get("schema_version") != "1.0":
            findings.append(Finding("PUBLIC_SEED_INVALID", display_path))
        if _contains_answer_field(record):
            findings.append(Finding("ANSWER_CONTAMINATION", display_path))
        if record.get("record_type") != "chunk":
            continue
        record_id = record.get("id")
        text = record.get("text")
        if (
            not isinstance(record_id, str)
            or not isinstance(text, str)
            or not text.strip()
            or record.get("corpus_role") != "corpus"
            or not isinstance(record.get("document_id"), str)
            or not isinstance(record.get("revision_id"), str)
        ):
            findings.append(Finding("PUBLIC_SEED_INVALID", display_path))
            continue
        normalized = " ".join(text.casefold().split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if digest in content_hashes:
            findings.append(Finding("PUBLIC_SEED_DUPLICATE_CONTENT", display_path))
        content_hashes.add(digest)
        chunks[record_id] = record

    for record in records:
        if record.get("record_type") == "chunk":
            continue
        if record.get("record_type") != "query":
            findings.append(Finding("PUBLIC_SEED_INVALID", display_path))
            continue
        relevant = record.get("relevant_chunk_ids")
        expect_abstain = record.get("expect_abstain")
        split = record.get("split")
        if (
            not isinstance(record.get("query"), str)
            or not isinstance(relevant, list)
            or any(not isinstance(item, str) for item in relevant)
            or not isinstance(expect_abstain, bool)
            or split not in {"train", "validation", "test"}
            or (expect_abstain and relevant)
            or (not expect_abstain and not relevant)
        ):
            findings.append(Finding("PUBLIC_SEED_INVALID", display_path))
            continue
        referenced = set(relevant)
        for route in ("dense", "bm25", "rerank"):
            candidates = record.get(route)
            if not isinstance(candidates, list):
                findings.append(Finding("PUBLIC_SEED_INVALID", display_path))
                continue
            for candidate in candidates:
                if (
                    not isinstance(candidate, dict)
                    or not isinstance(candidate.get("chunk_id"), str)
                    or not isinstance(candidate.get("score"), int | float)
                ):
                    findings.append(Finding("PUBLIC_SEED_INVALID", display_path))
                    continue
                referenced.add(candidate["chunk_id"])
        if not referenced.issubset(chunks):
            findings.append(Finding("PUBLIC_SEED_DANGLING_CHUNK", display_path))
    if not chunks or not records:
        findings.append(Finding("PUBLIC_SEED_INVALID", display_path))
    return sorted(set(findings), key=lambda item: (item.path, item.code))


def _contains_answer_field(record: dict[str, object]) -> bool:
    return any(str(key).casefold() in _FORBIDDEN_CORPUS_FIELDS for key in record)


def candidate_paths(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [Path(value.decode()) for value in result.stdout.split(b"\0") if value]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    root = arguments.root.expanduser().resolve(strict=True)
    findings = scan_workspace(root, candidate_paths(root))
    findings.extend(verify_public_manifest(root))
    if findings:
        for finding in sorted(set(findings), key=lambda item: (item.path, item.code)):
            print(f"{finding.code}: {finding.path}", file=sys.stderr)
        return 1
    print("private-data and public-fixture guard passed")
    return 0


def _under_allowed_document_root(path: Path) -> bool:
    return any(path.is_relative_to(root) for root in _ALLOWED_DOCUMENT_ROOTS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
