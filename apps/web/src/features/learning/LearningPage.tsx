import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpenCheck,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  LoaderCircle,
  Play,
  RefreshCw,
  Save,
  Sparkles,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { studyApi } from "../../api/client";
import type {
  LearningUnit,
  PracticeBatchSnapshot,
  PracticeSessionSnapshot,
  ReviewQueueItem,
} from "../../api/types";
import { useWorkspace } from "../../app/WorkspaceContext";
import { ErrorNotice } from "../../components/ui/ErrorNotice";
import { PageHeader } from "../../components/ui/PageHeader";
import { LearningSummary } from "./LearningSummary";
import { PracticeSession } from "./PracticeSession";

const MAX_QUESTIONS = 10;
const MAX_SELECTED_SCOPES = MAX_QUESTIONS;
const LEARNING_UNITS_PAGE_SIZE = 7;
const EMPTY_UNITS: [] = [];

type LearningView = "overview" | "practice" | "summary";

type VisibleUnitGroup = {
  concepts: LearningUnit[];
  section: LearningUnit | null;
};

function storageKey(courseId: string, suffix: string): string {
  return `study-agent.learning:${courseId}:${suffix}`;
}

function readStorage(courseId: string, suffix: string): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(storageKey(courseId, suffix));
}

function questionIds(batch: PracticeBatchSnapshot | undefined): string[] {
  if (!batch) return [];
  return batch.question_ids?.length
    ? batch.question_ids
    : (batch.items ?? [])
        .map((item) => item.question_id)
        .filter((id): id is string => id !== null && id !== undefined);
}

const batchStatusLabels: Record<PracticeBatchSnapshot["status"], string> = {
  queued: "排队中",
  running: "生成中",
  partial_success: "部分完成",
  succeeded: "题目已就绪",
  failed: "生成失败",
  cancelled: "已取消",
};

const batchFailureLabels: Record<string, string> = {
  QUESTION_ANSWER_INVALID: "答案与选项不匹配",
  QUESTION_OUTPUT_INVALID: "题目结构不完整",
  QUESTION_OPTIONS_EQUIVALENT: "选项只是换序或包含重复要素",
  QUESTION_REVIEW_INVALID: "题目语义审校结果无效",
  QUESTION_SEMANTIC_INVALID: "选项存在歧义或不止一个正确答案",
  QUESTION_STRUCTURE_INVALID: "题目结构不符合要求",
  QUESTION_VARIANT_TOO_SIMILAR: "变式题与原习题过于相似",
  QUESTION_VARIANT_DUPLICATE: "变式题与已有练习重复",
  QUESTION_VARIANT_NOT_TRANSFORMED: "变式题没有更换输入条件",
  QUESTION_VARIANT_NOT_SELF_CONTAINED: "变式题缺少独立求解条件",
  QUESTION_VARIANT_NOT_SAME_METHOD: "变式题偏离了原题的知识点或解法",
  QUESTION_UNSOLVABLE: "题目条件不足或无法可靠求解",
  QUESTION_REFERENCE_ANSWER_INCONSISTENT: "参考答案或解题过程与题干不一致",
  QUESTION_EVIDENCE_UNSUPPORTED: "题目使用了资料无法支持的知识或方法",
  EVIDENCE_QUOTE_MISSING: "引用无法回到原文",
  PROVIDER_BAD_RESPONSE: "模型返回内容不完整，重试后仍未通过校验",
  SOURCE_UNAVAILABLE: "学习资料来源不可用",
  INSUFFICIENT_EVIDENCE: "有效正文不足，暂时无法稳定出题",
};

function formatBatchFailure(value: string): string {
  return value
    .split(",")
    .map((code) => batchFailureLabels[code] ?? code)
    .join("、");
}

const unitStatusLabels = {
  available: "可练习",
  stale: "来源已变更",
  unavailable: "暂无有效来源",
} as const;

const practiceStatusLabels = {
  ready: "可练习",
  insufficient_evidence: "资料不足",
  stale: "来源失效",
} as const;

function isExerciseVariant(unit: LearningUnit): boolean {
  return unit.practice_mode === "exercise_variant";
}

function prototypeQuestionTypeLabel(
  type: LearningUnit["prototype_question_type"],
): string | null {
  if (type === "calculation") return "计算题";
  if (type === "short_answer") return "简答题";
  if (type === "single_choice") return "单选题";
  if (type === "true_false") return "判断题";
  return null;
}

function exercisePrototypeNumber(label: string): number | null {
  const match = /^第\s*(\d+)\s*题$/.exec(label.trim());
  return match ? Number(match[1]) : null;
}

