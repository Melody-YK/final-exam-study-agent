from study_agent.modules.learning.concepts import (
    ChunkCandidate,
    build_learning_unit_candidates,
    canonical_key,
    clean_learning_unit_label,
    document_topic_from_filename,
    practice_evidence_stats,
    source_status,
)
from study_contracts import LearningSourceStatus, LearningUnitKind, LearningUnitStatus


def _chunk(**overrides: object) -> ChunkCandidate:
    value: dict[str, object] = {
        "course_id": "course-1",
        "document_id": "document-1",
        "revision_id": "revision-1",
        "chunk_id": "chunk-1",
        "content_sha256": "a" * 64,
        "text": "函数定义描述了定义域和对应关系。",
        "section_path": ("函数基础",),
    }
    value.update(overrides)
    return ChunkCandidate(**value)


def test_canonical_key_is_stable_and_parented() -> None:
    parent = canonical_key(LearningUnitKind.SECTION, " 函数基础 ")
    assert parent == "section:函数基础"
    assert (
        canonical_key(LearningUnitKind.CONCEPT, " 定义域 ", parent)
        == "concept:section:函数基础/定义域"
    )


def test_clean_learning_unit_label_removes_display_numbering_but_keeps_hierarchy() -> None:
    assert clean_learning_unit_label("16. I/O 控制方式") == "I/O 控制方式"
    assert clean_learning_unit_label("2) 数组选择通道") == "数组选择通道"
    assert clean_learning_unit_label("6.5.3 设备分配") == "6.5.3 设备分配"


def test_document_topic_from_filename_extracts_chapter_titles() -> None:
    assert document_topic_from_filename("《操作系统》第3章 处理机调度与死锁.pdf") == (
        "第3章 处理机调度与死锁"
    )
    assert document_topic_from_filename("《操作系统》第6章输入输出系统.pdf") == "第6章 输入输出系统"
    assert document_topic_from_filename("课程资料.pdf") is None


def test_candidate_builder_uses_clean_labels_without_changing_canonical_keys() -> None:
    units = build_learning_unit_candidates(
        "course-1",
        [_chunk(section_path=("2. 库函数",))],
    )

    assert units[0].label == "库函数"
    assert units[0].canonical_key == "section:2. 库函数"


def test_candidate_builder_groups_root_headings_that_only_differ_by_numbering() -> None:
    units = build_learning_unit_candidates(
        "course-1",
        [
            _chunk(chunk_id="numbered", section_path=("16. I/O 控制方式",)),
            _chunk(chunk_id="plain", section_path=("I/O 控制方式",)),
        ],
    )

    roots = [unit for unit in units if unit.kind is LearningUnitKind.SECTION]
    assert len(roots) == 1
    assert roots[0].label == "I/O 控制方式"
    assert {source.chunk_id for source in roots[0].sources} == {"numbered", "plain"}


def test_candidate_builder_uses_document_topic_as_root_for_flat_chunks() -> None:
    units = build_learning_unit_candidates(
        "course-1",
        [
            _chunk(chunk_id="flat-1", section_path=(), document_topic="第3章 处理机调度与死锁"),
            _chunk(
                chunk_id="flat-2",
                section_path=("6.1 I/O系统的功能、模型和接口",),
                document_topic="第6章 输入输出系统",
                text=(
                    "I/O 系统负责设备管理、接口抽象和控制流程。"
                    "该部分说明设备、控制器以及软件层次之间如何协作，"  # noqa: RUF001
                    "并给出足够的正文作为独立练习目标的出题依据。"
                    "学习者可以据此解释各层职责并区分不同接口。"
                ),
            ),
        ],
    )

    roots = [unit for unit in units if unit.parent_canonical_key is None]
    assert {unit.label for unit in roots} == {"第3章 处理机调度与死锁", "第6章 输入输出系统"}
    concept = next(unit for unit in units if unit.kind is LearningUnitKind.CONCEPT)
    assert concept.label == "理解I/O系统的功能、模型和接口"
    assert concept.parent_canonical_key == "section:第6章 输入输出系统"
    assert {source.chunk_id for source in concept.sources} == {"flat-2"}


def test_document_topic_projection_keeps_opposite_scheduling_modes_separate() -> None:
    shared = {
        "document_topic": "第3章 处理机调度与死锁",
        "text": (
            "调度方式决定任务能否中途让出处理机。"
            "本节给出完整定义、适用场景、响应时间和实现约束，"  # noqa: RUF001
            "可作为一个独立知识目标进行辨析与练习。"
            "学习者还需要比较两类方式在切换时机、系统开销和实时响应方面的差异，"  # noqa: RUF001
            "并能根据给定任务场景判断应采用哪一种调度方式。"
        ),
    }
    units = build_learning_unit_candidates(
        "course-1",
        [
            _chunk(
                chunk_id="non-preemptive",
                ordinal=1,
                section_path=("非抢占式调度算法",),
                **shared,
            ),
            _chunk(
                chunk_id="preemptive",
                ordinal=2,
                section_path=("抢占式调度算法",),
                **shared,
            ),
        ],
    )

    concepts = [unit for unit in units if unit.kind is LearningUnitKind.CONCEPT]
    assert {unit.label for unit in concepts} == {
        "理解非抢占式调度算法",
        "理解抢占式调度算法",
    }
    assert {source.chunk_id for unit in concepts for source in unit.sources} == {
        "non-preemptive",
        "preemptive",
    }


