import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { studyApi } from "../../api/client";
import type { LearningUnit, LearningUnitEvidenceItem } from "../../api/types";
import { sourcePreview } from "../../test/fixtures";
import { renderInWorkspace } from "../../test/render";
import { LearningEvidenceModal } from "./LearningEvidenceModal";

const unit: LearningUnit = {
  id: "unit-1",
  course_id: "course-1",
  canonical_key: "section:process",
  label: "进程管理",
  kind: "section",
  parent_id: null,
  status: "available",
  practice_mode: "knowledge_recall",
  practice_status: "insufficient_evidence",
  practice_confidence_note: "有效正文不足。",
  evidence_chunk_count: 1,
  evidence_char_count: 18,
  mastery_level: "new",
  next_review_at: null,
  sources: [],
};

const parsed: LearningUnitEvidenceItem = {
  id: "source-1",
  unit_id: "unit-1",
  source_id: "source-1",
  supplement_id: null,
  origin: "parsed",
  role: null,
  document_id: "document-1",
  document_name: "process.pdf",
  revision_id: "revision-1",
  chunk_id: "chunk-1",
  content_sha256: "a".repeat(64),
  locator: { kind: "page", ordinal: 3 },
  text: "OCR 损坏的原始片段",
  is_primary: true,
  practice_status: "insufficient_evidence",
  confidence_note: "有效正文不足。",
  created_at: "2026-08-05T10:00:00Z",
};

function supplement(text: string): LearningUnitEvidenceItem {
  return {
    ...parsed,
    id: "supplement-1",
    supplement_id: "supplement-1",
    origin: "user_supplied",
    role: "complete_prototype",
    content_sha256: "b".repeat(64),
    text,
    is_primary: true,
    practice_status: "ready",
    confidence_note: "已采用用户补充的完整原型。",
    created_at: "2026-08-05T10:01:00Z",
  };
}

