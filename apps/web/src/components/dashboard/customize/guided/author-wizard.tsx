"use client";

/**
 * Unified Author wizard (PR-E5) — single 6-step flow that covers all
 * policy authoring shapes the runtime currently supports.
 *
 * Replaces the four PR-E3 sub-wizards. Each step's options are
 * dynamically filtered by prior step choices so the user never sees an
 * unsupported combination, and the wizard internally picks the right
 * backend primitive (CustomRule kind / DashboardCheck) on Save.
 *
 * Steps
 * -----
 *   1. When?         (trigger = lifecycle + scope, two radios in one screen)
 *   2. What to do?   (action archetype, filtered by lifecycle)
 *   3. Condition kind (filtered by lifecycle + archetype; skipped for emit)
 *   4. Specifics      (form per condition kind)
 *   5. Name           (id + optional description)
 *   6. Review         (auto-built English sentence + dl)
 *
 * Routing
 * -------
 * The save handler maps (lifecycle, archetype, condition) → primitive:
 *
 *   (before_tool, block/ask, tool|domain|allowlist) → putCustomRule {kind:tool_perm}
 *   (after_tool, audit, regex)                       → putDashboardCheck
 *   (after_tool, strip|audit, llm)                   → putCustomRule {kind:llm_criterion, firesAt:after_tool_use, action:override|audit}
 *   (pre_final, block/ask/audit, evidence_ref)       → putCustomRule {kind:deterministic_ref}
 *   (pre_final, block/ask/audit, shacl)              → putCustomRule {kind:shacl_constraint}
 *   (pre_final, block/ask/audit, llm)                → putCustomRule {kind:llm_criterion}
 */

import {
  Ban,
  Filter,
  HelpCircle,
  Megaphone,
  ShieldOff,
} from "lucide-react";
import React, { useMemo, useState } from "react";

import {
  putCustomRule,
  type CustomRule,
  type CustomizeCatalog,
} from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";
import {
  putDashboardCheck,
  type DashboardCheck,
  type DashboardScope,
} from "@/lib/packs-dashboard-api";
import type { EvidenceTypeEntry } from "@/lib/policy-model";

import { RadioCard, WizardChrome } from "./wizard-chrome";


// ---------------------------------------------------------------------------
// Domain
// ---------------------------------------------------------------------------


type Lifecycle = "before_tool_use" | "after_tool_use" | "pre_final";
type Scope = "always" | "coding" | "research" | "delivery" | "memory" | "task";
type Archetype = "block" | "ask" | "audit" | "strip" | "emit";
type ConditionKind =
  | "tool_name"
  | "domain"
  | "domain_allowlist"
  | "evidence_ref"
  | "shacl"
  | "llm_criterion"
  | "regex"
  | "none"; // archetype=emit


interface Draft {
  lifecycle: Lifecycle;
  scope: Scope;
  archetype: Archetype;
  conditionKind: ConditionKind;
  // payload fields
  toolName: string;
  domain: string;
  domainAllowlist: string;
  evidenceRef: string;
  shapeTtl: string;
  criterion: string;
  regexPattern: string;
  regexIsRegex: boolean;
  // common
  ruleId: string;
  description: string;
}


const EMPTY: Draft = {
  lifecycle: "pre_final",
  scope: "coding",
  archetype: "block",
  conditionKind: "evidence_ref",
  toolName: "",
  domain: "",
  domainAllowlist: "",
  evidenceRef: "",
  shapeTtl: "",
  criterion: "",
  regexPattern: "",
  regexIsRegex: false,
  ruleId: "",
  description: "",
};


const TOTAL = 6;


export interface AuthorWizardProps {
  catalog: CustomizeCatalog;
  evidenceTypes: EvidenceTypeEntry[];
  onActivated: () => void;
  onCancel: () => void;
}


