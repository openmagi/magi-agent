"use client";

/**
 * Guided policy wizard — toss-style step-by-step authoring (PR-E2).
 *
 * Mirrors control-plane's Guided flow: one decision per screen, big
 * primary action, persistent progress dots, "← Pick different" + Back
 * affordances. The user never sees raw JSON / IR until the Review step;
 * the final draft activates via the same PUT path the Raw mode uses,
 * so persistence behavior is byte-identical between the two modes.
 *
 * PR-E2 ships only the BLOCK-BAD-ANSWER flow (deterministic_ref kind)
 * because it is the most common shape and mirrors control-plane Image
 * 12-17 closely enough to validate the wizard chrome. The other three
 * kinds (tool_perm / custom_check / seam_spec) stay on the Raw mode
 * picker; PR-E3 adds wizards for them on top of the same chrome.
 */

import React, { useMemo, useState } from "react";

import {
  putCustomRule,
  type CustomRule,
  type CustomizeCatalog,
} from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";
import type { EvidenceTypeEntry } from "@/lib/policy-model";


export interface GuidedWizardProps {
  catalog: CustomizeCatalog;
  evidenceTypes: EvidenceTypeEntry[];
  onActivated: () => void;
  onPickDifferent: () => void;
  onCancel: () => void;
}


type Scope = "always" | "coding" | "research" | "delivery" | "memory" | "task";
type Action = "block" | "ask" | "audit";


interface DraftState {
  scope: Scope;
  evidenceRef: string;     // ref selected from the catalog
  onMissing: Action;
  ruleId: string;
  description: string;
}


const EMPTY_DRAFT: DraftState = {
  scope: "coding",
  evidenceRef: "",
  onMissing: "block",
  ruleId: "",
  description: "",
};


const STEPS = [
  "When?",
  "What evidence must pass?",
  "What happens if missing?",
  "Name your policy",
  "Review",
] as const;


export function GuidedWizard({
  catalog,
  evidenceTypes,
  onActivated,
  onPickDifferent,
  onCancel,
}: GuidedWizardProps): React.ReactElement {
  const agentFetch = useAgentFetch();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<DraftState>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Combine the catalog's customRuleMenu refs (producer-backed evidence)
  // with the dynamic evidenceTypes derived from existing policies so the
  // user sees both built-in and reusable user-emitted refs.
  const refOptions = useMemo(() => buildRefOptions(catalog, evidenceTypes), [
    catalog,
    evidenceTypes,
  ]);

  const handleNext = () => setStep((s) => Math.min(s + 1, STEPS.length - 1));
  const handleBack = () => setStep((s) => Math.max(s - 1, 0));

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const rule = buildRule(draft);
      await putCustomRule(agentFetch, rule);
      onActivated();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const canAdvance = stepIsComplete(step, draft);

  return (
    <section
      aria-label="Guided policy wizard"
      className="space-y-4 rounded-2xl border border-primary/20 bg-primary/[0.02] p-5 shadow-sm"
    >
      <WizardHeader
        step={step}
        total={STEPS.length}
        onPickDifferent={onPickDifferent}
      />

      <div className="min-h-[280px]">
        {step === 0 ? <ScopeStep draft={draft} setDraft={setDraft} /> : null}
        {step === 1 ? (
          <EvidenceStep
            draft={draft}
            setDraft={setDraft}
            options={refOptions}
          />
        ) : null}
        {step === 2 ? <OnMissingStep draft={draft} setDraft={setDraft} /> : null}
        {step === 3 ? <NameStep draft={draft} setDraft={setDraft} /> : null}
        {step === 4 ? <ReviewStep draft={draft} refOptions={refOptions} /> : null}
      </div>

      {saveError ? (
        <p className="rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-xs text-red-700">
          {saveError}
        </p>
      ) : null}

      <div className="flex items-center justify-between">
        {step > 0 ? (
          <button
            type="button"
            onClick={handleBack}
            className="rounded-lg px-3 py-1.5 text-xs font-medium text-secondary hover:bg-black/[0.04] hover:text-foreground"
          >
            ← Back
          </button>
        ) : (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg px-3 py-1.5 text-xs font-medium text-secondary hover:bg-black/[0.04] hover:text-foreground"
          >
            Cancel
          </button>
        )}
        {step === STEPS.length - 1 ? (
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !canAdvance}
            className="inline-flex items-center rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save policy"}
          </button>
        ) : (
          <button
            type="button"
            onClick={handleNext}
            disabled={!canAdvance}
            className="inline-flex items-center rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Next →
          </button>
        )}
      </div>
    </section>
  );
}


// ---------------------------------------------------------------------------
// Header + progress dots
// ---------------------------------------------------------------------------


