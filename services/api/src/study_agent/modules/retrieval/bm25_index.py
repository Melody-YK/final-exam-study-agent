"""Immutable, mmap-loaded BM25S index versions with verified manifests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bm25s  # type: ignore[import-untyped]

from study_agent.modules.retrieval.tokenizer import ChineseTokenizer


@dataclass(frozen=True, slots=True)
class LexicalDocument:
    chunk_id: str
    user_id: str
    course_id: str
    document_id: str
    revision_id: str
    text: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class BuiltLexicalIndex:
    version_id: str
    storage_path: Path
    manifest_hash: str
    document_set_hash: str
    tokenizer_version: str
    dictionary_hash: str
    chunk_count: int
    document_ids: tuple[str, ...]
    revision_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LexicalHit:
    chunk_id: str
    document_id: str
    revision_id: str
    score: float


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def document_set_hash(documents: Sequence[LexicalDocument]) -> str:
    identities = sorted(
        (
            item.user_id,
            item.course_id,
            item.document_id,
            item.revision_id,
            item.chunk_id,
            item.content_sha256,
        )
        for item in documents
    )
    return hashlib.sha256(_canonical_bytes(identities)).hexdigest()


class LoadedBm25Index:
    def __init__(
        self,
        retriever: Any,
        tokenizer: ChineseTokenizer,
        documents: dict[str, LexicalDocument],
    ) -> None:
        self.retriever = retriever
        self._tokenizer = tokenizer
        self._documents = documents

    def search(
        self,
        query: str,
        *,
        limit: int,
        document_ids: frozenset[str] | None = None,
    ) -> list[LexicalHit]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if not self._documents:
            return []
        query_tokens = [self._tokenizer.tokenize(query)]
        result = self.retriever.retrieve(
            query_tokens,
            k=len(self._documents),
            show_progress=False,
        )
        hits: list[LexicalHit] = []
        for raw_chunk_id, raw_score in zip(result.documents[0], result.scores[0], strict=True):
            chunk_id = (
                str(raw_chunk_id["text"])
                if isinstance(raw_chunk_id, dict) and "text" in raw_chunk_id
                else str(raw_chunk_id)
            )
            document = self._documents[chunk_id]
            if document_ids is not None and document.document_id not in document_ids:
                continue
            hits.append(
                LexicalHit(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    revision_id=document.revision_id,
                    score=float(raw_score),
                )
            )
            if len(hits) == limit:
                break
        return hits


class Bm25IndexStore:
    """Build complete versions off-path, then expose them with one rename."""

    def __init__(self, root: Path, tokenizer: ChineseTokenizer) -> None:
        self._root = root.expanduser().resolve()
        self._tokenizer = tokenizer

    def build(
        self,
        documents: Sequence[LexicalDocument],
        *,
        version_id: str,
    ) -> BuiltLexicalIndex:
        if not documents:
            raise ValueError("a lexical index requires at least one document")
        if not version_id or Path(version_id).name != version_id:
            raise ValueError("version_id must be one safe path component")
        ordered = sorted(documents, key=lambda item: item.chunk_id)
        chunk_ids = [item.chunk_id for item in ordered]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("lexical chunk IDs must be unique")
        scopes = {(item.user_id, item.course_id) for item in ordered}
        if len(scopes) != 1:
            raise ValueError("one lexical version may contain only one user/course scope")
        user_id, course_id = next(iter(scopes))
        course_root = self._root / user_id / course_id
        course_root.mkdir(parents=True, exist_ok=True)
        final_path = course_root / version_id
        if final_path.exists():
            raise FileExistsError(f"lexical version already exists: {version_id}")
        staging = Path(tempfile.mkdtemp(prefix=".tmp-", dir=course_root))
        try:
            retriever = bm25s.BM25(method="lucene")
            retriever.index(
                [self._tokenizer.tokenize(item.text) for item in ordered],
                show_progress=False,
            )
            retriever.save(staging, corpus=chunk_ids)
            payload: dict[str, object] = {
                "schema_version": "1",
                "version_id": version_id,
                "user_id": user_id,
                "course_id": course_id,
                "tokenizer_version": self._tokenizer.version,
                "dictionary_hash": self._tokenizer.dictionary_hash,
                "document_set_hash": document_set_hash(ordered),
                "documents": [
                    {
                        "chunk_id": item.chunk_id,
                        "user_id": item.user_id,
                        "course_id": item.course_id,
                        "document_id": item.document_id,
                        "revision_id": item.revision_id,
                        "text": item.text,
                        "content_sha256": item.content_sha256,
                    }
                    for item in ordered
                ],
            }
            manifest_hash = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
            payload["manifest_hash"] = manifest_hash
            manifest_path = staging / "manifest.json"
            with manifest_path.open("wb") as manifest_file:
                manifest_file.write(_canonical_bytes(payload))
                manifest_file.flush()
                os.fsync(manifest_file.fileno())
            os.replace(staging, final_path)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return BuiltLexicalIndex(
            version_id=version_id,
            storage_path=final_path,
            manifest_hash=manifest_hash,
            document_set_hash=str(payload["document_set_hash"]),
            tokenizer_version=self._tokenizer.version,
            dictionary_hash=self._tokenizer.dictionary_hash,
            chunk_count=len(ordered),
            document_ids=tuple(sorted({item.document_id for item in ordered})),
            revision_ids=tuple(sorted({item.revision_id for item in ordered})),
        )

    def load(self, manifest: BuiltLexicalIndex) -> LoadedBm25Index:
        path = Path(manifest.storage_path).expanduser().resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("lexical manifest path escapes configured root")
        payload = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        recorded_hash = payload.pop("manifest_hash", None)
        actual_hash = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        if recorded_hash != actual_hash or actual_hash != manifest.manifest_hash:
            raise ValueError("lexical manifest hash mismatch")
        if payload["tokenizer_version"] != self._tokenizer.version:
            raise ValueError("lexical tokenizer version mismatch")
        if payload["dictionary_hash"] != self._tokenizer.dictionary_hash:
            raise ValueError("lexical dictionary hash mismatch")
        if payload["document_set_hash"] != manifest.document_set_hash:
            raise ValueError("lexical document set hash mismatch")
        documents = {item["chunk_id"]: LexicalDocument(**item) for item in payload["documents"]}
        retriever = bm25s.BM25.load(path, load_corpus=True, mmap=True)
        return LoadedBm25Index(retriever, self._tokenizer, documents)