export function AuthorWizard({
  catalog,
  evidenceTypes,
  onActivated,
  onCancel,
}: AuthorWizardProps): React.ReactElement {
  const agentFetch = useAgentFetch();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Re-validate downstream fields when an upstream axis changes. Without
  // this, going back to step 1 and switching lifecycle from pre_final to
  // before_tool_use would leave archetype="audit" + conditionKind="shacl"
  // which is no longer a valid combination.
  const reseedDownstream = (next: Partial<Draft>): Draft => {
    const merged = { ...draft, ...next };
    if ("lifecycle" in next) {
      const archetypes = availableArchetypes(merged.lifecycle);
      if (!archetypes.includes(merged.archetype)) {
        merged.archetype = archetypes[0];
      }
    }
    const kinds = availableConditionKinds(merged.lifecycle, merged.archetype);
    if (!kinds.includes(merged.conditionKind)) {
      merged.conditionKind = kinds[0] ?? "none";
    }
    return merged;
  };
  const updateDraft = (patch: Partial<Draft>) => setDraft(reseedDownstream(patch));

  const refOptions = useMemo(
    () => buildRefOptions(catalog, evidenceTypes),
    [catalog, evidenceTypes],
  );

  // Step 2 skips when archetype=emit (no condition) — but emit is
  // currently disabled so this path never fires. Kept for forward-compat.
  const skipsConditionStep = draft.archetype === "emit";

  const handleNext = () => {
    let nextStep = step + 1;
    if (nextStep === 2 && skipsConditionStep) nextStep = 3;
    if (nextStep === 3 && skipsConditionStep) nextStep = 4;
    setStep(Math.min(nextStep, TOTAL - 1));
  };
  const handleBack = () => {
    let prevStep = step - 1;
    if (prevStep === 3 && skipsConditionStep) prevStep = 2;
    if (prevStep === 2 && skipsConditionStep) prevStep = 1;
    setStep(Math.max(prevStep, 0));
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const built = buildPolicy(draft);
      if (built.kind === "custom_rule") {
        await putCustomRule(agentFetch, built.rule);
      } else {
        await putDashboardCheck(agentFetch, built.check);
      }
      onActivated();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <WizardChrome
      step={step}
      total={TOTAL}
      onPickDifferent={onCancel /* there is no second wizard to pick — cancel */}
      onCancel={onCancel}
      onBack={handleBack}
      onNext={handleNext}
      onSave={handleSave}
      canAdvance={stepIsComplete(step, draft)}
      saving={saving}
      error={saveError}
    >
      {step === 0 ? (
        <TriggerStep draft={draft} update={updateDraft} />
      ) : null}
      {step === 1 ? (
        <ArchetypeStep draft={draft} update={updateDraft} />
      ) : null}
      {step === 2 ? (
        <ConditionKindStep draft={draft} update={updateDraft} />
      ) : null}
      {step === 3 ? (
        <SpecificsStep
          draft={draft}
          update={updateDraft}
          refOptions={refOptions}
        />
      ) : null}
      {step === 4 ? <NameStep draft={draft} update={updateDraft} /> : null}
      {step === 5 ? (
        <ReviewStep draft={draft} refOptions={refOptions} />
      ) : null}
    </WizardChrome>
  );
}


// ---------------------------------------------------------------------------
// Step 0 — When?
// ---------------------------------------------------------------------------


const LIFECYCLE_OPTIONS: ReadonlyArray<{
  id: Lifecycle;
  label: string;
  description: string;
}> = [
  { id: "before_tool_use", label: "Before a tool runs", description: "Fires at PreToolUse — before the agent invokes a tool." },
  { id: "after_tool_use", label: "After a tool returns", description: "Fires at PostToolUse — before the agent reads the tool's output." },
  { id: "pre_final", label: "Before the final answer commits", description: "Fires just before the runtime accepts the agent's final answer." },
];


const SCOPE_OPTIONS: ReadonlyArray<{
  id: Scope;
  label: string;
  description: string;
}> = [
  { id: "coding", label: "Coding turns", description: "Turns where the agent is writing or modifying code." },
  { id: "research", label: "Research turns", description: "Turns where the agent is fetching or citing sources." },
  { id: "delivery", label: "Delivery turns", description: "Turns where the agent is producing a final deliverable." },
  { id: "always", label: "Every turn", description: "Any turn regardless of scope." },
];


