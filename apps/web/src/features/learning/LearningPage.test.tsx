import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { studyApi } from "../../api/client";
import type {
  LearningSummary,
  LearningUnit,
  PracticeBatchSnapshot,
  PracticeSessionSnapshot,
  ReviewQueueItem,
} from "../../api/types";
import { availableCapabilities, renderInWorkspace } from "../../test/render";
import { LearningPage } from "./LearningPage";
import { learningSourceKey } from "./learningLaunch";

const evidence = {
  chunk_id: "chunk-process",
  content_sha256: "a".repeat(64),
  document_id: "document-1",
  locator: { kind: "page" as const, ordinal: 3 },
  quote: "进程是资源分配的基本单位。",
  revision_id: "revision-1",
};

const units: LearningUnit[] = [
  {
    id: "unit-1",
    course_id: "course-1",
    canonical_key: "process-management",
    label: "进程管理",
    kind: "section",
    parent_id: null,
    status: "available",
    practice_mode: "knowledge_recall",
    practice_status: "ready",
    evidence_chunk_count: 1,
    evidence_char_count: 160,
    mastery_level: "new",
    next_review_at: null,
    sources: [
      {
        ...evidence,
        status: "valid",
      },
    ],
  },
];

const queue: ReviewQueueItem[] = [
  {
    learning_unit_id: "unit-1",
    label: "进程管理",
    kind: "section",
    mastery_level: "learning",
    weakness_score: 0.7,
    next_review_at: "2026-08-03T09:00:00Z",
    source_status: "valid",
  },
];

const summary: LearningSummary = {
  course_id: "course-1",
  accuracy: 0.5,
  correct_questions: 1,
  total_questions: 2,
  due_review_count: 1,
  next_action: "先复习进程管理。",
  units,
  weak_units: queue,
};

const batch = {
  id: "batch-1",
  course_id: "course-1",
  learning_unit_ids: ["unit-1"],
  target_question_count: 5,
  total_items: 1,
  completed_items: 1,
  status: "succeeded" as const,
  phase: "saving" as const,
  question_ids: ["question-1"],
  items: [],
  created_at: "2026-08-02T08:00:00Z",
  started_at: "2026-08-02T08:00:01Z",
  completed_at: "2026-08-02T08:00:02Z",
} satisfies PracticeBatchSnapshot;

const session = {
  id: "session-1",
  course_id: "course-1",
  question_count: 1,
  started_at: "2026-08-02T08:01:00Z",
  completed_at: null,
  status: "active" as const,
  questions: [
    {
      id: "question-1",
      learning_unit_id: "unit-1",
      prompt: "进程是什么？",
      question_type: "single_choice" as const,
      practice_mode: "knowledge_recall",
      difficulty: 1,
      options: [
        { id: "a", label: "资源分配的基本单位" },
        { id: "b", label: "调度的基本单位" },
      ],
      status: "ready" as const,
      evidence_refs: [evidence],
      answered: false,
      outcome: null,
    },
  ],
} satisfies PracticeSessionSnapshot;

function mockLearningQueries() {
  vi.spyOn(studyApi, "listLearningUnits").mockResolvedValue(units);
  vi.spyOn(studyApi, "getLearningSummary").mockResolvedValue(summary);
  vi.spyOn(studyApi, "getReviewQueue").mockResolvedValue(queue);
}

function manyLearningUnits(): LearningUnit[] {
  return Array.from({ length: 15 }, (_, index) => ({
    ...units[0]!,
    id: `unit-${index + 1}`,
    canonical_key: `process-management-${index + 1}`,
    label: `学习单元 ${index + 1}`,
    sources: [
      {
        ...evidence,
        chunk_id: `chunk-${index + 1}`,
        status: "valid" as const,
      },
    ],
  }));
}

