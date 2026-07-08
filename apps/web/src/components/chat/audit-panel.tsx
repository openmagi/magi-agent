"use client";

import { useState } from "react";
import { Badge, type BadgeVariant } from "@/components/ui/_ds/Badge";
import {
  useAuditEvents,
  type AuditRunGroup,
  type AuditSeverity,
  type AuditSource,
  type AuditVerdict,
  type AuditVerdictVerify,
} from "@/hooks/use-audit-events";

interface AuditPanelProps {
  botId: string;
  sessionId?: string | null;
}

// Source-citation governance now rides the BACKEND gate record (Wave 4b Piece
// E). The driver's source_citation.gate producer emits a rule_check-family
// observability event that the audit feed already carries, so it renders as a
// normal VerdictRow keyed by subject "source_citation.gate" with its richer
// affordances (repaired / induced search / fail-open) surfaced as reason-code
// chips by the backend projection. The Wave 3b client-side render-verdict
// projection was removed here so the verdict is never double-shown; the inline
// [src_N] chips and the Sources tab (Wave 3 render, unaffected) stay as-is.

// Severity → design-system badge variant. The backend (evidence/audit_labels.py)
// is the single source of truth that maps every verdict label to one of these
// four severities, so the panel never re-classifies — it only skins.
const SEVERITY_VARIANT: Record<AuditSeverity, BadgeVariant> = {
  pass: "ok",
  deny: "deny",
  review: "review",
  info: "info",
};

function severityVariant(severity: AuditSeverity): BadgeVariant {
  return SEVERITY_VARIANT[severity] ?? "default";
}

// Subjects can be digests like `verifier:sha256:<64hex>`. A plain `truncate`
// shows an ugly raw prefix, so middle-elide digest-shaped values (keeping the
// full value available via the `title` attribute).
function elideSubject(subject: string): string {
  const looksDigest =
    subject.includes(":sha256:") ||
    (subject.length > 28 && !/\s/.test(subject));
  if (!looksDigest) return subject;
  return `${subject.slice(0, 13)}…${subject.slice(-6)}`;
}

function runLabel(group: AuditRunGroup): string {
  const count = group.policyCount;
  const noun = count === 1 ? "policy" : "policies";
  return `Magi applied ${count} ${noun} to this run`;
}

function sourceHost(uri: string): string {
  if (!uri) return "";
  try {
    return new URL(uri).host || uri;
  } catch {
    return uri;
  }
}

function sourceBadge(source: AuditSource): { variant: BadgeVariant; label: string } {
  if (source.credibility === "contradicted") {
    return { variant: "deny", label: "Contradicted" };
  }
  if (source.verified || source.credibility === "credible") {
    return { variant: "ok", label: "Verified" };
  }
  return { variant: "muted", label: "Not verified" };
}

