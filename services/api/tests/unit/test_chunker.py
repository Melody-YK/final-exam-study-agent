import hashlib

import pytest

from study_agent.modules.ingestion.chunker import chunk_pages
from study_contracts import Block, BlockType, BoundingBox, Page


def _block(
    block_id: str,
    text: str,
    order: int,
    *,
    block_type: BlockType = BlockType.PARAGRAPH,
    parent_id: str | None = None,
    section_path: list[str] | None = None,
) -> Block:
    return Block(
        id=block_id,
        type=block_type,
        text=text,
        bbox_norm=BoundingBox(x=0, y=0, width=1, height=0.1),
        reading_order=order,
        confidence=1,
        source_backend="pdf-native",
        source_version="1.0",
        raw_result_ref=f"checkpoint-{order}",
        parent_id=parent_id,
        section_path=section_path or [],
    )


def _page(ordinal: int, blocks: list[Block]) -> Page:
    return Page(
        ordinal=ordinal,
        width=1000,
        height=1000,
        source_backend="pdf-native",
        source_version="1.0",
        raw_result_ref=f"checkpoint-page-{ordinal}",
        blocks=blocks,
    )


def test_chunker_is_deterministic_and_never_crosses_page_or_section() -> None:
    pages = [
        _page(
            1,
            [
                _block(
                    "title-1",
                    "进程管理",
                    0,
                    block_type=BlockType.TITLE,
                    section_path=["进程管理"],
                ),
                _block(
                    "paragraph-1",
                    "进程是资源分配的基本单位。",
                    1,
                    parent_id="title-1",
                    section_path=["进程管理"],
                ),
                _block(
                    "title-2",
                    "线程",
                    2,
                    block_type=BlockType.TITLE,
                    section_path=["线程"],
                ),
            ],
        ),
        _page(2, [_block("paragraph-2", "第二页内容。", 0, section_path=["线程"])]),
    ]

    first = chunk_pages(pages, revision_id="revision-1", max_chars=100)
    second = chunk_pages(pages, revision_id="revision-1", max_chars=100)

    assert first == second
    assert [(chunk.locator.ordinal, chunk.section_path) for chunk in first] == [
        (1, ["进程管理"]),
        (1, ["线程"]),
        (2, ["线程"]),
    ]
    assert [chunk.ordinal for chunk in first] == [1, 2, 3]
    assert all(
        chunk.content_sha256 == hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        for chunk in first
    )


def test_chunker_repeats_parent_context_after_a_deterministic_split() -> None:
    title = _block(
        "title-1",
        "内存管理",
        0,
        block_type=BlockType.TITLE,
        section_path=["内存管理"],
    )
    child = _block(
        "paragraph-1",
        "分页机制用于隔离地址空间。" * 8,
        1,
        parent_id=title.id,
        section_path=["内存管理"],
    )

    chunks = chunk_pages([_page(1, [title, child])], revision_id="revision-1", max_chars=45)

    assert len(chunks) > 2
    assert chunks[0].source_block_ids == ["title-1"]
    for chunk in chunks[1:]:
        assert chunk.text.startswith("内存管理\n\n")
        assert chunk.source_block_ids == ["title-1", "paragraph-1"]
        assert len(chunk.text) <= 45


def test_chunker_rejects_unordered_pages_and_parent_cycles() -> None:
    with pytest.raises(ValueError, match="ordered"):
        chunk_pages(
            [_page(2, [_block("b-2", "two", 0)]), _page(1, [_block("b-1", "one", 0)])],
            revision_id="revision-1",
        )

    first = _block("first", "first", 0, parent_id="second")
    second = _block("second", "second", 1, parent_id="first")
    with pytest.raises(ValueError, match="cycle"):
        chunk_pages([_page(1, [first, second])], revision_id="revision-1")
