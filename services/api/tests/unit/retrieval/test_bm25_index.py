import json
from pathlib import Path

import numpy as np

from study_agent.modules.retrieval.bm25_index import (
    Bm25IndexStore,
    LexicalDocument,
)
from study_agent.modules.retrieval.tokenizer import ChineseTokenizer


def _documents() -> list[LexicalDocument]:
    return [
        LexicalDocument(
            chunk_id="chunk-a",
            user_id="user-a",
            course_id="course-a",
            document_id="document-a",
            revision_id="revision-a",
            text="页式虚拟存储管理与缺页中断",
            content_sha256="a" * 64,
        ),
        LexicalDocument(
            chunk_id="chunk-b",
            user_id="user-a",
            course_id="course-a",
            document_id="document-b",
            revision_id="revision-b",
            text="进程调度与时间片轮转",
            content_sha256="b" * 64,
        ),
    ]


def test_bm25_build_is_versioned_hashed_and_atomically_visible(tmp_path: Path) -> None:
    tokenizer = ChineseTokenizer(course_terms=["页式虚拟存储管理"])
    store = Bm25IndexStore(tmp_path, tokenizer)

    first = store.build(_documents(), version_id="version-1")
    second = store.build(list(reversed(_documents())), version_id="version-2")

    assert first.storage_path.name == "version-1"
    assert second.storage_path.name == "version-2"
    assert first.document_set_hash == second.document_set_hash
    assert first.manifest_hash != second.manifest_hash
    assert not list((tmp_path / "user-a" / "course-a").glob(".tmp-*"))
    payload = json.loads((first.storage_path / "manifest.json").read_text())
    assert payload["document_set_hash"] == first.document_set_hash
    assert payload["tokenizer_version"] == tokenizer.version


def test_bm25_load_uses_mmap_and_respects_document_filter(tmp_path: Path) -> None:
    store = Bm25IndexStore(
        tmp_path,
        ChineseTokenizer(course_terms=["页式虚拟存储管理"]),
    )
    manifest = store.build(_documents(), version_id="version-1")

    loaded = store.load(manifest)
    results = loaded.search("页式虚拟存储管理", limit=2)
    filtered = loaded.search(
        "页式虚拟存储管理",
        limit=2,
        document_ids=frozenset({"document-b"}),
    )

    assert isinstance(loaded.retriever.scores["data"], np.memmap)
    assert results[0].chunk_id == "chunk-a"
    assert [item.document_id for item in filtered] == ["document-b"]
