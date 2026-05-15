import { describe, expect, it } from "vitest";
import { createGuestTaxSession, taxAssistantReducer } from "./session-machine";
import {
  buildMockInputPlan,
  runMockApprovedInput,
  runMockMiddleSave,
} from "./mock-runner";

describe("mock tax automation runner", () => {
  it("generates a deterministic input plan with evidence and review flags", () => {
    const plan = buildMockInputPlan();

    expect(plan.rows).toEqual([
      expect.objectContaining({
        id: "income-business-prefill",
        label: "사업소득 수입금액",
        currentValue: "홈택스 모두채움",
        proposedValue: "홈택스 제공값 유지",
        source: "hometax_prefill",
        confidence: "high",
        approved: false,
      }),
      expect.objectContaining({
        id: "expense-simple-rate",
        label: "단순경비율 필요경비",
        source: "uploaded_evidence",
        confidence: "medium",
        approved: false,
      }),
      expect.objectContaining({
        id: "deduction-dependent",
        confidence: "needs_review",
        riskFlag: "부양가족 공제는 중복 공제 여부 확인 필요",
      }),
    ]);
    expect(plan.events.map((event) => event.label)).toContain("홈택스 신고서 화면 읽기");
  });

  it("refuses auto-input until at least one row is approved", () => {
    const session = taxAssistantReducer(
      createGuestTaxSession({
        sessionId: "tax_guest_runner",
        nowIso: "2026-05-01T09:00:00.000Z",
      }),
      { type: "read_filing", ...buildMockInputPlan() },
    );

    const result = runMockApprovedInput(session, "2026-05-01T09:05:00.000Z");

    expect(result.ok).toBe(false);
    expect(result.error).toEqual({
      code: "approval_required",
      message: "Approve at least one proposed value before Open Magi changes Hometax fields.",
    });
  });

  it("simulates approved input and verified middle-save", () => {
    const plan = buildMockInputPlan();
    const session = taxAssistantReducer(
      taxAssistantReducer(
        createGuestTaxSession({
          sessionId: "tax_guest_runner_2",
          nowIso: "2026-05-01T09:00:00.000Z",
        }),
        { type: "read_filing", ...plan },
      ),
      { type: "approve_rows", rowIds: plan.rows.map((row) => row.id) },
    );

    const input = runMockApprovedInput(session, "2026-05-01T09:06:00.000Z");
    expect(input.ok).toBe(true);
    if (!input.ok) throw new Error("expected approved input");

    const saved = runMockMiddleSave(input.session, "2026-05-01T09:07:00.000Z");

    expect(saved.step).toBe("middle_saved");
    expect(saved.middleSave).toEqual({
      confirmed: true,
      savedAt: "2026-05-01T09:07:00.000Z",
      confirmationText: "홈택스 중간저장 완료 화면을 확인했습니다.",
    });
  });
});
