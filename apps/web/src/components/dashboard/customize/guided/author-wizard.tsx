"use client";

/**
 * Unified Author wizard — single 6-step flow that covers all policy
 * authoring shapes the runtime currently supports.
 *
 * Step ordering follows the user's mental model: pick WHEN, pick the
 * CONDITION, then pick what action to take when that condition fires.
 *
 * Steps
 * -----
 *   1. When?         (lifecycle + scope)
 *   2. Condition     (kind, filtered by lifecycle)
 *   3. Specifics     (per-kind form; auto-skipped when conditionKind=none)
 *   4. Action        (archetype, filtered by lifecycle; header phrasing
 *                     reflects the chosen condition trigger so the
 *                     positive/negative semantics survive)
 *   5. Name          (id + optional description)
 *   6. Review        (auto-built English sentence + dl)
 *
 * "(no condition)" is exposed ONLY for after_tool_use, where it routes
 * to a DashboardCheck with pattern=".*" — the only lifecycle whose
 * backend cleanly supports an unconditional fire today. before_tool_use
 * tool_perm has no wildcard matcher and pre_final rules have no
 * always-fail sentinel, so the option is omitted there rather than
 * synthesised with a fake-condition workaround.
 *
 * Routing
 * -------
 *   (after_tool, regex,                 audit|block) → putDashboardCheck
 *   (after_tool, none,                  audit|block) → putDashboardCheck  (pattern=".*")
 *   (before_tool, tool|domain|allowlist, any)        → putCustomRule {kind:tool_perm}
 *   (after_tool, llm,                    any)        → putCustomRule {kind:llm_criterion, firesAt:after_tool_use}
 *   (pre_final, evidence_ref,            any)        → putCustomRule {kind:deterministic_ref}
 *   (pre_final, shacl,                   any)        → putCustomRule {kind:shacl_constraint}
 *   (pre_final, llm,                     any)        → putCustomRule {kind:llm_criterion}
 */

