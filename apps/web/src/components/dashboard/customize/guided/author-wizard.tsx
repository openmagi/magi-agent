"use client";

/**
 * Unified Author wizard — variable-length flow that covers all policy
 * authoring shapes the runtime currently supports.
 *
 * F1.5 restructure: tool targeting was previously conflated with the
 * per-call condition (a "Tool name" entry in the condition list). The
 * two are now distinct steps so the operator's mental model is:
 *   1. Which tool(s) does this policy apply to?
 *   2. Under what per-call condition does it fire?
 *
 * Step ordering
 * -------------
 *   0. Trigger      (lifecycle + scope)
 *   1. Target       (Any tool / Specific tool)  [only for tool-bearing
 *                    lifecycles; skipped for pre_final]
 *   2. Condition    (per-call check, filtered by lifecycle + target)
 *   3. Specifics    (per-condition form; auto-skipped when condition=none)
 *   4. Action       (archetype, filtered by lifecycle; header phrasing
 *                    reflects the chosen condition trigger so positive
 *                    vs negative semantics survive)
 *   5. Name
 *   6. Review
 *
 * Total step count is dynamic: pre_final has 6 steps (target step is
 * absent because there is no tool layer at pre_final); before_tool_use
 * and after_tool_use have 7 steps. The wizard chrome's progress bar
 * adapts.
 *
 * Routing — (lifecycle, target, condition) → backend primitive
 * ----------------------------------------------------------
 *   (after_tool,  any,      none)            → putDashboardCheck       (tool='*', pattern='.*')
 *   (after_tool,  specific, none)            → putDashboardCheck       (tool=X,   pattern='.*')
 *   (after_tool,  any,      regex)           → putDashboardCheck       (tool='*', pattern=P)
 *   (after_tool,  specific, regex)           → putDashboardCheck       (tool=X,   pattern=P)
 *   (after_tool,  any,      llm_criterion)   → putCustomRule           (llm_criterion, firesAt=after_tool_use)
 *   (after_tool,  specific, llm_criterion)   → refused (no tool filter on llm_criterion today)
 *   (before_tool, specific, none)            → putCustomRule           (tool_perm match={tool:X})
 *   (before_tool, any,      domain)          → putCustomRule           (tool_perm match={domain})
 *   (before_tool, any,      domain_allowlist)→ putCustomRule           (tool_perm match={domainAllowlist})
 *   (before_tool, any,      none)            → refused (tool_perm has no wildcard)
 *   (before_tool, specific, domain*)         → refused (tool_perm has no AND in backend)
 *   (pre_final,   n/a,      evidence_ref)    → putCustomRule           (deterministic_ref)
 *   (pre_final,   n/a,      shacl)           → putCustomRule           (shacl_constraint)
 *   (pre_final,   n/a,      llm_criterion)   → putCustomRule           (llm_criterion)
 *
 * Unsupported combos are kept off the wizard by the per-step option
 * filter (availableConditionKinds(lifecycle, target)) so the operator
 * never assembles a draft that cannot be saved. If a future backend
 * extension lifts a constraint, surface it here.
 */

import {
  Ban,
  Filter,
  HelpCircle,
  ShieldOff,
} from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";

import {
  getEvidenceLiveCatalog,
  putCustomRule,
  type CustomRule,
  type CustomizeCatalog,
  type EvidenceLiveCatalogTypeEntry,
} from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";
import {
  putDashboardCheck,
  type DashboardCheck,
  type DashboardScope,
} from "@/lib/packs-dashboard-api";
import type { EvidenceTypeEntry } from "@/lib/policy-model";

import { TrustBadge, type TrustClass } from "../trust-badge";

import { RadioCard, WizardChrome } from "./wizard-chrome";


// ---------------------------------------------------------------------------
// Domain
// ---------------------------------------------------------------------------


type Lifecycle = "before_tool_use" | "after_tool_use" | "pre_final";
type Scope = "always" | "coding" | "research" | "delivery" | "memory" | "task";
type Archetype = "block" | "ask" | "audit" | "strip";
type ToolTarget = "any" | "specific";
type ConditionKind =
  | "none"
  | "domain"
  | "domain_allowlist"
  | "path"
  | "path_allowlist"
  | "evidence_ref"
  | "shacl"
  | "llm_criterion"
  | "regex"
  | "field_constraint";


// PR-F3: deterministic operators for field_constraint. The eight
// single-record operators map 1:1 to SHACL constraints on
// magi:field_<key>; forEachExistsCovering is the cross-record cardinality
// form used for "for each entry in <source.field>, there exists a
// <target>" patterns (intent 2 endgame).
type FieldOperator =
  | "eq"
  | "neq"
  | "gt"
  | "lt"
  | "ge"
  | "le"
  | "exists"
  | "notExists"
  | "forEachExistsCovering";


interface Draft {
  lifecycle: Lifecycle;
  scope: Scope;
  toolTarget: ToolTarget;      // F1.5: targets which tool(s); ignored on pre_final.
  toolName: string;            // populated when toolTarget="specific".
  conditionKind: ConditionKind;
  archetype: Archetype;
  // payload fields
  domain: string;
  domainAllowlist: string;
  pathPrefix: string;
  pathAllowlist: string;
  evidenceRef: string;
  shapeTtl: string;
  criterion: string;
  regexPattern: string;
  regexIsRegex: boolean;
  // PR-F6.5 (BLOCKER fix): comma-separated list of tool names the after-tool
  // llm_criterion rule fires on. Backend validator
  // (`magi_agent/customize/custom_rules.py:185`) REQUIRES a non-empty
  // `toolMatch` list for every after_tool_use llm_criterion rule, and the
  // runtime gate (`after_tool_gate.py:150`) matches by exact membership
  // (`tool_name not in tool_match` → skip). The wizard collects the list
  // here and the payload builder splits/trims into a string[]. target=any
  // is retained (per-tool filtering lives on the rule, not on the wizard's
  // top-level Target step) so the existing availableConditionKinds wiring
  // ("after_tool_use + any + llm_criterion") stays intact.
  llmToolMatch: string;
  // PR-F6.5: optional deterministic regex pre-filter on an after-tool
  // llm_criterion rule. When enabled, the runtime gate only invokes the
  // (cost-bearing) LLM critic on tool results that match the pattern, so
  // the combo composes a deterministic input-definition slot in front of
  // an advisory verdict. See `magi_agent/customize/after_tool_gate.py`
  // for the runtime check.
  llmContentMatchEnabled: boolean;
  llmContentMatchPattern: string;
  llmContentMatchIsRegex: boolean;
  llmContentMatchNegate: boolean;
  // PR-F3: field_constraint structured IR.
  // Single-record form: (fcEvidenceType, fcField, fcOperator, fcValue).
  // Cross-record form (operator = forEachExistsCovering): the source side
  // reuses (fcEvidenceType, fcField); the target side adds the four
  // fcCrossTarget* fields and fcCrossCovering names the join key.
  fcEvidenceType: string;
  fcField: string;
  fcOperator: FieldOperator;
  fcValue: string;
  fcCrossSourceType: string;
  fcCrossSourceField: string;
  fcCrossTargetType: string;
  fcCrossTargetField: string;
  // common
  ruleId: string;
  description: string;
}


