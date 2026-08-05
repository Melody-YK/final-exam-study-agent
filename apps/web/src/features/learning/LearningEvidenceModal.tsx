import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Eye,
  FileText,
  LoaderCircle,
  Pencil,
  RotateCcw,
  Save,
  ScanSearch,
  ShieldCheck,
} from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";

import { studyApi } from "../../api/client";
import type {
  LearningUnit,
  LearningUnitEvidenceItem,
  LearningUnitEvidenceRole,
  VisionEvidenceReview,
  SourcePreview,
} from "../../api/types";
import { ErrorNotice } from "../../components/ui/ErrorNotice";
import { Modal } from "../../components/ui/Modal";
import { SourceViewer } from "../source-viewer/SourceViewer";

interface LearningEvidenceModalProps {
  courseId: string;
  onClose: () => void;
  open: boolean;
  unit: LearningUnit | null;
}

const roleLabels: Record<LearningUnitEvidenceRole, string> = {
  complete_prototype: "完整题目与解答",
  reference_solution: "参考解答",
  additional_context: "补充上下文",
};

const statusLabels: Record<
  LearningUnitEvidenceItem["practice_status"],
  string
> = {
  ready: "高置信度",
  low_confidence: "低置信度",
  insufficient_evidence: "资料不足",
  stale: "来源失效",
};

function locatorLabel(item: LearningUnitEvidenceItem): string {
  if (item.locator.kind === "page") return `第 ${item.locator.ordinal} 页`;
  if (item.locator.kind === "slide")
    return `第 ${item.locator.ordinal} 张幻灯片`;
  return `第 ${item.locator.ordinal} 节`;
}

function questionTypeLabel(
  value: VisionEvidenceReview["question_type"],
): string {
  if (value === "single_choice") return "单项选择题";
  if (value === "true_false") return "判断题";
  if (value === "short_answer") return "简答题";
  if (value === "calculation") return "计算题";
  return "未判断";
}

function visionReviewSupplementText(review: VisionEvidenceReview): string {
  const sections = [review.extracted_text];
  const conditions = review.conditions ?? [];
  const uncertainSpans = review.uncertain_spans ?? [];
  if (conditions.length) {
    sections.push(
      `已知条件：\n${conditions.map((item) => `- ${item}`).join("\n")}`,
    );
  }
  if (review.reference_answer)
    sections.push(`参考答案或解题过程：\n${review.reference_answer}`);
  if (uncertainSpans.length) {
    sections.push(
      `待确认内容：\n${uncertainSpans.map((item) => `- ${item}`).join("\n")}`,
    );
  }
  return sections.join("\n\n").slice(0, 20_000);
}