function TriggerStep({
  draft,
  update,
}: { draft: Draft; update: (patch: Partial<Draft>) => void }): React.ReactElement {
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <h2 className="text-lg font-bold text-foreground">When should this policy fire?</h2>
        <p className="text-xs text-secondary">
          Two axes: <em>when</em> in the agent's lifecycle, and <em>on which
          kind of turn</em>. Pick one of each.
        </p>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Lifecycle event
        </legend>
        {LIFECYCLE_OPTIONS.map((opt) => (
          <RadioCard
            key={opt.id}
            checked={draft.lifecycle === opt.id}
            onClick={() => update({ lifecycle: opt.id })}
            label={opt.label}
            description={opt.description}
          />
        ))}
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Turn scope
        </legend>
        {SCOPE_OPTIONS.map((opt) => (
          <RadioCard
            key={opt.id}
            checked={draft.scope === opt.id}
            onClick={() => update({ scope: opt.id })}
            label={opt.label}
            description={opt.description}
          />
        ))}
      </fieldset>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Step 1 — What to do?  (archetype filtered by lifecycle)
// ---------------------------------------------------------------------------


interface ArchetypeOption {
  id: Archetype;
  label: string;
  description: string;
  icon: React.ReactNode;
  disabled?: boolean;
  badge?: string;
}


function availableArchetypes(lifecycle: Lifecycle): Archetype[] {
  if (lifecycle === "before_tool_use") return ["block", "ask", "audit", "emit"];
  if (lifecycle === "after_tool_use") return ["block", "audit", "strip", "emit"];
  return ["block", "ask", "audit", "emit"];
}


const ARCHETYPE_META: Record<Archetype, ArchetypeOption> = {
  block: {
    id: "block",
    label: "Block / refuse",
    description: "Reject the action when the condition fires.",
    icon: <Ban className="h-5 w-5" />,
  },
  ask: {
    id: "ask",
    label: "Ask the user for approval",
    description: "Pause and prompt the user when the condition fires.",
    icon: <HelpCircle className="h-5 w-5" />,
  },
  audit: {
    id: "audit",
    label: "Audit / record evidence",
    description: "Emit an evidence record when the condition fires — does not block.",
    icon: <Filter className="h-5 w-5" />,
  },
  strip: {
    id: "strip",
    label: "Strip / transform output",
    description: "Modify the tool result before the agent reads it (after-tool only).",
    icon: <ShieldOff className="h-5 w-5" />,
  },
  emit: {
    id: "emit",
    label: "Emit a signal unconditionally",
    description: "At the trigger, emit an evidence record every time — no condition, no fail path.",
    icon: <Megaphone className="h-5 w-5" />,
    disabled: true,
    badge: "Coming soon",
  },
};


function ArchetypeStep({
  draft,
  update,
}: { draft: Draft; update: (patch: Partial<Draft>) => void }): React.ReactElement {
  const ids = availableArchetypes(draft.lifecycle);
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">What should the policy do?</h2>
      <p className="text-xs text-secondary">
        Pick the action the policy takes at this lifecycle moment. Options
        not valid for the chosen trigger are hidden.
      </p>
      <div className="space-y-2">
        {ids.map((id) => {
          const meta = ARCHETYPE_META[id];
          return (
            <RadioCard
              key={meta.id}
              checked={draft.archetype === meta.id}
              onClick={() => {
                if (meta.disabled) return;
                update({ archetype: meta.id });
              }}
              label={meta.label}
              description={meta.description}
              badge={meta.badge}
            />
          );
        })}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Step 2 — Condition kind (filtered by lifecycle + archetype)
// ---------------------------------------------------------------------------


function availableConditionKinds(
  lifecycle: Lifecycle,
  archetype: Archetype,
): ConditionKind[] {
  if (archetype === "emit") return ["none"];
  if (lifecycle === "before_tool_use") {
    return ["tool_name", "domain", "domain_allowlist"];
  }
  if (lifecycle === "after_tool_use") {
    if (archetype === "audit") return ["regex", "llm_criterion"];
    if (archetype === "strip") return ["llm_criterion", "regex"];
    if (archetype === "block") return ["regex", "llm_criterion"];
    return ["regex", "llm_criterion"];
  }
  // pre_final
  return ["evidence_ref", "shacl", "llm_criterion"];
}


const CONDITION_META: Record<ConditionKind, { label: string; description: string }> = {
  tool_name: { label: "Tool name", description: "Match a specific tool by name (e.g. shell_exec)." },
  domain: { label: "Fetch domain", description: "Match a fetch to an exact domain." },
  domain_allowlist: { label: "Domain allowlist", description: "Match any fetch outside a comma-separated allowlist." },
  evidence_ref: { label: "Evidence reference", description: "Check that a named evidence ref returned ok this turn." },
  shacl: { label: "SHACL shape", description: "Validate an evidence record against a Turtle SHACL shape." },
  llm_criterion: { label: "LLM criterion", description: "Ask an LLM critic whether a free-text criterion holds." },
  regex: { label: "Regex / literal pattern", description: "Match a regex or literal substring against the tool's output." },
  none: { label: "(no condition)", description: "Fires unconditionally at the trigger." },
};


function ConditionKindStep({
  draft,
  update,
}: { draft: Draft; update: (patch: Partial<Draft>) => void }): React.ReactElement {
  const kinds = availableConditionKinds(draft.lifecycle, draft.archetype);
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Under what condition does it fire?</h2>
      <p className="text-xs text-secondary">
        Pick the kind of check that triggers the action. Options not valid
        for your trigger / action choice are hidden.
      </p>
      <div className="space-y-2">
        {kinds.map((kind) => {
          const meta = CONDITION_META[kind];
          return (
            <RadioCard
              key={kind}
              checked={draft.conditionKind === kind}
              onClick={() => update({ conditionKind: kind })}
              label={meta.label}
              description={meta.description}
            />
          );
        })}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Step 3 — Specifics (form per condition kind)
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


function SpecificsStep({
  draft,
  update,
  refOptions,
}: {
  draft: Draft;
  update: (patch: Partial<Draft>) => void;
  refOptions: RefOption[];
}): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Fill in the details</h2>
      <p className="text-xs text-secondary">
        Specifics for your <code>{draft.conditionKind}</code> condition.
      </p>
      {draft.conditionKind === "tool_name" ? (
        <TextField
          value={draft.toolName}
          onChange={(v) => update({ toolName: v })}
          label="Tool name"
          placeholder="shell_exec"
        />
      ) : null}
      {draft.conditionKind === "domain" ? (
        <TextField
          value={draft.domain}
          onChange={(v) => update({ domain: v })}
          label="Fetch domain"
          placeholder="example.com"
        />
      ) : null}
      {draft.conditionKind === "domain_allowlist" ? (
        <TextField
          value={draft.domainAllowlist}
          onChange={(v) => update({ domainAllowlist: v })}
          label="Allowed domains (comma-separated)"
          placeholder="github.com, openmagi.ai"
        />
      ) : null}
      {draft.conditionKind === "evidence_ref" ? (
        refOptions.length === 0 ? (
          <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs text-secondary">
            No evidence refs available in this runtime.
          </p>
        ) : (
          <div className="space-y-2">
            {refOptions.map((opt) => (
              <RadioCard
                key={opt.ref}
                checked={draft.evidenceRef === opt.ref}
                onClick={() => update({ evidenceRef: opt.ref })}
                label={opt.label}
                description={opt.description}
                badge={opt.origin === "user" ? "user" : undefined}
                monoLabel={opt.ref}
              />
            ))}
          </div>
        )
      ) : null}
      {draft.conditionKind === "shacl" ? (
        <label className="block">
          <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
            SHACL shape (Turtle)
          </span>
          <textarea
            value={draft.shapeTtl}
            onChange={(e) => update({ shapeTtl: e.target.value })}
            rows={10}
            placeholder={SHACL_PLACEHOLDER}
            aria-label="SHACL shape"
            className="mt-1 w-full resize-y rounded-lg border border-primary/30 bg-white px-3 py-2 text-xs font-mono text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </label>
      ) : null}
      {draft.conditionKind === "llm_criterion" ? (
        <TextField
          value={draft.criterion}
          onChange={(v) => update({ criterion: v })}
          label="LLM criterion (single sentence)"
          placeholder="The answer cites at least one source."
        />
      ) : null}
      {draft.conditionKind === "regex" ? (
        <div className="space-y-2">
          <TextField
            value={draft.regexPattern}
            onChange={(v) => update({ regexPattern: v })}
            label="Pattern"
            placeholder={draft.regexIsRegex ? "AKIA[0-9A-Z]{16}" : "secret"}
            mono
          />
          <label className="flex items-center gap-2 text-xs text-secondary">
            <input
              type="checkbox"
              checked={draft.regexIsRegex}
              onChange={(e) => update({ regexIsRegex: e.target.checked })}
              className="rounded border-black/[0.20] text-primary focus:ring-primary/30"
            />
            Treat as regular expression
          </label>
        </div>
      ) : null}
      {draft.conditionKind === "none" ? (
        <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs text-secondary">
          No payload needed — the policy fires unconditionally at the
          trigger.
        </p>
      ) : null}
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


function TextField({
  value,
  onChange,
  label,
  placeholder,
  mono,
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  placeholder?: string;
  mono?: boolean;
}): React.ReactElement {
  return (
    <label className="block">
      <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
        {label}
      </span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={label}
        className={`mt-1 w-full rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 ${
          mono ? "font-mono" : ""
        }`}
      />
    </label>
  );
}


// ---------------------------------------------------------------------------
// Step 4 — Name
// ---------------------------------------------------------------------------


function NameStep({
  draft,
  update,
}: { draft: Draft; update: (patch: Partial<Draft>) => void }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Name your policy</h2>
      <p className="text-xs text-secondary">
        Shown in the policies list and audit logs.
      </p>
      <TextField
        value={draft.ruleId}
        onChange={(v) => update({ ruleId: v })}
        label="Policy ID"
        placeholder={defaultIdHint(draft)}
      />
      <p className="text-[11px] text-secondary">
        Lowercase alphanumeric + dash / underscore, max 128 chars.
      </p>
      <TextField
        value={draft.description}
        onChange={(v) => update({ description: v })}
        label="Description (optional)"
      />
    </div>
  );
}


function defaultIdHint(draft: Draft): string {
  if (draft.archetype === "block" && draft.lifecycle === "before_tool_use") return "deny-shell-exec";
  if (draft.archetype === "audit" && draft.lifecycle === "after_tool_use") return "block-aws-key-leak";
  if (draft.lifecycle === "pre_final") return "block-on-missing-tests";
  return "my-policy";
}


// ---------------------------------------------------------------------------
// Step 5 — Review
// ---------------------------------------------------------------------------


function ReviewStep({
  draft,
  refOptions,
}: { draft: Draft; refOptions: RefOption[] }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Review</h2>
      <p className="text-xs text-secondary">
        Saving applies the policy to the runtime immediately.
      </p>
      <div className="rounded-xl border border-black/[0.06] bg-white p-4">
        <p className="text-sm font-semibold text-foreground">What this policy does</p>
        <p className="mt-1 text-xs leading-relaxed text-foreground">
          {describePolicy(draft, refOptions)}
        </p>
        <hr className="my-3 border-black/[0.05]" />
        <dl className="grid grid-cols-[7rem_1fr] gap-y-1.5 text-xs">
          <dt className="text-secondary">ID</dt>
          <dd className="font-mono text-foreground">{draft.ruleId || "(unnamed)"}</dd>
          <dt className="text-secondary">When</dt>
          <dd>{draft.scope} · {draft.lifecycle}</dd>
          <dt className="text-secondary">Action</dt>
          <dd>{draft.archetype}</dd>
          <dt className="text-secondary">Condition</dt>
          <dd>{draft.conditionKind}</dd>
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


function describePolicy(draft: Draft, refOptions: RefOption[]): string {
  const whenClause =
    draft.scope === "always"
      ? whenForLifecycle(draft.lifecycle, /*scoped*/ false)
      : `On ${draft.scope} turns, ${whenForLifecycle(draft.lifecycle, true)}`;
  const archVerb = archetypeVerb(draft);
  const condClause = conditionClause(draft, refOptions);
  if (draft.archetype === "emit") {
    return `${whenClause}, emit an evidence record (no condition).`;
  }
  return `${whenClause}, ${archVerb} when ${condClause}.`;
}


function whenForLifecycle(lifecycle: Lifecycle, lower: boolean): string {
  if (lifecycle === "before_tool_use") return lower ? "before any tool call" : "Before any tool call";
  if (lifecycle === "after_tool_use") return lower ? "after a tool returns" : "After a tool returns";
  return lower ? "before the final answer commits" : "Before the final answer commits";
}


function archetypeVerb(draft: Draft): string {
  switch (draft.archetype) {
    case "block":
      return draft.lifecycle === "before_tool_use" ? "deny the tool call" : "block the final answer";
    case "ask":
      return "require human approval";
    case "audit":
      return "emit an evidence record (audit-mode, does not block)";
    case "strip":
      return "strip / override the tool result";
    case "emit":
      return "emit an evidence record";
  }
}


function conditionClause(draft: Draft, refOptions: RefOption[]): string {
  switch (draft.conditionKind) {
    case "tool_name":
      return `the tool is "${draft.toolName}"`;
    case "domain":
      return `the fetch domain is ${draft.domain}`;
    case "domain_allowlist":
      return `the fetch domain is NOT in [${csv(draft.domainAllowlist)}]`;
    case "evidence_ref": {
      const ref = refOptions.find((r) => r.ref === draft.evidenceRef);
      return `evidence "${ref?.label ?? draft.evidenceRef}" did not return ok`;
    }
    case "shacl":
      return "the SHACL shape does NOT conform on any evidence record";
    case "llm_criterion":
      return `an LLM critic judges "${draft.criterion}" is false`;
    case "regex":
      return `the result ${draft.regexIsRegex ? "matches regex" : "contains"} "${draft.regexPattern}"`;
    case "none":
      return "(unconditional)";
  }
}


function csv(s: string): string {
  return s.split(",").map((x) => x.trim()).filter(Boolean).join(", ");
}


// ---------------------------------------------------------------------------
// Step completion + activation routing
// ---------------------------------------------------------------------------


function stepIsComplete(step: number, draft: Draft): boolean {
  if (step === 0) return !!draft.lifecycle && !!draft.scope;
  if (step === 1) return !!draft.archetype;
  if (step === 2) return !!draft.conditionKind;
  if (step === 3) {
    switch (draft.conditionKind) {
      case "tool_name":
        return draft.toolName.trim().length > 0;
      case "domain":
        return draft.domain.trim().length > 0;
      case "domain_allowlist":
        return draft.domainAllowlist.trim().length > 0;
      case "evidence_ref":
        return draft.evidenceRef.length > 0;
      case "shacl":
        return draft.shapeTtl.trim().length > 0;
      case "llm_criterion":
        return draft.criterion.trim().length > 0;
      case "regex":
        return draft.regexPattern.trim().length > 0;
      case "none":
        return true;
    }
  }
  if (step === 4) return /^[a-z0-9][a-z0-9_-]{0,127}$/.test(draft.ruleId);
  return true;
}


type Built =
  | { kind: "custom_rule"; rule: CustomRule }
  | { kind: "dashboard_check"; check: DashboardCheck };


function buildPolicy(draft: Draft): Built {
  // After-tool regex with audit OR block routes to the Dashboard pack
  // primitive (the only after-tool regex implementation today).
  if (
    draft.lifecycle === "after_tool_use"
    && draft.conditionKind === "regex"
    && (draft.archetype === "audit" || draft.archetype === "block")
  ) {
    const check: DashboardCheck = {
      id: draft.ruleId,
      label: draft.description || draft.ruleId,
      scope: draft.scope as DashboardScope,
      enabled: true,
      trigger: {
        tool: "*", // tool name not asked in this flow — Dashboard pack wildcard
        match: { pattern: draft.regexPattern.trim(), isRegex: draft.regexIsRegex },
      },
      action: draft.archetype === "audit" ? "audit" : "block",
    };
    return { kind: "dashboard_check", check };
  }

  // All other shapes route to CustomRule.
  const action = customRuleAction(draft);
  const payload = customRulePayload(draft);
  const kind = customRuleKind(draft);
  return {
    kind: "custom_rule",
    rule: {
      id: draft.ruleId,
      scope: draft.scope,
      enabled: true,
      firesAt: draft.lifecycle,
      action,
      what: { kind, payload },
    },
  };
}


function customRuleKind(draft: Draft): string {
  if (draft.conditionKind === "tool_name" || draft.conditionKind === "domain" || draft.conditionKind === "domain_allowlist") {
    return "tool_perm";
  }
  if (draft.conditionKind === "evidence_ref") return "deterministic_ref";
  if (draft.conditionKind === "shacl") return "shacl_constraint";
  if (draft.conditionKind === "regex") return "llm_criterion"; // after-tool regex via LLM kind (the only path other than dashboard_check)
  // llm_criterion / none
  return "llm_criterion";
}


function customRuleAction(draft: Draft): string {
  switch (draft.archetype) {
    case "block":
      return "block";
    case "ask":
      return "ask_approval";
    case "audit":
      return "audit";
    case "strip":
      return "override";
    case "emit":
      return "audit"; // closest backend-supported semantic until emit lands
  }
}


function customRulePayload(draft: Draft): Record<string, unknown> {
  switch (draft.conditionKind) {
    case "tool_name":
      return {
        match: { tool: draft.toolName.trim() },
        decision: draft.archetype === "ask" ? "ask" : "deny",
      };
    case "domain":
      return {
        match: { domain: draft.domain.trim() },
        decision: draft.archetype === "ask" ? "ask" : "deny",
      };
    case "domain_allowlist":
      return {
        match: {
          domainAllowlist: draft.domainAllowlist
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        },
        decision: draft.archetype === "ask" ? "ask" : "deny",
      };
    case "evidence_ref":
      return { ref: draft.evidenceRef };
    case "shacl":
      return { shapeTtl: draft.shapeTtl.trim() };
    case "regex":
      return {
        contentMatch: { pattern: draft.regexPattern.trim(), isRegex: draft.regexIsRegex },
      };
    case "llm_criterion":
      return { criterion: draft.criterion.trim() };
    case "none":
      return {};
  }
}
