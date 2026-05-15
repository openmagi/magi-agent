import { taxAssistantReducer } from "./session-machine";
import type {
  TaxAssistantError,
  TaxAssistantSession,
  TaxAutomationEvent,
  TaxInputPlanRow,
} from "./types";

function event(
  id: string,
  label: string,
  at: string,
  status: TaxAutomationEvent["status"],
): TaxAutomationEvent {
  return { id, label, at, status };
}

export function buildMockInputPlan(): {
  rows: TaxInputPlanRow[];
  events: TaxAutomationEvent[];
} {
  return {
    rows: [
      {
        id: "income-business-prefill",
        label: "사업소득 수입금액",
        currentValue: "홈택스 모두채움",
        proposedValue: "홈택스 제공값 유지",
        source: "hometax_prefill",
        confidence: "high",
        approved: false,
      },
      {
        id: "expense-simple-rate",
        label: "단순경비율 필요경비",
        currentValue: "미확인",
        proposedValue: "업로드 자료 기준 자동 계산",
        source: "uploaded_evidence",
        confidence: "medium",
        approved: false,
      },
      {
        id: "deduction-dependent",
        label: "부양가족 공제",
        currentValue: "홈택스 표시값",
        proposedValue: "검토 후 유지",
        source: "manual_user_input",
        confidence: "needs_review",
        approved: false,
        riskFlag: "부양가족 공제는 중복 공제 여부 확인 필요",
      },
    ],
    events: [
      event("read-screen", "홈택스 신고서 화면 읽기", "mock", "done"),
      event("extract-evidence", "업로드 자료에서 입력 후보 추출", "mock", "done"),
      event("build-plan", "자동입력 계획 생성", "mock", "done"),
    ],
  };
}

export function runMockApprovedInput(
  session: TaxAssistantSession,
  nowIso: string,
): { ok: true; session: TaxAssistantSession } | { ok: false; error: TaxAssistantError } {
  const approvedRows = session.inputPlan.filter((row) => row.approved);
  if (approvedRows.length === 0) {
    return {
      ok: false,
      error: {
        code: "approval_required",
        message: "Approve at least one proposed value before Open Magi changes Hometax fields.",
      },
    };
  }

  const events = [
    event("open-form", "신고서 입력 화면 이동", nowIso, "done"),
    event("fill-approved", `${approvedRows.length}개 승인 필드 자동입력`, nowIso, "done"),
    event("verify-values", "입력 전후 값 대조", nowIso, "done"),
  ];

  return {
    ok: true,
    session: taxAssistantReducer(session, {
      type: "execute_approved_input",
      events,
    }),
  };
}

export function runMockMiddleSave(
  session: TaxAssistantSession,
  nowIso: string,
): TaxAssistantSession {
  return taxAssistantReducer(session, {
    type: "confirm_middle_save",
    savedAt: nowIso,
    confirmationText: "홈택스 중간저장 완료 화면을 확인했습니다.",
    events: [
      ...session.automationEvents,
      event("click-middle-save", "중간저장 클릭", nowIso, "done"),
      event("verify-middle-save", "중간저장 완료 화면 확인", nowIso, "done"),
    ],
  });
}
