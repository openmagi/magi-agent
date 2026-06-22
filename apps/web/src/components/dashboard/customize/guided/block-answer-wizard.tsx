"use client";

/**
 * Block-bad-answer Guided wizard — deterministic_ref kind.
 *
 * 5-step flow extracted from PR-E2's monolithic GuidedWizard. The chrome
 * (header / progress / nav buttons / RadioCard) lives in
 * ``wizard-chrome.tsx``; this sub-wizard owns step state + step bodies
 * + activation handler.
 */

import React, { useMemo, useState } from "react";

import {
  putCustomRule,
  type CustomRule,
  type CustomizeCatalog,
} from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";
import type { EvidenceTypeEntry } from "@/lib/policy-model";

import { RadioCard, WizardChrome } from "./wizard-chrome";


type Scope = "always" | "coding" | "research" | "delivery" | "memory" | "task";
type Action = "block" | "ask" | "audit";


interface Draft {
  scope: Scope;
  evidenceRef: string;
  onMissing: Action;
  ruleId: string;
  description: string;
}


const EMPTY: Draft = {
  scope: "coding",
  evidenceRef: "",
  onMissing: "block",
  ruleId: "",
  description: "",
};


const TOTAL = 5;


export interface BlockAnswerWizardProps {
  catalog: CustomizeCatalog;
  evidenceTypes: EvidenceTypeEntry[];
  onActivated: () => void;
  onPickDifferent: () => void;
  onCancel: () => void;
}


export function BlockAnswerWizard({
  catalog,
  evidenceTypes,
  onActivated,
  onPickDifferent,
  onCancel,
}: BlockAnswerWizardProps): React.ReactElement {
  const agentFetch = useAgentFetch();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const refOptions = useMemo(
    () => buildRefOptions(catalog, evidenceTypes),
    [catalog, evidenceTypes],
  );

  return (
    <WizardChrome
      step={step}
      total={TOTAL}
      onPickDifferent={onPickDifferent}
      onCancel={onCancel}
      onBack={() => setStep((s) => Math.max(s - 1, 0))}
      onNext={() => setStep((s) => Math.min(s + 1, TOTAL - 1))}
      onSave={async () => {
        setSaving(true);
        setSaveError(null);
        try {
          await putCustomRule(agentFetch, buildRule(draft));
          onActivated();
        } catch (err) {
          setSaveError(err instanceof Error ? err.message : "Save failed");
        } finally {
          setSaving(false);
        }
      }}
      canAdvance={stepIsComplete(step, draft)}
      saving={saving}
      error={saveError}
    >
      {step === 0 ? <ScopeStep draft={draft} setDraft={setDraft} /> : null}
      {step === 1 ? (
        <EvidenceStep draft={draft} setDraft={setDraft} options={refOptions} />
      ) : null}
      {step === 2 ? <OnMissingStep draft={draft} setDraft={setDraft} /> : null}
      {step === 3 ? <NameStep draft={draft} setDraft={setDraft} /> : null}
      {step === 4 ? <ReviewStep draft={draft} options={refOptions} /> : null}
    </WizardChrome>
  );
}


// ---------------------------------------------------------------------------
// Step 1 — scope
// ---------------------------------------------------------------------------


const SCOPE_OPTIONS: ReadonlyArray<{
  id: Scope;
  label: string;
  description: string;
  recommended?: boolean;
}> = [
  { id: "coding", label: "Coding turns", description: "Fire on turns where the agent is writing or modifying code.", recommended: true },
  { id: "always", label: "Every turn", description: "Fire on every turn regardless of scope." },
  { id: "research", label: "Research turns", description: "Fire on turns where the agent is fetching or citing sources." },
  { id: "delivery", label: "Delivery turns", description: "Fire on turns where the agent is producing a final deliverable." },
];