function WizardHeader({
  step,
  total,
  onPickDifferent,
}: {
  step: number;
  total: number;
  onPickDifferent: () => void;
}): React.ReactElement {
  return (
    <header className="flex items-center justify-between gap-3">
      <button
        type="button"
        onClick={onPickDifferent}
        className="rounded-lg px-2 py-1 text-[11px] font-medium text-secondary hover:bg-black/[0.04] hover:text-foreground"
      >
        ← Pick different
      </button>
      <div
        role="progressbar"
        aria-valuenow={step + 1}
        aria-valuemax={total}
        aria-label="Wizard progress"
        className="flex items-center gap-1.5"
      >
        {Array.from({ length: total }).map((_, i) => (
          <span
            key={i}
            className={`h-1.5 rounded-full transition-all ${
              i === step
                ? "w-6 bg-primary"
                : i < step
                  ? "w-1.5 bg-primary/60"
                  : "w-1.5 bg-black/[0.10]"
            }`}
          />
        ))}
        <span className="ml-2 text-[11px] font-medium text-secondary">
          {step + 1} / {total}
        </span>
      </div>
    </header>
  );
}


// ---------------------------------------------------------------------------
// Step 1 — scope (when does this policy fire?)
// ---------------------------------------------------------------------------


const SCOPE_OPTIONS: ReadonlyArray<{ id: Scope; label: string; description: string; recommended?: boolean }> = [
  { id: "coding", label: "Coding turns", description: "Fire on turns where the agent is writing or modifying code.", recommended: true },
  { id: "always", label: "Every turn", description: "Fire on every turn regardless of scope." },
  { id: "research", label: "Research turns", description: "Fire on turns where the agent is fetching or citing sources." },
  { id: "delivery", label: "Delivery turns", description: "Fire on turns where the agent is producing a final deliverable." },
];


function ScopeStep({
  draft,
  setDraft,
}: { draft: DraftState; setDraft: React.Dispatch<React.SetStateAction<DraftState>> }): React.ReactElement {
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
// Step 2 — evidence ref (which check must pass?)
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
  // Add user evidence types not already covered by the producer-backed menu.
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
  draft: DraftState;
  setDraft: React.Dispatch<React.SetStateAction<DraftState>>;
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
          No evidence refs available in this runtime. Author a policy with
          an evidence ref in Raw mode first, or expose more
          producer-backed refs in the catalog.
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


const ACTION_OPTIONS: ReadonlyArray<{ id: Action; label: string; description: string; recommended?: boolean }> = [
  { id: "block", label: "Block (deny)", description: "Reject the final answer and show a deny message — safest.", recommended: true },
  { id: "ask", label: "Ask the user", description: "Prompt the user for approval before letting the answer through." },
  { id: "audit", label: "Log only (audit mode)", description: "Record the failure but let the answer through. Useful for observation." },
];


function OnMissingStep({
  draft,
  setDraft,
}: { draft: DraftState; setDraft: React.Dispatch<React.SetStateAction<DraftState>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">
        What happens if the check fails?
      </h2>
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
// Step 4 — name + description
// ---------------------------------------------------------------------------


function NameStep({
  draft,
  setDraft,
}: { draft: DraftState; setDraft: React.Dispatch<React.SetStateAction<DraftState>> }): React.ReactElement {
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
// Step 5 — review + save
// ---------------------------------------------------------------------------


function ReviewStep({
  draft,
  refOptions,
}: { draft: DraftState; refOptions: RefOption[] }): React.ReactElement {
  const ref = refOptions.find((o) => o.ref === draft.evidenceRef);
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
// Shared row primitive
// ---------------------------------------------------------------------------


function RadioCard({
  checked,
  onClick,
  label,
  description,
  badge,
  monoLabel,
}: {
  checked: boolean;
  onClick: () => void;
  label: string;
  description?: string;
  badge?: string;
  monoLabel?: string;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={checked}
      className={`flex w-full items-start justify-between gap-3 rounded-xl border px-4 py-3 text-left transition-colors ${
        checked
          ? "border-primary bg-primary/[0.04]"
          : "border-black/[0.08] bg-white hover:border-primary/40 hover:bg-primary/[0.02]"
      }`}
    >
      <div className="min-w-0">
        <p className="text-sm font-semibold text-foreground">{label}</p>
        {monoLabel ? (
          <p className="mt-0.5 text-[11px] font-mono text-secondary/80">{monoLabel}</p>
        ) : null}
        {description ? (
          <p className="mt-1 text-xs leading-relaxed text-secondary">{description}</p>
        ) : null}
      </div>
      {badge ? (
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
            badge === "recommended"
              ? "bg-emerald-500/10 text-emerald-700"
              : "bg-blue-500/10 text-blue-700"
          }`}
        >
          {badge}
        </span>
      ) : null}
    </button>
  );
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function stepIsComplete(step: number, draft: DraftState): boolean {
  if (step === 0) return !!draft.scope;
  if (step === 1) return !!draft.evidenceRef;
  if (step === 2) return !!draft.onMissing;
  if (step === 3) {
    return (
      /^[a-z0-9][a-z0-9_-]{0,127}$/.test(draft.ruleId)
    );
  }
  return true;
}


function buildRule(draft: DraftState): CustomRule {
  return {
    id: draft.ruleId,
    scope: draft.scope,
    enabled: true,
    firesAt: "pre_final",
    action: draft.onMissing === "audit" ? "audit" : draft.onMissing,
    what: {
      kind: "deterministic_ref",
      payload: { ref: draft.evidenceRef },
    },
  };
}
