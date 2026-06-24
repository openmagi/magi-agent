"use client";

/**
 * Reusable Evidence types tab — live input-space browser (PR-F2).
 *
 * Primary view: data sourced from `GET /v1/app/customize/evidence/live-catalog`
 * which surfaces every registered evidence type alongside the fields the
 * producer has actually populated in recent runs. This lets operators see
 * the live shape of what they can author against, not just a static list.
 *
 * Secondary section: the policy-derived `EvidenceTypeEntry` list (still
 * passed in via the `entries` prop) lists named evidence refs the user's
 * authored policies emit / consume. Source of truth for refs remains the
 * originating policy; this section is read-only.
 *
 * Honesty invariants enforced here (per spec §2 / §5 PR-F2):
 *   - Inert-producer hide invariant: evidence types whose `registeredFields`
 *     is empty are NOT silently hidden. They render with a clear
 *     "no field constraints authorable (producer extension needed)" note.
 *   - "Authorable now" badge fires only when (a) at least one field has
 *     been populated in the recent sampling window AND (b) at least one
 *     rule-ready ref targets the type.
 *   - Fail-open: a stale or empty live catalog degrades to the empty state,
 *     never to a broken page.
 */

import React, { useCallback, useEffect, useState } from "react";

import { useAgentFetch } from "@/lib/local-api";
import {
  getEvidenceLiveCatalog,
  type EvidenceLiveCatalog,
  type EvidenceLiveCatalogTypeEntry,
} from "@/lib/customize-api";
import type { EvidenceTypeEntry } from "@/lib/policy-model";


export interface ReusableEvidenceTabProps {
  /**
   * Policy-derived evidence refs (consumers / producers). Optional — when
   * absent the tab renders only the live-catalog browser. Kept for back-compat
   * with `customize-hub.tsx` which already derives this view from policies.
   */
  entries?: EvidenceTypeEntry[];
}


/**
 * "Authorable now" gate. Both conditions are binary per PR-F2 spec:
 *   - producer actually emits >=1 structured field in the recent window
 *   - >=1 rule-ready ref targets the type
 *
 * Either alone is insufficient: populated-but-no-ref means the field exists
 * but no rule path references it yet; ref-but-no-population means the
 * runtime registration exists but the producer has not emitted it, so any
 * rule authored against it would silently never fire.
 */
function isAuthorableNow(entry: EvidenceLiveCatalogTypeEntry): boolean {
  return (
    entry.fieldsPopulatedRecently.length >= 1 && entry.refsUsing.length >= 1
  );
}


