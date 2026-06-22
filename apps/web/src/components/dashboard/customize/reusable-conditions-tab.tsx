"use client";

/**
 * Reusable Conditions tab — read-only catalog of user-defined condition
 * payloads (SHACL shapes, LLM criteria, regex patterns, tool-match
 * patterns) so the NL compiler and Guided wizard can reference them.
 *
 * Source of truth = the originating policy. Edit a policy → its condition
 * payload re-surfaces here. There is no separate condition store.
 */

import React from "react";

import type { NamedConditionEntry } from "@/lib/policy-model";


export interface ReusableConditionsTabProps {
  entries: NamedConditionEntry[];
}


const KIND_LABEL: Record<NamedConditionEntry["kind"], string> = {
  shacl_constraint: "SHACL shape",
  llm_criterion: "LLM criterion",
  regex: "Regex / pattern",
  tool_perm: "Tool / domain match",
  evidence_ref: "Evidence ref",
  seam_action: "Seam action",
  none: "Built-in",
};


export function ReusableConditionsTab({
  entries,
}: ReusableConditionsTabProps): React.ReactElement {
  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-secondary">
        Read-only inventory of user-defined condition payloads. Each row was
        authored as part of a policy and is reusable as a reference in
        future NL compiles and Guided wizard steps. The originating policy
        is the source of truth — edit it there.
      </p>
      {entries.length === 0 ? (
        <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs leading-relaxed text-secondary">
          No user-defined conditions yet. Author a policy with a SHACL
          shape / LLM criterion / regex / tool-match condition; it will
          appear here.
        </p>
      ) : (
        <ul className="space-y-2">
          {entries.map((entry) => (
            <li
              key={entry.key}
              className="rounded-xl border border-black/[0.06] bg-white px-4 py-3"
            >
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-foreground">
                    {entry.summary}
                  </p>
                  <p className="mt-0.5 text-[10px] uppercase tracking-wider text-secondary/70">
                    {KIND_LABEL[entry.kind]} · from policy{" "}
                    <span className="font-mono text-secondary">
                      {entry.ownerPolicyName}
                    </span>
                  </p>
                </div>
                <span className="shrink-0 rounded-full bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-700">
                  {KIND_LABEL[entry.kind]}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