describe("LearningPage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("loads the learning scope, creates a batch, and opens a practice session", async () => {
    mockLearningQueries();
    vi.spyOn(studyApi, "createPracticeBatch").mockResolvedValue(batch);
    vi.spyOn(studyApi, "getPracticeBatch").mockResolvedValue(batch);
    vi.spyOn(studyApi, "createPracticeSession").mockResolvedValue(session);

    const { user } = renderInWorkspace(<LearningPage />);

    expect(
      await screen.findByRole("heading", { name: "从哪些内容开始？" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: "学习单元分页" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /进程管理/ }));
    await user.click(screen.getByRole("button", { name: /开始生成题目/ }));
    expect(await screen.findByText("题目已就绪")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /开始作答/ }));

    expect(
      await screen.findByRole("heading", { name: "进程是什么？" }),
    ).toBeInTheDocument();
    expect(studyApi.createPracticeSession).toHaveBeenCalledWith("course-1", {
      question_ids: ["question-1"],
    });
  });

  it("preselects concept scopes launched from a related note", async () => {
    const concept: LearningUnit = {
      ...units[0]!,
      id: "unit-process-state",
      canonical_key: "concept:process-management/process-state",
      label: "进程状态",
      kind: "concept",
      parent_id: "unit-1",
    };
    const launchUnits = [units[0]!, concept];
    const launchSummary = { ...summary, units: launchUnits };
    const launchBatch = {
      ...batch,
      learning_unit_ids: [concept.id],
      target_question_count: 1,
    } satisfies PracticeBatchSnapshot;
    vi.spyOn(studyApi, "listLearningUnits").mockResolvedValue(launchUnits);
    vi.spyOn(studyApi, "getLearningSummary").mockResolvedValue(launchSummary);
    vi.spyOn(studyApi, "getReviewQueue").mockResolvedValue([]);
    const createBatch = vi
      .spyOn(studyApi, "createPracticeBatch")
      .mockResolvedValue(launchBatch);
    vi.spyOn(studyApi, "getPracticeBatch").mockResolvedValue(launchBatch);

    const { user } = renderInWorkspace(<LearningPage />, {
      route: {
        pathname: "/learning",
        state: {
          noteId: "note-1",
          noteTitle: "进程基础",
          sourceKeys: [learningSourceKey(evidence)],
          knowledgePointTexts: ["进程状态"],
        },
      },
    });

    expect(
      await screen.findByText("已从“进程基础”预选 1 个相关练习范围。"),
    ).toBeInTheDocument();
    const conceptCheckbox = screen.getByRole("checkbox", {
      name: /进程状态，知识目标 · 精准练习/,
    });
    expect(conceptCheckbox).toBeChecked();
    expect(screen.getByRole("spinbutton", { name: "题目数量" })).toHaveValue(1);

    await user.click(screen.getByRole("button", { name: /开始生成题目/ }));
    expect(createBatch).toHaveBeenCalledWith("course-1", {
      learning_unit_ids: [concept.id],
      question_count: 1,
    });
  });

  it("shows a clear resume action after returning to the learning overview", async () => {
    mockLearningQueries();
    localStorage.setItem("study-agent.learning:course-1:session-id", session.id);
    vi.spyOn(studyApi, "getPracticeSession").mockResolvedValue(session);
    const { user } = renderInWorkspace(<LearningPage />);

    expect(
      await screen.findByRole("heading", { name: "进程是什么？" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "返回学习台" }));

    expect(
      await screen.findByRole("button", { name: "继续上次练习" }),
    ).toBeInTheDocument();
    expect(screen.getByText("已完成 0 / 1 题")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "继续上次练习" }));
    expect(
      await screen.findByRole("heading", { name: "进程是什么？" }),
    ).toBeInTheDocument();
  });

  it("keeps the page usable but disables new generation when Provider is unavailable", async () => {
    mockLearningQueries();
    const capabilities = {
      ...availableCapabilities,
      provider: { status: "not_configured" as const, label: "未配置回答模型" },
    };

    renderInWorkspace(<LearningPage />, { workspace: { capabilities } });

    expect(await screen.findByText("Provider 不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开始生成题目/ })).toBeDisabled();
  });

  it("shows an explicit empty state when the course has no learning units", async () => {
    vi.spyOn(studyApi, "listLearningUnits").mockResolvedValue([]);
    vi.spyOn(studyApi, "getLearningSummary").mockResolvedValue(summary);
    vi.spyOn(studyApi, "getReviewQueue").mockResolvedValue([]);

    renderInWorkspace(<LearningPage />);

    expect(
      await screen.findByRole("heading", { name: "暂时没有学习单元" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("先完成资料审核和索引，再回来开始练习。"),
    ).toBeInTheDocument();
  });

  it("does not render legacy zero placeholder units returned by an old API response", async () => {
    const legacyPlaceholder: LearningUnit = {
      ...units[0]!,
      id: "unit-zero-placeholder",
      canonical_key: "section:0 0 0 0",
      label: "0 0 0 0",
      status: "unavailable",
      sources: [],
    };
    vi.spyOn(studyApi, "listLearningUnits").mockResolvedValue([
      legacyPlaceholder,
      ...units,
    ]);
    vi.spyOn(studyApi, "getLearningSummary").mockResolvedValue(summary);
    vi.spyOn(studyApi, "getReviewQueue").mockResolvedValue(queue);

    renderInWorkspace(<LearningPage />);

    expect(
      await screen.findByRole("heading", { name: "从哪些内容开始？" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("进程管理").length).toBeGreaterThan(0);
    expect(screen.queryByText("0 0 0 0")).not.toBeInTheDocument();
  });

  it("hides zero placeholders even when the legacy response marks them as concepts", async () => {
    const legacyPlaceholder: LearningUnit = {
      ...units[0]!,
      id: "unit-zero-concept-placeholder",
      canonical_key: "concept:section:进程管理/0",
      kind: "concept",
      label: "0\u200b 0\ufeff 0 0",
      status: "available",
      sources: [],
    };
    vi.spyOn(studyApi, "listLearningUnits").mockResolvedValue([
      legacyPlaceholder,
      ...units,
    ]);
    vi.spyOn(studyApi, "getLearningSummary").mockResolvedValue(summary);
    vi.spyOn(studyApi, "getReviewQueue").mockResolvedValue(queue);

    renderInWorkspace(<LearningPage />);

    expect(
      await screen.findByRole("heading", { name: "从哪些内容开始？" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/0\s*0\s*0\s*0/)).not.toBeInTheDocument();
  });

  it("paginates units while preserving selections across pages", async () => {
    const pagedUnits = manyLearningUnits();
    vi.spyOn(studyApi, "listLearningUnits").mockResolvedValue(pagedUnits);
    vi.spyOn(studyApi, "getLearningSummary").mockResolvedValue({
      ...summary,
      units: pagedUnits,
    });
    vi.spyOn(studyApi, "getReviewQueue").mockResolvedValue(queue);

    const { user } = renderInWorkspace(<LearningPage />);

    expect(await screen.findByText("学习单元 1")).toBeInTheDocument();
    expect(screen.queryByText("学习单元 8")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "第 1 页" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByText(/课程共 15 章 · 0 个知识目标/)).toBeInTheDocument();
    expect(
      screen.getByText(/已选 0 个范围 · 覆盖 0 个知识目标/),
    ).toBeInTheDocument();

    const selection = screen.getByRole("region", { name: "从哪些内容开始？" });
    const unitList = selection.querySelector(".learning-unit-list");
    expect(unitList).toHaveClass("learning-unit-list");
    expect(selection.querySelector(".learning-start-controls")).not.toBeNull();
    await user.click(screen.getByRole("checkbox", { name: /学习单元 1/ }));

    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByText("学习单元 8")).toBeInTheDocument();
    expect(screen.queryByText("学习单元 1")).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /学习单元 8/ }));
    expect(
      screen.getByText(/已选 2 个范围 · 覆盖 0 个知识目标/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "第 1 页" }));
    const firstPageUnit = screen.getByRole("checkbox", { name: /学习单元 1/ });
    expect(firstPageUnit).toBeChecked();

    await user.click(screen.getByRole("button", { name: "第 2 页" }));
    expect(
      within(
        screen.getByRole("region", { name: "从哪些内容开始？" }),
      ).getByRole("checkbox", { name: /学习单元 8/ }),
    ).toBeChecked();
  });

  it("groups knowledge goals under a chapter and avoids overlapping selections", async () => {
    const chapter: LearningUnit = {
      ...units[0]!,
      id: "chapter-1",
      canonical_key: "section:第3章 处理机调度与死锁",
      label: "第3章 处理机调度与死锁",
    };
    const goals: LearningUnit[] = [
      {
        ...units[0]!,
        id: "goal-1",
        canonical_key: "concept:section:第3章 处理机调度与死锁/理解银行家算法",
        kind: "concept",
        label: "理解银行家算法",
        parent_id: chapter.id,
      },
      {
        ...units[0]!,
        id: "goal-2",
        canonical_key:
          "concept:section:第3章 处理机调度与死锁/理解死锁检测算法",
        kind: "concept",
        label: "理解死锁检测算法",
        parent_id: chapter.id,
      },
    ];
    const groupedUnits = [chapter, ...goals];
    vi.spyOn(studyApi, "listLearningUnits").mockResolvedValue(groupedUnits);
    vi.spyOn(studyApi, "getLearningSummary").mockResolvedValue({
      ...summary,
      units: groupedUnits,
    });
    vi.spyOn(studyApi, "getReviewQueue").mockResolvedValue(queue);

    const { user } = renderInWorkspace(<LearningPage />);

    const chapterCheckbox = await screen.findByRole("checkbox", {
      name: /第3章 处理机调度与死锁/,
    });
    const firstGoalCheckbox = screen.getByRole("checkbox", {
      name: /理解银行家算法/,
    });
    expect(
      screen.getByText(
        "章节适合整章练习；知识目标适合精准练习。单批可选 1–10 个范围，题目数不能少于范围数。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("课程共 1 章 · 2 个知识目标")).toBeInTheDocument();
    expect(chapterCheckbox).toHaveAccessibleName(/2 个知识目标/);
    expect(firstGoalCheckbox).not.toBeChecked();
    expect(chapterCheckbox).not.toBeChecked();

    await user.click(firstGoalCheckbox);
    expect(firstGoalCheckbox).toBeChecked();
    expect(
      screen.getByText("已选 1 个范围 · 覆盖 1 个知识目标"),
    ).toBeInTheDocument();

    await user.click(chapterCheckbox);
    expect(chapterCheckbox).toBeChecked();
    expect(firstGoalCheckbox).not.toBeChecked();
    expect(
      screen.getByText("已选 1 个范围 · 覆盖 2 个知识目标"),
    ).toBeInTheDocument();

    await user.click(firstGoalCheckbox);
    expect(firstGoalCheckbox).toBeChecked();
    expect(chapterCheckbox).not.toBeChecked();
  });

  it("defaults to one question per knowledge goal and preserves a manual count", async () => {
    const chapter: LearningUnit = {
      ...units[0]!,
      id: "chapter-goal-count",
      canonical_key: "section:进程管理",
      label: "进程管理",
    };
    const goals = Array.from({ length: 4 }, (_, index): LearningUnit => ({
      ...units[0]!,
      id: `goal-${index + 1}`,
      canonical_key: `concept:section:进程管理/知识目标${index + 1}`,
      kind: "concept",
      label: `知识目标 ${index + 1}`,
      parent_id: chapter.id,
    }));
    const goalUnits = [chapter, ...goals];
    vi.spyOn(studyApi, "listLearningUnits").mockResolvedValue(goalUnits);
    vi.spyOn(studyApi, "getLearningSummary").mockResolvedValue({
      ...summary,
      units: goalUnits,
    });
    vi.spyOn(studyApi, "getReviewQueue").mockResolvedValue(queue);
    const createBatch = vi
      .spyOn(studyApi, "createPracticeBatch")
      .mockResolvedValue({
        ...batch,
        learning_unit_ids: goals.map((goal) => goal.id),
        target_question_count: 5,
        total_items: 5,
      });
    vi.spyOn(studyApi, "getPracticeBatch").mockResolvedValue({
      ...batch,
      learning_unit_ids: goals.map((goal) => goal.id),
      target_question_count: 5,
      total_items: 5,
    });

    const { user } = renderInWorkspace(<LearningPage />);
    const questionCount = await screen.findByRole("spinbutton", {
      name: "题目数量",
    });

    for (const [index, goal] of goals.entries()) {
      await user.click(
        screen.getByRole("checkbox", { name: new RegExp(goal.label) }),
      );
      if (index === 0) expect(questionCount).toHaveValue(1);
    }
    expect(questionCount).toHaveValue(4);
    expect(
      screen.getByText(
        "精准练习默认每个知识目标 1 道题；需要巩固时可手动增加。",
      ),
    ).toBeInTheDocument();

    await user.clear(questionCount);
    await user.type(questionCount, "5");
    await user.click(
      screen.getByRole("checkbox", { name: /知识目标 1/ }),
    );
    expect(questionCount).toHaveValue(5);
    await user.click(
      screen.getByRole("checkbox", { name: /知识目标 1/ }),
    );
    expect(questionCount).toHaveValue(5);

    await user.click(screen.getByRole("button", { name: /开始生成题目/ }));
    expect(createBatch).toHaveBeenCalledWith("course-1", {
      learning_unit_ids: expect.arrayContaining(goals.map((goal) => goal.id)),
      question_count: 5,
    });
  });

  it("labels exercise prototypes and defaults an exercise scope to its prototype count", async () => {
    const chapter: LearningUnit = {
      ...units[0]!,
      id: "exercise-chapter",
      canonical_key: "section:23-24A",
      label: "23-24A",
      practice_mode: "exercise_variant",
    };
    const prototypes = [10, 3, 6].map(
      (number): LearningUnit => ({
        ...units[0]!,
        id: `prototype-${number}`,
        canonical_key: `concept:section:23-24A/第${number}题`,
        kind: "concept",
        label: `第${number}题`,
        parent_id: chapter.id,
        practice_mode: "exercise_variant",
        prototype_question_type: number === 6 ? "calculation" : "short_answer",
      }),
    );
    const exerciseUnits = [chapter, ...prototypes];
    const exerciseBatch = {
      ...batch,
      learning_unit_ids: [chapter.id],
      target_question_count: 3,
      total_items: 3,
    } satisfies PracticeBatchSnapshot;
    vi.spyOn(studyApi, "listLearningUnits").mockResolvedValue(exerciseUnits);
    vi.spyOn(studyApi, "getLearningSummary").mockResolvedValue({
      ...summary,
      units: exerciseUnits,
    });
    vi.spyOn(studyApi, "getReviewQueue").mockResolvedValue([]);
    const createBatch = vi
      .spyOn(studyApi, "createPracticeBatch")
      .mockResolvedValue(exerciseBatch);
    vi.spyOn(studyApi, "getPracticeBatch").mockResolvedValue(exerciseBatch);

    const { user } = renderInWorkspace(<LearningPage />);

    const chapterCheckbox = await screen.findByRole("checkbox", {
      name: /23-24A/,
    });
    expect(chapterCheckbox).toHaveAccessibleName(/习题范围 · 同型变式/);
    expect(chapterCheckbox).toHaveAccessibleName(/3 道原型题/);
    expect(
      screen.getByRole("checkbox", { name: /第6题/ }),
    ).toHaveAccessibleName(/原型题 · 计算题 · 同型变式/);

    const questionCount = screen.getByRole("spinbutton", {
      name: "题目数量",
    });
    await user.click(chapterCheckbox);

    expect(questionCount).toHaveValue(3);
    expect(
      screen.getByText("已选 1 个范围 · 覆盖 3 道原型题"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始生成变式题" })).toBeEnabled();
    expect(screen.getByText(/习题资料会生成同知识点变式题/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "开始生成变式题" }));
    expect(createBatch).toHaveBeenCalledWith("course-1", {
      learning_unit_ids: [chapter.id],
      question_count: 3,
    });
  });

  it("allows trying a low-confidence prototype with an explicit warning", async () => {
    const lowConfidenceUnit: LearningUnit = {
      ...units[0]!,
      id: "prototype-low-confidence",
      canonical_key: "concept:第9题",
      kind: "concept",
      label: "第9题",
      parent_id: null,
      practice_mode: "exercise_variant",
      practice_status: "low_confidence",
      practice_confidence_note: "原型含有可能的公式或 OCR 噪声，生成时只使用能够确认的方法。",
    };
    const lowConfidenceBatch = {
      ...batch,
      learning_unit_ids: [lowConfidenceUnit.id],
      target_question_count: 1,
      total_items: 1,
    } satisfies PracticeBatchSnapshot;
    vi.spyOn(studyApi, "listLearningUnits").mockResolvedValue([lowConfidenceUnit]);
    vi.spyOn(studyApi, "getLearningSummary").mockResolvedValue({
      ...summary,
      units: [lowConfidenceUnit],
    });
    vi.spyOn(studyApi, "getReviewQueue").mockResolvedValue([]);
    const createBatch = vi
      .spyOn(studyApi, "createPracticeBatch")
      .mockResolvedValue(lowConfidenceBatch);

    const { user } = renderInWorkspace(<LearningPage />);
    const checkbox = await screen.findByRole("checkbox", { name: /第9题/ });

    expect(checkbox).toHaveAccessibleName(/低置信度/);
    await user.click(checkbox);
    expect(
      screen.getByText(/所选范围包含低置信度原型/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /开始生成变式题/ }));
    expect(createBatch).toHaveBeenCalledWith("course-1", {
      learning_unit_ids: [lowConfidenceUnit.id],
      question_count: 1,
    });
  });

  it("warns when a selected chapter contains a low-confidence prototype", async () => {
    const chapter: LearningUnit = {
      ...units[0]!,
      id: "chapter-with-low-confidence-prototype",
      canonical_key: "section:习题章节",
      label: "习题章节",
    };
    const prototype: LearningUnit = {
      ...units[0]!,
      id: "prototype-child-low-confidence",
      canonical_key: "concept:section:习题章节/第3题",
      kind: "concept",
      label: "第3题",
      parent_id: chapter.id,
      practice_mode: "exercise_variant",
      practice_status: "low_confidence",
      practice_confidence_note: "原型材料较短，可能缺少完整题干或求解条件。",
    };
    const groupedUnits = [chapter, prototype];
    vi.spyOn(studyApi, "listLearningUnits").mockResolvedValue(groupedUnits);
    vi.spyOn(studyApi, "getLearningSummary").mockResolvedValue({
      ...summary,
      units: groupedUnits,
    });
    vi.spyOn(studyApi, "getReviewQueue").mockResolvedValue([]);

    const { user } = renderInWorkspace(<LearningPage />);
    const chapterCheckbox = await screen.findByRole("checkbox", {
      name: /习题章节/,
    });

    await user.click(chapterCheckbox);
    expect(
      screen.getByText(/所选范围包含低置信度原型/),
    ).toBeInTheDocument();
  });

  it("allows nine user-selected scopes and requires enough questions to cover them", async () => {
    const selectableUnits = manyLearningUnits().slice(0, 9);
    vi.spyOn(studyApi, "listLearningUnits").mockResolvedValue(selectableUnits);
    vi.spyOn(studyApi, "getLearningSummary").mockResolvedValue({
      ...summary,
      units: selectableUnits,
    });
    vi.spyOn(studyApi, "getReviewQueue").mockResolvedValue(queue);
    const createBatch = vi
      .spyOn(studyApi, "createPracticeBatch")
      .mockResolvedValue({
        ...batch,
        learning_unit_ids: selectableUnits.map((unit) => unit.id),
        target_question_count: 9,
        total_items: 9,
      });
    vi.spyOn(studyApi, "getPracticeBatch").mockResolvedValue({
      ...batch,
      learning_unit_ids: selectableUnits.map((unit) => unit.id),
      target_question_count: 9,
      total_items: 9,
    });

    const { user } = renderInWorkspace(<LearningPage />);
    await screen.findByText("学习单元 1");

    for (const checkbox of screen.getAllByRole("checkbox")) {
      await user.click(checkbox);
    }
    await user.click(screen.getByRole("button", { name: "下一页" }));
    for (const checkbox of screen.getAllByRole("checkbox")) {
      await user.click(checkbox);
    }

    expect(
      screen.getByText("已选 9 个范围 · 覆盖 0 个知识目标"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "当前选择了 9 个范围，至少需要 9 道题，才能保证每个范围都分到题目。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开始生成题目/ })).toBeDisabled();

    const questionCount = screen.getByRole("spinbutton", { name: "题目数量" });
    await user.clear(questionCount);
    await user.type(questionCount, "9");
    expect(
      screen.getByText("整章练习会在章内抽样，建议生成 5–10 道题。"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /开始生成题目/ }));
    expect(createBatch).toHaveBeenCalledWith("course-1", {
      learning_unit_ids: selectableUnits.map((unit) => unit.id),
      question_count: 9,
    });
  });
});
