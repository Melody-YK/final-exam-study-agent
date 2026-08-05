from study_agent.modules.learning.concepts import (
    ChunkCandidate,
    build_learning_unit_candidates,
    canonical_key,
    clean_learning_unit_label,
    document_title_from_filename,
    document_topic_from_filename,
    exercise_prototype_number,
    is_answer_key_text,
    is_exercise_prototype_label,
    practice_confidence_for_unit,
    practice_evidence_stats,
    practice_mode_for_unit,
    source_status,
)
from study_contracts import (
    LearningSourceStatus,
    LearningUnitKind,
    LearningUnitPracticeMode,
    LearningUnitPracticeStatus,
    LearningUnitStatus,
)


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


def test_document_title_from_filename_provides_a_readable_fallback() -> None:
    assert document_title_from_filename("23-24A.pdf") == "23-24A"
    assert document_title_from_filename("/uploads/课程资料.markdown") == "课程资料"
    assert document_title_from_filename("0000.pdf") == "课程资料"


def test_exercise_prototypes_and_answer_keys_select_variant_practice() -> None:
    assert is_exercise_prototype_label("第 3 题")
    assert exercise_prototype_number("第十题") == 10
    assert not is_exercise_prototype_label("第3章")
    assert is_answer_key_text("六.（10分）参考答案：")  # noqa: RUF001

    assert (
        practice_mode_for_unit(LearningUnitKind.CONCEPT, "第3题")
        is LearningUnitPracticeMode.EXERCISE_VARIANT
    )
    assert (
        practice_mode_for_unit(
            LearningUnitKind.SECTION,
            "23-24A",
            child_labels=("第3题", "理解进程状态"),
        )
        is LearningUnitPracticeMode.EXERCISE_VARIANT
    )
    assert (
        practice_mode_for_unit(
            LearningUnitKind.SECTION,
            "期末答案",
            evidence_texts=("第一题参考答案", "第二题评分: 10"),
        )
        is LearningUnitPracticeMode.EXERCISE_VARIANT
    )
    assert (
        practice_mode_for_unit(LearningUnitKind.CONCEPT, "理解进程状态")
        is LearningUnitPracticeMode.KNOWLEDGE_RECALL
    )


def test_structure_markup_is_low_confidence_even_when_text_is_long() -> None:
    status, note = practice_confidence_for_unit(
        ["<table><tr><td>参考答案</td></tr></table>" + "计算过程 " * 30],
        practice_mode=LearningUnitPracticeMode.EXERCISE_VARIANT,
        is_exercise_prototype=True,
    )

    assert status is LearningUnitPracticeStatus.LOW_CONFIDENCE
    assert note is not None
    assert "表格" in note


def test_plain_text_remains_ready() -> None:
    status, note = practice_confidence_for_unit(
        ["进程状态转换的触发条件、调度过程和判断依据。" * 6],
        practice_mode=LearningUnitPracticeMode.KNOWLEDGE_RECALL,
    )

    assert status is LearningUnitPracticeStatus.READY
    assert note is None


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