function VerdictRow({ verdict }: { verdict: AuditVerdict }): React.ReactElement {
  const [open, setOpen] = useState(false);
  const hasDetails =
    Boolean(verdict.summary) ||
    verdict.reasonCodes.length > 0 ||
    verdict.evidenceRefs.length > 0;

  return (
    <li
      className="rounded-lg border border-black/[0.06] bg-white/85 px-2.5 py-2 shadow-[0_1px_3px_rgba(15,23,42,0.04)]"
      data-audit-verdict-row="true"
    >
      <div className="flex min-w-0 items-start gap-2">
        <Badge variant={severityVariant(verdict.severity)} className="shrink-0">
          {verdict.displayLabel}
        </Badge>
        <div className="min-w-0 flex-1">
          {verdict.subject && (
            <div
              className="truncate text-[12px] font-medium text-foreground/85"
              title={verdict.subject}
            >
              {elideSubject(verdict.subject)}
            </div>
          )}
          {verdict.affordances.length > 0 && (
            <div className="mt-0.5 flex flex-wrap gap-1" data-audit-affordances="true">
              {verdict.affordances.map((affordance) => (
                <span
                  key={affordance}
                  className="rounded bg-black/[0.04] px-1.5 py-0.5 text-[10px] font-medium text-secondary/60"
                >
                  {affordance}
                </span>
              ))}
            </div>
          )}
          {hasDetails && (
            <button
              type="button"
              onClick={() => setOpen((value) => !value)}
              className="mt-0.5 inline-flex items-center gap-1 text-[11px] font-medium text-secondary/70 transition-colors hover:text-foreground"
              aria-expanded={open}
              aria-label={`${open ? "Hide" : "Show"} verdict details`}
            >
              <svg
                className={`h-3 w-3 transition-transform ${open ? "" : "-rotate-90"}`}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
              details
            </button>
          )}
        </div>
      </div>

      {open && hasDetails && (
        <div className="mt-1.5 space-y-1.5 border-t border-black/[0.05] pt-1.5">
          {verdict.summary && (
            <p className="break-words text-[11px] leading-snug text-secondary/65">
              {verdict.summary}
            </p>
          )}
          {verdict.reasonCodes.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {verdict.reasonCodes.map((code) => (
                <span
                  key={code}
                  className="rounded bg-black/[0.04] px-1.5 py-0.5 text-[10px] font-medium text-secondary/60"
                >
                  {code}
                </span>
              ))}
            </div>
          )}
          {verdict.evidenceRefs.length > 0 && (
            <div>
              <div className="text-[9px] font-semibold uppercase tracking-wide text-secondary/45">
                Evidence
              </div>
              <ul className="mt-0.5 space-y-0.5">
                {verdict.evidenceRefs.map((ref) => (
                  <li
                    key={ref}
                    className="truncate font-mono text-[10px] text-secondary/55"
                    title={ref}
                  >
                    {ref}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

// ---- Verify-before-replying section ----------------------------------------

const VERIFY_SUBJECT = "verify_before_replying.audit";
const VERIFY_PREFIX = "verify_before_replying.";

/**
 * Strip the "verify_before_replying." prefix from a subject string to get the
 * member-rule short name (e.g. "verify_before_replying.evidence_consistency"
 * -> "evidence_consistency").
 */
function memberRuleShortName(subject: string | null): string {
  if (!subject) return "";
  return subject.startsWith(VERIFY_PREFIX)
    ? subject.slice(VERIFY_PREFIX.length)
    : subject;
}

/**
 * Compose the trajectory line from turn scalars.
 * Format: "{N} audit pass(es) . {decision verb} . {N} tool call(s) during recheck"
 * Singular-safe for passes and loopBackToolCalls.
 */
function trajectoryLine(vfy: AuditVerdictVerify): string {
  const parts: string[] = [];

  const passes = vfy.passes ?? 1;
  parts.push(passes === 1 ? "1 audit pass" : `${passes} audit passes`);

  if (vfy.verdict === "revised") {
    parts.push("model revised");
  } else if (vfy.verdict === "shipped_acknowledged") {
    parts.push("model shipped as-is, acknowledged the findings");
  } else if (vfy.verdict === "nudge_ignored") {
    parts.push("finding not addressed, reply shipped unchanged");
  }
  // "verified_clean" gets no decision verb in the trajectory line (clean turn
  // has its own calm sub-line and no trajectory section)

  if (vfy.loopBackToolCalls && vfy.loopBackToolCalls > 0) {
    const n = vfy.loopBackToolCalls;
    parts.push(n === 1 ? "1 tool call during recheck" : `${n} tool calls during recheck`);
  }

  return parts.join(" . ");
}

/**
 * Single finding row inside the verify section. Renders as a nested variant
 * of VerdictRow: resolution badge, member-rule + claimClass chip, quoted claim
 * (or fallback), expected/observed, evidenceRefs.
 */
function FindingRow({ verdict }: { verdict: AuditVerdict }): React.ReactElement {
  const vfy = verdict.verify as AuditVerdictVerify;
  const shortName = memberRuleShortName(verdict.subject);
  const isRedacted =
    !vfy.claimText || vfy.claimText === "[redacted]";

  return (
    <li
      className="rounded-lg border border-black/[0.06] bg-white/85 px-2.5 py-2 shadow-[0_1px_3px_rgba(15,23,42,0.04)]"
      data-audit-verdict-row="true"
    >
      <div className="flex min-w-0 items-start gap-2">
        <Badge variant={severityVariant(verdict.severity)} className="shrink-0">
          {verdict.displayLabel}
        </Badge>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
            {shortName && (
              <span className="text-[11px] font-medium text-foreground/80">
                {shortName}
              </span>
            )}
            {vfy.claimClass && (
              <span className="rounded bg-black/[0.04] px-1.5 py-0.5 text-[10px] font-medium text-secondary/60">
                {vfy.claimClass}
              </span>
            )}
          </div>
          {isRedacted ? (
            <div className="mt-0.5 text-[11px] text-secondary/45">
              {shortName && (
                <span>{shortName}</span>
              )}
              {vfy.claimClass && shortName && (
                <span> ({vfy.claimClass})</span>
              )}
            </div>
          ) : (
            <blockquote className="mt-0.5 border-l-2 border-black/[0.10] pl-1.5 text-[11px] italic text-secondary/65">
              {vfy.claimText}
            </blockquote>
          )}
          {!isRedacted && vfy.expected && vfy.observed && (
            <div className="mt-0.5 text-[10px] text-secondary/50">
              expected {vfy.expected} . observed {vfy.observed}
            </div>
          )}
          {verdict.evidenceRefs.length > 0 && (
            <div className="mt-0.5">
              <div className="text-[9px] font-semibold uppercase tracking-wide text-secondary/45">
                Evidence
              </div>
              <ul className="mt-0.5 space-y-0.5">
                {verdict.evidenceRefs.map((ref) => (
                  <li
                    key={ref}
                    className="truncate font-mono text-[10px] text-secondary/55"
                    title={ref}
                  >
                    {ref}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

/**
 * "Verify before replying" process section rendered inside RunGroup when a
 * kind:"turn" verify row is present.
 *
 * Clean turn: one calm row with a corpus-count sub-line.
 * Process view (any turn with findings): trajectory line, standing framing
 *   sub-line, findings grouped HIGH then ADVISORY.
 */
function VerifySection({
  turnRow,
  findingRows,
}: {
  turnRow: AuditVerdict;
  findingRows: AuditVerdict[];
}): React.ReactElement {
  const vfy = turnRow.verify as AuditVerdictVerify;
  const isClean =
    vfy.verdict === "verified_clean" && findingRows.length === 0;

  const friendlyTitle =
    turnRow.subject === VERIFY_SUBJECT
      ? "Verify before replying"
      : (turnRow.subject ?? "Verify before replying");

  const hasFindings = findingRows.length > 0;

  const highFindings = findingRows.filter(
    (v) => v.verify?.confidence === "high",
  );
  const advisoryFindings = findingRows.filter(
    (v) => v.verify?.confidence === "advisory",
  );

  const corpusCount = vfy.corpusRecordCount ?? 0;
  const corpusLine =
    corpusCount === 1
      ? `Audited against 1 evidence record. No issues found.`
      : `Audited against ${corpusCount} evidence records. No issues found.`;

  return (
    <li
      className="rounded-lg border border-black/[0.06] bg-white/85 px-2.5 py-2 shadow-[0_1px_3px_rgba(15,23,42,0.04)]"
      data-audit-verdict-row="true"
    >
      <div className="flex min-w-0 items-start gap-2">
        <Badge variant={severityVariant(turnRow.severity)} className="shrink-0">
          {turnRow.displayLabel}
        </Badge>
        <div className="min-w-0 flex-1">
          <div
            className="truncate text-[12px] font-medium text-foreground/85"
            title={turnRow.subject ?? undefined}
          >
            {friendlyTitle}
          </div>

          {isClean ? (
            <p className="mt-0.5 text-[11px] leading-snug text-secondary/65">
              {corpusLine}
            </p>
          ) : (
            <>
              {/* Trajectory line */}
              <p className="mt-0.5 text-[11px] leading-snug text-secondary/65">
                {trajectoryLine(vfy)}
              </p>

              {/* Standing framing sub-line (always present when there are findings) */}
              {hasFindings && (
                <p className="mt-0.5 text-[11px] leading-snug text-secondary/55">
                  Findings were advisory: nothing was blocked, the model chose how to respond.
                </p>
              )}

              {/* HIGH findings group */}
              {highFindings.length > 0 && (
                <div className="mt-2">
                  <div className="mb-1 text-[10px] font-semibold text-secondary/60">
                    Evidence-backed findings (high confidence)
                  </div>
                  <ul className="space-y-1.5">
                    {highFindings.map((fv) => (
                      <FindingRow
                        key={fv.id ?? fv.verify?.findingId ?? fv.subject}
                        verdict={fv}
                      />
                    ))}
                  </ul>
                </div>
              )}

              {/* ADVISORY findings group (header only when advisory findings exist) */}
              {advisoryFindings.length > 0 && (
                <div className="mt-2">
                  <div className="mb-1 text-[10px] font-semibold text-secondary/40">
                    Heuristic observations (may be wrong)
                  </div>
                  <ul className="space-y-1.5">
                    {advisoryFindings.map((fv) => (
                      <FindingRow
                        key={fv.id ?? fv.verify?.findingId ?? fv.subject}
                        verdict={fv}
                      />
                    ))}
                  </ul>
                </div>
              )}

              {/* Overflow indicator */}
              {(vfy.findingsOmitted ?? 0) > 0 && (
                <p className="mt-1.5 text-[10px] text-secondary/40">
                  +{vfy.findingsOmitted} more findings recorded
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </li>
  );
}

// ---- RunGroup with verify partitioning -------------------------------------

function RunGroup({ group }: { group: AuditRunGroup }): React.ReactElement {
  // Partition verify rows out of the generic list.
  const verifyRows = group.verdicts.filter((v) => v.verify !== undefined);
  const nonVerifyRows = group.verdicts.filter((v) => v.verify === undefined);

  const turnRow = verifyRows.find((v) => v.verify?.kind === "turn");
  const findingRows = verifyRows.filter((v) => v.verify?.kind === "finding");
  // kind:"pass" rows are consumed for nothing visible (pass count comes from turn row).

  // When a turn row exists, anchor the VerifySection at the position of the
  // FIRST verify row in the original list (temporal placement).
  const firstVerifyIndex =
    turnRow !== undefined
      ? group.verdicts.findIndex((v) => v.verify !== undefined)
      : -1;

  // Build the rendered list interleaving VerifySection at the right position.
  const renderedItems: React.ReactElement[] = [];
  let verifyInserted = false;
  let nonVerifyIdx = 0;

  for (let i = 0; i < group.verdicts.length; i++) {
    const v = group.verdicts[i];
    if (v.verify !== undefined) {
      // First verify slot: insert VerifySection (if turn row exists) or
      // fall back to plain rows for finding/pass rows only.
      if (!verifyInserted) {
        verifyInserted = true;
        if (turnRow !== undefined) {
          renderedItems.push(
            <VerifySection
              key={`verify:${group.runId ?? "run"}`}
              turnRow={turnRow}
              findingRows={findingRows}
            />,
          );
        } else {
          // No turn row: render finding/pass rows as plain VerdictRows.
          for (const fv of verifyRows) {
            renderedItems.push(
              <VerdictRow
                key={fv.id ?? `${group.runId ?? "run"}:verify:${fv.verify?.findingId ?? ""}`}
                verdict={fv}
              />,
            );
          }
        }
      }
      // All subsequent verify row slots are consumed (already rendered above).
      void firstVerifyIndex; // suppress unused warning
    } else {
      // Non-verify row: render in original order.
      renderedItems.push(
        <VerdictRow
          key={v.id ?? `${group.runId ?? "run"}:${nonVerifyIdx}`}
          verdict={v}
        />,
      );
      nonVerifyIdx++;
    }
  }

  return (
    <section
      className="rounded-lg border border-black/[0.06] bg-white/75 px-2 py-2"
      data-audit-run-id={group.runId ?? undefined}
    >
      <div className="mb-1.5 px-1">
        <h3 className="min-w-0 text-[11px] font-semibold text-secondary/70">
          {runLabel(group)}
        </h3>
      </div>
      {group.verdicts.length === 0 ? (
        <div className="rounded-md bg-black/[0.03] px-2 py-1.5 text-[11px] text-secondary/45">
          No verdicts recorded for this run.
        </div>
      ) : (
        <ul className="space-y-1.5">{renderedItems}</ul>
      )}
    </section>
  );
}

function SourcesBox({ sources }: { sources: AuditSource[] }): React.ReactElement | null {
  // Sources are empty in OSS local-serve today by design (Box B is a Phase-2
  // durable-source-projection dependency). Render nothing rather than a
  // prominent empty "Sources (0)" box.
  if (sources.length === 0) return null;
  return (
    <section className="rounded-lg border border-black/[0.06] bg-white/75 px-2 py-2">
      <div className="mb-1.5 flex items-center justify-between gap-2 px-1">
        <h3 className="text-[11px] font-semibold text-secondary/70">
          Sources ({sources.length})
        </h3>
      </div>
      <ul className="space-y-1.5">
        {sources.map((source, index) => {
          const badge = sourceBadge(source);
          const host = sourceHost(source.uri);
          return (
            <li
              key={`${source.label}:${index}`}
              className="rounded-lg border border-black/[0.06] bg-white/85 px-2.5 py-2"
              data-audit-source-row="true"
            >
              <div className="flex min-w-0 items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12px] font-medium text-foreground/85">
                    {source.label}
                  </div>
                  {host && (
                    <div className="mt-0.5 truncate text-[10px] text-secondary/45" title={source.uri}>
                      {host}
                    </div>
                  )}
                </div>
                <Badge variant={badge.variant} className="shrink-0">
                  {badge.label}
                </Badge>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function AuditPanel({
  botId,
  sessionId,
}: AuditPanelProps): React.ReactElement {
  const { data, loading, error } = useAuditEvents(botId, sessionId);
  const runs = data?.runs ?? [];
  const sources = data?.sources ?? [];
  const showInitialLoading = loading && !data;

  return (
    <div className="flex min-h-0 flex-1 flex-col" aria-label="Policy audit log">
      <div className="border-b border-black/[0.06] px-3 py-2">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-secondary/70">
          Audit
        </div>
        <p className="mt-1 text-[11px] leading-snug text-secondary/45">
          Policy enforcement and source verification for this channel&apos;s runs.
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {error && !data && (
          <div className="rounded-lg border border-black/[0.06] bg-white/70 px-3 py-3 text-[11.5px] text-secondary/55">
            Couldn&apos;t load audit log.
          </div>
        )}

        {showInitialLoading && !error && (
          <div className="space-y-1.5" aria-busy="true" aria-label="Loading audit log">
            {[0, 1, 2].map((row) => (
              <div
                key={row}
                className="h-12 animate-pulse rounded-lg border border-black/[0.06] bg-black/[0.03]"
              />
            ))}
          </div>
        )}

        {!showInitialLoading && !error && runs.length === 0 && (
          <div className="rounded-lg border border-black/[0.06] bg-white/70 px-3 py-3 text-[11.5px] text-secondary/55">
            No policies enforced yet.
          </div>
        )}

        {!error && runs.length > 0 && (
          <div className="space-y-2">
            {runs.map((group, index) => (
              <RunGroup key={group.runId ?? `run:${index}`} group={group} />
            ))}
            <SourcesBox sources={sources} />
          </div>
        )}
      </div>
    </div>
  );
}
