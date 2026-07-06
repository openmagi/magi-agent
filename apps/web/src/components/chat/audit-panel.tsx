"use client";

import { useState } from "react";
import { Badge, type BadgeVariant } from "@/components/ui/_ds/Badge";
import {
  useAuditEvents,
  type AuditRunGroup,
  type AuditSeverity,
  type AuditSource,
  type AuditVerdict,
} from "@/hooks/use-audit-events";
import type { SessionCitationGroup } from "./sources-panel";
import type { CitationVerdict } from "@/chat-core";

interface AuditPanelProps {
  botId: string;
  sessionId?: string | null;
  /** Cited-source payloads whose terminal `verdict` projects into this tab. */
  citationGroups?: SessionCitationGroup[];
}

// Render-provisional citation-governance projection (Wave 3b, Piece C).
//
// Wave 3b projects the deterministic terminal render `verdict` into the Audit
// tab so citation governance is visible alongside every other rule verdict.
// Wave 4 emits a richer `custom:CitationVerdict` gate record into the
// observability store that the audit feed already carries; when that lands this
// section should switch to the backend-produced verdict (superseding the client
// projection) rather than assume this label shape.
const CITATION_VERDICT_LABEL: Record<CitationVerdict, string> = {
  cited: "Sources cited",
  partial: "Partially cited",
  uncited: "Uncited claims",
  not_applicable: "No sources",
};

const CITATION_VERDICT_VARIANT: Record<CitationVerdict, BadgeVariant> = {
  cited: "ok",
  partial: "review",
  uncited: "review",
  not_applicable: "muted",
};

function CitationVerdictSection({
  groups,
}: {
  groups: SessionCitationGroup[];
}): React.ReactElement | null {
  // Only surface turns that actually engaged citation (drop `not_applicable`,
  // i.e. turns with no external-read sources at all).
  const rows = groups.filter((group) => group.citations.verdict !== "not_applicable");
  if (rows.length === 0) return null;
  return (
    <section className="rounded-lg border border-black/[0.06] bg-white/75 px-2 py-2">
      <div className="mb-1.5 px-1">
        <h3 className="text-[11px] font-semibold text-secondary/70">
          Source citation
        </h3>
      </div>
      <ul className="space-y-1.5">
        {rows.map((group, index) => {
          const verdict = group.citations.verdict;
          const dangling = group.citations.danglingRefs.length;
          return (
            <li
              key={group.messageId}
              className="rounded-lg border border-black/[0.06] bg-white/85 px-2.5 py-2"
              data-citation-verdict={verdict}
            >
              <div className="flex min-w-0 items-start gap-2">
                <Badge variant={CITATION_VERDICT_VARIANT[verdict]} className="shrink-0">
                  {CITATION_VERDICT_LABEL[verdict]}
                </Badge>
                <div className="min-w-0 flex-1 text-[11px] text-secondary/60">
                  <div className="truncate">
                    {rows.length > 1 ? `Response ${index + 1}` : "This response"}
                    {" · "}
                    {group.citations.sources.length}{" "}
                    {group.citations.sources.length === 1 ? "source" : "sources"}
                  </div>
                  {dangling > 0 && (
                    <div className="mt-0.5 text-amber-600">
                      {dangling} dangling reference{dangling === 1 ? "" : "s"}
                    </div>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

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

function RunGroup({ group }: { group: AuditRunGroup }): React.ReactElement {
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
        <ul className="space-y-1.5">
          {group.verdicts.map((verdict, index) => (
            <VerdictRow key={verdict.id ?? `${group.runId ?? "run"}:${index}`} verdict={verdict} />
          ))}
        </ul>
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
  citationGroups = [],
}: AuditPanelProps): React.ReactElement {
  const { data, loading, error } = useAuditEvents(botId, sessionId);
  const runs = data?.runs ?? [];
  const sources = data?.sources ?? [];
  const showInitialLoading = loading && !data;
  const hasCitationVerdicts = citationGroups.some(
    (group) => group.citations.verdict !== "not_applicable",
  );

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

        {!showInitialLoading && !error && runs.length === 0 && !hasCitationVerdicts && (
          <div className="rounded-lg border border-black/[0.06] bg-white/70 px-3 py-3 text-[11.5px] text-secondary/55">
            No policies enforced yet.
          </div>
        )}

        {!error && (runs.length > 0 || hasCitationVerdicts) && (
          <div className="space-y-2">
            <CitationVerdictSection groups={citationGroups} />
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
