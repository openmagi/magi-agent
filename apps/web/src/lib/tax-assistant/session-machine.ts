import type {
  TaxAssistantAction,
  TaxAssistantSession,
} from "./types";

const DEFAULT_HARD_STOPS = [
  "credential_storage",
  "auth_bypass",
  "final_submit",
  "tax_payment",
  "refund_account_finalization",
] as const;

const FINAL_ACTION_MESSAGE =
  "Open Magi stops before final submission, payment, and refund-account finalization.";

function addHours(iso: string, hours: number): string {
  const date = new Date(iso);
  date.setHours(date.getHours() + hours);
  return date.toISOString();
}

export function createGuestTaxSession({
  sessionId,
  nowIso,
}: {
  sessionId: string;
  nowIso: string;
}): TaxAssistantSession {
  return {
    id: sessionId,
    mode: "guest",
    step: "start",
    filingYear: 2025,
    createdAt: nowIso,
    expiresAt: addHours(nowIso, 24),
    paymentGate: "after_middle_save_report_export",
    finalActionBlocked: true,
    hardStops: [...DEFAULT_HARD_STOPS],
    evidenceFiles: [],
    inputPlan: [],
    automationEvents: [],
  };
}

export function taxAssistantReducer(
  session: TaxAssistantSession,
  action: TaxAssistantAction,
): TaxAssistantSession {
  if (action.type === "attempt_final_action") {
    return {
      ...session,
      step: "blocked",
      error: {
        code: "final_action_blocked",
        message: FINAL_ACTION_MESSAGE,
      },
    };
  }

  switch (action.type) {
    case "register_evidence":
      return {
        ...session,
        step: "evidence",
        evidenceFiles: [...session.evidenceFiles, action.file],
        error: undefined,
      };
    case "connect_hometax":
      return { ...session, step: "hometax_login", error: undefined };
    case "read_filing":
      return {
        ...session,
        step: "input_plan",
        inputPlan: action.rows,
        automationEvents: action.events,
        error: undefined,
      };
    case "approve_rows": {
      const approved = new Set(action.rowIds);
      return {
        ...session,
        inputPlan: session.inputPlan.map((row) => ({
          ...row,
          approved: approved.has(row.id) || row.approved,
        })),
        error: undefined,
      };
    }
    case "execute_approved_input":
      return {
        ...session,
        step: "approved_input",
        automationEvents: action.events,
        error: undefined,
      };
    case "confirm_middle_save":
      return {
        ...session,
        step: "middle_saved",
        automationEvents: action.events,
        middleSave: {
          confirmed: true,
          savedAt: action.savedAt,
          confirmationText: action.confirmationText,
        },
        error: undefined,
      };
    case "open_report_gate":
      return { ...session, step: "report_gate", error: undefined };
  }
}