export function LearningEvidenceModal({
  courseId,
  onClose,
  open,
  unit,
}: LearningEvidenceModalProps) {
  const queryClient = useQueryClient();
  const [sourceId, setSourceId] = useState("");
  const [role, setRole] =
    useState<LearningUnitEvidenceRole>("complete_prototype");
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<SourcePreview | null>(null);
  const [displayedEvidence, setDisplayedEvidence] =
    useState<LearningUnitEvidenceItem | null>(null);
  const [visionReview, setVisionReview] = useState<VisionEvidenceReview | null>(
    null,
  );
  const [confirmingRevokeId, setConfirmingRevokeId] = useState<string | null>(
    null,
  );
  const unitId = unit?.id ?? "";
  const evidenceKey = ["learning-unit-evidence", courseId, unitId] as const;
  const evidenceQuery = useQuery({
    queryKey: evidenceKey,
    queryFn: () => studyApi.listLearningUnitEvidence(courseId, unitId),
    enabled: open && unit !== null,
    retry: false,
  });
  const parsedItems = useMemo(
    () => (evidenceQuery.data ?? []).filter((item) => item.origin === "parsed"),
    [evidenceQuery.data],
  );

  const activeSourceId = parsedItems.some((item) => item.source_id === sourceId)
    ? sourceId
    : (parsedItems[0]?.source_id ?? "");

  const invalidateEvidence = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: evidenceKey }),
      queryClient.invalidateQueries({ queryKey: ["learning-units", courseId] }),
      queryClient.invalidateQueries({
        queryKey: ["learning-summary", courseId],
      }),
      queryClient.invalidateQueries({
        queryKey: ["learning-review-queue", courseId],
      }),
    ]);
  };
  const saveSupplement = useMutation({
    mutationFn: () =>
      studyApi.createLearningUnitEvidenceSupplement(courseId, unitId, {
        source_id: activeSourceId,
        role,
        text: text.trim(),
      }),
    onSuccess: () => {
      setText("");
      setRole("complete_prototype");
      void invalidateEvidence();
    },
  });
  const revokeSupplement = useMutation({
    mutationFn: (supplementId: string) =>
      studyApi.revokeLearningUnitEvidenceSupplement(
        courseId,
        unitId,
        supplementId,
      ),
    onSuccess: () => {
      setConfirmingRevokeId(null);
      void invalidateEvidence();
    },
  });
  const previewSource = useMutation({
    mutationFn: (item: LearningUnitEvidenceItem) =>
      studyApi.getKnowledgeGraphSourcePreview(
        courseId,
        item.revision_id,
        item.chunk_id,
      ),
    onSuccess: setPreview,
  });
  const reviewWithVision = useMutation({
    mutationFn: (item: LearningUnitEvidenceItem) =>
      studyApi.reviewLearningUnitEvidenceWithVision(
        courseId,
        unitId,
        item.source_id,
      ),
    onSuccess: (result) => {
      setSourceId(result.source_id);
      setVisionReview(result);
    },
  });
  const close = () => {
    setText("");
    setRole("complete_prototype");
    setConfirmingRevokeId(null);
    setPreview(null);
    setDisplayedEvidence(null);
    setVisionReview(null);
    saveSupplement.reset();
    revokeSupplement.reset();
    previewSource.reset();
    reviewWithVision.reset();
    onClose();
  };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!activeSourceId || !text.trim() || saveSupplement.isPending) return;
    saveSupplement.mutate();
  };
  const editSupplement = (item: LearningUnitEvidenceItem) => {
    if (!item.role) return;
    setSourceId(item.source_id);
    setRole(item.role);
    setText(item.text);
  };
  const minimumLength = role === "complete_prototype" ? 20 : 1;
  const canSave = activeSourceId !== "" && text.trim().length >= minimumLength;
  const mutationError = saveSupplement.error ?? revokeSupplement.error;

  if (preview) {
    return <SourceViewer onClose={() => setPreview(null)} source={preview} />;
  }

  if (displayedEvidence) {
    return (
      <Modal
        description={`${displayedEvidence.document_name} · ${locatorLabel(displayedEvidence)}`}
        onClose={() => setDisplayedEvidence(null)}
        open={open && unit !== null}
        size="wide"
        title="采用的证据"
      >
        <article className="learning-evidence-display">
          <header>
            <div>
              <p className="learning-kicker">生成主证据</p>
              <h3>
                <FileText aria-hidden="true" size={17} />
                {displayedEvidence.role
                  ? roleLabels[displayedEvidence.role]
                  : "用户补充"}
              </h3>
            </div>
            <span>{statusLabels[displayedEvidence.practice_status]}</span>
          </header>
          <pre>
            {displayedEvidence.text.trim() || "当前证据没有可展示的正文内容。"}
          </pre>
          <footer>
            <button
              className="button button--small"
              disabled={previewSource.isPending}
              onClick={() => previewSource.mutate(displayedEvidence)}
              type="button"
            >
              {previewSource.isPending ? (
                <LoaderCircle aria-hidden="true" className="spin" size={14} />
              ) : (
                <Eye aria-hidden="true" size={14} />
              )}
              {previewSource.isPending ? "正在加载原页" : "查看原页"}
            </button>
          </footer>
        </article>
      </Modal>
    );
  }

  return (
    <Modal
      description={unit ? `${unit.label} · 原解析与用户补充` : undefined}
      onClose={close}
      open={open && unit !== null}
      size="wide"
      title="学习单元证据"
    >
      <div className="learning-evidence-manager">
        {evidenceQuery.isLoading ? (
          <div aria-busy="true" className="learning-evidence-manager__loading">
            <LoaderCircle aria-hidden="true" className="spin" size={19} />
            正在读取证据
          </div>
        ) : evidenceQuery.isError ? (
          <ErrorNotice
            error={evidenceQuery.error}
            onRetry={() => void evidenceQuery.refetch()}
            title="证据读取失败"
          />
        ) : evidenceQuery.data?.length ? (
          <ol className="learning-unit-evidence-list">
            {evidenceQuery.data.map((item) => {
              const isSupplement = item.origin === "user_supplied";
              const isRevoking =
                revokeSupplement.isPending &&
                revokeSupplement.variables === item.supplement_id;
              return (
                <li
                  className={item.is_primary ? "is-primary" : undefined}
                  key={`${item.origin}-${item.id}`}
                >
                  <header>
                    <div>
                      {isSupplement ? (
                        <ShieldCheck aria-hidden="true" size={16} />
                      ) : (
                        <FileText aria-hidden="true" size={16} />
                      )}
                      <strong>
                        {isSupplement && item.role
                          ? roleLabels[item.role]
                          : "解析原文"}
                      </strong>
                      {item.is_primary ? <span>生成主证据</span> : null}
                    </div>
                    <div className="learning-unit-evidence-list__meta">
                      <small>
                        {statusLabels[item.practice_status]}
                        {item.confidence_note
                          ? ` · ${item.confidence_note}`
                          : ""}
                      </small>
                      {isSupplement && item.is_primary ? (
                        <button
                          className="button button--small"
                          onClick={() => setDisplayedEvidence(item)}
                          type="button"
                        >
                          <Eye aria-hidden="true" size={14} />
                          查看证据
                        </button>
                      ) : null}
                    </div>
                  </header>
                  <p className="learning-unit-evidence-list__source">
                    {item.document_name} · {locatorLabel(item)}
                  </p>
                  <pre>{item.text}</pre>
                  <footer>
                    <button
                      className="button button--small"
                      disabled={previewSource.isPending}
                      onClick={() => previewSource.mutate(item)}
                      type="button"
                    >
                      {previewSource.isPending &&
                      previewSource.variables?.id === item.id ? (
                        <LoaderCircle
                          aria-hidden="true"
                          className="spin"
                          size={14}
                        />
                      ) : (
                        <Eye aria-hidden="true" size={14} />
                      )}
                      查看原页
                    </button>
                    {isSupplement && item.supplement_id ? (
                      <>
                        <button
                          className="button button--small"
                          onClick={() => editSupplement(item)}
                          type="button"
                        >
                          <Pencil aria-hidden="true" size={14} />
                          编辑
                        </button>
                        {confirmingRevokeId === item.supplement_id ? (
                          <>
                            <button
                              className="button button--danger button--small"
                              disabled={isRevoking}
                              onClick={() =>
                                revokeSupplement.mutate(item.supplement_id!)
                              }
                              type="button"
                            >
                              {isRevoking ? (
                                <LoaderCircle
                                  aria-hidden="true"
                                  className="spin"
                                  size={14}
                                />
                              ) : (
                                <RotateCcw aria-hidden="true" size={14} />
                              )}
                              确认撤销
                            </button>
                            <button
                              className="button button--small"
                              disabled={isRevoking}
                              onClick={() => setConfirmingRevokeId(null)}
                              type="button"
                            >
                              取消
                            </button>
                          </>
                        ) : (
                          <button
                            className="button button--small"
                            onClick={() =>
                              setConfirmingRevokeId(item.supplement_id ?? null)
                            }
                            type="button"
                          >
                            <RotateCcw aria-hidden="true" size={14} />
                            撤销补充
                          </button>
                        )}
                      </>
                    ) : null}
                    {!isSupplement &&
                    item.practice_status === "low_confidence" ? (
                      <button
                        className="button button--small"
                        disabled={reviewWithVision.isPending}
                        onClick={() => {
                          setVisionReview(null);
                          reviewWithVision.mutate(item);
                        }}
                        type="button"
                      >
                        {reviewWithVision.isPending &&
                        reviewWithVision.variables?.id === item.id ? (
                          <LoaderCircle
                            aria-hidden="true"
                            className="spin"
                            size={14}
                          />
                        ) : (
                          <ScanSearch aria-hidden="true" size={14} />
                        )}
                        {reviewWithVision.isPending &&
                        reviewWithVision.variables?.id === item.id
                          ? "正在复核"
                          : "多模态复核"}
                      </button>
                    ) : null}
                  </footer>
                </li>
              );
            })}
          </ol>
        ) : (
          <p className="learning-evidence-manager__empty">
            当前没有可读取的证据片段。
          </p>
        )}

        {previewSource.isError ? (
          <ErrorNotice error={previewSource.error} title="原页预览失败" />
        ) : null}
        {reviewWithVision.isError ? (
          <ErrorNotice error={reviewWithVision.error} title="多模态复核失败" />
        ) : null}
        {visionReview ? (
          <section className="learning-evidence-vision-result">
            <header>
              <div>
                <p className="learning-kicker">页面复核</p>
                <h3>多模态识别结果</h3>
              </div>
              <span>
                {questionTypeLabel(visionReview.question_type)} ·{" "}
                {visionReview.confidence} ·{" "}
                {visionReview.evidence_complete ? "证据完整" : "证据不完整"}
              </span>
            </header>
            <p>{visionReview.reason}</p>
            <pre>{visionReview.extracted_text}</pre>
            {visionReview.uncertain_spans?.length ? (
              <p className="learning-evidence-vision-result__uncertain">
                待确认：{visionReview.uncertain_spans.join("；")}
              </p>
            ) : null}
            <button
              className="button button--primary button--small"
              onClick={() => {
                setSourceId(visionReview.source_id);
                setRole("complete_prototype");
                setText(visionReviewSupplementText(visionReview));
              }}
              type="button"
            >
              <Pencil aria-hidden="true" size={14} />
              填入补充表单
            </button>
          </section>
        ) : null}
        {mutationError ? (
          <ErrorNotice error={mutationError} title="证据更新失败" />
        ) : null}

        <form className="learning-evidence-supplement" onSubmit={submit}>
          <header>
            <div>
              <p className="learning-kicker">用户补充</p>
              <h3>修正生成依据</h3>
            </div>
            <span>{text.trim().length} / 20000</span>
          </header>
          <div className="learning-evidence-supplement__fields">
            <label>
              关联原文
              <select
                disabled={!parsedItems.length}
                onChange={(event) => setSourceId(event.target.value)}
                value={activeSourceId}
              >
                {parsedItems.map((item) => (
                  <option key={item.source_id} value={item.source_id}>
                    {item.document_name} · {locatorLabel(item)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              补充类型
              <select
                onChange={(event) =>
                  setRole(event.target.value as LearningUnitEvidenceRole)
                }
                value={role}
              >
                {(Object.keys(roleLabels) as LearningUnitEvidenceRole[]).map(
                  (value) => (
                    <option key={value} value={value}>
                      {roleLabels[value]}
                    </option>
                  ),
                )}
              </select>
            </label>
          </div>
          <label>
            补充内容
            <textarea
              maxLength={20_000}
              onChange={(event) => setText(event.target.value)}
              placeholder="完整题干、已知条件、参考答案与解题过程"
              rows={8}
              value={text}
            />
          </label>
          <button
            className="button button--primary"
            disabled={!canSave || saveSupplement.isPending}
            type="submit"
          >
            {saveSupplement.isPending ? (
              <LoaderCircle aria-hidden="true" className="spin" size={16} />
            ) : (
              <Save aria-hidden="true" size={16} />
            )}
            {saveSupplement.isPending ? "正在保存" : "保存为主证据"}
          </button>
        </form>
      </div>
    </Modal>
  );
}
