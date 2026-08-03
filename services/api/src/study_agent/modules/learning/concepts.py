"""Stable course learning-unit candidates and source validity rules."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypedDict
from unicodedata import normalize

from study_contracts import (
    LearningSourceStatus,
    LearningUnitKind,
    LearningUnitSource,
    LearningUnitStatus,
    SourceLocator,
)

_WHITESPACE = re.compile(r"\s+")
_CJK_WHITESPACE = re.compile(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])")
_ZERO_PLACEHOLDER = re.compile(r"^0+$")
_SINGLE_LEVEL_NUMBER_PREFIX = re.compile(r"^\s*\d+\s*[.)]\s*(?!\d)")
_ANY_NUMBER_PREFIX = re.compile(r"^\s*(?:第\s*)?\d+(?:\.\d+)*\s*[.)、:-]?\s*")
_DOCUMENT_CHAPTER = re.compile(
    r"第\s*(?P<number>\d+)\s*章\s*(?P<title>.*?)(?:\.[^.]+)?$",
    re.IGNORECASE,
)
_CHAPTER_PREFIX = re.compile(
    r"^第\s*(?:\d+|[〇零一二三四五六七八九十百]+)\s*章\s*",
    re.IGNORECASE,
)
_SENTENCE_PUNCTUATION = re.compile(r"[。\uff01\uff1f\uff1b;]")
MIN_PRACTICE_EVIDENCE_CHARS = 80
MIN_PRACTICE_MULTI_CHUNK_CHARS = 80
MAX_DERIVED_GOALS_PER_TOPIC = 24
_NOISE_HEADING_MARKERS = (
    "习题",
    "参考答案",
    "动手练一练",
    "课程思政",
    "知识导图",
    "教学大纲",
    "课程小结",
    "动手练习",
)
_CONTINUATION_HEADING_MARKERS = (
    "例",
    "例子",
    "示例",
    "举例",
    "图示",
    "续",
    "基本事实",
    "简单事实",
)
_TERMINAL_NOISE_HEADING_MARKERS = ("习题", "参考答案")
_AUXILIARY_GOAL_MARKERS = (
    "例子",
    "示例",
    "举例",
    "图示",
    "工作示意图",
    "知识导图",
    "课程小结",
    "重难点",
    "最熟悉",
    "不太熟悉",
    "动手练",
    "参考答案",
    "主要由以下",
)
_GENERIC_GOAL_TOKENS = {
    "抽象",
    "并行抽象",
    "上接口",
    "调度算法",
    "常用调度算法",
    "资源问题",
}
_GOAL_SIGNAL_MARKERS = (
    "概念",
    "原理",
    "机制",
    "模型",
    "结构",
    "接口",
    "功能",
    "层次",
    "任务",
    "方式",
    "类型",
    "算法",
    "条件",
    "方法",
    "过程",
    "状态",
    "原因",
    "性能",
    "指标",
    "调度",
    "管理",
    "控制",
    "分配",
    "缓冲",
    "中断",
    "死锁",
    "设备",
    "软件",
    "驱动",
    "通道",
    "独立",
    "映射",
    "访问",
    "实时",
    "优先级",
    "轮转",
    "扫描",
    "磁盘",
    "脱机",
    "dma",
    "spooling",
)


@dataclass(frozen=True, slots=True)
class PracticeEvidenceStats:
    chunk_count: int
    char_count: int

    @property
    def is_sufficient(self) -> bool:
        """Require either one substantial passage or several contextual passages."""

        return self.char_count >= MIN_PRACTICE_EVIDENCE_CHARS or (
            self.chunk_count >= 2 and self.char_count >= MIN_PRACTICE_MULTI_CHUNK_CHARS
        )


def practice_evidence_stats(texts: Iterable[str]) -> PracticeEvidenceStats:
    non_empty = [text.strip() for text in texts if text and text.strip()]
    return PracticeEvidenceStats(
        chunk_count=len(non_empty),
        char_count=sum(len(text) for text in non_empty),
    )


class _CandidateData(TypedDict):
    label: str
    kind: LearningUnitKind
    parent: str | None
    sources: dict[tuple[str, str, str], LearningUnitSource]


class _GoalData(TypedDict):
    label: str
    first_ordinal: int
    explicit: bool
    sources: dict[tuple[str, str, str], LearningUnitSource]
    texts: list[str]


def canonical_key(kind: LearningUnitKind, label: str, parent_key: str | None = None) -> str:
    """Build a course-stable key without depending on graph node identifiers."""

    normalized = _WHITESPACE.sub(" ", label.strip()).casefold()
    if not normalized:
        raise ValueError("learning unit label must not be blank")
    if parent_key:
        return f"{kind.value}:{parent_key}/{normalized}"[:255]
    return f"{kind.value}:{normalized}"[:255]


@dataclass(frozen=True, slots=True)
class ChunkCandidate:
    course_id: str
    document_id: str
    revision_id: str
    chunk_id: str
    content_sha256: str
    text: str
    section_path: tuple[str, ...]
    locator_kind: str = "page"
    page_ordinal: int = 1
    ordinal: int = 1
    source_status: LearningSourceStatus = LearningSourceStatus.VALID
    document_topic: str | None = None

    def source(self) -> LearningUnitSource:
        kind = self.locator_kind if self.locator_kind in {"page", "slide", "section"} else "page"
        return LearningUnitSource(
            document_id=self.document_id,
            revision_id=self.revision_id,
            chunk_id=self.chunk_id,
            content_sha256=self.content_sha256,
            locator=SourceLocator(kind=kind, ordinal=max(1, self.page_ordinal)),
            status=self.source_status,
        )


@dataclass(frozen=True, slots=True)
class LearningUnitCandidate:
    course_id: str
    canonical_key: str
    label: str
    kind: LearningUnitKind
    parent_canonical_key: str | None
    sources: tuple[LearningUnitSource, ...]

    @property
    def status(self) -> LearningUnitStatus:
        return (
            LearningUnitStatus.AVAILABLE
            if any(source.status is LearningSourceStatus.VALID for source in self.sources)
            else LearningUnitStatus.UNAVAILABLE
        )


def source_is_current(
    *,
    active_revision_id: str | None,
    source_revision_id: str,
    review_status: str,
    deleted_at_is_none: bool,
    chunk_exists: bool,
    expected_content_sha256: str,
    actual_content_sha256: str | None,
) -> LearningSourceStatus:
    """Return the only status under which a source can authorize a question."""

    return source_status(
        active_revision_id=active_revision_id,
        source_revision_id=source_revision_id,
        review_status=review_status,
        deleted_at_is_none=deleted_at_is_none,
        chunk_exists=chunk_exists,
        expected_content_sha256=expected_content_sha256,
        actual_content_sha256=actual_content_sha256,
    )


def source_status(
    *,
    active_revision_id: str | None,
    source_revision_id: str,
    review_status: str,
    deleted_at_is_none: bool,
    chunk_exists: bool,
    expected_content_sha256: str,
    actual_content_sha256: str | None,
) -> LearningSourceStatus:
    """Validate the complete document/revision/chunk source tuple."""

    if (
        active_revision_id != source_revision_id
        or review_status != "approved"
        or not deleted_at_is_none
        or not chunk_exists
    ):
        return LearningSourceStatus.STALE
    return (
        LearningSourceStatus.VALID
        if actual_content_sha256 == expected_content_sha256
        else LearningSourceStatus.STALE
    )


def is_zero_placeholder_label(label: str) -> bool:
    """Identify section labels made only from zeroes, including spaced zeroes."""

    compact = re.sub(r"[\s\u200b\ufeff]+", "", normalize("NFKC", label).strip())
    return _ZERO_PLACEHOLDER.fullmatch(compact) is not None


def clean_learning_unit_label(label: str) -> str:
    """Remove display-only single-level numbering without changing source identity."""

    normalized = _WHITESPACE.sub(" ", normalize("NFKC", label).strip())
    cleaned = _SINGLE_LEVEL_NUMBER_PREFIX.sub("", normalized).strip()
    return cleaned or normalized


def document_topic_from_filename(filename: str) -> str | None:
    """Extract a stable top-level topic from a chapter-style document name."""

    normalized = _WHITESPACE.sub(" ", normalize("NFKC", filename).strip())
    match = _DOCUMENT_CHAPTER.search(normalized)
    if match is None:
        return None
    number = match.group("number")
    title = _WHITESPACE.sub(" ", match.group("title").strip(" -_:").strip("\uff1a")).strip()
    return f"第{number}章 {title}" if title else f"第{number}章"


def _clean_section_path(section_path: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        label
        for raw_label in section_path
        if (label := _WHITESPACE.sub(" ", raw_label.strip()))
        and not is_zero_placeholder_label(label)
    )


def _topic_token(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalize("NFKC", value).casefold())


def _goal_subject(value: str) -> str:
    """Turn a source heading into a concise, stable learning-goal subject."""

    label = _WHITESPACE.sub(" ", normalize("NFKC", value).strip())
    label = _CJK_WHITESPACE.sub("", label)
    label = _ANY_NUMBER_PREFIX.sub("", label).strip()
    label = re.sub(r"[\uff08(]\s*(?:重点|续)\s*[)\uff09]", "", label).strip()
    label = re.sub(r"[\uff08(]\s*[)\uff09]", "", label).strip()
    label = re.sub(r"[-—:\uff1a]+$", "", label).strip()
    label = _WHITESPACE.sub(" ", label)
    return label[:80]


def _is_noise_heading(value: str, document_topic: str) -> bool:
    label = _goal_subject(value)
    if not label or len(label) < 2 or len(label) > 80:
        return True
    token = _topic_token(label)
    topic_token = _topic_token(document_topic)
    chapter_subject = _topic_token(_CHAPTER_PREFIX.sub("", label))
    topic_subject = _topic_token(_CHAPTER_PREFIX.sub("", document_topic))
    if (
        not token
        or token == topic_token
        or (_CHAPTER_PREFIX.match(label) is not None and chapter_subject == topic_subject)
    ):
        return True
    if any(_topic_token(marker) in token for marker in _NOISE_HEADING_MARKERS):
        return True
    if "?" in label or "\uff1f" in label:
        return True
    return bool(re.fullmatch(r"[0-9.()\uff08\uff09\s]+", label))


def _is_continuation_heading(value: str) -> bool:
    label = _topic_token(_goal_subject(value))
    return any(
        label.startswith(_topic_token(marker)) or label.endswith(_topic_token(marker))
        for marker in _CONTINUATION_HEADING_MARKERS
    )


def _looks_like_heading(value: str) -> bool:
    label = _goal_subject(value)
    if not label or len(label) > 64 or _SENTENCE_PUNCTUATION.search(label):
        return False
    if len(label) > 36 and sum(label.count(mark) for mark in (",", "\uff0c", "、")) > 1:
        return False
    return not label.startswith(("", "", "•", "图", "表", "如果", "由于", "当", "N="))


def _chunk_heading(chunk: ChunkCandidate, document_topic: str) -> tuple[str | None, bool]:
    path = _clean_section_path(chunk.section_path)
    for raw_label in reversed(path):
        if _is_continuation_heading(raw_label):
            return None, True
        if not _is_noise_heading(raw_label, document_topic) and _looks_like_heading(raw_label):
            return _goal_subject(raw_label), True

    lines = [
        _WHITESPACE.sub(" ", line.strip())
        for line in normalize("NFKC", chunk.text).splitlines()
        if line.strip()
    ]
    for line in lines[:4]:
        if _is_continuation_heading(line):
            return None, False
        if not _is_noise_heading(line, document_topic) and _looks_like_heading(line):
            return _goal_subject(line), False
    return None, False


def _is_substantive_text(text: str, document_topic: str) -> bool:
    normalized = _WHITESPACE.sub(" ", text.strip())
    if len(normalized) < 40:
        return False
    opening_token = _topic_token(normalized[:160])
    if any(_topic_token(marker) in opening_token for marker in _NOISE_HEADING_MARKERS):
        return False
    without_topic = normalized.replace(document_topic, "").strip()
    return len(without_topic) >= 40


def _goal_label(subject: str) -> str:
    comparison = re.fullmatch(r"(.+?)\s+(?:vs\.?|VS\.?)\s+(.+)", subject)
    if comparison is not None:
        return f"比较{comparison.group(1).strip()}与{comparison.group(2).strip()}"[:80]
    if subject.endswith("优缺点"):
        return f"分析{subject}"[:80]
    if subject.startswith(("理解", "掌握", "比较", "分析", "判断", "计算", "应用", "说明")):
        return subject
    return f"理解{subject}"[:80]


def _same_goal_family(active_label: str, next_subject: str) -> bool:
    """Keep obvious subtitle continuations inside the active goal."""

    active = _topic_token(active_label.removeprefix("理解").removeprefix("掌握"))
    following = _topic_token(next_subject)
    if active.startswith("非") != following.startswith("非") and (
        active.removeprefix("非") == following.removeprefix("非")
    ):
        return False
    return len(active) >= 4 and (following in active or active in following)


def _goal_is_usable(goal: _GoalData) -> bool:
    subject = goal["label"].removeprefix("理解").removeprefix("掌握")
    token = _topic_token(subject)
    if len(token) < 4 or token in _GENERIC_GOAL_TOKENS:
        return False
    if any(_topic_token(marker) in token for marker in _AUXILIARY_GOAL_MARKERS):
        return False
    if subject.endswith(("基本概", "是什么接", "包括哪些公有操作的软")):
        return False
    return not subject.startswith(("基本原理:", "操作 P R"))


def _goal_quality(goal: _GoalData) -> tuple[int, int, int, int]:
    label = goal["label"].casefold()
    signal_count = sum(marker in label for marker in _GOAL_SIGNAL_MARKERS)
    ideal_length = 1 if 6 <= len(label) <= 32 else 0
    return (
        signal_count,
        ideal_length,
        min(len(goal["sources"]), 4),
        min(sum(len(text) for text in goal["texts"]), 2_000),
    )


def _bounded_goal_keys(goals: dict[str, _GoalData]) -> list[str]:
    """Keep broad document coverage while bounding the selector."""

    eligible = [
        (key, goal)
        for key, goal in goals.items()
        if _goal_is_usable(goal) and practice_evidence_stats(goal["texts"]).is_sufficient
    ]
    eligible.sort(key=lambda item: (item[1]["first_ordinal"], item[0]))
    if len(eligible) <= MAX_DERIVED_GOALS_PER_TOPIC:
        return [key for key, _goal in eligible]

    selected: list[tuple[str, _GoalData]] = []
    selected_keys: set[str] = set()
    coverage_slots = max(1, MAX_DERIVED_GOALS_PER_TOPIC * 2 // 3)
    first_ordinal = eligible[0][1]["first_ordinal"]
    last_ordinal = eligible[-1][1]["first_ordinal"]
    ordinal_span = max(1, last_ordinal - first_ordinal + 1)
    for bucket in range(coverage_slots):
        start = first_ordinal + bucket * ordinal_span // coverage_slots
        end = first_ordinal + (bucket + 1) * ordinal_span // coverage_slots
        candidates = [
            item for item in eligible if start <= item[1]["first_ordinal"] < max(start + 1, end)
        ]
        if not candidates:
            continue
        winner = max(
            candidates,
            key=lambda item: (
                _goal_quality(item[1]),
                item[1]["explicit"],
            ),
        )
        selected.append(winner)
        selected_keys.add(winner[0])

    remaining = sorted(
        (item for item in eligible if item[0] not in selected_keys),
        key=lambda item: (
            _goal_quality(item[1]),
            item[1]["explicit"],
            -item[1]["first_ordinal"],
        ),
        reverse=True,
    )
    selected.extend(remaining[: MAX_DERIVED_GOALS_PER_TOPIC - len(selected)])
    selected.sort(key=lambda item: (item[1]["first_ordinal"], item[0]))
    return [key for key, _goal in selected]


def _add_document_topic_candidates(
    candidates: dict[str, _CandidateData],
    chunks: list[ChunkCandidate],
    topic: str,
) -> str:
    root_label = clean_learning_unit_label(topic)
    root_key = canonical_key(LearningUnitKind.SECTION, root_label)
    root = candidates.setdefault(
        root_key,
        {
            "label": root_label,
            "kind": LearningUnitKind.SECTION,
            "parent": None,
            "sources": {},
        },
    )
    goals: dict[str, _GoalData] = {}
    active_goal_key: str | None = None
    suppress_implicit_headings = False
    terminal_noise_section = False
    for chunk in sorted(chunks, key=lambda item: (item.ordinal, item.chunk_id)):
        source_key = (chunk.document_id, chunk.revision_id, chunk.chunk_id)
        path = _clean_section_path(chunk.section_path)
        if path and any(
            any(
                _topic_token(marker) in _topic_token(label)
                for marker in _TERMINAL_NOISE_HEADING_MARKERS
            )
            for label in path
        ):
            active_goal_key = None
            suppress_implicit_headings = True
            terminal_noise_section = True
            continue
        if terminal_noise_section:
            continue
        heading, explicit = _chunk_heading(chunk, root_label)
        if heading is not None:
            suppress_implicit_headings = False
        if suppress_implicit_headings and not explicit:
            continue
        if _is_substantive_text(chunk.text, root_label):
            root["sources"][source_key] = chunk.source()
        if heading is not None:
            active_goal = goals.get(active_goal_key or "")
            if active_goal is None or not _same_goal_family(active_goal["label"], heading):
                goal_label = _goal_label(heading)
                active_goal_key = canonical_key(LearningUnitKind.CONCEPT, goal_label, root_key)
                goals.setdefault(
                    active_goal_key,
                    {
                        "label": goal_label,
                        "first_ordinal": chunk.ordinal,
                        "explicit": explicit,
                        "sources": {},
                        "texts": [],
                    },
                )
        if active_goal_key is None or not _is_substantive_text(chunk.text, root_label):
            continue
        goal = goals[active_goal_key]
        goal["explicit"] = goal["explicit"] or explicit
        goal["sources"][source_key] = chunk.source()
        goal["texts"].append(chunk.text)

    for goal_key in _bounded_goal_keys(goals):
        goal = goals[goal_key]
        candidates[goal_key] = {
            "label": goal["label"],
            "kind": LearningUnitKind.CONCEPT,
            "parent": root_key,
            "sources": goal["sources"],
        }
    return root_key


def build_learning_unit_candidates(
    course_id: str,
    chunks: Iterable[ChunkCandidate],
    *,
    controlled_terms: Iterable[str] = (),
) -> list[LearningUnitCandidate]:
    """Project approved chunk structure into bounded section/concept units."""

    terms = tuple(
        dict.fromkeys(
            _WHITESPACE.sub(" ", term.strip()) for term in controlled_terms if term.strip()
        )
    )
    candidates: dict[str, _CandidateData] = {}
    root_keys_by_label: dict[str, str] = {}
    ordered_chunks = sorted(
        chunks,
        key=lambda chunk: (chunk.document_id, chunk.revision_id, chunk.ordinal, chunk.chunk_id),
    )
    scoped_chunks = [chunk for chunk in ordered_chunks if chunk.course_id == course_id]
    topic_groups: dict[tuple[str, str], list[ChunkCandidate]] = {}
    legacy_chunks: list[ChunkCandidate] = []
    root_key_by_chunk: dict[str, str] = {}
    for chunk in scoped_chunks:
        if chunk.document_topic:
            topic_groups.setdefault((chunk.document_id, chunk.document_topic), []).append(chunk)
        else:
            legacy_chunks.append(chunk)
    for (_document_id, topic), topic_chunks in sorted(topic_groups.items()):
        topic_root_key = _add_document_topic_candidates(candidates, topic_chunks, topic)
        for chunk in topic_chunks:
            root_key_by_chunk[chunk.chunk_id] = topic_root_key

    for chunk in legacy_chunks:
        if chunk.course_id != course_id:
            continue
        path = _clean_section_path(chunk.section_path)
        if not path:
            path = ("未分类",)
        parent_key: str | None = None
        legacy_root_key: str | None = None
        for path_index, raw_label in enumerate(path):
            label = clean_learning_unit_label(raw_label)
            if path_index == 0:
                # Keep the first source-facing canonical key for compatibility,
                # while grouping headings that differ only by display numbering.
                group_key = label.casefold()
                key = root_keys_by_label.setdefault(
                    group_key, canonical_key(LearningUnitKind.SECTION, raw_label)
                )
            else:
                key = canonical_key(LearningUnitKind.SECTION, raw_label, parent_key)
            entry = candidates.setdefault(
                key,
                {
                    "label": label,
                    "kind": LearningUnitKind.SECTION,
                    "parent": parent_key,
                    "sources": {},
                },
            )
            entry["sources"][(chunk.document_id, chunk.revision_id, chunk.chunk_id)] = (
                chunk.source()
            )
            if legacy_root_key is None:
                legacy_root_key = key
            parent_key = key

        root_key_by_chunk[chunk.chunk_id] = legacy_root_key or parent_key or ""

    for chunk in scoped_chunks:
        chunk_root_key = root_key_by_chunk.get(chunk.chunk_id)
        if not chunk_root_key:
            continue
        for term in terms:
            if term.casefold() not in chunk.text.casefold():
                continue
            goal_label = _goal_label(term)
            key = canonical_key(LearningUnitKind.CONCEPT, goal_label, chunk_root_key)
            entry = candidates.setdefault(
                key,
                {
                    "label": goal_label,
                    "kind": LearningUnitKind.CONCEPT,
                    "parent": chunk_root_key,
                    "sources": {},
                },
            )
            entry["sources"][(chunk.document_id, chunk.revision_id, chunk.chunk_id)] = (
                chunk.source()
            )

    return [
        LearningUnitCandidate(
            course_id=course_id,
            canonical_key=key,
            label=str(entry["label"]),
            kind=entry["kind"],
            parent_canonical_key=entry["parent"],
            sources=tuple(entry["sources"].values()),
        )
        for key, entry in sorted(candidates.items())
    ]