import {
  Ban,
  Filter,
  HelpCircle,
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
type Archetype = "block" | "ask" | "audit" | "strip";
type ConditionKind =
  | "none"
  | "tool_name"
  | "domain"
  | "domain_allowlist"
  | "evidence_ref"
  | "shacl"
  | "llm_criterion"
  | "regex";


interface Draft {
  lifecycle: Lifecycle;
  scope: Scope;
  conditionKind: ConditionKind;
  archetype: Archetype;
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
  conditionKind: "evidence_ref",
  archetype: "block",
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
const STEP_SPECIFICS = 2;


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
  // before_tool_use would leave conditionKind="shacl" + archetype="audit"
  // which is no longer a valid combination.
  const reseedDownstream = (next: Partial<Draft>): Draft => {
    const merged = { ...draft, ...next };
    if ("lifecycle" in next) {
      const kinds = availableConditionKinds(merged.lifecycle);
      if (!kinds.includes(merged.conditionKind)) {
        merged.conditionKind = kinds[0];
      }
    }
    const archetypes = availableArchetypes(merged.lifecycle);
    if (!archetypes.includes(merged.archetype)) {
      merged.archetype = archetypes[0];
    }
    return merged;
  };
  const updateDraft = (patch: Partial<Draft>) => setDraft(reseedDownstream(patch));

  const refOptions = useMemo(
    () => buildRefOptions(catalog, evidenceTypes),
    [catalog, evidenceTypes],
  );

  const skipsSpecificsStep = draft.conditionKind === "none";

  const handleNext = () => {
    let nextStep = step + 1;
    if (nextStep === STEP_SPECIFICS && skipsSpecificsStep) nextStep += 1;
    setStep(Math.min(nextStep, TOTAL - 1));
  };
  const handleBack = () => {
    let prevStep = step - 1;
    if (prevStep === STEP_SPECIFICS && skipsSpecificsStep) prevStep -= 1;
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
        <ConditionKindStep draft={draft} update={updateDraft} />
      ) : null}
      {step === 2 ? (
        <SpecificsStep
          draft={draft}
          update={updateDraft}
          refOptions={refOptions}
        />
      ) : null}
      {step === 3 ? (
        <ArchetypeStep draft={draft} update={updateDraft} refOptions={refOptions} />
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
// Step 1 — Condition kind (filtered by lifecycle)
// ---------------------------------------------------------------------------


function availableConditionKinds(lifecycle: Lifecycle): ConditionKind[] {
  // "none" (unconditional) is only listed where backend supports it
  // natively. after_tool_use synthesises it via DashboardCheck pattern=".*".
  // before_tool_use's tool_perm matcher has no wildcard; pre_final rules
  // have no always-fail sentinel — listing "none" there would require a
  // fake-condition workaround, so we keep it out.
  if (lifecycle === "before_tool_use") {
    return ["tool_name", "domain", "domain_allowlist"];
  }
  if (lifecycle === "after_tool_use") {
    return ["none", "regex", "llm_criterion"];
  }
  return ["evidence_ref", "shacl", "llm_criterion"];
}


const CONDITION_META: Record<ConditionKind, { label: string; description: string }> = {
  none: {
    label: "(no condition — fire on every trigger)",
    description: "Run the action unconditionally at this lifecycle moment.",
  },
  tool_name: {
    label: "Tool name",
    description: "Match a specific tool by name (e.g. shell_exec).",
  },
  domain: {
    label: "Fetch domain (network tools only)",
    description: "Match a fetch whose URL host equals this domain. Only fires for tools that perform an HTTP fetch.",
  },
  domain_allowlist: {
    label: "Domain allowlist (network tools only)",
    description: "Match any fetch whose URL host is NOT in this comma-separated allowlist. Only fires for tools that perform an HTTP fetch.",
  },
  evidence_ref: {
    label: "Evidence reference",
    description: "Check that a named evidence ref returned ok this turn.",
  },
  shacl: {
    label: "SHACL shape",
    description: "Validate an evidence record against a Turtle SHACL shape.",
  },
  llm_criterion: {
    label: "LLM criterion",
    description: "Ask an LLM critic whether a free-text criterion holds.",
  },
  regex: {
    label: "Regex / literal pattern",
    description: "Match a regex or literal substring against the tool's output.",
  },
};


function ConditionKindStep({
  draft,
  update,
}: { draft: Draft; update: (patch: Partial<Draft>) => void }): React.ReactElement {
  const kinds = availableConditionKinds(draft.lifecycle);
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Under what condition does it fire?</h2>
      <p className="text-xs text-secondary">
        Pick a check that triggers the action — or <em>(no condition)</em>{" "}
        to fire on every trigger. Options not valid for your lifecycle are
        hidden.
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
// Step 2 — Specifics (form per condition kind; auto-skipped for "none")
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
// Step 3 — Action archetype (filtered by lifecycle; header reflects trigger)
// ---------------------------------------------------------------------------


interface ArchetypeOption {
  id: Archetype;
  label: string;
  description: string;
  icon: React.ReactNode;
}


function availableArchetypes(lifecycle: Lifecycle): Archetype[] {
  if (lifecycle === "before_tool_use") return ["block", "ask", "audit"];
  if (lifecycle === "after_tool_use") return ["block", "audit", "strip"];
  return ["block", "ask", "audit"];
}


const ARCHETYPE_META: Record<Archetype, ArchetypeOption> = {
  block: {
    id: "block",
    label: "Block / refuse",
    description: "Reject the action.",
    icon: <Ban className="h-5 w-5" />,
  },
  ask: {
    id: "ask",
    label: "Ask the user for approval",
    description: "Pause and prompt the user.",
    icon: <HelpCircle className="h-5 w-5" />,
  },
  audit: {
    id: "audit",
    label: "Audit / record evidence",
    description: "Emit an evidence record — does not block.",
    icon: <Filter className="h-5 w-5" />,
  },
  strip: {
    id: "strip",
    label: "Strip / transform output",
    description: "Modify the tool result before the agent reads it (after-tool only).",
    icon: <ShieldOff className="h-5 w-5" />,
  },
};


/**
 * Human-readable description of the EVENT that fires the action. Used to
 * compose the dynamic Action-step header so the positive/negative
 * semantics survive the unification: pre_final rules fire on a check
 * FAILURE; before_tool_use rules fire on a positive MATCH; after_tool_use
 * regex fires on a positive match; an LLM criterion always fires when the
 * critic returns NO (so the framing is consistent across lifecycles).
 */
function triggerEventPhrase(draft: Draft, refOptions: RefOption[]): string {
  switch (draft.conditionKind) {
    case "none":
      return "On every trigger";
    case "tool_name":
      return `When the tool is "${draft.toolName || "…"}"`;
    case "domain":
      return `When the fetch domain matches "${draft.domain || "…"}"`;
    case "domain_allowlist":
      return "When the fetch is outside the allowlist";
    case "regex":
      return `When the tool output ${draft.regexIsRegex ? "matches the regex" : "contains"} "${draft.regexPattern || "…"}"`;
    case "llm_criterion":
      return `When the LLM critic judges "${draft.criterion || "…"}" is false`;
    case "evidence_ref": {
      const ref = refOptions.find((r) => r.ref === draft.evidenceRef);
      return `When evidence "${ref?.label ?? (draft.evidenceRef || "…")}" did NOT return ok`;
    }
    case "shacl":
      return "When the SHACL shape does NOT conform on any evidence record";
  }
}


function ArchetypeStep({
  draft,
  update,
  refOptions,
}: {
  draft: Draft;
  update: (patch: Partial<Draft>) => void;
  refOptions: RefOption[];
}): React.ReactElement {
  const ids = availableArchetypes(draft.lifecycle);
  const trigger = triggerEventPhrase(draft, refOptions);
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">What should the policy do?</h2>
      <p className="text-xs text-secondary">
        <strong className="font-semibold text-foreground">{trigger}</strong>
        , do this. Options not valid for the chosen lifecycle are hidden.
      </p>
      <div className="space-y-2">
        {ids.map((id) => {
          const meta = ARCHETYPE_META[id];
          return (
            <RadioCard
              key={meta.id}
              checked={draft.archetype === meta.id}
              onClick={() => update({ archetype: meta.id })}
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
          <dt className="text-secondary">Condition</dt>
          <dd>{draft.conditionKind}</dd>
          <dt className="text-secondary">Action</dt>
          <dd>{draft.archetype}</dd>
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
  if (draft.conditionKind === "none") {
    return `${whenClause}, ${archVerb} on every trigger.`;
  }
  const condClause = conditionClause(draft, refOptions);
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
  }
}


function conditionClause(draft: Draft, refOptions: RefOption[]): string {
  switch (draft.conditionKind) {
    case "none":
      return "(unconditional)";
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
  if (step === 1) return !!draft.conditionKind;
  if (step === 2) {
    switch (draft.conditionKind) {
      case "none":
        return true;
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
    }
  }
  if (step === 3) return !!draft.archetype;
  if (step === 4) return /^[a-z0-9][a-z0-9_-]{0,127}$/.test(draft.ruleId);
  return true;
}


type Built =
  | { kind: "custom_rule"; rule: CustomRule }
  | { kind: "dashboard_check"; check: DashboardCheck };


function buildPolicy(draft: Draft): Built {
  // After-tool unconditional fire — synthesise pattern=".*" so the
  // DashboardCheck regex matcher fires on every tool return.
  if (
    draft.lifecycle === "after_tool_use"
    && draft.conditionKind === "none"
    && (draft.archetype === "audit" || draft.archetype === "block")
  ) {
    const check: DashboardCheck = {
      id: draft.ruleId,
      label: draft.description || draft.ruleId,
      scope: draft.scope as DashboardScope,
      enabled: true,
      trigger: {
        tool: "*", // Dashboard pack wildcard — tool name not asked in this flow
        match: { pattern: ".*", isRegex: true },
      },
      action: draft.archetype === "audit" ? "audit" : "block",
    };
    return { kind: "dashboard_check", check };
  }

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
  // llm_criterion
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
  }
}


function customRulePayload(draft: Draft): Record<string, unknown> {
  switch (draft.conditionKind) {
    case "none":
      return {};
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
  }
}