const EMPTY: Draft = {
  lifecycle: "pre_final",
  scope: "coding",
  toolTarget: "any",
  toolName: "",
  conditionKind: "evidence_ref",
  archetype: "block",
  domain: "",
  domainAllowlist: "",
  pathPrefix: "",
  pathAllowlist: "",
  evidenceRef: "",
  shapeTtl: "",
  criterion: "",
  regexPattern: "",
  regexIsRegex: false,
  llmToolMatch: "",
  llmContentMatchEnabled: false,
  llmContentMatchPattern: "",
  llmContentMatchIsRegex: false,
  llmContentMatchNegate: false,
  fcEvidenceType: "",
  fcField: "",
  fcOperator: "eq",
  fcValue: "",
  fcCrossSourceType: "",
  fcCrossSourceField: "",
  fcCrossTargetType: "",
  fcCrossTargetField: "",
  ruleId: "",
  description: "",
};


// Variable step plan: pre_final has no tool layer.
type StepKey = "trigger" | "target" | "condition" | "specifics" | "action" | "name" | "review";

function stepPlan(lifecycle: Lifecycle): StepKey[] {
  if (lifecycle === "pre_final") {
    return ["trigger", "condition", "specifics", "action", "name", "review"];
  }
  return ["trigger", "target", "condition", "specifics", "action", "name", "review"];
}


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

  const plan = stepPlan(draft.lifecycle);
  const TOTAL = plan.length;
  const currentKey: StepKey = plan[Math.min(step, TOTAL - 1)];

  // Re-validate downstream fields when an upstream axis changes. Without
  // this, going back to step 0 and switching lifecycle from pre_final to
  // before_tool_use would leave conditionKind="shacl" + archetype="audit"
  // which is no longer a valid combination.
  const reseedDownstream = (next: Partial<Draft>): Draft => {
    const merged = { ...draft, ...next };
    const kinds = availableConditionKinds(merged.lifecycle, merged.toolTarget);
    if (!kinds.includes(merged.conditionKind)) {
      merged.conditionKind = kinds[0] ?? "none";
    }
    const archetypes = availableArchetypes(merged.lifecycle);
    if (!archetypes.includes(merged.archetype)) {
      merged.archetype = archetypes[0];
    }
    return merged;
  };
  const updateDraft = (patch: Partial<Draft>) => {
    const reseeded = reseedDownstream(patch);
    setDraft(reseeded);
    // If the lifecycle changed and the plan shrank, clamp step.
    const newPlan = stepPlan(reseeded.lifecycle);
    if (step >= newPlan.length) setStep(newPlan.length - 1);
  };

  const refOptions = useMemo(
    () => buildRefOptions(catalog, evidenceTypes),
    [catalog, evidenceTypes],
  );

  // PR-F3: field_constraint picker reads from the F2 evidence live-catalog
  // so it can hide inert-producer types (empty registeredFields). The
  // helper is fail-open: on network/HTTP error it resolves to an empty
  // catalog and the picker renders the honest "no fields available" state.
  const [liveCatalogTypes, setLiveCatalogTypes] = useState<
    EvidenceLiveCatalogTypeEntry[]
  >([]);
  useEffect(() => {
    let cancelled = false;
    void getEvidenceLiveCatalog(agentFetch).then((cat) => {
      if (!cancelled) setLiveCatalogTypes(cat.evidenceTypes);
    });
    return () => {
      cancelled = true;
    };
  }, [agentFetch]);

  // The Specifics step has nothing to ask when conditionKind=none.
  const isSpecificsEmpty = draft.conditionKind === "none";

  const handleNext = () => {
    let nextStep = step + 1;
    if (
      plan[nextStep] === "specifics"
      && isSpecificsEmpty
    ) {
      nextStep += 1;
    }
    setStep(Math.min(nextStep, TOTAL - 1));
  };
  const handleBack = () => {
    let prevStep = step - 1;
    if (
      plan[prevStep] === "specifics"
      && isSpecificsEmpty
    ) {
      prevStep -= 1;
    }
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
      canAdvance={stepIsComplete(currentKey, draft)}
      saving={saving}
      error={saveError}
    >
      {currentKey === "trigger" ? (
        <TriggerStep draft={draft} update={updateDraft} />
      ) : null}
      {currentKey === "target" ? (
        <TargetStep draft={draft} update={updateDraft} />
      ) : null}
      {currentKey === "condition" ? (
        <ConditionKindStep draft={draft} update={updateDraft} />
      ) : null}
      {currentKey === "specifics" ? (
        <SpecificsStep
          draft={draft}
          update={updateDraft}
          refOptions={refOptions}
          liveCatalogTypes={liveCatalogTypes}
        />
      ) : null}
      {currentKey === "action" ? (
        <ArchetypeStep draft={draft} update={updateDraft} refOptions={refOptions} />
      ) : null}
      {currentKey === "name" ? <NameStep draft={draft} update={updateDraft} /> : null}
      {currentKey === "review" ? (
        <ReviewStep draft={draft} refOptions={refOptions} />
      ) : null}
    </WizardChrome>
  );
}


