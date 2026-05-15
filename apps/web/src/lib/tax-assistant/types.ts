export type TaxAssistantStep =
  | "start"
  | "evidence"
  | "hometax_login"
  | "filing_read"
  | "input_plan"
  | "approved_input"
  | "middle_saved"
  | "report_gate"
  | "blocked";

export type TaxAssistantMode = "guest" | "attached_user";

export type TaxHardStop =
  | "credential_storage"
  | "auth_bypass"
  | "final_submit"
  | "tax_payment"
  | "refund_account_finalization";

export type FinalTaxAction =
  | "final_submit"
  | "tax_payment"
  | "refund_account_finalization";

export type TaxAssistantErrorCode =
  | "login_required"
  | "challenge_required"
  | "unsupported_filing_state"
  | "evidence_missing"
  | "approval_required"
  | "field_mismatch"
  | "save_failed"
  | "final_action_blocked"
  | "browser_unavailable"
  | "session_expired";

export interface TaxAssistantError {
  code: TaxAssistantErrorCode;
  message: string;
}

export interface TaxEvidenceFile {
  id: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  status: "registered" | "extracted" | "rejected";
}

export interface TaxInputPlanRow {
  id: string;
  label: string;
  currentValue: string;
  proposedValue: string;
  source: "hometax_prefill" | "uploaded_evidence" | "manual_user_input";
  confidence: "high" | "medium" | "needs_review";
  approved: boolean;
  riskFlag?: string;
}

export interface TaxAutomationEvent {
  id: string;
  at: string;
  label: string;
  status: "pending" | "running" | "done" | "blocked";
}

export interface TaxAssistantSession {
  id: string;
  mode: TaxAssistantMode;
  step: TaxAssistantStep;
  filingYear: number;
  createdAt: string;
  expiresAt: string;
  paymentGate: "after_middle_save_report_export";
  finalActionBlocked: true;
  hardStops: TaxHardStop[];
  evidenceFiles: TaxEvidenceFile[];
  inputPlan: TaxInputPlanRow[];
  automationEvents: TaxAutomationEvent[];
  middleSave?: {
    confirmed: boolean;
    savedAt: string;
    confirmationText: string;
  };
  error?: TaxAssistantError;
}

export type TaxAssistantAction =
  | { type: "register_evidence"; file: TaxEvidenceFile }
  | { type: "connect_hometax" }
  | { type: "read_filing"; rows: TaxInputPlanRow[]; events: TaxAutomationEvent[] }
  | { type: "approve_rows"; rowIds: string[] }
  | { type: "execute_approved_input"; events: TaxAutomationEvent[] }
  | { type: "confirm_middle_save"; savedAt: string; confirmationText: string; events: TaxAutomationEvent[] }
  | { type: "open_report_gate" }
  | { type: "attempt_final_action"; action: FinalTaxAction };
