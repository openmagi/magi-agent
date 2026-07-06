/**
 * Tests for the source-citation verdict in the Audit tab (Wave 4b, Piece E).
 *
 * Wave 4b switched the Audit tab from the Wave 3b CLIENT render-verdict
 * projection to the BACKEND gate record: the driver's source_citation.gate
 * producer emits a rule_check-family observability event that the audit feed
 * (useAuditEvents) already carries, so it renders as a normal VerdictRow keyed
 * by subject "source_citation.gate", with its richer affordances (repaired /
 * induced search / fail-open) surfaced as reason-code chips by the backend
 * projection. These tests assert the backend verdict renders, is not
 * double-shown, and that the tab is render-safe when the record is absent.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AuditData,
  AuditVerdict,
  UseAuditEventsResult,
} from "@/hooks/use-audit-events";

const mockUseAuditEvents = vi.fn<
  (botId: string, sessionId?: string | null) => UseAuditEventsResult
>();

vi.mock("@/hooks/use-audit-events", () => ({
  useAuditEvents: (botId: string, sessionId?: string | null) =>
    mockUseAuditEvents(botId, sessionId),
}));

// Imported AFTER vi.mock so the component picks up the mocked hook.
import { AuditPanel } from "./audit-panel";

function verdict(overrides: Partial<AuditVerdict> = {}): AuditVerdict {
  return {
    id: "1",
    kind: "rule_check",
    status: "cited",
    displayLabel: "SOURCES CITED",
    severity: "pass",
    subject: "source_citation.gate",
    reasonCodes: [],
    affordances: [],
    summary: "source citation verdict=cited: cited=3 high_risk=0 dangling=0",
    evidenceRefs: [],
    ...overrides,
  };
}

function auditData(verdicts: AuditVerdict[]): AuditData {
  return {
    sessionId: "sess",
    runs: [{ runId: "run-a", startedAt: 1, policyCount: verdicts.length, verdicts }],
    sources: [],
  };
}

function setAudit(result: Partial<UseAuditEventsResult>): void {
  mockUseAuditEvents.mockReturnValue({
    data: null,
    loading: false,
    error: null,
    ...result,
  });
}

describe("AuditPanel source-citation backend verdict", () => {
  beforeEach(() => {
    mockUseAuditEvents.mockReset();
  });

  it("renders the backend source_citation.gate verdict as a normal row", () => {
    setAudit({ data: auditData([verdict()]) });
    const html = renderToStaticMarkup(<AuditPanel botId="bot-1" sessionId="sess" />);
    expect(html).toContain("SOURCES CITED");
    expect(html).toContain("source_citation.gate");
    // Exactly one verdict row for the gate (the label renders once even though
    // the subject also appears in the row's title attribute).
    const rowCount = html.split('data-audit-verdict-row="true"').length - 1;
    expect(rowCount).toBe(1);
    expect(html.split("SOURCES CITED").length - 1).toBe(1);
  });

  it("surfaces richer affordances (repaired / induced search / fail-open) as chips", () => {
    setAudit({
      data: auditData([
        verdict({
          status: "uncited",
          displayLabel: "UNCITED CLAIMS",
          severity: "review",
          affordances: ["repaired (2)", "induced search", "fail-open"],
        }),
      ]),
    });
    const html = renderToStaticMarkup(<AuditPanel botId="bot-1" sessionId="sess" />);
    expect(html).toContain("UNCITED CLAIMS");
    // Affordances are glanceable (rendered inline, not hidden behind expand).
    expect(html).toContain('data-audit-affordances="true"');
    expect(html).toContain("repaired (2)");
    expect(html).toContain("induced search");
    expect(html).toContain("fail-open");
  });

  it("does NOT double-render: the client render-verdict projection is gone", () => {
    setAudit({ data: auditData([verdict()]) });
    const html = renderToStaticMarkup(<AuditPanel botId="bot-1" sessionId="sess" />);
    // Wave 3b client-projection copy must not reappear.
    expect(html).not.toContain("Sources cited"); // sentence-case client label
    expect(html).not.toContain("This response");
    expect(html).not.toContain("Source citation");
  });

  it("renders safely with no crash and an empty state when the record is absent", () => {
    setAudit({ data: null });
    const html = renderToStaticMarkup(<AuditPanel botId="bot-1" sessionId="sess" />);
    expect(html).toContain("No policies enforced yet");
    expect(html).not.toContain("source_citation.gate");
  });

  it("renders other rule verdicts alongside without a citation ghost row", () => {
    setAudit({
      data: auditData([
        verdict({
          id: "2",
          status: "violation",
          displayLabel: "BLOCKED",
          severity: "deny",
          subject: "evidence:sha256:abc",
          summary: "evidence verdict state=failed",
        }),
      ]),
    });
    const html = renderToStaticMarkup(<AuditPanel botId="bot-1" sessionId="sess" />);
    expect(html).toContain("BLOCKED");
    expect(html).not.toContain("Source citation");
  });
});
