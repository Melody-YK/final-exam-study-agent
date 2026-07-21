"""Deterministic page-, section-, and parent-aware chunk derivation."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from study_contracts import Block, Chunk, Page, SourceLocator, canonical_sha256

DEFAULT_MAX_CHARS = 1_200


def chunk_pages(
    pages: Sequence[Page],
    *,
    revision_id: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[Chunk]:
    """Derive stable chunks without crossing a source page or section boundary."""

    if not revision_id.strip():
        raise ValueError("revision_id must not be blank")
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    page_ordinals = [page.ordinal for page in pages]
    if page_ordinals != sorted(set(page_ordinals)):
        raise ValueError("pages must be ordered by unique ordinal")

    drafts: list[_ChunkDraft] = []
    for page in pages:
        drafts.extend(_chunk_page(page, max_chars=max_chars))

    chunks: list[Chunk] = []
    for ordinal, draft in enumerate(drafts, start=1):
        content_sha256 = hashlib.sha256(draft.text.encode("utf-8")).hexdigest()
        identity = canonical_sha256(
            {
                "revision_id": revision_id,
                "locator": draft.locator.model_dump(mode="json"),
                "section_path": list(draft.section_path),
                "source_block_ids": list(draft.source_block_ids),
                "content_sha256": content_sha256,
            }
        )
        chunks.append(
            Chunk(
                id=f"chunk-{identity[:32]}",
                revision_id=revision_id,
                text=draft.text,
                locator=draft.locator,
                section_path=list(draft.section_path),
                source_block_ids=list(draft.source_block_ids),
                token_count_estimate=max(1, (len(draft.text) + 3) // 4),
                content_sha256=content_sha256,
                ordinal=ordinal,
            )
        )
    return chunks


class _ChunkDraft:
    __slots__ = ("locator", "section_path", "source_block_ids", "text")

    def __init__(
        self,
        *,
        text: str,
        locator: SourceLocator,
        section_path: tuple[str, ...],
        source_block_ids: tuple[str, ...],
    ) -> None:
        self.text = text
        self.locator = locator
        self.section_path = section_path
        self.source_block_ids = source_block_ids


def _chunk_page(page: Page, *, max_chars: int) -> list[_ChunkDraft]:
    blocks = sorted(page.blocks, key=lambda block: block.reading_order)
    by_id = {block.id: block for block in blocks}
    drafts: list[_ChunkDraft] = []
    current_texts: list[str] = []
    current_ids: list[str] = []
    current_section: tuple[str, ...] | None = None
    locator = SourceLocator(kind=page.source_kind, ordinal=page.ordinal)

    def flush() -> None:
        nonlocal current_texts, current_ids
        if not current_texts:
            return
        assert current_section is not None
        drafts.append(
            _ChunkDraft(
                text="\n\n".join(current_texts),
                locator=locator,
                section_path=current_section,
                source_block_ids=tuple(current_ids),
            )
        )
        current_texts = []
        current_ids = []

    for block in blocks:
        if not block.text:
            continue
        section = tuple(block.section_path)
        if current_section is not None and section != current_section:
            flush()
        current_section = section

        candidate = _joined_length(current_texts, block.text)
        if current_texts and candidate <= max_chars:
            current_texts.append(block.text)
            _append_unique(current_ids, block.id)
            continue
        if current_texts:
            flush()

        context_blocks = _parent_chain(block, by_id)
        context_texts = [parent.text for parent in context_blocks if parent.text]
        context_ids = [parent.id for parent in context_blocks if parent.text]
        context_texts = _fit_context(context_texts, max_chars=max_chars)
        context_length = len("\n\n".join(context_texts))
        separator_length = 2 if context_texts else 0
        fragment_limit = max(1, max_chars - context_length - separator_length)
        fragments = _split_text(block.text, fragment_limit)

        for index, fragment in enumerate(fragments):
            current_texts = [*context_texts, fragment]
            current_ids = []
            for source_id in (*context_ids, block.id):
                _append_unique(current_ids, source_id)
            if index < len(fragments) - 1:
                flush()

    flush()
    return drafts


def _parent_chain(block: Block, by_id: dict[str, Block]) -> list[Block]:
    chain: list[Block] = []
    seen = {block.id}
    parent_id = block.parent_id
    while parent_id is not None:
        if parent_id in seen:
            raise ValueError("block parent relationship contains a cycle")
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            raise ValueError("block parent is missing from its page")
        chain.append(parent)
        parent_id = parent.parent_id
    chain.reverse()
    return chain


def _fit_context(texts: list[str], *, max_chars: int) -> list[str]:
    if not texts or max_chars <= 3:
        return []
    budget = max(1, min(max_chars // 3, max_chars - 1))
    joined = " > ".join(texts)
    if len(joined) > budget:
        joined = joined[:budget] if budget <= 3 else joined[: budget - 3].rstrip() + "..."
    return [joined]


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    fragments: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            fragments.append(remaining)
            break
        boundary = max(
            remaining.rfind("\n", 0, limit + 1),
            remaining.rfind("。", 0, limit + 1),
            remaining.rfind(" ", 0, limit + 1),
        )
        if boundary <= 0:
            boundary = limit
        elif remaining[boundary] == "。":
            boundary += 1
        fragment = remaining[:boundary].strip()
        if not fragment:
            fragment = remaining[:limit]
            boundary = limit
        fragments.append(fragment)
        remaining = remaining[boundary:].strip()
    return fragments


def _joined_length(parts: list[str], value: str) -> int:
    if not parts:
        return len(value)
    return sum(len(part) for part in parts) + len(value) + 2 * len(parts)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