function ScopeStep({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">When should this policy fire?</h2>
      <p className="text-xs text-secondary">
        Pick the agent turns where this policy gates the final answer. The
        check runs at the pre-final moment for the selected scope.
      </p>
      <div className="space-y-2">
        {SCOPE_OPTIONS.map((opt) => (
          <RadioCard
            key={opt.id}
            checked={draft.scope === opt.id}
            onClick={() => setDraft((d) => ({ ...d, scope: opt.id }))}
            label={opt.label}
            description={opt.description}
            badge={opt.recommended ? "recommended" : undefined}
          />
        ))}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Step 2 — evidence ref
// ---------------------------------------------------------------------------


interface RefOption {
  ref: string;
  label: string;
  description: string;
  origin: "builtin" | "user";
}


function buildRefOptions(
  catalog: CustomizeCatalog,
  evidenceTypes: EvidenceTypeEntry[],
): RefOption[] {
  const out: RefOption[] = [];
  for (const item of catalog.verification.customRuleMenu) {
    out.push({
      ref: item.ref,
      label: item.label,
      description: `evidence: ${item.evidenceType} · tier: ${item.tier}`,
      origin: "builtin",
    });
  }
  const seen = new Set(out.map((o) => o.ref));
  for (const entry of evidenceTypes) {
    if (seen.has(entry.ref) || entry.ref.startsWith("preset:")) continue;
    out.push({
      ref: entry.ref,
      label: entry.label,
      description: `${entry.consumedBy.length} policy ref${entry.consumedBy.length === 1 ? "" : "s"}`,
      origin: entry.origin,
    });
  }
  return out.sort((a, b) => a.label.localeCompare(b.label));
}


function EvidenceStep({
  draft,
  setDraft,
  options,
}: {
  draft: Draft;
  setDraft: React.Dispatch<React.SetStateAction<Draft>>;
  options: RefOption[];
}): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">
        What evidence must pass before the answer?
      </h2>
      <p className="text-xs text-secondary">
        Pick the check that must return ok. Built-in items come from the
        runtime; "User" items are auto-derived from policies you have
        already authored.
      </p>
      {options.length === 0 ? (
        <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs text-secondary">
          No evidence refs available in this runtime.
        </p>
      ) : (
        <div className="space-y-2">
          {options.map((opt) => (
            <RadioCard
              key={opt.ref}
              checked={draft.evidenceRef === opt.ref}
              onClick={() => setDraft((d) => ({ ...d, evidenceRef: opt.ref }))}
              label={opt.label}
              description={opt.description}
              badge={opt.origin === "user" ? "user" : undefined}
              monoLabel={opt.ref}
            />
          ))}
        </div>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Step 3 — on-missing action
// ---------------------------------------------------------------------------


const ACTION_OPTIONS: ReadonlyArray<{
  id: Action;
  label: string;
  description: string;
  recommended?: boolean;
}> = [
  { id: "block", label: "Block (deny)", description: "Reject the final answer and show a deny message — safest.", recommended: true },
  { id: "ask", label: "Ask the user", description: "Prompt the user for approval before letting the answer through." },
  { id: "audit", label: "Log only (audit mode)", description: "Record the failure but let the answer through. Useful for observation." },
];


function OnMissingStep({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">What happens if the check fails?</h2>
      <p className="text-xs text-secondary">
        The action the gate takes when the evidence check does not return ok.
      </p>
      <div className="space-y-2">
        {ACTION_OPTIONS.map((opt) => (
          <RadioCard
            key={opt.id}
            checked={draft.onMissing === opt.id}
            onClick={() => setDraft((d) => ({ ...d, onMissing: opt.id }))}
            label={opt.label}
            description={opt.description}
            badge={opt.recommended ? "recommended" : undefined}
          />
        ))}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Step 4 — name
// ---------------------------------------------------------------------------


function NameStep({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Name your policy</h2>
      <p className="text-xs text-secondary">
        Shown in the policies list and audit logs.
      </p>
      <label className="block">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Policy ID
        </span>
        <input
          type="text"
          value={draft.ruleId}
          onChange={(e) => setDraft((d) => ({ ...d, ruleId: e.target.value }))}
          placeholder="block-on-missing-tests"
          className="mt-1 w-full rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          aria-label="Policy ID"
        />
        <p className="mt-1 text-[11px] text-secondary">
          Alphanumeric + dash / underscore, max 128 chars.
        </p>
      </label>
      <label className="block">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Description (optional)
        </span>
        <input
          type="text"
          value={draft.description}
          onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
          placeholder="Block answers on coding turns when tests have not run."
          className="mt-1 w-full rounded-lg border border-black/[0.10] bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          aria-label="Policy description"
        />
      </label>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Step 5 — review
// ---------------------------------------------------------------------------


function ReviewStep({
  draft,
  options,
}: { draft: Draft; options: RefOption[] }): React.ReactElement {
  const ref = options.find((o) => o.ref === draft.evidenceRef);
  const sentence = `On ${draft.scope === "always" ? "every" : draft.scope} turn, ${draft.onMissing === "block" ? "block the final answer" : draft.onMissing === "ask" ? "require human approval" : "audit-log the turn"} when ${ref?.label ?? draft.evidenceRef} does not return ok.`;
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Review</h2>
      <p className="text-xs text-secondary">
        Saving applies the policy to the runtime immediately.
      </p>
      <div className="rounded-xl border border-black/[0.06] bg-white p-4">
        <p className="text-sm font-semibold text-foreground">What this policy does</p>
        <p className="mt-1 text-xs leading-relaxed text-foreground">{sentence}</p>
        <hr className="my-3 border-black/[0.05]" />
        <dl className="grid grid-cols-[6rem_1fr] gap-y-1.5 text-xs">
          <dt className="text-secondary">ID</dt>
          <dd className="font-mono text-foreground">{draft.ruleId || "(unnamed)"}</dd>
          <dt className="text-secondary">When</dt>
          <dd>{draft.scope} · pre-final</dd>
          <dt className="text-secondary">Requires</dt>
          <dd className="font-mono text-foreground">{draft.evidenceRef}</dd>
          <dt className="text-secondary">On missing</dt>
          <dd>{draft.onMissing}</dd>
          {draft.description ? (
            <>
              <dt className="text-secondary">Note</dt>
              <dd className="text-foreground">{draft.description}</dd>
            </>
          ) : null}
        </dl>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function stepIsComplete(step: number, draft: Draft): boolean {
  if (step === 0) return !!draft.scope;
  if (step === 1) return !!draft.evidenceRef;
  if (step === 2) return !!draft.onMissing;
  if (step === 3) return /^[a-z0-9][a-z0-9_-]{0,127}$/.test(draft.ruleId);
  return true;
}


function buildRule(draft: Draft): CustomRule {
  return {
    id: draft.ruleId,
    scope: draft.scope,
    enabled: true,
    firesAt: "pre_final",
    action: draft.onMissing === "audit" ? "audit" : draft.onMissing,
    what: { kind: "deterministic_ref", payload: { ref: draft.evidenceRef } },
  };
}