describe("LearningEvidenceModal", () => {
  it("shows the parsed source, previews its page, and saves a primary supplement", async () => {
    let currentEvidence: LearningUnitEvidenceItem[] = [parsed];
    const listEvidence = vi
      .spyOn(studyApi, "listLearningUnitEvidence")
      .mockImplementation(async () => currentEvidence);
    const createSupplement = vi
      .spyOn(studyApi, "createLearningUnitEvidenceSupplement")
      .mockImplementation(async (_courseId, _unitId, input) => {
        const created = supplement(input.text);
        currentEvidence = [{ ...parsed, is_primary: false }, created];
        return created;
      });
    const preview = vi
      .spyOn(studyApi, "getKnowledgeGraphSourcePreview")
      .mockResolvedValue(sourcePreview({ document_name: "process.pdf" }));

    const { user } = renderInWorkspace(
      <LearningEvidenceModal
        courseId="course-1"
        onClose={() => undefined}
        open
        unit={unit}
      />,
    );

    expect(await screen.findByText("OCR 损坏的原始片段")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看原页" }));
    expect(preview).toHaveBeenCalledWith("course-1", "revision-1", "chunk-1");
    expect(
      await screen.findByRole("heading", { name: "来源" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "关闭" }));

    const input = screen.getByLabelText("补充内容");
    await user.type(
      input,
      "完整题干：某进程使用 2 个资源单元，系统共有 8 个资源单元，求资源利用率并写出计算步骤。答案为 25%。",
    );
    await user.click(screen.getByRole("button", { name: "保存为主证据" }));

    await waitFor(() => {
      expect(createSupplement).toHaveBeenCalledWith(
        "course-1",
        "unit-1",
        expect.objectContaining({
          source_id: "source-1",
          role: "complete_prototype",
        }),
      );
    });
    expect(listEvidence).toHaveBeenCalledTimes(2);
    expect(screen.getAllByText("完整题目与解答").length).toBeGreaterThan(0);
    expect(screen.getByText("生成主证据")).toBeInTheDocument();
  });

  it("can revoke an active user supplement", async () => {
    const active = supplement(
      "用户确认的完整题目与答案，包含全部求解条件和参考过程。",
    );
    vi.spyOn(studyApi, "listLearningUnitEvidence").mockResolvedValue([
      parsed,
      active,
    ]);
    const revoke = vi
      .spyOn(studyApi, "revokeLearningUnitEvidenceSupplement")
      .mockResolvedValue(undefined);

    const { user } = renderInWorkspace(
      <LearningEvidenceModal
        courseId="course-1"
        onClose={() => undefined}
        open
        unit={unit}
      />,
    );

    expect(
      await screen.findByText(
        "用户确认的完整题目与答案，包含全部求解条件和参考过程。",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "撤销补充" }));
    await user.click(screen.getByRole("button", { name: "确认撤销" }));
    await waitFor(() => {
      expect(revoke).toHaveBeenCalledWith("course-1", "unit-1", "supplement-1");
    });
  });

  it("opens the adopted evidence from its primary card", async () => {
    const active = supplement(
      "完整题干：某进程使用 2 个资源单元，系统共有 8 个资源单元。参考答案：资源利用率为 25%。",
    );
    vi.spyOn(studyApi, "listLearningUnitEvidence").mockResolvedValue([
      parsed,
      active,
    ]);

    const { user } = renderInWorkspace(
      <LearningEvidenceModal
        courseId="course-1"
        onClose={() => undefined}
        open
        unit={unit}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "查看证据" }));
    const viewer = screen.getByRole("dialog", { name: "采用的证据" });
    expect(viewer).toContainElement(
      screen.getByText(
        "完整题干：某进程使用 2 个资源单元，系统共有 8 个资源单元。参考答案：资源利用率为 25%。",
      ),
    );
    expect(viewer).toContainElement(screen.getByText("生成主证据"));

    await user.click(within(viewer).getByRole("button", { name: "关闭" }));
    expect(
      screen.getByRole("dialog", { name: "学习单元证据" }),
    ).toBeInTheDocument();
  });

  it("reviews low-confidence evidence with vision before filling the supplement form", async () => {
    const lowConfidence: LearningUnitEvidenceItem = {
      ...parsed,
      practice_status: "low_confidence",
      confidence_note: "检测到未展开的表格或页面结构标记。",
      text: "<table><tr><td>损坏的表格内容</td></tr></table>",
    };
    vi.spyOn(studyApi, "listLearningUnitEvidence").mockResolvedValue([
      lowConfidence,
    ]);
    const review = vi
      .spyOn(studyApi, "reviewLearningUnitEvidenceWithVision")
      .mockResolvedValue({
        source_id: "source-1",
        document_name: "process.pdf",
        locator: { kind: "page", ordinal: 3 },
        extracted_text:
          "某进程使用 2 个资源单元，系统共有 8 个资源单元，求资源利用率。",
        question_type: "calculation",
        conditions: ["已使用 2 个资源单元", "系统共有 8 个资源单元"],
        reference_answer: "资源利用率为 25%。",
        uncertain_spans: [],
        evidence_complete: true,
        confidence: "high",
        reason: "页面中的题干、条件和答案均可辨认。",
        model: "vision-model",
      });

    const { user } = renderInWorkspace(
      <LearningEvidenceModal
        courseId="course-1"
        onClose={() => undefined}
        open
        unit={unit}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "多模态复核" }));
    await waitFor(() => {
      expect(review).toHaveBeenCalledWith("course-1", "unit-1", "source-1");
    });
    expect(
      await screen.findByText("页面中的题干、条件和答案均可辨认。"),
    ).toBeInTheDocument();
    expect(screen.getByText(/计算题 · high · 证据完整/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "填入补充表单" }));
    expect(screen.getByLabelText("补充内容")).toHaveValue(
      "某进程使用 2 个资源单元，系统共有 8 个资源单元，求资源利用率。\n\n已知条件：\n- 已使用 2 个资源单元\n- 系统共有 8 个资源单元\n\n参考答案或解题过程：\n资源利用率为 25%。",
    );
  });
});