function LiveCatalogRow({
  entry,
  expanded,
  onToggle,
}: {
  entry: EvidenceLiveCatalogTypeEntry;
  expanded: boolean;
  onToggle: () => void;
}): React.ReactElement {
  const inert = entry.registeredFields.length === 0;
  const authorable = isAuthorableNow(entry);

  return (
    <li className="rounded-xl border border-black/[0.06] bg-[var(--glass-regular-bg)] backdrop-blur-xl">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-black/[0.02]"
      >
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-mono font-medium text-foreground">
            {entry.type}
          </p>
          <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] uppercase tracking-wider text-secondary/70">
            <span>
              {entry.registeredFields.length} registered field
              {entry.registeredFields.length === 1 ? "" : "s"}
            </span>
            <span>·</span>
            <span>
              {entry.fieldsPopulatedRecently.length} populated
            </span>
            <span>·</span>
            <span>
              {entry.refsUsing.length} ref{entry.refsUsing.length === 1 ? "" : "s"}
            </span>
            <span>·</span>
            <span>
              {entry.rulesReferencing} rule{entry.rulesReferencing === 1 ? "" : "s"}
            </span>
          </p>
          {inert ? (
            <p className="mt-1.5 text-[11px] leading-snug text-amber-700">
              No field constraints authorable for this type yet — producer
              extension needed. The runtime registers the type but the
              producer does not emit structured fields.
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {authorable ? (
            <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
              Authorable now
            </span>
          ) : null}
          {inert ? (
            <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-700">
              Producer extension needed
            </span>
          ) : null}
        </div>
      </button>
      {expanded ? (
        <div className="border-t border-black/[0.04] bg-gray-50/40 px-4 py-3 text-xs">
          <div className="space-y-2">
            <div>
              <p className="text-[10px] uppercase tracking-wider text-secondary/70">
                Registered fields
              </p>
              {entry.registeredFields.length === 0 ? (
                <p className="mt-1 text-secondary">
                  None registered. Author a producer extension to surface
                  fields here.
                </p>
              ) : (
                <ul className="mt-1 flex flex-wrap gap-1">
                  {entry.registeredFields.map((f) => {
                    const isPopulated = entry.fieldsPopulatedRecently.includes(f);
                    return (
                      <li
                        key={f}
                        className={`rounded-md border px-1.5 py-0.5 font-mono text-[11px] ${
                          isPopulated
                            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-800"
                            : "border-black/[0.08] bg-white text-secondary"
                        }`}
                        title={
                          isPopulated
                            ? "Populated in recent runs"
                            : "Registered but not observed in the recent sampling window"
                        }
                      >
                        {f}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-wider text-secondary/70">
                Populated fields ({entry.samplePopulationCount} sample
                {entry.samplePopulationCount === 1 ? "" : "s"})
              </p>
              {entry.fieldsPopulatedRecently.length === 0 ? (
                <p className="mt-1 text-secondary">
                  No fields populated in the recent sampling window. Rules
                  authored against this type would not fire yet.
                </p>
              ) : (
                <ul className="mt-1 flex flex-wrap gap-1">
                  {entry.fieldsPopulatedRecently.map((f) => (
                    <li
                      key={f}
                      className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 font-mono text-[11px] text-emerald-800"
                    >
                      {f}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {entry.refsUsing.length > 0 ? (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-secondary/70">
                  Rule-ready refs
                </p>
                <ul className="mt-1 flex flex-wrap gap-1">
                  {entry.refsUsing.map((r) => (
                    <li
                      key={r}
                      className="rounded-md border border-blue-500/30 bg-blue-500/10 px-1.5 py-0.5 font-mono text-[11px] text-blue-800"
                    >
                      {r}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </li>
  );
}


function PolicyDerivedRefs({
  entries,
}: {
  entries: EvidenceTypeEntry[];
}): React.ReactElement {
  return (
    <section className="space-y-2">
      <header>
        <h3 className="text-sm font-semibold text-foreground">
          Refs from your policies
        </h3>
        <p className="text-xs leading-relaxed text-secondary">
          Named evidence refs auto-derived from policies. Each row shows who
          emits / consumes the ref. To add a new ref, author a policy that
          emits it; it will appear here automatically.
        </p>
      </header>
      {entries.length === 0 ? (
        <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs leading-relaxed text-secondary">
          No evidence refs in use yet. Once a policy emits or requires an
          evidence ref, it will land here.
        </p>
      ) : (
        <ul className="space-y-2">
          {entries.map((entry) => (
            <li
              key={entry.ref}
              className="rounded-xl border border-black/[0.06] bg-[var(--glass-regular-bg)] backdrop-blur-xl px-4 py-3"
            >
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-mono text-foreground">
                    {entry.ref}
                  </p>
                  {entry.label && entry.label !== entry.ref ? (
                    <p className="mt-0.5 truncate text-xs text-secondary">
                      {entry.label}
                    </p>
                  ) : null}
                  <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] uppercase tracking-wider text-secondary/70">
                    <span>
                      consumed by {entry.consumedBy.length} policy
                      {entry.consumedBy.length === 1 ? "" : "ies"}
                    </span>
                    <span>·</span>
                    <span>
                      produced by {entry.producedBy.length} policy
                      {entry.producedBy.length === 1 ? "" : "ies"}
                    </span>
                  </p>
                </div>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                    entry.origin === "user"
                      ? "bg-blue-500/10 text-blue-700"
                      : "bg-emerald-500/10 text-emerald-700"
                  }`}
                >
                  {entry.origin === "user" ? "User" : "Built-in"}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}


export function ReusableEvidenceTab({
  entries,
}: ReusableEvidenceTabProps): React.ReactElement {
  const agentFetch = useAgentFetch();
  const [catalog, setCatalog] = useState<EvidenceLiveCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(
    () => new Set(),
  );

  const toggle = useCallback((type: string) => {
    setExpandedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getEvidenceLiveCatalog(agentFetch)
      .then((payload) => {
        if (!cancelled) setCatalog(payload);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentFetch]);

  return (
    <div className="space-y-5">
      {/* PR-F-UX5 — orientation note: "evidence" here means the RAW producer
          records the runtime captures from tools/skills/spawns (the inputs
          a deterministic rule operates against). Verdict primitives
          (verifiers / named conditions) live in the Conditions tab — they
          are judgments over evidence, not evidence itself. The split keeps
          the two pickers in the wizard distinct and prevents the operator
          from mistaking a verdict for a raw record. */}
      <section className="rounded-xl border border-black/[0.06] bg-blue-500/[0.04] px-4 py-3">
        <p className="text-xs leading-relaxed text-secondary">
          <strong className="font-semibold text-foreground">
            Evidence = raw producer records.
          </strong>{" "}
          The runtime captures these from tools, skills, and spawns; rules
          operate over them. Verdict primitives (verifiers / named conditions)
          live in the <strong className="text-foreground">Conditions</strong>{" "}
          tab and are judgments OVER evidence, not evidence itself.
        </p>
      </section>
      <section className="space-y-2">
        <header>
          <h3 className="text-sm font-semibold text-foreground">
            Live evidence catalog
          </h3>
          <p className="text-xs leading-relaxed text-secondary">
            Every evidence type the runtime knows about, with the fields the
            producer has actually populated in recent runs. Use this to author
            field constraints against shapes you know exist on the wire.
            {catalog && catalog.samplingWindow ? (
              <>
                {" "}
                Sampling window: <span className="font-mono">{catalog.samplingWindow}</span>
                {catalog.asOf ? (
                  <>
                    {" "}· as of <span className="font-mono">{catalog.asOf}</span>
                  </>
                ) : null}.
              </>
            ) : null}
          </p>
        </header>
        {loading ? (
          <p
            role="status"
            aria-live="polite"
            className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs leading-relaxed text-secondary"
          >
            Loading live evidence catalog...
          </p>
        ) : !catalog || catalog.evidenceTypes.length === 0 ? (
          <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs leading-relaxed text-secondary">
            No evidence types observed yet. Once the runtime emits a record,
            its type will appear here.
          </p>
        ) : (
          <ul className="space-y-2">
            {catalog.evidenceTypes.map((entry) => (
              <LiveCatalogRow
                key={entry.type}
                entry={entry}
                expanded={expandedTypes.has(entry.type)}
                onToggle={() => toggle(entry.type)}
              />
            ))}
          </ul>
        )}
      </section>

      {entries && entries.length > 0 ? (
        <PolicyDerivedRefs entries={entries} />
      ) : null}
    </div>
  );
}
