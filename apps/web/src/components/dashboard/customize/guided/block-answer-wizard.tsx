"use client";

/**
 * Block-bad-answer Guided wizard — supports all three pre-final check
 * kinds (PR-E4 audit fix).
 *
 * PR-E2 originally only emitted ``deterministic_ref`` rules; the audit
 * called out that LLM-critic and SHACL-shape policies had NO Guided
 * path. This refactor adds a check-kind picker step + branches the
 * payload step so a single "Block bad answer" intent covers all three.
 *
 * Steps:
 *   1. When?                   (scope)
 *   2. How should it be judged? (check kind: evidence_ref / shacl / llm)
 *   3. Check definition         (branches by kind)
 *   4. What if it fails?        (block / ask / audit)
 *   5. Name your policy
 *   6. Review
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
type CheckKind = "evidence_ref" | "shacl_constraint" | "llm_criterion";


interface Draft {
  scope: Scope;
  checkKind: CheckKind;
  evidenceRef: string;
  shapeTtl: string;
  criterion: string;
  onMissing: Action;
  ruleId: string;
  description: string;
}


const EMPTY: Draft = {
  scope: "coding",
  checkKind: "evidence_ref",
  evidenceRef: "",
  shapeTtl: "",
  criterion: "",
  onMissing: "block",
  ruleId: "",
  description: "",
};


const TOTAL = 6;


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
      {step === 1 ? <CheckKindStep draft={draft} setDraft={setDraft} /> : null}
      {step === 2 ? (
        <CheckDefinitionStep
          draft={draft}
          setDraft={setDraft}
          refOptions={refOptions}
        />
      ) : null}
      {step === 3 ? <OnMissingStep draft={draft} setDraft={setDraft} /> : null}
      {step === 4 ? <NameStep draft={draft} setDraft={setDraft} /> : null}
      {step === 5 ? <ReviewStep draft={draft} refOptions={refOptions} /> : null}
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
        Pick the agent turns where this policy gates the final answer.
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
// Step 2 — check kind (PR-E4 new)
// ---------------------------------------------------------------------------


const CHECK_KIND_OPTIONS: ReadonlyArray<{
  id: CheckKind;
  label: string;
  description: string;
  recommended?: boolean;
}> = [
  {
    id: "evidence_ref",
    label: "Evidence reference",
    description:
      "Block when a known evidence ref does NOT pass this turn. Deterministic, cheapest, recommended when there is a built-in producer for the signal you care about.",
    recommended: true,
  },
  {
    id: "shacl_constraint",
    label: "SHACL shape",
    description:
      "Block when an evidence record does not conform to a structural shape you author. Deterministic, no LLM call.",
  },
  {
    id: "llm_criterion",
    label: "LLM critic",
    description:
      "Block when an LLM judge decides the answer fails a free-text criterion. Adds an LLM call at gate time.",
  },
];


function CheckKindStep({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">
        How should the answer be judged?
      </h2>
      <p className="text-xs text-secondary">
        Pick the kind of check this policy uses. Different kinds have
        different cost and determinism trade-offs.
      </p>
      <div className="space-y-2">
        {CHECK_KIND_OPTIONS.map((opt) => (
          <RadioCard
            key={opt.id}
            checked={draft.checkKind === opt.id}
            onClick={() => setDraft((d) => ({ ...d, checkKind: opt.id }))}
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
// Step 3 — check definition (branches by checkKind)
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


function CheckDefinitionStep({
  draft,
  setDraft,
  refOptions,
}: {
  draft: Draft;
  setDraft: React.Dispatch<React.SetStateAction<Draft>>;
  refOptions: RefOption[];
}): React.ReactElement {
  if (draft.checkKind === "evidence_ref") {
    return <EvidenceRefBody draft={draft} setDraft={setDraft} options={refOptions} />;
  }
  if (draft.checkKind === "shacl_constraint") {
    return <ShaclBody draft={draft} setDraft={setDraft} />;
  }
  return <LlmCriterionBody draft={draft} setDraft={setDraft} />;
}


function EvidenceRefBody({
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
        Which evidence ref must pass?
      </h2>
      <p className="text-xs text-secondary">
        Built-in items come from the runtime; "User" items are auto-derived
        from policies you already authored.
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


function ShaclBody({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">
        What SHACL shape should the evidence conform to?
      </h2>
      <p className="text-xs text-secondary">
        Paste a SHACL Turtle (.ttl) shape that targets <code>magi:Evidence</code>.
        For NL → SHACL compilation, use the Natural language authoring
        mode instead — that path runs the NL compiler + reviewer for you.
      </p>
      <textarea
        value={draft.shapeTtl}
        onChange={(e) => setDraft((d) => ({ ...d, shapeTtl: e.target.value }))}
        rows={10}
        placeholder={SHACL_PLACEHOLDER}
        aria-label="SHACL shape (.ttl)"
        className="w-full resize-y rounded-lg border border-primary/30 bg-white px-3 py-2 text-xs font-mono text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
      />
    </div>
  );
}


const SHACL_PLACEHOLDER = `@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .

magi:MyShape
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:property [
        sh:path magi:field_exitCode ;
        sh:hasValue 0 ;
    ] .`;


function LlmCriterionBody({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">
        What should the LLM judge check?
      </h2>
      <p className="text-xs text-secondary">
        Write the criterion as a single sentence the critic LLM evaluates
        against the agent's final answer + turn context.
      </p>
      <input
        type="text"
        value={draft.criterion}
        onChange={(e) => setDraft((d) => ({ ...d, criterion: e.target.value }))}
        placeholder="The answer cites at least one source."
        aria-label="LLM criterion"
        className="w-full rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
      />
      <p className="text-[11px] text-secondary">
        Fail = critic answers NO. Keep it concrete and verifiable from the
        turn alone.
      </p>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Step 4 — on-missing action
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
      <h2 className="text-lg font-bold text-foreground">
        What happens if the check fails?
      </h2>
      <p className="text-xs text-secondary">
        The action the gate takes when the check does not return ok.
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
// Step 5 — name
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
          aria-label="Policy ID"
          className="mt-1 w-full rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
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
          aria-label="Policy description"
          className="mt-1 w-full rounded-lg border border-black/[0.10] bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </label>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Step 6 — review
// ---------------------------------------------------------------------------


function ReviewStep({
  draft,
  refOptions,
}: { draft: Draft; refOptions: RefOption[] }): React.ReactElement {
  const verb =
    draft.onMissing === "block"
      ? "block the final answer"
      : draft.onMissing === "ask"
        ? "require human approval"
        : "audit-log the turn";
  const checkSummary = describeCheck(draft, refOptions);
  const whenClause = draft.scope === "always" ? "Every turn" : `On ${draft.scope} turns`;
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Review</h2>
      <p className="text-xs text-secondary">
        Saving applies the policy to the runtime immediately.
      </p>
      <div className="rounded-xl border border-black/[0.06] bg-white p-4">
        <p className="text-sm font-semibold text-foreground">What this policy does</p>
        <p className="mt-1 text-xs leading-relaxed text-foreground">
          {whenClause}, {verb} when {checkSummary}.
        </p>
        <hr className="my-3 border-black/[0.05]" />
        <dl className="grid grid-cols-[7rem_1fr] gap-y-1.5 text-xs">
          <dt className="text-secondary">ID</dt>
          <dd className="font-mono text-foreground">{draft.ruleId || "(unnamed)"}</dd>
          <dt className="text-secondary">When</dt>
          <dd>{draft.scope} · pre-final</dd>
          <dt className="text-secondary">Check kind</dt>
          <dd>{draft.checkKind}</dd>
          <dt className="text-secondary">On fail</dt>
          <dd>{draft.onMissing}</dd>
          {draft.description ? (
            <>
              <dt className="text-secondary">Note</dt>
              <dd>{draft.description}</dd>
            </>
          ) : null}
        </dl>
      </div>
    </div>
  );
}


function describeCheck(draft: Draft, refOptions: RefOption[]): string {
  if (draft.checkKind === "evidence_ref") {
    const ref = refOptions.find((o) => o.ref === draft.evidenceRef);
    return `evidence "${ref?.label ?? draft.evidenceRef}" does not return ok`;
  }
  if (draft.checkKind === "shacl_constraint") {
    return "the SHACL shape does NOT conform on any evidence record";
  }
  return `an LLM critic judges "${draft.criterion}" is false`;
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function stepIsComplete(step: number, draft: Draft): boolean {
  if (step === 0) return !!draft.scope;
  if (step === 1) return !!draft.checkKind;
  if (step === 2) {
    if (draft.checkKind === "evidence_ref") return !!draft.evidenceRef;
    if (draft.checkKind === "shacl_constraint") return draft.shapeTtl.trim().length > 0;
    return draft.criterion.trim().length > 0;
  }
  if (step === 3) return !!draft.onMissing;
  if (step === 4) return /^[a-z0-9][a-z0-9_-]{0,127}$/.test(draft.ruleId);
  return true;
}


function buildRule(draft: Draft): CustomRule {
  const base = {
    id: draft.ruleId,
    scope: draft.scope,
    enabled: true,
    firesAt: "pre_final",
    action: draft.onMissing === "audit" ? "audit" : draft.onMissing,
  };
  if (draft.checkKind === "evidence_ref") {
    return {
      ...base,
      what: { kind: "deterministic_ref", payload: { ref: draft.evidenceRef } },
    };
  }
  if (draft.checkKind === "shacl_constraint") {
    return {
      ...base,
      what: { kind: "shacl_constraint", payload: { shapeTtl: draft.shapeTtl.trim() } },
    };
  }
  return {
    ...base,
    what: { kind: "llm_criterion", payload: { criterion: draft.criterion.trim() } },
  };
}