def test_document_topic_projection_excludes_noise_from_chapter_practice_scope() -> None:
    body = (
        "银行家算法通过安全性检查决定资源分配是否会使系统进入不安全状态。"
        "请求向量、可利用向量和需求矩阵共同参与判断，"  # noqa: RUF001
        "学习者可以据此完成一轮资源请求与安全序列分析。"
        "算法只有在试分配后的系统仍存在安全序列时才允许正式分配资源。"
    )
    units = build_learning_unit_candidates(
        "course-1",
        [
            _chunk(
                chunk_id="outline",
                ordinal=1,
                section_path=(),
                document_topic="第3章 处理机调度与死锁",
                text="第3章教学大纲 " + "课程目标 " * 30,
            ),
            _chunk(
                chunk_id="banker",
                ordinal=2,
                section_path=("银行家算法",),
                document_topic="第3章 处理机调度与死锁",
                text=body,
            ),
            _chunk(
                chunk_id="answers",
                ordinal=3,
                section_path=("参考答案",),
                document_topic="第3章 处理机调度与死锁",
                text="参考答案 " + "某题的计算过程与答案 " * 30,
            ),
            _chunk(
                chunk_id="answer-continuation",
                ordinal=4,
                section_path=(),
                document_topic="第3章 处理机调度与死锁",
                text=(
                    "资源分配算法的计算过程如下。"
                    "该页继续列出习题中的需求矩阵、安全序列和最终答案，"  # noqa: RUF001
                    "但没有再次携带参考答案标题，因此也不能重新进入章节练习范围。"  # noqa: RUF001
                ),
            ),
        ],
    )

    chapter = next(unit for unit in units if unit.kind is LearningUnitKind.SECTION)
    concept = next(unit for unit in units if unit.kind is LearningUnitKind.CONCEPT)
    assert {source.chunk_id for source in chapter.sources} == {"banker"}
    assert concept.label == "理解银行家算法"
    assert {source.chunk_id for source in concept.sources} == {"banker"}


def test_practice_evidence_stats_rejects_short_single_heading_and_accepts_context() -> None:
    assert not practice_evidence_stats(["只有标题"]).is_sufficient
    assert practice_evidence_stats(["正文 " * 60]).is_sufficient
    assert practice_evidence_stats(["正文 " * 15, "补充 " * 15]).is_sufficient


def test_candidates_deduplicate_keys_and_preserve_section_concept_hierarchy() -> None:
    units = build_learning_unit_candidates(
        "course-1",
        [_chunk(), _chunk(chunk_id="chunk-2", section_path=("函数基础", "映射"))],
        controlled_terms=("定义域", "不存在"),
    )

    assert {unit.label for unit in units} == {"函数基础", "理解定义域", "映射"}
    section = next(unit for unit in units if unit.label == "函数基础")
    concept = next(unit for unit in units if unit.label == "理解定义域")
    assert section.status is LearningUnitStatus.AVAILABLE
    assert concept.parent_canonical_key == section.canonical_key
    assert len(section.sources) == 2


def test_candidate_builder_cleans_zero_placeholders_and_preserves_numeric_sections() -> None:
    units = build_learning_unit_candidates(
        "course-1",
        [
            _chunk(chunk_id="empty", section_path=()),
            _chunk(chunk_id="zero", section_path=("0000",)),
            _chunk(chunk_id="spaced-zero", section_path=("0 0 0 0",)),
            _chunk(chunk_id="nested", section_path=("0000", "章节", "000")),
            _chunk(chunk_id="numbered", section_path=("1", "2.1")),
        ],
    )

    labels = {unit.label for unit in units}
    assert "0000" not in labels
    assert "000" not in labels
    assert "0 0 0 0" not in labels
    assert {"未分类", "章节", "1", "2.1"} <= labels

    section = next(unit for unit in units if unit.label == "章节")
    numbered_child = next(unit for unit in units if unit.label == "2.1")
    assert section.parent_canonical_key is None
    assert numbered_child.parent_canonical_key == "section:1"
    assert {source.chunk_id for source in section.sources} == {"nested"}
    assert {
        source.chunk_id for source in next(unit for unit in units if unit.label == "未分类").sources
    } == {
        "empty",
        "zero",
        "spaced-zero",
    }


def test_zero_placeholder_detection_handles_invisible_spacing() -> None:
    units = build_learning_unit_candidates(
        "course-1",
        [_chunk(section_path=("0\u200b 0\ufeff 0 0",))],
    )

    assert {unit.label for unit in units} == {"未分类"}


def test_candidate_builder_does_not_cross_course_scope() -> None:
    units = build_learning_unit_candidates("course-1", [_chunk(course_id="other-course")])
    assert units == []


def test_source_status_rejects_owner_revision_review_deletion_and_hash_failures() -> None:
    valid = source_status(
        active_revision_id="revision-1",
        source_revision_id="revision-1",
        review_status="approved",
        deleted_at_is_none=True,
        chunk_exists=True,
        expected_content_sha256="a" * 64,
        actual_content_sha256="a" * 64,
    )
    assert valid is LearningSourceStatus.VALID
    for values in (
        {"active_revision_id": "revision-2"},
        {"review_status": "pending"},
        {"deleted_at_is_none": False},
        {"chunk_exists": False},
        {"actual_content_sha256": "b" * 64},
    ):
        args = {
            "active_revision_id": "revision-1",
            "source_revision_id": "revision-1",
            "review_status": "approved",
            "deleted_at_is_none": True,
            "chunk_exists": True,
            "expected_content_sha256": "a" * 64,
            "actual_content_sha256": "a" * 64,
        }
        args.update(values)
        assert source_status(**args) is LearningSourceStatus.STALE
