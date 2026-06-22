"use client";

/**
 * Restrict-tool Guided wizard — tool_perm kind (before-tool gate).
 *
 * Mirrors control-plane's Guided Image 12-17 example most closely:
 * a 5-step flow that produces a CustomRule with kind=tool_perm,
 * firesAt=before_tool_use, action=block|ask_approval.
 *
 * Steps:
 *   1. When?  (scope)
 *   2. Match target  (tool name / domain / domain allowlist)
 *   3. Action  (deny / require approval)
 *   4. Name
 *   5. Review
 */

import React, { useState } from "react";

import { putCustomRule, type CustomRule } from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";

import { RadioCard, WizardChrome } from "./wizard-chrome";


type Scope = "always" | "coding" | "research" | "delivery" | "memory" | "task";
type MatchType = "tool" | "domain" | "domainAllowlist";
type Decision = "deny" | "ask";


interface Draft {
  scope: Scope;
  matchType: MatchType;
  matchValue: string;
  decision: Decision;
  ruleId: string;
  description: string;
}


const EMPTY: Draft = {
  scope: "always",
  matchType: "tool",
  matchValue: "",
  decision: "deny",
  ruleId: "",
  description: "",
};


const TOTAL = 5;


export interface RestrictToolWizardProps {
  onActivated: () => void;
  onPickDifferent: () => void;
  onCancel: () => void;
}


export function RestrictToolWizard({
  onActivated,
  onPickDifferent,
  onCancel,
}: RestrictToolWizardProps): React.ReactElement {
  const agentFetch = useAgentFetch();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

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
      {step === 1 ? <MatchStep draft={draft} setDraft={setDraft} /> : null}
      {step === 2 ? <ActionStep draft={draft} setDraft={setDraft} /> : null}
      {step === 3 ? <NameStep draft={draft} setDraft={setDraft} /> : null}
      {step === 4 ? <ReviewStep draft={draft} /> : null}
    </WizardChrome>
  );
}


const SCOPE_OPTIONS: ReadonlyArray<{ id: Scope; label: string; description: string; recommended?: boolean }> = [
  { id: "always", label: "Every turn", description: "Apply this restriction regardless of what the agent is doing.", recommended: true },
  { id: "coding", label: "Coding turns", description: "Restrict only on coding turns." },
  { id: "research", label: "Research turns", description: "Restrict only on research turns." },
  { id: "delivery", label: "Delivery turns", description: "Restrict only on delivery turns." },
];


