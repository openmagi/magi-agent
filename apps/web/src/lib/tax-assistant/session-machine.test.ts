import { describe, expect, it } from "vitest";
import {
  createGuestTaxSession,
  taxAssistantReducer,
} from "./session-machine";

describe("tax assistant session machine", () => {
  it("creates a guest session in the start step with safety boundaries", () => {
    const session = createGuestTaxSession({
      sessionId: "tax_guest_1",
      nowIso: "2026-05-01T09:00:00.000Z",
    });

    expect(session).toMatchObject({
      id: "tax_guest_1",
      mode: "guest",
      step: "start",
      filingYear: 2025,
      finalActionBlocked: true,
      paymentGate: "after_middle_save_report_export",
    });
    expect(session.hardStops).toContain("final_submit");
    expect(session.hardStops).toContain("tax_payment");
    expect(session.hardStops).toContain("refund_account_finalization");
  });

  it("blocks final filing actions regardless of current step", () => {
    const session = createGuestTaxSession({
      sessionId: "tax_guest_2",
      nowIso: "2026-05-01T09:00:00.000Z",
    });

    const next = taxAssistantReducer(session, {
      type: "attempt_final_action",
      action: "final_submit",
    });

    expect(next.step).toBe("blocked");
    expect(next.error).toEqual({
      code: "final_action_blocked",
      message: "Open Magi stops before final submission, payment, and refund-account finalization.",
    });
  });
});
