"use client";

/**
 * Reusable Evidence types tab — read-only catalog auto-derived from the
 * Policy list. Surfaces named evidence refs the user (or built-in)
 * policies emit or consume so other policies (and the NL compiler /
 * Guided wizard) can reference them by name.
 *
 * Per PR-E1 architecture: this is NOT a separately editable entity. The
 * source of truth is the originating policy. Edit a policy → its evidence
 * ref shows up here automatically.
 */

import React from "react";

import type { EvidenceTypeEntry } from "@/lib/policy-model";


export interface ReusableEvidenceTabProps {
  entries: EvidenceTypeEntry[];
}


export function ReusableEvidenceTab({
  entries,
}: ReusableEvidenceTabProps): React.ReactElement {
  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-secondary">
        Read-only inventory of named evidence types the runtime knows about.
        Auto-derived from policies — each row shows who emits/consumes the
        ref. To add a new evidence ref, author a policy that emits it (or
        a Custom Rule that references one); it will appear here
        automatically.
      </p>
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
              className="rounded-xl border border-black/[0.06] bg-white px-4 py-3"
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
    </div>
  );
}