// ---------------------------------------------------------------------------
// Step — Trigger (lifecycle + scope)
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
// Step — Target (Which tool(s)? Only for tool-bearing lifecycles)
// ---------------------------------------------------------------------------


function TargetStep({
  draft,
  update,
}: { draft: Draft; update: (patch: Partial<Draft>) => void }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Which tool(s) does this policy apply to?</h2>
      <p className="text-xs text-secondary">
        Apply to every tool call, or narrow to a specific tool.
      </p>
      <div className="space-y-2">
        <RadioCard
          checked={draft.toolTarget === "any"}
          onClick={() => update({ toolTarget: "any" })}
          label="Any tool"
          description="Match every tool call regardless of name."
        />
        <RadioCard
          checked={draft.toolTarget === "specific"}
          onClick={() => update({ toolTarget: "specific" })}
          label="Specific tool"
          description="Match calls to a single named tool. Pick which one below."
        />
      </div>
      {draft.toolTarget === "specific" ? (
        <TextField
          value={draft.toolName}
          onChange={(v) => update({ toolName: v })}
          label="Tool name"
          placeholder="shell_exec"
        />
      ) : null}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Step — Condition kind (filtered by lifecycle + target)
// ---------------------------------------------------------------------------


function availableConditionKinds(
  lifecycle: Lifecycle,
  toolTarget: ToolTarget,
): ConditionKind[] {
  // pre_final has no tool layer; target is ignored.
  if (lifecycle === "pre_final") {
    // PR-F3: field_constraint is the deterministic SHACL-via-picker path
    // and is the preferred default for evidence-shape rules — it sits
    // beside the raw `shacl` escape hatch (TTL textarea) for power users.
    return ["evidence_ref", "shacl", "llm_criterion", "field_constraint"];
  }
  if (lifecycle === "before_tool_use") {
    if (toolTarget === "specific") {
      // tool_perm has no AND between tool name and url-shape matchers,
      // so a per-tool rule can only fire unconditionally per call.
      // Refusing the AND combo here keeps the wizard from assembling a
      // draft the backend cannot save.
      return ["none"];
    }
    // target=any: tool_perm has no wildcard matcher, so "no condition"
    // is omitted (no honest backend mapping). F6 adds path / path_allowlist
    // alongside domain / domain_allowlist — the backend tool_perm matcher
    // already supports both via match.path / match.pathAllowlist, firing
    // only for tools that surface a file/path argument.
    return ["domain", "domain_allowlist", "path", "path_allowlist"];
  }
  // after_tool_use
  if (toolTarget === "specific") {
    // llm_criterion is offered only under target=any because the
    // SpecificsStep already exposes its own `llmToolMatch` text field
    // for the backend-required per-tool filter (custom_rules.py:185-188
    // rejects any after_tool_use llm_criterion without a non-empty
    // toolMatch). Surfacing llm_criterion under target=specific too
    // would force the user to fill BOTH the top-level toolName and the
    // SpecificsStep llmToolMatch — duplicated tool entry, no clearer
    // semantic. stepIsComplete enforces a non-empty llmToolMatch for
    // the target=any path.
    return ["none", "regex"];
  }
  return ["none", "regex", "llm_criterion"];
}


const CONDITION_META: Record<ConditionKind, { label: string; description: string }> = {
  none: {
    label: "No condition",
    description: "Fires on every matching tool call (no per-call check).",
  },
  domain: {
    label: "Fetch domain",
    description: "Fires when a fetch tool's URL host equals this domain.",
  },
  domain_allowlist: {
    label: "Domain allowlist",
    description: "Fires when a fetch tool's URL host is NOT in the allowlist.",
  },
  path: {
    label: "File / path",
    description:
      "Match when the tool acts on a path at or under this prefix. Only fires for tools whose argument schema surfaces a `path` (or alias: file, filename, filepath, filePath, pathRef) key. Examples: FileRead, FileEdit, FileWrite, PatchApply. Does NOT match Glob or Grep (whose arg is `pattern`, not `path`).",
  },
  path_allowlist: {
    label: "Path allowlist",
    description:
      "Match when the tool's path argument is NOT under any allowed prefix. Same surface as 'File / path': only fires for tools whose argument schema surfaces a `path` (or alias) key (FileRead, FileEdit, FileWrite, PatchApply); not for Glob or Grep.",
  },
  evidence_ref: {
    label: "Evidence reference",
    description: "Fires when a named evidence ref did not return ok this turn.",
  },
  shacl: {
    label: "SHACL shape",
    description: "Fires when a SHACL shape does not conform on any evidence record this turn.",
  },
  llm_criterion: {
    label: "LLM critic",
    description: "Asks an LLM critic a yes/no criterion; fires on NO.",
  },
  regex: {
    label: "Regex / literal pattern",
    description: "Fires when the tool output matches a regex or literal substring.",
  },
  field_constraint: {
    label: "Field constraint",
    description:
      "Pick an evidence type, field, operator, and value. Deterministic SHACL compile, no LLM.",
  },
};


function ConditionKindStep({
  draft,
  update,
}: { draft: Draft; update: (patch: Partial<Draft>) => void }): React.ReactElement {
  const kinds = availableConditionKinds(draft.lifecycle, draft.toolTarget);
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Under what condition does it fire?</h2>
      <p className="text-xs text-secondary">
        Pick a check that triggers the action. Options not valid for your
        lifecycle and tool target are hidden.
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
// Step — Specifics (form per condition kind; auto-skipped for "none")
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
  liveCatalogTypes,
}: {
  draft: Draft;
  update: (patch: Partial<Draft>) => void;
  refOptions: RefOption[];
  liveCatalogTypes: EvidenceLiveCatalogTypeEntry[];
}): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Fill in the details</h2>
      <p className="text-xs text-secondary">
        Specifics for your <code>{draft.conditionKind}</code> condition.
      </p>
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
      {draft.conditionKind === "path" ? (
        <TextField
          value={draft.pathPrefix}
          onChange={(v) => update({ pathPrefix: v })}
          label="Path prefix"
          placeholder="/etc/passwd"
          mono
        />
      ) : null}
      {draft.conditionKind === "path_allowlist" ? (
        <TextField
          value={draft.pathAllowlist}
          onChange={(v) => update({ pathAllowlist: v })}
          label="Allowed path prefixes (comma-separated)"
          placeholder="/Users/me/proj, /tmp/scratch"
          mono
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
        <div className="space-y-3">
          {/* PR-F6.5 BLOCKER fix — the backend validator
              (`magi_agent/customize/custom_rules.py:185`) REQUIRES a
              non-empty `toolMatch` list on every after_tool_use
              llm_criterion rule and the runtime gate matches by exact
              membership. Without this input the wizard always emitted a
              payload that PUT /custom-rules rejected with HTTP 400. Hidden
              on pre_final (no tool layer there). */}
          {draft.lifecycle === "after_tool_use" ? (
            <div className="space-y-1">
              <TextField
                value={draft.llmToolMatch}
                onChange={(v) => update({ llmToolMatch: v })}
                label="Tool name(s) to match (comma-separated, exact match)"
                placeholder="fetch_url, web_search"
                mono
              />
              <p className="text-[11px] leading-relaxed text-secondary">
                The critic only fires for these tool names. Required by the
                runtime gate — leave empty and the wizard refuses to save.
                One name per rule is fine; commas split a multi-tool list.
              </p>
            </div>
          ) : null}
          <TextField
            value={draft.criterion}
            onChange={(v) => update({ criterion: v })}
            label="LLM criterion (single sentence)"
            placeholder="The answer cites at least one source."
          />
          {/* PR-F6.5 — deterministic contentMatch pre-filter on after-tool
              llm_criterion rules. The runtime gate only invokes the LLM
              critic when the tool output matches the pattern. Surface this
              ONLY on the after-tool branch: pre_final rules do not see a
              tool result text, so contentMatch is rejected upstream by
              `_validate_content_match`. */}
          {draft.lifecycle === "after_tool_use" ? (
            <div className="rounded-xl border border-black/[0.08] bg-gray-50/60 px-3 py-2.5 text-xs">
              <label className="flex items-start gap-2 text-foreground">
                <input
                  type="checkbox"
                  checked={draft.llmContentMatchEnabled}
                  onChange={(e) =>
                    update({ llmContentMatchEnabled: e.target.checked })
                  }
                  className="mt-0.5 rounded border-black/[0.20] text-primary focus:ring-primary/30"
                />
                <span>
                  <span className="font-semibold">
                    Add a regex pre-filter (only invoke the critic when the
                    tool output matches)
                  </span>
                  <span className="mt-0.5 block text-[11px] leading-relaxed text-secondary">
                    Optional deterministic gate in front of the advisory
                    LLM check. Keeps critic cost low and adds a byte-stable
                    pre-condition before the model runs.
                  </span>
                </span>
              </label>
              {draft.llmContentMatchEnabled ? (
                <div className="mt-3 space-y-2 border-t border-black/[0.06] pt-3">
                  <TextField
                    value={draft.llmContentMatchPattern}
                    onChange={(v) => update({ llmContentMatchPattern: v })}
                    label="Pre-filter pattern"
                    placeholder={
                      draft.llmContentMatchIsRegex
                        ? "AKIA[0-9A-Z]{16}"
                        : "AWS_SECRET"
                    }
                    mono
                  />
                  <label className="flex items-center gap-2 text-xs text-secondary">
                    <input
                      type="checkbox"
                      checked={draft.llmContentMatchIsRegex}
                      onChange={(e) =>
                        update({ llmContentMatchIsRegex: e.target.checked })
                      }
                      className="rounded border-black/[0.20] text-primary focus:ring-primary/30"
                    />
                    Treat as regular expression
                  </label>
                  <label className="flex items-center gap-2 text-xs text-secondary">
                    <input
                      type="checkbox"
                      checked={draft.llmContentMatchNegate}
                      onChange={(e) =>
                        update({ llmContentMatchNegate: e.target.checked })
                      }
                      className="rounded border-black/[0.20] text-primary focus:ring-primary/30"
                    />
                    Negate (invoke critic when the output does NOT match)
                  </label>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
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
      {draft.conditionKind === "field_constraint" ? (
        <FieldConstraintPicker
          draft={draft}
          update={update}
          liveCatalogTypes={liveCatalogTypes}
        />
      ) : null}
    </div>
  );
}


// ---------------------------------------------------------------------------
// PR-F3 — field_constraint picker (evidence type → field → operator → value)
// ---------------------------------------------------------------------------


/**
 * Picker for the deterministic SHACL-via-picker path. Only types with a
 * non-empty `registeredFields` vocabulary are shown — silently letting
 * the user author a shape against a producer-less type would compile to a
 * vacuous SHACL shape (no triples, always passes) and that's exactly the
 * silent-non-firing failure the live-catalog filter is designed to prevent.
 *
 * Operator catalogue (deterministic; no LLM at compile time):
 *  - eq / neq / gt / lt / ge / le : single-record value comparison on
 *    magi:field_<fcField>. Numeric operators expect a number in fcValue
 *    and route to xsd:decimal at compile time.
 *  - exists / notExists : single-record cardinality only; no value input.
 *  - forEachExistsCovering : cross-record cardinality. Hides the single
 *    value input and surfaces (source.field, target.type, target.field)
 *    instead. Spec §5 acceptance #1: "each changed file covered by passing
 *    TestRun" lowers to this operator with covering = source.entry.
 */
function FieldConstraintPicker({
  draft,
  update,
  liveCatalogTypes,
}: {
  draft: Draft;
  update: (patch: Partial<Draft>) => void;
  liveCatalogTypes: EvidenceLiveCatalogTypeEntry[];
}): React.ReactElement {
  // Inert-producer hide invariant: only surface types that have a
  // non-empty registered field vocabulary. Producer-less types would
  // compile to a vacuous shape.
  const authorableTypes = useMemo(
    () => liveCatalogTypes.filter((t) => t.registeredFields.length > 0),
    [liveCatalogTypes],
  );
  const selectedType = authorableTypes.find(
    (t) => t.type === draft.fcEvidenceType,
  );
  const isCrossRecord = draft.fcOperator === "forEachExistsCovering";
  const valueless =
    draft.fcOperator === "exists" || draft.fcOperator === "notExists";
  const numeric =
    draft.fcOperator === "gt"
    || draft.fcOperator === "lt"
    || draft.fcOperator === "ge"
    || draft.fcOperator === "le";
  const crossTargetType = authorableTypes.find(
    (t) => t.type === draft.fcCrossTargetType,
  );

  if (authorableTypes.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs text-secondary">
        No evidence types with a registered field vocabulary are available.
        Field constraints require the producer to publish at least one field
        — see the docs for extending an evidence producer.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {/* Step 3a: evidence type picker (inert-producer types hidden).
          Plain div with role=group rather than a HTML form group so the
          TriggerStep group-count assertion stays at 2 — the other radio
          groups in this picker follow the same pattern. */}
      <div role="group" aria-label="Evidence type" className="space-y-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Evidence type
        </span>
        <div className="space-y-2">
          {authorableTypes.map((t) => (
            <RadioCard
              key={t.type}
              checked={draft.fcEvidenceType === t.type}
              onClick={() =>
                update({ fcEvidenceType: t.type, fcField: "" })
              }
              label={t.type}
              description={`${t.registeredFields.length} field${
                t.registeredFields.length === 1 ? "" : "s"
              } · ${t.fieldsPopulatedRecently.length} populated recently`}
              monoLabel={t.type}
            />
          ))}
        </div>
      </div>

      {/* Step 3b: field picker (only after a type is chosen) */}
      {selectedType ? (
        selectedType.registeredFields.length === 0 ? (
          <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs text-secondary">
            No fields available — producer extension needed.
          </p>
        ) : (
          <div role="group" aria-label="Field" className="space-y-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
              Field
            </span>
            <div className="space-y-2">
              {selectedType.registeredFields.map((f) => (
                <RadioCard
                  key={f}
                  checked={draft.fcField === f}
                  onClick={() => update({ fcField: f })}
                  label={f}
                  description=""
                  monoLabel={`magi:field_${f}`}
                />
              ))}
            </div>
          </div>
        )
      ) : null}

      {/* Step 3c: operator picker (always available once a field is chosen) */}
      {draft.fcField ? (
        <label className="block">
          <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
            Operator
          </span>
          <select
            value={draft.fcOperator}
            onChange={(e) =>
              update({ fcOperator: e.target.value as FieldOperator })
            }
            aria-label="Operator"
            className="mt-1 w-full rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          >
            <option value="eq">equals (eq)</option>
            <option value="neq">not equals (neq)</option>
            <option value="gt">greater than (gt)</option>
            <option value="lt">less than (lt)</option>
            <option value="ge">greater or equal (ge)</option>
            <option value="le">less or equal (le)</option>
            <option value="exists">exists</option>
            <option value="notExists">not exists (notExists)</option>
            <option value="forEachExistsCovering">
              for-each-exists-covering (cross-record)
            </option>
          </select>
        </label>
      ) : null}

      {/* Step 3d: value input — hidden for exists / notExists, and
          replaced by the cross-record sub-form for forEachExistsCovering. */}
      {draft.fcField && !valueless && !isCrossRecord ? (
        <TextField
          value={draft.fcValue}
          onChange={(v) => update({ fcValue: v })}
          label={numeric ? "Value (number)" : "Value"}
          placeholder={numeric ? "0" : "expected"}
          mono
        />
      ) : null}

      {/* Cross-record sub-form: source.field is the same as (fcEvidenceType,
          fcField) above. Add target.evidenceType + target.field here. */}
      {draft.fcField && isCrossRecord ? (
        <div className="space-y-3 rounded-xl border border-black/[0.06] bg-gray-50/60 p-3">
          <p className="text-[11px] text-secondary">
            For each entry in <code>{draft.fcEvidenceType}.{draft.fcField}</code>
            , assert that a covering record exists in the target type below.
          </p>
          <div role="group" aria-label="Target evidence type" className="space-y-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
              Target evidence type
            </span>
            <div className="space-y-2">
              {authorableTypes.map((t) => (
                <RadioCard
                  key={t.type}
                  checked={draft.fcCrossTargetType === t.type}
                  onClick={() =>
                    update({
                      fcCrossTargetType: t.type,
                      fcCrossTargetField: "",
                      // Mirror source picks into the cross-record IR so the
                      // payload writer doesn't have to special-case both
                      // shapes.
                      fcCrossSourceType: draft.fcEvidenceType,
                      fcCrossSourceField: draft.fcField,
                    })
                  }
                  label={t.type}
                  description={`${t.registeredFields.length} field${
                    t.registeredFields.length === 1 ? "" : "s"
                  }`}
                  monoLabel={t.type}
                />
              ))}
            </div>
          </div>
          {crossTargetType
          && crossTargetType.registeredFields.length > 0 ? (
            <div role="group" aria-label="Target field" className="space-y-2">
              <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
                Target field (covering key)
              </span>
              <div className="space-y-2">
                {crossTargetType.registeredFields.map((f) => (
                  <RadioCard
                    key={f}
                    checked={draft.fcCrossTargetField === f}
                    onClick={() => update({ fcCrossTargetField: f })}
                    label={f}
                    description=""
                    monoLabel={`magi:field_${f}`}
                  />
                ))}
              </div>
            </div>
          ) : null}
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
// Step — Action archetype (filtered by lifecycle; header reflects trigger)
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
 * compose the dynamic Action-step header so positive vs negative
 * semantics survive the unification: pre_final rules fire on a check
 * FAILURE; before_tool_use rules fire on a positive MATCH; after_tool_use
 * regex fires on a positive match; an LLM criterion always fires when the
 * critic returns NO (so the framing is consistent across lifecycles).
 */
function triggerEventPhrase(draft: Draft, refOptions: RefOption[]): string {
  const targetPhrase = targetEventPhrase(draft);
  switch (draft.conditionKind) {
    case "none":
      return targetPhrase;
    case "domain":
      return `When ${lowerHead(targetPhrase)} fetches "${draft.domain || "…"}"`;
    case "domain_allowlist":
      return `When ${lowerHead(targetPhrase)} fetches a host outside the allowlist`;
    case "path":
      return `When ${lowerHead(targetPhrase)} touches a path under "${draft.pathPrefix || "…"}"`;
    case "path_allowlist":
      return `When ${lowerHead(targetPhrase)} touches a path outside the allowed prefixes`;
    case "regex":
      return `When ${lowerHead(targetPhrase)}'s output ${draft.regexIsRegex ? "matches the regex" : "contains"} "${draft.regexPattern || "…"}"`;
    case "llm_criterion":
      return `When the LLM critic judges "${draft.criterion || "…"}" is false`;
    case "evidence_ref": {
      const ref = refOptions.find((r) => r.ref === draft.evidenceRef);
      return `When evidence "${ref?.label ?? (draft.evidenceRef || "…")}" did NOT return ok`;
    }
    case "shacl":
      return "When the SHACL shape does NOT conform on any evidence record";
    case "field_constraint":
      return fieldConstraintTriggerPhrase(draft);
  }
}


function fieldConstraintTriggerPhrase(draft: Draft): string {
  const ev = draft.fcEvidenceType || "…";
  const f = draft.fcField || "…";
  switch (draft.fcOperator) {
    case "exists":
      return `When ${ev}.${f} is missing`;
    case "notExists":
      return `When ${ev}.${f} is present`;
    case "forEachExistsCovering":
      return `When some entry in ${ev}.${f} has no covering ${
        draft.fcCrossTargetType || "…"
      }.${draft.fcCrossTargetField || "…"}`;
    default:
      return `When ${ev}.${f} ${draft.fcOperator} ${draft.fcValue || "…"} is false`;
  }
}


function targetEventPhrase(draft: Draft): string {
  if (draft.lifecycle === "pre_final") return "Before the final answer commits";
  if (draft.lifecycle === "before_tool_use") {
    return draft.toolTarget === "specific"
      ? `Before "${draft.toolName || "…"}" runs`
      : "Before any tool runs";
  }
  // after_tool_use
  return draft.toolTarget === "specific"
    ? `After "${draft.toolName || "…"}" returns`
    : "After any tool returns";
}


function lowerHead(s: string): string {
  return s.charAt(0).toLowerCase() + s.slice(1);
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
// Step — Name
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
// Step — Review
// ---------------------------------------------------------------------------


/**
 * F5 — derive the honesty trust class an operator can place in this draft.
 *
 * The wizard's only Advisory authoring path today is `llm_criterion`
 * (the LLM critic): the rule is surfaced to the model as guidance and
 * may be ignored. Every other conditionKind (evidence_ref / shacl /
 * field_constraint / tool_perm / regex / domain* / none) routes to a
 * deterministic runtime gate the model cannot opt out of.
 *
 * Keyed on `conditionKind` (not lifecycle / archetype) so adding a new
 * Advisory authoring kind in the future re-classifies here, not at the
 * call site.
 */
function trustClassForDraft(draft: Draft): TrustClass {
  if (draft.conditionKind === "llm_criterion") return "advisory";
  return "deterministic";
}


function ReviewStep({
  draft,
  refOptions,
}: { draft: Draft; refOptions: RefOption[] }): React.ReactElement {
  const trust = trustClassForDraft(draft);
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Review</h2>
      <p className="text-xs text-secondary">
        Saving applies the policy to the runtime immediately.
      </p>
      <div className="rounded-xl border border-black/[0.06] bg-[var(--glass-regular-bg)] backdrop-blur-xl p-4">
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold text-foreground">What this policy does</p>
          <TrustBadge trustClass={trust} />
        </div>
        <p className="mt-1 text-xs leading-relaxed text-foreground">
          {describePolicy(draft, refOptions)}
        </p>
        <hr className="my-3 border-black/[0.05]" />
        <dl className="grid grid-cols-[7rem_1fr] gap-y-1.5 text-xs">
          <dt className="text-secondary">ID</dt>
          <dd className="font-mono text-foreground">{draft.ruleId || "(unnamed)"}</dd>
          <dt className="text-secondary">When</dt>
          <dd>{draft.scope} · {draft.lifecycle}</dd>
          {draft.lifecycle !== "pre_final" ? (
            <>
              <dt className="text-secondary">Target</dt>
              <dd>{draft.toolTarget === "any" ? "any tool" : draft.toolName || "(unnamed tool)"}</dd>
            </>
          ) : null}
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
      ? whenForLifecycle(draft)
      : `On ${draft.scope} turns, ${lowerHead(whenForLifecycle(draft))}`;
  const archVerb = archetypeVerb(draft);
  if (draft.conditionKind === "none") {
    return `${whenClause}, ${archVerb} on every matching call.`;
  }
  const condClause = conditionClause(draft, refOptions);
  return `${whenClause}, ${archVerb} when ${condClause}.`;
}


function whenForLifecycle(draft: Draft): string {
  if (draft.lifecycle === "pre_final") return "Before the final answer commits";
  if (draft.lifecycle === "before_tool_use") {
    return draft.toolTarget === "specific"
      ? `Before "${draft.toolName}" runs`
      : "Before any tool runs";
  }
  return draft.toolTarget === "specific"
    ? `After "${draft.toolName}" returns`
    : "After any tool returns";
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
    case "domain":
      return `the fetch domain is ${draft.domain}`;
    case "domain_allowlist":
      return `the fetch domain is NOT in [${csv(draft.domainAllowlist)}]`;
    case "path":
      return `the tool's path argument is at or under ${draft.pathPrefix}`;
    case "path_allowlist":
      return `the tool's path argument is NOT under any of [${csv(draft.pathAllowlist)}]`;
    case "evidence_ref": {
      const ref = refOptions.find((r) => r.ref === draft.evidenceRef);
      return `evidence "${ref?.label ?? draft.evidenceRef}" did not return ok`;
    }
    case "shacl":
      return "the SHACL shape does NOT conform on any evidence record";
    case "llm_criterion": {
      let base = `an LLM critic judges "${draft.criterion}" is false`;
      // PR-F6.5 BLOCKER fix: prefix the per-rule tool filter on after-tool
      // rules so the operator sees which tool(s) the critic actually runs
      // against. Mirrors the runtime gate's exact-membership check.
      if (draft.lifecycle === "after_tool_use") {
        const tools = splitToolMatchList(draft.llmToolMatch);
        if (tools.length > 0) {
          base = `for tool ${tools.map((t) => `"${t}"`).join(" / ")}, ${base}`;
        }
      }
      // PR-F6.5: surface the deterministic regex pre-filter so the operator
      // sees the combo (regex gate → critic) at review time. Mirrors the
      // runtime: critic only runs when the pre-filter matches.
      if (
        draft.lifecycle === "after_tool_use"
        && draft.llmContentMatchEnabled
        && draft.llmContentMatchPattern.trim().length > 0
      ) {
        // Imperative verb under negate ("does NOT match regex" / "does NOT
        // contain") to keep the sentence grammatical.
        const positive = draft.llmContentMatchIsRegex
          ? "matches regex"
          : "contains";
        const baseVerb = draft.llmContentMatchIsRegex
          ? "match regex"
          : "contain";
        const clause = draft.llmContentMatchNegate
          ? ` does NOT ${baseVerb}`
          : ` ${positive}`;
        return `${base} (with pre-filter: critic invoked only when output${clause} "${draft.llmContentMatchPattern.trim()}")`;
      }
      return base;
    }
    case "regex":
      return `the result ${draft.regexIsRegex ? "matches regex" : "contains"} "${draft.regexPattern}"`;
    case "field_constraint":
      return fieldConstraintClause(draft);
  }
}


function fieldConstraintClause(draft: Draft): string {
  const ev = draft.fcEvidenceType || "(unset)";
  const f = draft.fcField || "(unset)";
  switch (draft.fcOperator) {
    case "exists":
      return `${ev}.${f} is missing`;
    case "notExists":
      return `${ev}.${f} is present`;
    case "forEachExistsCovering":
      return `some entry in ${ev}.${f} has no covering ${
        draft.fcCrossTargetType || "(unset)"
      }.${draft.fcCrossTargetField || "(unset)"}`;
    default:
      return `${ev}.${f} ${draft.fcOperator} ${draft.fcValue || "(unset)"} is false`;
  }
}


function csv(s: string): string {
  return s.split(",").map((x) => x.trim()).filter(Boolean).join(", ");
}


// ---------------------------------------------------------------------------
// Step completion + activation routing
// ---------------------------------------------------------------------------


function stepIsComplete(currentKey: StepKey, draft: Draft): boolean {
  switch (currentKey) {
    case "trigger":
      return !!draft.lifecycle && !!draft.scope;
    case "target":
      return draft.toolTarget === "any" || draft.toolName.trim().length > 0;
    case "condition":
      return !!draft.conditionKind;
    case "specifics":
      switch (draft.conditionKind) {
        case "none":
          return true;
        case "domain":
          return draft.domain.trim().length > 0;
        case "domain_allowlist":
          return draft.domainAllowlist.trim().length > 0;
        case "path":
          return draft.pathPrefix.trim().length > 0;
        case "path_allowlist":
          return draft.pathAllowlist.trim().length > 0;
        case "evidence_ref":
          return draft.evidenceRef.length > 0;
        case "shacl":
          return draft.shapeTtl.trim().length > 0;
        case "llm_criterion":
          // PR-F6.5: when the after-tool contentMatch pre-filter is enabled,
          // the pattern must be non-empty — backend `_validate_content_match`
          // rejects an empty pattern. Criterion remains required for the
          // wizard's authoring path (the spec exposes contentMatch as an
          // optional add-on to the critic, not as a standalone gate).
          //
          // PR-F6.5 BLOCKER fix: after_tool_use also requires a non-empty
          // `toolMatch` list — backend validator
          // (`magi_agent/customize/custom_rules.py:185`) rejects any
          // after_tool_use llm_criterion payload without one with HTTP 400.
          // pre_final has no tool layer so the list is omitted.
          return (
            draft.criterion.trim().length > 0
            && (draft.lifecycle !== "after_tool_use"
              || splitToolMatchList(draft.llmToolMatch).length > 0)
            && (!draft.llmContentMatchEnabled
              || draft.llmContentMatchPattern.trim().length > 0)
          );
        case "regex":
          return draft.regexPattern.trim().length > 0;
        case "field_constraint":
          return fieldConstraintIsComplete(draft);
      }
    // eslint-disable-next-line no-fallthrough
    case "action":
      return !!draft.archetype;
    case "name":
      return /^[a-z0-9][a-z0-9_-]{0,127}$/.test(draft.ruleId);
    case "review":
      return true;
  }
}


type Built =
  | { kind: "custom_rule"; rule: CustomRule }
  | { kind: "dashboard_check"; check: DashboardCheck };


function buildPolicy(draft: Draft): Built {
  // ===== after_tool: DashboardCheck path (no condition + regex) =====
  if (
    draft.lifecycle === "after_tool_use"
    && (draft.conditionKind === "none" || draft.conditionKind === "regex")
    && (draft.archetype === "audit" || draft.archetype === "block")
  ) {
    const pattern = draft.conditionKind === "none" ? ".*" : draft.regexPattern.trim();
    const isRegex = draft.conditionKind === "none" ? true : draft.regexIsRegex;
    const tool = draft.toolTarget === "specific" ? draft.toolName.trim() : "*";
    const check: DashboardCheck = {
      id: draft.ruleId,
      label: draft.description || draft.ruleId,
      scope: draft.scope as DashboardScope,
      enabled: true,
      trigger: { tool, match: { pattern, isRegex } },
      action: draft.archetype === "audit" ? "audit" : "block",
    };
    return { kind: "dashboard_check", check };
  }

  // ===== Everything else routes to CustomRule. =====
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
  if (draft.lifecycle === "before_tool_use") {
    // before_tool authoring always routes to tool_perm: target=specific
    // sets match.tool; target=any with domain* sets the url-shape matcher.
    return "tool_perm";
  }
  if (draft.conditionKind === "evidence_ref") return "deterministic_ref";
  if (draft.conditionKind === "shacl") return "shacl_constraint";
  // PR-F3: field_constraint is the structured-picker path; it stores as
  // shacl_constraint on the backend with an authoredAs IR carried inside
  // the payload for round-trip editing.
  if (draft.conditionKind === "field_constraint") return "shacl_constraint";
  if (draft.conditionKind === "regex") return "llm_criterion"; // after-tool regex via LLM kind (the only path other than dashboard_check)
  return "llm_criterion";
}


/**
 * PR-F3: a field_constraint draft is "complete" once enough has been
 * picked that the backend deterministic compiler can synthesise a
 * non-vacuous shape. The branches mirror the picker's progressive
 * disclosure (type → field → operator → value or cross-record target).
 */
function fieldConstraintIsComplete(draft: Draft): boolean {
  if (!draft.fcEvidenceType.trim() || !draft.fcField.trim()) return false;
  if (draft.fcOperator === "exists" || draft.fcOperator === "notExists") {
    return true;
  }
  if (draft.fcOperator === "forEachExistsCovering") {
    return (
      draft.fcCrossTargetType.trim().length > 0
      && draft.fcCrossTargetField.trim().length > 0
    );
  }
  return draft.fcValue.trim().length > 0;
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


/**
 * PR-F6.5 BLOCKER fix — split the wizard's comma-separated tool-name list
 * into the `string[]` shape the backend validator expects (a non-empty list
 * is REQUIRED for every after_tool_use llm_criterion rule). Trimmed,
 * de-empty'd, no dedupe (backend treats duplicates as harmless membership).
 */
function splitToolMatchList(raw: string): string[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}


function customRulePayload(draft: Draft): Record<string, unknown> {
  // before_tool tool_perm: pick the matcher shape from target + condition.
  if (draft.lifecycle === "before_tool_use") {
    const decision = draft.archetype === "ask" ? "ask" : "deny";
    if (draft.toolTarget === "specific") {
      // Per the availableConditionKinds filter, this combo is always
      // condition=none — a per-tool unconditional rule.
      return {
        match: { tool: draft.toolName.trim() },
        decision,
      };
    }
    // target=any: condition supplies the matcher.
    if (draft.conditionKind === "domain") {
      return { match: { domain: draft.domain.trim() }, decision };
    }
    if (draft.conditionKind === "domain_allowlist") {
      return {
        match: {
          domainAllowlist: draft.domainAllowlist
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        },
        decision,
      };
    }
    if (draft.conditionKind === "path") {
      return { match: { path: draft.pathPrefix.trim() }, decision };
    }
    if (draft.conditionKind === "path_allowlist") {
      return {
        match: {
          pathAllowlist: draft.pathAllowlist
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        },
        decision,
      };
    }
    // No other combos are exposed.
    return { match: {}, decision };
  }

  // pre_final / after-tool llm_criterion paths.
  switch (draft.conditionKind) {
    case "evidence_ref":
      return { ref: draft.evidenceRef };
    case "shacl":
      return { shapeTtl: draft.shapeTtl.trim() };
    case "regex":
      return {
        contentMatch: { pattern: draft.regexPattern.trim(), isRegex: draft.regexIsRegex },
      };
    case "llm_criterion": {
      // PR-F6.5: after-tool llm_criterion may carry an optional deterministic
      // `contentMatch` regex pre-filter. The runtime gate uses it to skip the
      // (costly) LLM critic call entirely when the tool output does not match
      // — turning the combo into "deterministic pre-condition + advisory
      // critic". Pre-final rules never see a tool output so contentMatch is
      // omitted there (and would be rejected by `_validate_content_match`).
      const payload: Record<string, unknown> = {
        criterion: draft.criterion.trim(),
      };
      if (draft.lifecycle === "after_tool_use") {
        // BLOCKER fix: `toolMatch` is REQUIRED by the backend validator on
        // every after_tool_use llm_criterion rule. The runtime gate
        // (`after_tool_gate.py:150`) skips the rule unless `tool_name in
        // tool_match`, so the list is the per-rule tool filter the
        // wizard's top-level Target step cannot express for this combo.
        payload.toolMatch = splitToolMatchList(draft.llmToolMatch);
        if (
          draft.llmContentMatchEnabled
          && draft.llmContentMatchPattern.trim().length > 0
        ) {
          payload.contentMatch = {
            pattern: draft.llmContentMatchPattern.trim(),
            isRegex: draft.llmContentMatchIsRegex,
            negate: draft.llmContentMatchNegate,
          };
        }
      }
      return payload;
    }
    case "field_constraint":
      // Storage kind is shacl_constraint (see customRuleKind). The
      // structured IR rides in `authoredAs` so re-opening the rule in the
      // wizard surfaces chips, not raw TTL. `shapeTtl` is left empty here
      // and synthesised server-side by field_constraint_compiler so the
      // frontend never has to ship a partial Turtle builder.
      return {
        shapeTtl: "",
        authoredAs: {
          kind: "field_constraint",
          ...fieldConstraintIR(draft),
        },
      };
    default:
      return {};
  }
}


/**
 * Build the field_constraint authoredAs IR body (without the outer
 * `kind` discriminator — the caller adds that so the literal text
 * `kind: "field_constraint"` appears once next to `authoredAs:`).
 * Matches the schema in
 * docs/plans/2026-06-23-customize-depth-enrichment-design.md §5 PR-F3:
 * single-record form has `{operator, evidenceType, field, value?}`;
 * forEachExistsCovering form has `{operator, source:{type,field},
 * target:{type,field,covering}}`.
 */
function fieldConstraintIR(draft: Draft): Record<string, unknown> {
  const operator = draft.fcOperator;
  if (operator === "forEachExistsCovering") {
    return {
      operator,
      source: {
        evidenceType: draft.fcEvidenceType,
        field: draft.fcField,
      },
      target: {
        evidenceType: draft.fcCrossTargetType,
        field: draft.fcCrossTargetField,
        covering: "source.entry",
      },
    };
  }
  if (operator === "exists" || operator === "notExists") {
    return {
      operator,
      evidenceType: draft.fcEvidenceType,
      field: draft.fcField,
    };
  }
  return {
    operator,
    evidenceType: draft.fcEvidenceType,
    field: draft.fcField,
    value: draft.fcValue.trim(),
  };
}