function ScopeStep({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">When should this restriction apply?</h2>
      <p className="text-xs text-secondary">
        The restriction fires before the agent invokes the tool. Pick the
        turn scopes where the gate is active.
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


const MATCH_OPTIONS: ReadonlyArray<{
  id: MatchType;
  label: string;
  hint: string;
  placeholder: string;
}> = [
  { id: "tool", label: "Tool name", hint: "Match a specific tool by name (e.g. shell_exec).", placeholder: "shell_exec" },
  { id: "domain", label: "Fetch domain", hint: "Match a fetch to an exact domain.", placeholder: "example.com" },
  { id: "domainAllowlist", label: "Domain allowlist", hint: "Restrict fetches to a comma-separated allowlist; everything else is matched.", placeholder: "github.com, openmagi.ai" },
];


function MatchStep({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  const placeholder = MATCH_OPTIONS.find((o) => o.id === draft.matchType)?.placeholder ?? "";
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">What should be restricted?</h2>
      <p className="text-xs text-secondary">
        Pick how the gate identifies the call to restrict.
      </p>
      <div className="space-y-2">
        {MATCH_OPTIONS.map((opt) => (
          <RadioCard
            key={opt.id}
            checked={draft.matchType === opt.id}
            onClick={() => setDraft((d) => ({ ...d, matchType: opt.id, matchValue: "" }))}
            label={opt.label}
            description={opt.hint}
          />
        ))}
      </div>
      <label className="block pt-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Value
        </span>
        <input
          type="text"
          value={draft.matchValue}
          onChange={(e) => setDraft((d) => ({ ...d, matchValue: e.target.value }))}
          placeholder={placeholder}
          aria-label="Match value"
          className="mt-1 w-full rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </label>
    </div>
  );
}


const DECISION_OPTIONS: ReadonlyArray<{ id: Decision; label: string; description: string; recommended?: boolean }> = [
  { id: "deny", label: "Deny", description: "Block the tool call outright — safest.", recommended: true },
  { id: "ask", label: "Require human approval", description: "Prompt the user; the call proceeds only on approve." },
];


function ActionStep({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">What should happen when matched?</h2>
      <p className="text-xs text-secondary">
        The gate's response when the call matches your target above.
      </p>
      <div className="space-y-2">
        {DECISION_OPTIONS.map((opt) => (
          <RadioCard
            key={opt.id}
            checked={draft.decision === opt.id}
            onClick={() => setDraft((d) => ({ ...d, decision: opt.id }))}
            label={opt.label}
            description={opt.description}
            badge={opt.recommended ? "recommended" : undefined}
          />
        ))}
      </div>
    </div>
  );
}


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
          placeholder="deny-shell-exec"
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


function ReviewStep({ draft }: { draft: Draft }): React.ReactElement {
  const verb = draft.decision === "deny" ? "deny" : "require human approval for";
  const target = describeTarget(draft);
  const whenClause = draft.scope === "always" ? "Before any tool call" : `On ${draft.scope} turns, before any tool call`;
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Review</h2>
      <p className="text-xs text-secondary">
        Saving applies the policy to the runtime immediately.
      </p>
      <div className="rounded-xl border border-black/[0.06] bg-white p-4">
        <p className="text-sm font-semibold text-foreground">What this policy does</p>
        <p className="mt-1 text-xs leading-relaxed text-foreground">
          {whenClause}, {verb} {target}.
        </p>
        <hr className="my-3 border-black/[0.05]" />
        <dl className="grid grid-cols-[7rem_1fr] gap-y-1.5 text-xs">
          <dt className="text-secondary">ID</dt>
          <dd className="font-mono text-foreground">{draft.ruleId || "(unnamed)"}</dd>
          <dt className="text-secondary">When</dt>
          <dd>{draft.scope} · before-tool</dd>
          <dt className="text-secondary">Match</dt>
          <dd className="font-mono text-foreground">{draft.matchType}: {draft.matchValue}</dd>
          <dt className="text-secondary">Decision</dt>
          <dd>{draft.decision}</dd>
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


function describeTarget(draft: Draft): string {
  if (draft.matchType === "tool") return `the tool "${draft.matchValue}"`;
  if (draft.matchType === "domain") return `any fetch to ${draft.matchValue}`;
  return `any fetch outside [${draft.matchValue.split(",").map((s) => s.trim()).filter(Boolean).join(", ")}]`;
}


function stepIsComplete(step: number, draft: Draft): boolean {
  if (step === 0) return !!draft.scope;
  if (step === 1) return draft.matchValue.trim().length > 0;
  if (step === 2) return !!draft.decision;
  if (step === 3) return /^[a-z0-9][a-z0-9_-]{0,127}$/.test(draft.ruleId);
  return true;
}


function buildRule(draft: Draft): CustomRule {
  const trimmed = draft.matchValue.trim();
  let match: Record<string, unknown>;
  if (draft.matchType === "tool") match = { tool: trimmed };
  else if (draft.matchType === "domain") match = { domain: trimmed };
  else
    match = {
      domainAllowlist: trimmed
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    };
  return {
    id: draft.ruleId,
    scope: draft.scope,
    enabled: true,
    firesAt: "before_tool_use",
    action: draft.decision === "ask" ? "ask_approval" : "block",
    what: { kind: "tool_perm", payload: { match, decision: draft.decision } },
  };
}