def test_flat_exam_document_uses_filename_root_and_normalized_question_ranges() -> None:
    answer = (
        "本题参考答案完整说明了进程状态转换的触发条件、调度过程和判断依据,"
        "正文长度足以作为一道独立练习题的来源证据。"
    )
    units = build_learning_unit_candidates(
        "course-1",
        [
            _chunk(
                chunk_id="table",
                ordinal=1,
                section_path=(),
                document_title="23-24A",
                text="<table>" + "调度计算数据" * 20 + "</table>",
            ),
            _chunk(
                chunk_id="question-3",
                ordinal=2,
                section_path=("三、\uff0810分\uff09参考答案\uff1a",),
                document_title="23-24A",
                text=answer,
            ),
            _chunk(
                chunk_id="question-10-a",
                ordinal=3,
                section_path=("十.\uff0810分\uff09参考答案\uff1a",),
                document_title="23-24A",
                text=answer,
            ),
            _chunk(
                chunk_id="question-10-b",
                ordinal=4,
                section_path=("十. \uff0810分\uff09参考答案\uff1a",),
                document_title="23-24A",
                text=answer,
            ),
        ],
    )

    labels = {unit.label for unit in units}
    assert "未分类" not in labels
    assert "参考答案" not in " ".join(labels)
    assert {"23-24A", "第3题", "第10题"} <= labels
    root = next(unit for unit in units if unit.label == "23-24A")
    question_10 = next(unit for unit in units if unit.label == "第10题")
    assert root.parent_canonical_key is None
    assert {source.chunk_id for source in root.sources} == {
        "table",
        "question-3",
        "question-10-a",
        "question-10-b",
    }
    assert question_10.parent_canonical_key == root.canonical_key
    assert {source.chunk_id for source in question_10.sources} == {
        "question-10-a",
        "question-10-b",
    }


def test_page_headers_do_not_become_document_sections_or_learning_goals() -> None:
    header = "广东工业大学试卷用纸\uff0c第3页\uff0c共5页"
    units = build_learning_unit_candidates(
        "course-1",
        [
            _chunk(
                section_path=(header,),
                document_title="23-24A",
                text=header,
            )
        ],
    )

    assert [(unit.label, unit.kind) for unit in units] == [("23-24A", LearningUnitKind.SECTION)]
    assert {source.chunk_id for source in units[0].sources} == {"chunk-1"}


def test_generic_document_keeps_meaningful_parser_sections() -> None:
    units = build_learning_unit_candidates(
        "course-1",
        [
            _chunk(
                chunk_id="structured",
                section_path=("测试章节",),
                document_title="learning",
            )
        ],
    )

    assert {unit.label for unit in units} == {"测试章节"}


def test_practice_evidence_stats_rejects_short_single_heading_and_accepts_context() -> None:
    assert not practice_evidence_stats(["只有标题"]).is_sufficient
    assert practice_evidence_stats(["正文 " * 60]).is_sufficient
    assert practice_evidence_stats(["正文 " * 15, "补充 " * 15]).is_sufficient


def test_practice_confidence_marks_damaged_exercise_as_low_confidence() -> None:
    status, note = practice_confidence_for_unit(
        (
            "参考答案：页面大小为2KB，计算 $2 \\mathrm { K B } / 8B$。"  # noqa: RUF001
            "补充条件说明。" * 12,
        ),
        practice_mode=LearningUnitPracticeMode.EXERCISE_VARIANT,
        is_exercise_prototype=True,
    )

    assert status is LearningUnitPracticeStatus.LOW_CONFIDENCE
    assert note is not None
    assert "OCR" in note


def test_practice_confidence_keeps_normal_latex_and_numbers_ready() -> None:
    status, note = practice_confidence_for_unit(
        (
            "页面大小为2.5KB，地址转换可写成 x^{2}，并使用 \\frac{1}{2} 的比例。"  # noqa: RUF001
            "该原型还包含完整的题干、求解条件、计算目标和参考步骤，足以独立复核。"  # noqa: RUF001
            "学习者可以依据这些条件列式、计算并解释结果，题目目标和评分依据均已给出。",  # noqa: RUF001
        ),
        practice_mode=LearningUnitPracticeMode.EXERCISE_VARIANT,
        is_exercise_prototype=True,
    )

    assert status is LearningUnitPracticeStatus.READY
    assert note is None


def test_practice_confidence_keeps_tiny_non_exercise_source_blocked() -> None:
    status, note = practice_confidence_for_unit(
        ("只有标题",),
        practice_mode=LearningUnitPracticeMode.KNOWLEDGE_RECALL,
    )

    assert status is LearningUnitPracticeStatus.INSUFFICIENT_EVIDENCE
    assert note == "有效正文不足。"


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