function isZeroPlaceholderLabel(value: string): boolean {
  return /^0+$/.test(
    value
      .normalize("NFKC")
      .trim()
      .replace(/[\s\u200B\uFEFF]+/g, ""),
  );
}

function isLegacyZeroPlaceholder(unit: LearningUnit): boolean {
  let keyLabel = unit.canonical_key.split("/").pop() ?? "";
  if (keyLabel.includes(":")) keyLabel = keyLabel.split(":").pop() ?? keyLabel;
  return isZeroPlaceholderLabel(unit.label) || isZeroPlaceholderLabel(keyLabel);
}

function isPracticeReady(unit: LearningUnit): boolean {
  return (
    unit.status === "available" && (unit.practice_status ?? "ready") === "ready"
  );
}

export function LearningPage() {
  const { capabilities, capabilitiesLoading, courseId } = useWorkspace();
  const queryClient = useQueryClient();
  const storedSessionId = readStorage(courseId, "session-id");
  const storedBatchId = readStorage(courseId, "batch-id");
  const [view, setView] = useState<LearningView>(
    storedSessionId ? "practice" : "overview",
  );
  const [selectedUnitIds, setSelectedUnitIds] = useState<string[]>([]);
  const [questionCountInput, setQuestionCountInput] = useState("5");
  const [questionCountTouched, setQuestionCountTouched] = useState(false);
  const [unitPage, setUnitPage] = useState(1);
  const [batchId, setBatchId] = useState<string | null>(storedBatchId);
  const [sessionId, setSessionId] = useState<string | null>(storedSessionId);
  const [activeSession, setActiveSession] =
    useState<PracticeSessionSnapshot | null>(null);

  const unitsQuery = useQuery({
    queryKey: ["learning-units", courseId],
    queryFn: () => studyApi.listLearningUnits(courseId),
    retry: false,
  });
  const summaryQuery = useQuery({
    queryKey: ["learning-summary", courseId],
    queryFn: () => studyApi.getLearningSummary(courseId),
    retry: false,
  });
  const reviewQueueQuery = useQuery({
    queryKey: ["learning-review-queue", courseId],
    queryFn: () => studyApi.getReviewQueue(courseId),
    retry: false,
  });
  const batchQuery = useQuery({
    queryKey: ["practice-batch", batchId],
    queryFn: () => studyApi.getPracticeBatch(batchId as string),
    enabled: batchId !== null,
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 1_000 : false;
    },
  });
  const sessionQuery = useQuery({
    queryKey: ["practice-session", sessionId],
    queryFn: () => studyApi.getPracticeSession(sessionId as string),
    enabled: sessionId !== null && activeSession === null,
    retry: false,
  });

  const createBatch = useMutation({
    mutationFn: (input: {
      learning_unit_ids: string[];
      question_count: number;
    }) => studyApi.createPracticeBatch(courseId, input),
    onSuccess: (batch) => {
      setBatchId(batch.id);
      localStorage.setItem(storageKey(courseId, "batch-id"), batch.id);
      void queryClient.invalidateQueries({
        queryKey: ["learning-summary", courseId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["learning-review-queue", courseId],
      });
    },
  });
  const regenerateUnits = useMutation({
    mutationFn: () => studyApi.regenerateLearningUnits(courseId),
    onSuccess: (updatedUnits) => {
      queryClient.setQueryData(["learning-units", courseId], updatedUnits);
      void queryClient.invalidateQueries({
        queryKey: ["learning-summary", courseId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["learning-review-queue", courseId],
      });
      setSelectedUnitIds([]);
      setQuestionCountInput("5");
      setQuestionCountTouched(false);
      setUnitPage(1);
    },
  });
  const createSession = useMutation({
    mutationFn: (input: { question_ids: string[] }) =>
      studyApi.createPracticeSession(courseId, input),
    onSuccess: (session) => {
      setActiveSession(session);
      setSessionId(session.id);
      localStorage.setItem(storageKey(courseId, "session-id"), session.id);
      setView("practice");
    },
  });

  // Keep the UI compatible with courses that were synced before placeholder cleanup.
  const units = useMemo(
    () =>
      (unitsQuery.data ?? EMPTY_UNITS).filter(
        (unit) => !isLegacyZeroPlaceholder(unit),
      ),
    [unitsQuery.data],
  );
  const displayUnits = useMemo(
    () => units.filter((unit) => unit.status === "available"),
    [units],
  );
  const sourceReadyUnits = displayUnits;
  const availableUnits = useMemo(
    () => displayUnits.filter(isPracticeReady),
    [displayUnits],
  );
  const sections = useMemo(
    () =>
      displayUnits.filter(
        (unit) => unit.kind === "section" && unit.parent_id == null,
      ),
    [displayUnits],
  );
  const conceptsBySection = useMemo(() => {
    const result = new Map<string, LearningUnit[]>();
    for (const unit of displayUnits) {
      const parentId = unit.parent_id;
      if (unit.kind !== "concept" || !parentId) continue;
      const concepts = result.get(parentId) ?? [];
      concepts.push(unit);
      result.set(parentId, concepts);
    }
    for (const concepts of result.values()) {
      concepts.sort((left, right) => {
        const leftNumber = exercisePrototypeNumber(left.label);
        const rightNumber = exercisePrototypeNumber(right.label);
        if (leftNumber !== null && rightNumber !== null) {
          return leftNumber - rightNumber;
        }
        return 0;
      });
    }
    return result;
  }, [displayUnits]);
  const knowledgeGoals = useMemo(
    () => displayUnits.filter((unit) => unit.kind === "concept"),
    [displayUnits],
  );
  const unitById = useMemo(
    () => new Map(displayUnits.map((unit) => [unit.id, unit])),
    [displayUnits],
  );
  const selectedCoverage = useMemo(() => {
    const selectedGoalIds = new Set<string>();
    const selectedPrototypeIds = new Set<string>();
    for (const unitId of selectedUnitIds) {
      const unit = unitById.get(unitId);
      if (unit?.kind === "concept") {
        if (isExerciseVariant(unit)) selectedPrototypeIds.add(unit.id);
        else selectedGoalIds.add(unit.id);
        continue;
      }
      if (unit?.kind === "section") {
        for (const concept of conceptsBySection.get(unit.id) ?? []) {
          if (isExerciseVariant(concept)) selectedPrototypeIds.add(concept.id);
          else selectedGoalIds.add(concept.id);
        }
      }
    }
    return {
      exercisePrototypes: selectedPrototypeIds.size,
      knowledgeGoals: selectedGoalIds.size,
    };
  }, [conceptsBySection, selectedUnitIds, unitById]);
  const hasSelectedSection = selectedUnitIds.some(
    (unitId) => unitById.get(unitId)?.kind === "section",
  );
  const hasSelectedExercise = selectedUnitIds.some((unitId) => {
    const unit = unitById.get(unitId);
    return unit ? isExerciseVariant(unit) : false;
  });
  const availableKnowledgeGoalCount = knowledgeGoals.filter(
    (unit) => !isExerciseVariant(unit),
  ).length;
  const availableExercisePrototypeCount = knowledgeGoals.filter((unit) =>
    isExerciseVariant(unit),
  ).length;
  const selectedCoverageLabel = [
    selectedCoverage.knowledgeGoals > 0
      ? `${selectedCoverage.knowledgeGoals} 个知识目标`
      : null,
    selectedCoverage.exercisePrototypes > 0
      ? `${selectedCoverage.exercisePrototypes} 道原型题`
      : null,
  ]
    .filter((label): label is string => label !== null)
    .join(" · ");
  const questionCount = Number(questionCountInput);
  const questionCountIsValid =
    Number.isInteger(questionCount) &&
    questionCount >= 1 &&
    questionCount <= MAX_QUESTIONS;
  const selectionBoundaryError = !questionCountIsValid
    ? `题目数量必须是 1–${MAX_QUESTIONS} 之间的整数。`
    : selectedUnitIds.length > MAX_SELECTED_SCOPES
      ? `单批最多选择 ${MAX_SELECTED_SCOPES} 个范围。`
      : selectedUnitIds.length > questionCount
        ? `当前选择了 ${selectedUnitIds.length} 个范围，至少需要 ${selectedUnitIds.length} 道题，才能保证每个范围都分到题目。`
        : null;
  const sectionById = useMemo(
    () => new Map(sections.map((section) => [section.id, section])),
    [sections],
  );
  const pagedUnits = useMemo(() => {
    const result: LearningUnit[] = [];
    const groupedConceptIds = new Set<string>();
    for (const section of sections) {
      const concepts = conceptsBySection.get(section.id) ?? [];
      if (concepts.length === 0) {
        result.push(section);
        continue;
      }
      result.push(...concepts);
      concepts.forEach((concept) => groupedConceptIds.add(concept.id));
    }
    result.push(
      ...displayUnits.filter(
        (unit) => unit.kind === "concept" && !groupedConceptIds.has(unit.id),
      ),
    );
    return result;
  }, [conceptsBySection, displayUnits, sections]);
  const totalUnitPages = Math.max(
    1,
    Math.ceil(pagedUnits.length / LEARNING_UNITS_PAGE_SIZE),
  );
  const currentUnitPage = Math.min(unitPage, totalUnitPages);
  const visibleUnitGroups = useMemo(() => {
    const start = (currentUnitPage - 1) * LEARNING_UNITS_PAGE_SIZE;
    const visibleUnits = pagedUnits.slice(
      start,
      start + LEARNING_UNITS_PAGE_SIZE,
    );
    const groups: VisibleUnitGroup[] = [];
    const groupByKey = new Map<string, VisibleUnitGroup>();
    for (const unit of visibleUnits) {
      const section =
        unit.kind === "section"
          ? unit
          : (sectionById.get(unit.parent_id ?? "") ?? null);
      const key = section?.id ?? `unscoped:${unit.parent_id ?? "root"}`;
      let group = groupByKey.get(key);
      if (!group) {
        group = { concepts: [], section };
        groupByKey.set(key, group);
        groups.push(group);
      }
      if (unit.kind === "concept") group.concepts.push(unit);
    }
    return groups;
  }, [currentUnitPage, pagedUnits, sectionById]);
  const providerAvailable = capabilities?.provider.status === "available";
  const currentBatch = batchQuery.data ?? createBatch.data ?? undefined;
  const availableQuestionIds = questionIds(currentBatch);
  const batchReady =
    currentBatch?.status === "succeeded" ||
    currentBatch?.status === "partial_success";
  const session = activeSession ?? sessionQuery.data;

  useEffect(() => {
    if (session?.status !== "completed" || view !== "practice") return;
    localStorage.removeItem(storageKey(courseId, "session-id"));
    localStorage.removeItem(`study-agent.learning:practice-progress:${session.id}`);
  }, [courseId, session, view]);

  const nextUnitSelection = (
    current: string[],
    unit: LearningUnit,
  ): string[] => {
    if (current.includes(unit.id))
      return current.filter((id) => id !== unit.id);
    if (unit.kind === "section") {
      const childIds = new Set(
        (conceptsBySection.get(unit.id) ?? []).map((concept) => concept.id),
      );
      return [...current.filter((id) => !childIds.has(id)), unit.id];
    }
    return [...current.filter((id) => id !== unit.parent_id), unit.id];
  };

  const defaultQuestionCount = (unitIds: string[]): number => {
    if (unitIds.length === 0) return 5;
    let includesRecallSection = false;
    let includesExercise = false;
    const suggested = unitIds.reduce((total, unitId) => {
      const unit = unitById.get(unitId);
      if (!unit) return total;
      if (unit.kind === "section") {
        if (isExerciseVariant(unit)) {
          includesExercise = true;
          const prototypeCount = (conceptsBySection.get(unit.id) ?? []).filter(
            (concept) => isExerciseVariant(concept) && isPracticeReady(concept),
          ).length;
          return total + Math.max(1, prototypeCount);
        }
        includesRecallSection = true;
        return total;
      }
      return total + 1;
    }, 0);
    if (includesRecallSection && !includesExercise) return 5;
    const sectionAllowance = includesRecallSection ? 5 : 0;
    return Math.min(
      MAX_QUESTIONS,
      Math.max(unitIds.length, suggested + sectionAllowance, 1),
    );
  };

  const handleUnitSelection = (unit: LearningUnit) => {
    const nextSelection = nextUnitSelection(selectedUnitIds, unit);
    setSelectedUnitIds(nextSelection);
    if (!questionCountTouched) {
      setQuestionCountInput(String(defaultQuestionCount(nextSelection)));
    }
  };

  const renderLearningUnit = (unit: LearningUnit) => {
    const selected = selectedUnitIds.includes(unit.id);
    const practiceReady = isPracticeReady(unit);
    const nextSelection = selected
      ? selectedUnitIds
      : nextUnitSelection(selectedUnitIds, unit);
    const disabled =
      !practiceReady ||
      (!selected && nextSelection.length > MAX_SELECTED_SCOPES);
    const evidenceCount =
      unit.evidence_chunk_count ?? unit.sources?.length ?? 0;
    const childCount =
      unit.kind === "section"
        ? (conceptsBySection.get(unit.id) ?? []).filter((concept) =>
            isExerciseVariant(unit) ? isExerciseVariant(concept) : true,
          ).length
        : null;
    const prototypeTypeLabel = prototypeQuestionTypeLabel(unit.prototype_question_type);
    const scopeLabel = isExerciseVariant(unit)
      ? unit.kind === "section"
        ? "习题范围 · 同型变式"
        : `原型题${prototypeTypeLabel ? ` · ${prototypeTypeLabel}` : ""} · 同型变式`
      : unit.kind === "section"
        ? "章节范围 · 整章练习"
        : "知识目标 · 精准练习";
    return (
      <label
        className={`learning-unit learning-unit--${unit.kind}${selected ? " is-selected" : ""}${disabled ? " is-disabled" : ""}`}
        key={unit.id}
      >
        <input
          checked={selected}
          disabled={disabled}
          onChange={() => handleUnitSelection(unit)}
          type="checkbox"
        />
        <span className="learning-unit__check" aria-hidden="true">
          <Check size={14} />
        </span>
        <span className="learning-unit__copy">
          <strong>{unit.label}</strong>
          <small>
            {scopeLabel}
            {childCount === null
              ? ""
              : isExerciseVariant(unit)
                ? ` · ${childCount} 道原型题`
                : ` · ${childCount} 个知识目标`}{" "}
            · {evidenceCount} 个证据片段
          </small>
        </span>
        <span
          className={`learning-unit__status learning-unit__status--${unit.practice_status ?? (unit.status === "available" ? "ready" : "stale")}`}
        >
          {unit.practice_status
            ? practiceStatusLabels[unit.practice_status]
            : unitStatusLabels[unit.status]}
        </span>
      </label>
    );
  };

  const displayView: LearningView =
    session?.status === "completed" && view === "practice" ? "summary" : view;

  const startBatch = (
    unitIds = selectedUnitIds,
    requestedQuestionCount = questionCount,
  ) => {
    const readyUnitIds = unitIds.filter((id) =>
      availableUnits.some((unit) => unit.id === id),
    );
    if (
      readyUnitIds.length === 0 ||
      readyUnitIds.length > MAX_SELECTED_SCOPES ||
      !Number.isInteger(requestedQuestionCount) ||
      requestedQuestionCount < 1 ||
      requestedQuestionCount > MAX_QUESTIONS ||
      requestedQuestionCount < readyUnitIds.length ||
      createBatch.isPending
    )
      return;
    createBatch.mutate({
      learning_unit_ids: readyUnitIds,
      question_count: requestedQuestionCount,
    });
  };

  const startSession = () => {
    const requestedQuestionCount =
      currentBatch?.target_question_count ??
      (questionCountIsValid ? questionCount : 1);
    const ids = availableQuestionIds.slice(
      0,
      Math.min(MAX_QUESTIONS, requestedQuestionCount),
    );
    if (!batchReady || ids.length === 0 || createSession.isPending) return;
    createSession.mutate({ question_ids: ids });
  };

  const handleComplete = () => {
    localStorage.removeItem(storageKey(courseId, "session-id"));
    if (sessionId)
      localStorage.removeItem(
        `study-agent.learning:practice-progress:${sessionId}`,
      );
    setSessionId(null);
    setActiveSession(null);
    setView("summary");
    void queryClient.invalidateQueries({
      queryKey: ["learning-summary", courseId],
    });
    void queryClient.invalidateQueries({
      queryKey: ["learning-review-queue", courseId],
    });
    void queryClient.invalidateQueries({
      queryKey: ["learning-units", courseId],
    });
  };

  const handleStartReview = (item: ReviewQueueItem) => {
    const suggestedQuestionCount = defaultQuestionCount([item.learning_unit_id]);
    setSelectedUnitIds([item.learning_unit_id]);
    setQuestionCountInput(String(suggestedQuestionCount));
    setView("overview");
    startBatch([item.learning_unit_id], suggestedQuestionCount);
  };

  if (displayView === "practice") {
    return (
      <div className="page page--learning">
        <PageHeader
          kicker="Learning loop"
          meta="每道题都绑定当前有效资料"
          title="主动练习"
        />
        {sessionQuery.isLoading && activeSession === null ? (
          <section className="loading-state" aria-busy="true">
            <LoaderCircle aria-hidden="true" className="spin" size={20} />
            <span>恢复练习会话</span>
          </section>
        ) : sessionQuery.isError ? (
          <ErrorNotice
            error={sessionQuery.error}
            onRetry={() => void sessionQuery.refetch()}
            title="练习会话不可用"
          />
        ) : session ? (
          <PracticeSession
            aiAvailable={providerAvailable}
            onComplete={handleComplete}
            onExit={() => {
              setActiveSession(null);
              if (sessionId) {
                queryClient.removeQueries({
                  queryKey: ["practice-session", sessionId],
                  exact: true,
                });
              }
              setView("overview");
            }}
            session={session}
          />
        ) : (
          <section className="page-state">
            <CircleAlert aria-hidden="true" size={24} />
            <h3>没有可恢复的练习</h3>
            <button
              className="button"
              onClick={() => setView("overview")}
              type="button"
            >
              返回学习台
            </button>
          </section>
        )}
      </div>
    );
  }

  if (displayView === "summary") {
    return (
      <div className="page page--learning">
        <PageHeader
          kicker="Learning loop"
          meta="按掌握度安排下一次练习"
          title="主动练习"
        />
        {summaryQuery.isLoading ? (
          <section className="loading-state" aria-busy="true">
            <LoaderCircle aria-hidden="true" className="spin" size={20} />
            <span>整理学习结果</span>
          </section>
        ) : summaryQuery.isError ? (
          <ErrorNotice
            error={summaryQuery.error}
            onRetry={() => void summaryQuery.refetch()}
            title="学习结果不可用"
          />
        ) : summaryQuery.data ? (
          <LearningSummary
            onBackToOverview={() => setView("overview")}
            onStartReview={handleStartReview}
            reviewQueue={reviewQueueQuery.data ?? []}
            summary={summaryQuery.data}
          />
        ) : null}
      </div>
    );
  }

  return (
    <div className="page page--learning">
      <PageHeader
        kicker="Learning loop"
        meta="从课程资料中主动回忆，再按薄弱点复习"
        title="主动练习"
      />

      {session?.status === "active" ? (
        <section className="learning-resume" aria-label="未完成的练习">
          <div>
            <span className="learning-resume__icon" aria-hidden="true">
              <Save size={17} />
            </span>
            <div>
              <strong>上次练习已保存</strong>
              <p>
                已完成 {session.questions.filter((question) => question.answered).length} /{" "}
                {session.question_count} 题
              </p>
            </div>
          </div>
          <button
            className="button button--primary"
            onClick={() => setView("practice")}
            type="button"
          >
            <Play aria-hidden="true" size={16} />
            继续上次练习
          </button>
        </section>
      ) : null}

      {unitsQuery.isLoading ? (
        <section className="loading-state" aria-busy="true">
          <LoaderCircle aria-hidden="true" className="spin" size={20} />
          <span>读取学习单元</span>
        </section>
      ) : unitsQuery.isError ? (
        <ErrorNotice
          error={unitsQuery.error}
          onRetry={() => void unitsQuery.refetch()}
          title="学习单元不可用"
        />
      ) : units.length === 0 ? (
        <section className="empty-state">
          <BookOpenCheck aria-hidden="true" size={28} />
          <h3>暂时没有学习单元</h3>
          <p>先完成资料审核和索引，再回来开始练习。</p>
        </section>
      ) : sourceReadyUnits.length === 0 ? (
        <section className="page-state">
          <CircleAlert aria-hidden="true" size={26} />
          <h3>暂无有效来源</h3>
          <p>当前课程的学习依据已失效或还未就绪。</p>
        </section>
      ) : availableUnits.length === 0 ? (
        <section className="page-state">
          <CircleAlert aria-hidden="true" size={26} />
          <h3>暂无可练习主题</h3>
          <p>
            当前资料有来源，但正文不足以稳定生成题目，请补充或重新解析课件。
          </p>
        </section>
      ) : (
        <div className="learning-workbench">
          <section
            className="learning-panel learning-panel--selection"
            aria-labelledby="learning-selection-title"
          >
            <header className="learning-panel__header">
              <div>
                <p className="learning-kicker">选择范围</p>
                <h2 id="learning-selection-title">从哪些内容开始？</h2>
              </div>
              <div className="learning-selection-actions">
                <div className="learning-selection-summary">
                  <span className="learning-count">
                    课程共 {sections.length} 章 · {availableKnowledgeGoalCount}{" "}
                    个知识目标
                    {availableExercisePrototypeCount > 0
                      ? ` · ${availableExercisePrototypeCount} 道原型题`
                      : ""}
                  </span>
                  <span className="learning-count">
                    已选 {selectedUnitIds.length} 个范围 · 覆盖{" "}
                    {selectedCoverageLabel || "0 个知识目标"}
                  </span>
                </div>
                <button
                  className="button button--small"
                  disabled={regenerateUnits.isPending}
                  onClick={() => regenerateUnits.mutate()}
                  type="button"
                >
                  <RefreshCw
                    aria-hidden="true"
                    className={regenerateUnits.isPending ? "spin" : undefined}
                    size={14}
                  />
                  {regenerateUnits.isPending
                    ? "正在重新生成"
                    : "重新生成学习单元"}
                </button>
              </div>
            </header>
            {regenerateUnits.isError ? (
              <ErrorNotice
                error={regenerateUnits.error}
                title="学习单元整理失败"
              />
            ) : null}
            {regenerateUnits.isSuccess ? (
              <p className="learning-inline-status" role="status">
                已按当前资料重新生成章节与知识目标。
              </p>
            ) : null}
            <p className="learning-selection-help">
              章节适合整章练习；知识目标适合精准练习。单批可选 1–10
              个范围，题目数不能少于范围数。
            </p>
            <div className="learning-unit-list">
              {visibleUnitGroups.map((group) => {
                const key =
                  group.section?.id ??
                  `unscoped:${group.concepts[0]?.parent_id ?? "root"}`;
                return (
                  <section className="learning-unit-group" key={key}>
                    {group.section ? (
                      renderLearningUnit(group.section)
                    ) : (
                      <p className="learning-unit-group__label">其他知识目标</p>
                    )}
                    {group.concepts.length > 0 ? (
                      <div className="learning-unit-group__concepts">
                        {group.concepts.map(renderLearningUnit)}
                      </div>
                    ) : null}
                  </section>
                );
              })}
            </div>
            {totalUnitPages > 1 ? (
              <nav aria-label="学习单元分页" className="learning-pagination">
                <button
                  aria-label="上一页"
                  className="icon-button icon-button--small"
                  disabled={currentUnitPage === 1}
                  onClick={() => setUnitPage((page) => Math.max(1, page - 1))}
                  title="上一页"
                  type="button"
                >
                  <ChevronLeft aria-hidden="true" size={16} />
                </button>
                <div className="learning-pagination__pages">
                  {Array.from({ length: totalUnitPages }, (_, index) => {
                    const page = index + 1;
                    return (
                      <button
                        aria-current={
                          page === currentUnitPage ? "page" : undefined
                        }
                        aria-label={`第 ${page} 页`}
                        className={`button button--small${page === currentUnitPage ? " is-active" : ""}`}
                        key={page}
                        onClick={() => setUnitPage(page)}
                        title={`第 ${page} 页`}
                        type="button"
                      >
                        {page}
                      </button>
                    );
                  })}
                </div>
                <button
                  aria-label="下一页"
                  className="icon-button icon-button--small"
                  disabled={currentUnitPage === totalUnitPages}
                  onClick={() =>
                    setUnitPage((page) => Math.min(totalUnitPages, page + 1))
                  }
                  title="下一页"
                  type="button"
                >
                  <ChevronRight aria-hidden="true" size={16} />
                </button>
              </nav>
            ) : null}
            <div className="learning-start-controls">
              <label className="learning-question-count">
                <span>题目数量</span>
                <input
                  aria-label="题目数量"
                  aria-describedby="learning-generation-guidance"
                  aria-invalid={selectionBoundaryError !== null}
                  max={MAX_QUESTIONS}
                  min={1}
                  onChange={(event) => {
                    setQuestionCountTouched(true);
                    setQuestionCountInput(event.target.value);
                  }}
                  type="number"
                  value={questionCountInput}
                />
                <small>可选 1–10 题</small>
              </label>
              <button
                className="button button--primary learning-start-button"
                disabled={
                  capabilitiesLoading ||
                  !providerAvailable ||
                  selectedUnitIds.length === 0 ||
                  selectionBoundaryError !== null ||
                  createBatch.isPending
                }
                onClick={() => startBatch()}
                type="button"
              >
                {createBatch.isPending ? (
                  <LoaderCircle aria-hidden="true" className="spin" size={17} />
                ) : (
                  <Sparkles aria-hidden="true" size={17} />
                )}
                {createBatch.isPending
                  ? hasSelectedExercise
                    ? "正在生成变式题"
                    : "正在创建批次"
                  : hasSelectedExercise
                    ? "开始生成变式题"
                    : "开始生成题目"}
              </button>
            </div>
            <p
              className={
                selectionBoundaryError
                  ? "learning-generation-guidance is-error"
                  : "learning-generation-guidance"
              }
              id="learning-generation-guidance"
              role={selectionBoundaryError ? "alert" : undefined}
            >
              {selectionBoundaryError ??
                (hasSelectedExercise
                  ? "习题资料会生成同知识点变式题；默认每道原型题 1 道。只有参考答案时，会生成可独立求解的答案步骤变式。"
                  : hasSelectedSection
                    ? "整章练习会在章内抽样，建议生成 5–10 道题。"
                    : "精准练习默认每个知识目标 1 道题；需要巩固时可手动增加。")}
            </p>
            {capabilitiesLoading ? (
              <p className="learning-inline-status">
                <LoaderCircle aria-hidden="true" className="spin" size={15} />
                正在检查 Provider
              </p>
            ) : !providerAvailable ? (
              <div
                className="provider-gate learning-provider-gate"
                role="status"
              >
                <CircleAlert aria-hidden="true" size={17} />
                <div>
                  <strong>Provider 不可用</strong>
                  <p>
                    不能生成新题目；如果下方已有有效批次，仍然可以继续练习。
                  </p>
                </div>
              </div>
            ) : null}
            {createBatch.isError ? (
              <ErrorNotice error={createBatch.error} title="题目生成未开始" />
            ) : null}
          </section>

          <aside className="learning-sidebar">
            {summaryQuery.data ? (
              <section
                className="learning-panel learning-panel--compact"
                aria-label="学习概览"
              >
                <header className="learning-panel__header">
                  <div>
                    <p className="learning-kicker">学习概览</p>
                    <h2>保持复习节奏</h2>
                  </div>
                  <BookOpenCheck aria-hidden="true" size={20} />
                </header>
                <dl className="learning-facts">
                  <div>
                    <dt>累计正确率</dt>
                    <dd>{Math.round(summaryQuery.data.accuracy * 100)}%</dd>
                  </div>
                  <div>
                    <dt>已完成题目</dt>
                    <dd>{summaryQuery.data.total_questions}</dd>
                  </div>
                  <div>
                    <dt>待复习</dt>
                    <dd>{summaryQuery.data.due_review_count}</dd>
                  </div>
                </dl>
                <p className="learning-next-action">
                  {summaryQuery.data.next_action}
                </p>
              </section>
            ) : null}

            <section
              className="learning-panel learning-panel--compact"
              aria-labelledby="learning-batch-title"
            >
              <header className="learning-panel__header">
                <div>
                  <p className="learning-kicker">题目批次</p>
                  <h2 id="learning-batch-title">练习准备</h2>
                </div>
                {currentBatch ? (
                  <span className="learning-status">
                    {batchStatusLabels[currentBatch.status]}
                  </span>
                ) : null}
              </header>
              {!currentBatch ? (
                <p className="muted">选择学习单元后生成一组有依据的题目。</p>
              ) : (
                <>
                  <p className="learning-batch-copy">
                    {currentBatch.status === "running" ||
                    currentBatch.status === "queued"
                      ? `正在处理 ${currentBatch.completed_items} / ${currentBatch.total_items} 道题`
                      : currentBatch.status === "failed"
                        ? "批次未能生成有效题目。"
                        : `已生成 ${availableQuestionIds.length} / ${currentBatch.target_question_count} 道有效题目。`}
                  </p>
                  {currentBatch.failure_code ? (
                    <p className="learning-batch-error">
                      部分题目未通过校验：
                      {formatBatchFailure(currentBatch.failure_code)}
                    </p>
                  ) : null}
                  <button
                    className="button learning-session-button"
                    disabled={
                      !batchReady ||
                      availableQuestionIds.length === 0 ||
                      createSession.isPending
                    }
                    onClick={startSession}
                    type="button"
                  >
                    {createSession.isPending ? (
                      <LoaderCircle
                        aria-hidden="true"
                        className="spin"
                        size={16}
                      />
                    ) : (
                      <Play aria-hidden="true" size={16} />
                    )}
                    {createSession.isPending ? "正在打开练习" : "开始作答"}
                    <ChevronRight aria-hidden="true" size={16} />
                  </button>
                  {createSession.isError ? (
                    <ErrorNotice
                      error={createSession.error}
                      title="练习会话未创建"
                    />
                  ) : null}
                </>
              )}
            </section>

            {reviewQueueQuery.data?.length ? (
              <section
                className="learning-panel learning-panel--compact"
                aria-labelledby="learning-queue-title"
              >
                <header className="learning-panel__header">
                  <div>
                    <p className="learning-kicker">复习队列</p>
                    <h2 id="learning-queue-title">下一项建议</h2>
                  </div>
                  <span className="learning-count">
                    {reviewQueueQuery.data.length}
                  </span>
                </header>
                <ul className="learning-queue-preview">
                  {reviewQueueQuery.data.slice(0, 3).map((item) => (
                    <li key={item.learning_unit_id}>
                      <span>{item.label}</span>
                      <small>
                        {item.source_status === "valid"
                          ? "来源有效"
                          : "来源不可用"}
                      </small>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </aside>
        </div>
      )}
    </div>
  );
}
