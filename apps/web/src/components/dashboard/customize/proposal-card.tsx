"use client";

/**
 * ProposalCard — PR-F-UX6 — renders the architect's proposed primitive
 * (or hybrid composition) with per-primitive trust badges, rationales,
 * and a single "Activate" affordance.
 *
 * mode="single" → one primitive card.
 * mode="hybrid" → N primitive cards stacked + a "composed as one policy"
 *                 header + explanation. Activate dispatches N saves sharing
 *                 a logical groupId (generated client-side on activate so
 *                 the operator never types it).
 */

import React from "react";

import type {
  ArchitectPrimitive,
  ArchitectProposal,
  ArchitectTrustClass,
} from "@/lib/customize-api";

import { TrustBadge, type TrustClass } from "./trust-badge";


export interface ProposalCardProps {
  proposal: ArchitectProposal;
  /** Activate the proposal. Implementation in the parent dispatches N
   *  putCustomRule (or putSeamSpec / putDashboardCheck) calls, sharing a
   *  groupId for hybrid composition. */
  onActivate: () => void;
  /** "Refine" sends a follow-up architect turn so the operator can tighten
   *  the proposal without dropping back to the wizard. */
  onRefine: () => void;
  /** "Author manually instead" drops to the guided wizard with whatever
   *  intent state was inferred. */
  onAuthorManually: () => void;
  busy?: boolean;
  errorText?: string | null;
}


export function ProposalCard({
  proposal,
  onActivate,
  onRefine,
  onAuthorManually,
  busy,
  errorText,
}: ProposalCardProps): React.ReactElement {
  const isHybrid = proposal.mode === "hybrid";
  return (
    <section
      aria-label="Architect proposal"
      className="space-y-3 rounded-2xl border border-emerald-200 bg-emerald-50/40 p-4"
    >
      <header className="space-y-1">
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-emerald-700">
          Proposed policy {isHybrid ? "(hybrid composition)" : "(single primitive)"}
        </p>
        <p className="text-sm font-bold text-foreground">{proposal.summary}</p>
        {isHybrid ? (
          <p className="text-[11px] leading-relaxed text-emerald-900/80">
            Composed as one logical policy. The deterministic primitive
            narrows what the advisory primitive sees, keeping the critic call
            cheap.
          </p>
        ) : null}
      </header>

      <div className="space-y-2">
        {proposal.primitives.map((primitive, idx) => (
          <PrimitiveCard
            key={`${primitive.kind}-${idx}`}
            primitive={primitive}
            position={idx + 1}
            total={proposal.primitives.length}
          />
        ))}
      </div>

      {proposal.explanation ? (
        <div>
          <p className="text-xs font-semibold text-foreground">Why this shape</p>
          <p className="mt-1 text-xs leading-relaxed text-foreground">
            {proposal.explanation}
          </p>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button
          type="button"
          disabled={busy}
          onClick={onActivate}
          className="inline-flex items-center rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Activating…" : "Activate"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onRefine}
          className="inline-flex items-center rounded-lg border border-black/[0.08] bg-white px-3 py-1.5 text-xs font-medium text-secondary hover:bg-black/[0.03] disabled:cursor-not-allowed disabled:opacity-50"
        >
          Refine
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onAuthorManually}
          className="text-[11px] font-medium text-secondary underline underline-offset-2 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        >
          Author manually instead
        </button>
      </div>

      {errorText ? (
        <p className="text-xs leading-relaxed text-red-700">{errorText}</p>
      ) : null}
    </section>
  );
}


function PrimitiveCard({
  primitive,
  position,
  total,
}: {
  primitive: ArchitectPrimitive;
  position: number;
  total: number;
}): React.ReactElement {
  return (
    <article className="rounded-xl border border-black/[0.06] bg-white px-3 py-2">
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-black/[0.05] px-2 py-0.5 font-mono text-[10px] text-secondary">
            {primitive.kind}
          </span>
          <TrustBadge trustClass={mapArchitectTrust(primitive.trustClass)} />
        </div>
        {total > 1 ? (
          <span className="text-[10px] text-secondary/70">
            {position} of {total}
          </span>
        ) : null}
      </header>
      {primitive.rationale ? (
        <p className="mt-1 text-xs leading-relaxed text-foreground">
          {primitive.rationale}
        </p>
      ) : null}
      <details className="mt-2 rounded-lg bg-gray-50/80 p-2">
        <summary className="cursor-pointer text-[11px] font-medium text-secondary">
          View payload
        </summary>
        <pre className="mt-2 max-h-60 overflow-auto rounded-lg bg-white p-2 text-[11px] leading-relaxed text-foreground">
          {JSON.stringify(primitive.payload, null, 2)}
        </pre>
      </details>
    </article>
  );
}


function mapArchitectTrust(t: ArchitectTrustClass): TrustClass {
  // The architect vocabulary is a subset of the dashboard TrustClass
  // vocabulary (no "hybrid" / "preview" at the primitive level — those
  // are policy-level buckets). Pass-through.
  return t;
}
