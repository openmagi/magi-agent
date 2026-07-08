/**
 * Tests for the "Verify before replying" section in the Audit tab (PR-3).
 *
 * The verify-before-replying policy emits three row species per turn:
 *   kind:"turn"    -- the terminal verdict (verified_clean / revised / shipped_acknowledged / nudge_ignored)
 *   kind:"finding" -- one row per finding (confidence high | advisory, with resolution)
 *   kind:"pass"    -- one row per audit pass (consumed, never rendered)
 *
 * The panel partitions verify rows out of the generic list and renders one
 * VerifySection per run group. When no turn row exists (old image, partial
 * data), finding/pass rows fall back to plain VerdictRows.
 *
 * Mirrors audit-panel.citations.test.tsx: vi.mock the hook, renderToStaticMarkup.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AuditData,
  AuditVerdict,
  AuditVerdictVerify,
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

// Base verdict factory (mirrors citations harness). Every fixture needs affordances: [].
function verdict(overrides: Partial<AuditVerdict> = {}): AuditVerdict {
  return {
    id: "v1",
    kind: "rule_check",
    status: "ok",
    displayLabel: "VERIFIED CLEAN",
    severity: "pass",
    subject: "verify_before_replying.audit",
    reasonCodes: [],
    affordances: [],
    summary: "",
    evidenceRefs: [],
    ...overrides,
  };
}

// Add a verify field to a verdict.
function withVerify(v: AuditVerdict, vfy: AuditVerdictVerify): AuditVerdict {
  return { ...v, verify: vfy };
}

function auditData(verdicts: AuditVerdict[]): AuditData {
  return {
    sessionId: "sess",
    runs: [
      {
        runId: "run-a",
        startedAt: 1,
        policyCount: verdicts.length,
        verdicts,
      },
    ],
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

// ---- Fixtures ---------------------------------------------------------------

const cleanTurnVerdict = withVerify(
  verdict({
    id: "turn-1",
    displayLabel: "VERIFIED CLEAN",
    severity: "pass",
    subject: "verify_before_replying.audit",
  }),
  {
    kind: "turn",
    verdict: "verified_clean",
    passes: 1,
    corpusRecordCount: 12,
    highTotal: 0,
    highResolved: 0,
    highAcknowledged: 0,
    highIgnored: 0,
    advisoryTotal: 0,
    advisoryIgnored: 0,
    loopBackToolCalls: 0,
    shipMarkerUsed: false,
    findingsOmitted: 0,
  },
);

const passRow = withVerify(
  verdict({
    id: "pass-1",
    displayLabel: "AUDIT PASS",
    severity: "info",
    subject: "verify_before_replying.audit",
  }),
  { kind: "pass" },
);

// Process-view (nudged-then-revised) fixture
const revisedTurnVerdict = withVerify(
  verdict({
    id: "turn-r",
    displayLabel: "REVISED",
    severity: "pass",
    subject: "verify_before_replying.audit",
    status: "ok",
  }),
  {
    kind: "turn",
    verdict: "revised",
    passes: 2,
    loopBackToolCalls: 3,
    shipMarkerUsed: false,
    highTotal: 2,
    highResolved: 1,
    highAcknowledged: 0,
    highIgnored: 1,
    advisoryTotal: 1,
    advisoryIgnored: 0,
    corpusRecordCount: 5,
    findingsOmitted: 2,
  },
);

const highResolvedFinding = withVerify(
  verdict({
    id: "f-1",
    displayLabel: "RESOLVED",
    severity: "pass",
    subject: "verify_before_replying.evidence_consistency",
    status: "ok",
  }),
  {
    kind: "finding",
    findingId: "fid-1",
    confidence: "high",
    claimClass: "tests_pass",
    resolution: "resolved",
    claimText: "all 93 tests pass",
    expected: "exitCode=0",
    observed: "exitCode=1",
    suggestedAction: "re-run tests",
  },
);

const highIgnoredFinding = withVerify(
  verdict({
    id: "f-2",
    displayLabel: "IGNORED",
    severity: "deny",
    subject: "verify_before_replying.claim_citation",
    status: "violation",
  }),
  {
    kind: "finding",
    findingId: "fid-2",
    confidence: "high",
    claimClass: "numeric",
    resolution: "ignored",
    claimText: "revenue grew 40% in Q1",
  },
);

const advisoryFinding = withVerify(
  verdict({
    id: "f-3",
    displayLabel: "ADVISORY",
    severity: "info",
    subject: "verify_before_replying.sycophancy_heuristics",
    status: "ok",
  }),
  {
    kind: "finding",
    findingId: "fid-3",
    confidence: "advisory",
    claimClass: "sycophancy",
    resolution: "ignored",
    claimText: "You're absolutely right, great catch",
  },
);

// ---- Tests ------------------------------------------------------------------

describe("AuditPanel verify-before-replying section", () => {
  beforeEach(() => {
    mockUseAuditEvents.mockReset();
  });

  it("renders a clean turn as one calm row", () => {
    setAudit({
      data: auditData([cleanTurnVerdict, passRow]),
    });
    const html = renderToStaticMarkup(
      <AuditPanel botId="bot-1" sessionId="sess" />,
    );

    // Turn badge and friendly title
    expect(html).toContain("VERIFIED CLEAN");
    expect(html).toContain("Verify before replying");

    // Calm sub-line with corpus count (singular-safe: 12 -> "12 evidence records")
    expect(html).toContain("Audited against 12 evidence records. No issues found.");

    // No section headers or empty stubs when clean
    expect(html).not.toContain("Evidence-backed findings");
    expect(html).not.toContain("Heuristic observations");
    expect(html).not.toContain("more findings recorded");

    // pass row is consumed, not rendered as a separate row
    // total verdict rows: 1 (the turn row only)
    const rowCount = html.split('data-audit-verdict-row="true"').length - 1;
    expect(rowCount).toBe(1);
  });

  it("renders the nudged-then-revised process view", () => {
    setAudit({
      data: auditData([
        revisedTurnVerdict,
        highResolvedFinding,
        highIgnoredFinding,
        advisoryFinding,
      ]),
    });
    const html = renderToStaticMarkup(
      <AuditPanel botId="bot-1" sessionId="sess" />,
    );

    // Turn verdict badge
    expect(html).toContain("REVISED");

    // Trajectory line components
    expect(html).toContain("2 audit passes");
    expect(html).toContain("model revised");
    expect(html).toContain("3 tool call");

    // Standing sub-line (framing rule)
    expect(html).toContain(
      "Findings were advisory: nothing was blocked, the model chose how to respond.",
    );

    // High findings group header
    expect(html).toContain("Evidence-backed findings (high confidence)");

    // Finding resolution badges
    expect(html).toContain("RESOLVED");
    expect(html).toContain("IGNORED");

    // Claim text and expected/observed for the resolved finding
    expect(html).toContain("all 93 tests pass");
    expect(html).toContain("exitCode=0");
    expect(html).toContain("exitCode=1");

    // Advisory group header
    expect(html).toContain("Heuristic observations (may be wrong)");

    // Overflow indicator
    expect(html).toContain("+2 more findings recorded");
  });

  it("advisory group is absent when no advisory findings exist", () => {
    setAudit({
      data: auditData([revisedTurnVerdict, highResolvedFinding]),
    });
    const html = renderToStaticMarkup(
      <AuditPanel botId="bot-1" sessionId="sess" />,
    );

    expect(html).toContain("Evidence-backed findings (high confidence)");
    expect(html).not.toContain("Heuristic observations");
  });

  it("falls back to plain rows when the turn row is missing", () => {
    // Only finding rows, no kind:"turn" row
    setAudit({
      data: auditData([highResolvedFinding, highIgnoredFinding]),
    });
    const html = renderToStaticMarkup(
      <AuditPanel botId="bot-1" sessionId="sess" />,
    );

    // Backend labels visible
    expect(html).toContain("RESOLVED");
    expect(html).toContain("IGNORED");

    // No "Verify before replying" section header
    expect(html).not.toContain("Verify before replying");

    // No crash (implicit: renderToStaticMarkup does not throw)
  });

  it("never uses blocked language", () => {
    setAudit({
      data: auditData([
        revisedTurnVerdict,
        highResolvedFinding,
        highIgnoredFinding,
        advisoryFinding,
      ]),
    });
    const html = renderToStaticMarkup(
      <AuditPanel botId="bot-1" sessionId="sess" />,
    );

    // The standing sub-line is present
    const standingLine =
      "Findings were advisory: nothing was blocked, the model chose how to respond.";
    expect(html).toContain("nothing was blocked");

    // After removing the exact standing line, the remainder must not contain forbidden words
    const remainder = html.replace(standingLine, "");
    expect(remainder).not.toMatch(/blocked|refused|prevented|stopped/i);
  });

  it("redacted claim falls back to rule copy", () => {
    const redactedFinding = withVerify(
      verdict({
        id: "f-red",
        displayLabel: "IGNORED",
        severity: "deny",
        subject: "verify_before_replying.evidence_consistency",
        status: "violation",
      }),
      {
        kind: "finding",
        findingId: "fid-red",
        confidence: "high",
        claimClass: "file_edit",
        resolution: "ignored",
        claimText: "[redacted]",
      },
    );

    setAudit({
      data: auditData([revisedTurnVerdict, redactedFinding]),
    });
    const html = renderToStaticMarkup(
      <AuditPanel botId="bot-1" sessionId="sess" />,
    );

    // Must not show the literal [redacted] string
    expect(html).not.toContain("[redacted]");

    // Should contain the member-rule short name and claimClass as fallback
    expect(html).toContain("evidence_consistency");
    expect(html).toContain("file_edit");
  });

  it("non-verify rows and empty state are untouched", () => {
    // Citation verdict alongside a verify turn row
    const citationVerdict: AuditVerdict = {
      id: "c-1",
      kind: "rule_check",
      status: "cited",
      displayLabel: "SOURCES CITED",
      severity: "pass",
      subject: "source_citation.gate",
      reasonCodes: [],
      affordances: [],
      summary: "source citation verdict=cited",
      evidenceRefs: [],
    };

    setAudit({
      data: auditData([cleanTurnVerdict, passRow, citationVerdict]),
    });
    const html = renderToStaticMarkup(
      <AuditPanel botId="bot-1" sessionId="sess" />,
    );

    // Both sections present
    expect(html).toContain("Verify before replying");
    expect(html).toContain("SOURCES CITED");
    expect(html).toContain("source_citation.gate");

    // Empty state test
    setAudit({ data: null });
    const emptyHtml = renderToStaticMarkup(
      <AuditPanel botId="bot-1" sessionId="sess" />,
    );
    expect(emptyHtml).toContain("No policies enforced yet");
  });
});
