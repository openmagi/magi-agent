"use client";

/**
 * Reusable Conditions tab — read-only catalog of every reusable judgment
 * primitive in the runtime.
 *
 * PR-F-UX5 — the tab now merges two sources:
 *
 *  * **Built-in verifiers** (``catalog.verification.judgmentMenu``): verdict
 *    primitives the runtime ships out of the box (``verifier:*`` refs and
 *    unprefixed named-judgment refs such as ``fact_grounding``). These are
 *    read-only — verifier authoring is a code surface, not a dashboard
 *    surface (F-UX5 design principle 1).
 *  * **User-authored conditions** (derived from the unified :class:`Policy`
 *    list via :func:`extractNamedConditions`): SHACL shapes, LLM criteria,
 *    regex patterns, and tool-match patterns the operator has authored as
 *    part of a policy. These remain owned by the originating policy; edit a
 *    policy → its condition payload re-surfaces here.
 *
 * Each row carries an origin badge ("built-in" / "user") so the operator
 * sees at a glance which list each row came from. The two halves render in
 * a single list rather than under separate headers — the F-UX5 spec
 * deliberately folds the Verifier vs Condition distinction into the badge
 * (origin-only) rather than spawning a third tab.
 */

import React from "react";

import type { NamedConditionEntry } from "@/lib/policy-model";


export interface ReusableConditionsTabProps {
  /** User-authored named conditions extracted from the unified policy list. */
  entries: NamedConditionEntry[];
  /**
   * PR-F-UX5 — built-in verdict primitives sourced from
   * ``catalog.verification.judgmentMenu`` via
   * :func:`extractBuiltinJudgmentRefs`. Optional so existing callers that
   * have not been updated render the user-only view unchanged.
   */
  builtinEntries?: NamedConditionEntry[];
}


const KIND_LABEL: Record<NamedConditionEntry["kind"], string> = {
  shacl_constraint: "SHACL shape",
  llm_criterion: "LLM criterion",
  regex: "Regex / pattern",
  tool_perm: "Tool / domain match",
  evidence_ref: "Evidence ref",
  capability_scope: "Subagent capability scope",
  // PR-F-MUT1 — mutator surface; the row label distinguishes it from gate
  // kinds so an operator scanning the table sees "this rewrites traffic"
  // before opening the row.
  prompt_injection: "Prompt injection (mutator)",
  // PR-F-MUT2 — second mutator kind; same labelling treatment as F-MUT1 so
  // the Conditions tab honestly signals "rewrites traffic" before the row
  // opens.
  output_rewrite: "Output rewrite (mutator)",
  // PR-F-EXEC1 — operator-authored shell action. Labelled distinctly so
  // the Conditions tab honestly signals "external script, magi does not
  // verify" before the row opens (F-EXEC3 ships the dedicated badge).
  shell_command: "Shell command (operator-defined)",
  seam_action: "Seam action",
  none: "Built-in",
};


export function ReusableConditionsTab({
  entries,
  builtinEntries,
}: ReusableConditionsTabProps): React.ReactElement {
  // PR-F-UX5 — built-in entries come first so the operator sees the
  // ready-made inventory before scrolling to their own rules. Each half
  // keeps its incoming sort (caller passes them in a meaningful order: the
  // judgment menu is curated; user conditions are policy-order).
  const merged: NamedConditionEntry[] = [
    ...(builtinEntries ?? []),
    ...entries,
  ];
  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-secondary">
        Read-only inventory of reusable judgment primitives. Each row is
        either a built-in verifier (runtime code) or a condition payload you
        authored as part of a policy. Edit the originating policy to change a
        user condition; built-in verifiers can only be changed by extending
        the runtime.
      </p>
      {merged.length === 0 ? (
        <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs leading-relaxed text-secondary">
          No verifiers or conditions yet. Author a policy with a SHACL shape
          / LLM criterion / regex / tool-match condition; it will appear
          here.
        </p>
      ) : (
        <ul className="space-y-2">
          {merged.map((entry) => (
            <li
              key={entry.key}
              className="rounded-xl border border-black/[0.06] bg-[var(--glass-regular-bg)] backdrop-blur-xl px-4 py-3"
            >
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-foreground">
                    {entry.summary}
                  </p>
                  <p className="mt-0.5 text-[10px] uppercase tracking-wider text-secondary/70">
                    {KIND_LABEL[entry.kind]}
                    {entry.origin === "user" ? (
                      <>
                        {" "}· from policy{" "}
                        <span className="font-mono text-secondary">
                          {entry.ownerPolicyName}
                        </span>
                      </>
                    ) : (
                      <>
                        {" "}·{" "}
                        <span className="font-mono text-secondary">
                          {entry.ownerPolicyName}
                        </span>
                      </>
                    )}
                  </p>
                </div>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                    entry.origin === "user"
                      ? "bg-blue-500/10 text-blue-700"
                      : "bg-emerald-500/10 text-emerald-700"
                  }`}
                >
                  {entry.origin === "user" ? "user" : "built-in"}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
