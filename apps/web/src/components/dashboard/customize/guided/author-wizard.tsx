"use client";

/**
 * Unified Author wizard — variable-length flow that covers all policy
 * authoring shapes the runtime currently supports.
 *
 * F1.5 restructure: tool targeting was previously conflated with the
 * per-call condition (a "Tool name" entry in the condition list). The
 * two are now distinct axes so the operator's mental model is:
 *   1. Which tool(s) does this policy apply to?
 *   2. Under what per-call condition does it fire?
 *
 * PR-F-UX3 restructure: the standalone "Target" step is collapsed back
 * into the "Trigger" step as a third sub-fieldset (tool-bearing lifecycles
 * only). The freeform tool-name text input is replaced with a dropdown
 * sourced from the runtime tool catalog (``catalog.tools``) so typo
 * risk is eliminated. The wizard is 6 steps for ALL lifecycles.
 *
 * Step ordering
 * -------------
 *   0. Trigger      (lifecycle + scope + tool target — the tool-target
 *                    sub-fieldset only renders for tool-bearing lifecycles)
 *   1. Condition    (per-call check, filtered by lifecycle + target)
 *   2. Specifics    (per-condition form; auto-skipped when condition=none)
 *   3. Action       (archetype, filtered by lifecycle; header phrasing
 *                    reflects the chosen condition trigger so positive
 *                    vs negative semantics survive)
 *   4. Name
 *   5. Review
 *
 * Total step count is a constant 6 across all lifecycles. The
 * tool-target sub-fieldset inside Trigger is the only piece that toggles
 * on lifecycle (hidden for pre_final / on_user_prompt_submit /
 * on_subagent_stop, where there is no tool layer).
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
import React, { useEffect, useMemo, useRef, useState } from "react";

import {
  getEvidenceLiveCatalog,
  putCustomRule,
  type CustomRule,
  type CustomizeCatalog,
  type EvidenceLiveCatalogTypeEntry,
  type ToolItem,
} from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";
import {
  putDashboardCheck,
  type DashboardCheck,
  type DashboardScope,
} from "@/lib/packs-dashboard-api";
import type { EvidenceTypeEntry } from "@/lib/policy-model";

import { TrustBadge, type TrustClass } from "../trust-badge";

import { RuntimeFieldChips } from "./runtime-field-chips";
import { RadioCard, WizardChrome } from "./wizard-chrome";


// ---------------------------------------------------------------------------
// Domain
// ---------------------------------------------------------------------------


type Lifecycle =
  | "before_tool_use"
  | "after_tool_use"
  | "pre_final"
  // PR-F-UX1 Tier 2 — bus-emitted gates with custom_rule paths wired in
  // magi_agent.customize.lifecycle_audit (audit-only, llm_criterion only).
  | "on_user_prompt_submit"
  | "on_subagent_stop";
type Scope = "always" | "coding" | "research" | "delivery" | "memory" | "task";
// PR-F-MUT3 — "mutate" is a friendly grouping archetype that surfaces the
// two mutator conditionKinds (prompt_injection + output_rewrite) as a
// first-class entry on the action picker. Selecting it routes the operator
// to the matching SpecificsStep picker (auto-set conditionKind based on
// lifecycle: before_tool_use / on_user_prompt_submit → prompt_injection;
// after_tool_use → output_rewrite). The backend customRuleKind /
// customRuleAction wiring already routes by conditionKind, so adding
// "mutate" here costs nothing at save time.
type Archetype = "block" | "ask" | "audit" | "strip" | "mutate";
type ToolTarget = "any" | "specific";
type ConditionKind =
  | "none"
  | "domain"
  | "domain_allowlist"
  | "path"
  | "path_allowlist"
  // PR-F-UX5 — two UX-distinct kinds that share the same backend payload
  // (kind: "deterministic_ref", payload: {ref}). The split is purely a
  // clarification of intent (raw evidence record vs verdict primitive).
  | "evidence_ref"
  | "verifier_passed"
  | "shacl"
  | "llm_criterion"
  | "regex"
  | "field_constraint"
  // PR-F-MUT1 — first mutator kind. UX is shared across two lifecycle slots
  // (before_tool_use → append to a tool's arg key; on_user_prompt_submit →
  // append a new system-prompt section). SpecificsStep branches on lifecycle
  // to render the right picker; both surfaces compile to the new backend
  // ``prompt_injection`` kind.
  | "prompt_injection"
  // PR-F-MUT2 — second mutator kind. Single lifecycle slot
  // (after_tool_use → re.sub-based redact of tool output text BEFORE the
  // model reads it). SpecificsStep renders the redact picker (pattern +
  // replacement + scope + isRegex); compiles to the new backend
  // ``output_rewrite`` kind.
  | "output_rewrite";


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
  // PR-F-MUT1 — prompt_injection draft fields. Both lifecycle slots share the
  // same shape; the wizard picks which to surface based on draft.lifecycle:
  // * before_tool_use → piTargetArgKey + piValue (+ optional condition_regex)
  // * on_user_prompt_submit → piValue (target hard-coded to "system_prompt")
  piTargetArgKey: string;
  piValue: string;
  piConditionEnabled: boolean;
  piConditionPattern: string;
  // PR-F-MUT2 — output_rewrite draft fields. Single lifecycle slot
  // (after_tool_use); shape compiles to the backend ``output_rewrite``
  // payload (mode locked to "redact" in v1; pattern + replacement + scope
  // + isRegex; toolMatch derived from draft.toolName when target=specific).
  orPattern: string;
  orReplacement: string;
  orScope: "match_only" | "full_output";
  orIsRegex: boolean;
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
  piTargetArgKey: "",
  piValue: "",
  piConditionEnabled: false,
  piConditionPattern: "",
  orPattern: "",
  orReplacement: "",
  orScope: "match_only",
  orIsRegex: true,
  ruleId: "",
  description: "",
};


// PR-F-UX3 — single constant 6-step plan for every lifecycle. The
// tool-target axis is folded into the Trigger step as a sub-fieldset
// rendered conditionally on lifecycle, so the step plan no longer
// branches on lifecycle. pre_final remains 6 steps and tool-bearing
// lifecycles collapse from 7 → 6.
type StepKey = "trigger" | "condition" | "specifics" | "action" | "name" | "review";

function stepPlan(lifecycle: Lifecycle): StepKey[] {
  if (lifecycle === "pre_final") {
    return ["trigger", "condition", "specifics", "action", "name", "review"];
  }
  // PR-F-UX1 Tier 2 — the two new lifecycle slots fire OUTSIDE the tool
  // boundary (one before the system prompt is assembled, the other after the
  // child's turn has emitted) so they have no tool target axis. Same step
  // shape as pre_final.
  if (lifecycle === "on_user_prompt_submit" || lifecycle === "on_subagent_stop") {
    return ["trigger", "condition", "specifics", "action", "name", "review"];
  }
  // Tool-bearing lifecycles (before_tool_use / after_tool_use): tool target
  // sub-fieldset renders inside TriggerStep, not as a separate step.
  return ["trigger", "condition", "specifics", "action", "name", "review"];
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
    // PR-F-UX3 — when the lifecycle changes to one with no tool layer
    // (pre_final / Tier 2 audit-only slots), force the tool target back to
    // ``any`` and clear the tool name. The tool-target sub-fieldset is
    // hidden for these lifecycles and we don't want a stale "specific" pick
    // bleeding into payloads / Review summaries.
    if (
      merged.lifecycle === "pre_final"
      || merged.lifecycle === "on_user_prompt_submit"
      || merged.lifecycle === "on_subagent_stop"
    ) {
      merged.toolTarget = "any";
      merged.toolName = "";
    }
    const kinds = availableConditionKinds(merged.lifecycle, merged.toolTarget);
    if (!kinds.includes(merged.conditionKind)) {
      merged.conditionKind = kinds[0] ?? "none";
    }
    const archetypes = availableArchetypes(merged.lifecycle);
    if (!archetypes.includes(merged.archetype)) {
      merged.archetype = archetypes[0];
    }
    // PR-F-MUT3 — when the operator picks the "Inject / Rewrite" archetype,
    // snap conditionKind to the matching mutator kind so the SpecificsStep
    // renders the right F-MUT picker without a second click. The map is
    // lifecycle-keyed because the two mutator kinds split by lifecycle:
    //   * after_tool_use      → output_rewrite (F-MUT2 redact picker)
    //   * before_tool_use     → prompt_injection (F-MUT1 tool-arg picker)
    //   * on_user_prompt_submit → prompt_injection (F-MUT1 system-prompt picker)
    // Reverse path: if conditionKind moves AWAY from a mutator kind (e.g. the
    // operator manually picks llm_criterion via the ConditionKind step), snap
    // archetype back to a non-mutate default so the Review summary stays
    // honest about which axis is driving the rule.
    if (merged.archetype === "mutate") {
      if (merged.lifecycle === "after_tool_use") {
        merged.conditionKind = "output_rewrite";
      } else if (
        merged.lifecycle === "before_tool_use"
        || merged.lifecycle === "on_user_prompt_submit"
      ) {
        merged.conditionKind = "prompt_injection";
      }
    } else if (
      merged.conditionKind === "prompt_injection"
      || merged.conditionKind === "output_rewrite"
    ) {
      // Operator picked a mutator conditionKind directly via ConditionKindStep
      // — promote archetype to "mutate" so the Action step + Review trust
      // badge agree with the actual rule shape. Hidden when the lifecycle
      // does not expose the "mutate" archetype card (defensive — should not
      // happen because availableConditionKinds + availableArchetypes are
      // gated by the same lifecycle set).
      if (archetypes.includes("mutate")) {
        merged.archetype = "mutate";
      }
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

  // PR-F-UX5 — two disjoint picker sources:
  //   * evidenceRefOptions reads catalog.evidenceMenu (raw evidence records,
  //     ``evidence:*``). Surfaced under the ``evidence_ref`` condition kind
  //     AND used as the type source for the field_constraint picker.
  //   * judgmentRefOptions reads catalog.judgmentMenu (verdict primitives:
  //     ``verifier:*`` and bare named judgments). Surfaced under the new
  //     ``verifier_passed`` condition kind.
  // Legacy ``refOptions`` is kept as the union so the existing trigger /
  // describe / review helpers can resolve a ref label regardless of which
  // bucket the user picked it from.
  const evidenceRefOptions = useMemo(
    () => buildRefOptionsFromMenu(catalog.verification.evidenceMenu, evidenceTypes),
    [catalog, evidenceTypes],
  );
  const judgmentRefOptions = useMemo(
    () => buildRefOptionsFromMenu(catalog.verification.judgmentMenu, []),
    [catalog],
  );
  const refOptions = useMemo(
    () => [...evidenceRefOptions, ...judgmentRefOptions],
    [evidenceRefOptions, judgmentRefOptions],
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
        <TriggerStep
          draft={draft}
          update={updateDraft}
          tools={catalog.tools}
        />
      ) : null}
      {currentKey === "condition" ? (
        <ConditionKindStep draft={draft} update={updateDraft} />
      ) : null}
      {currentKey === "specifics" ? (
        <SpecificsStep
          draft={draft}
          update={updateDraft}
          evidenceRefOptions={evidenceRefOptions}
          judgmentRefOptions={judgmentRefOptions}
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


// PR-F-UX1: lifecycle audit results.
//
// Tier 1 (gate exists, custom_rule wired today): before_tool_use, after_tool_use,
// pre_final. These are the legacy slots — every kind/action combo in
// custom_rules._LEGAL is authored against one of these three.
//
// Tier 2 (gate exists, custom_rule path wired in this PR): on_user_prompt_submit
// (BEFORE_SYSTEM_PROMPT bus event in runtime/message_builder) and
// on_subagent_stop (AFTER_TURN_END callback in the child runner). Both are
// audit-only at launch — backend ``_LEGAL`` restricts these slots to
// ``llm_criterion`` + ``audit``. Block at these slots would change the
// surrounding runtime contract (byte-identical prompt assembly /
// already-emitted child output) and is deferred to a later PR.
//
// Tier 3 (hook exists but no runtime emitter, OR audit-redundant): rendered as
// DISABLED radio cards with an honest tooltip pointing operators at file hooks
// (~/.magi/settings.json). Surfacing them keeps the UI honest about what the
// runtime can/cannot enforce; hiding them would invite operators to assume
// they don't exist.
type LifecycleTier = "tier1" | "tier2" | "tier3";

interface LifecycleOption {
  id: Lifecycle | string;
  label: string;
  description: string;
  tier: LifecycleTier;
  disabledReason?: string;
}

const LIFECYCLE_OPTIONS: ReadonlyArray<LifecycleOption> = [
  // --- Tier 1 — wired today ------------------------------------------------
  {
    id: "before_tool_use",
    label: "Before a tool runs",
    description: "Fires at PreToolUse — before the agent invokes a tool.",
    tier: "tier1",
  },
  {
    id: "after_tool_use",
    label: "After a tool returns",
    description: "Fires at PostToolUse — before the agent reads the tool's output.",
    tier: "tier1",
  },
  {
    id: "pre_final",
    label: "Before the final answer commits",
    description: "Fires just before the runtime accepts the agent's final answer.",
    tier: "tier1",
  },
  // --- Tier 2 — wired in PR-F-UX1, audit-only ------------------------------
  {
    id: "on_user_prompt_submit",
    label: "When the user submits a prompt (audit-only)",
    description:
      "Fires at BEFORE_SYSTEM_PROMPT — adjacent to system-prompt assembly. Audit-only: records the criterion verdict without mutating the assembled prompt.",
    tier: "tier2",
  },
  {
    id: "on_subagent_stop",
    label: "When a subagent finishes a turn (audit-only)",
    description:
      "Fires at AFTER_TURN_END — adjacent to the child-runner's turn-end callback. Audit-only: the child output has already been emitted, so blocks aren't honest at this slot.",
    tier: "tier2",
  },
  // --- Tier 3 — visible but disabled (file hook only) ----------------------
  {
    id: "before_llm_call",
    label: "Before each LLM call",
    description:
      "(file hook only — author via ~/.magi/settings.json) Fires every LLM call inside a turn; high blast radius, mostly subsumed by pre_final.",
    tier: "tier3",
    disabledReason:
      "No custom_rule gate yet — file hooks via ~/.magi/settings.json instead.",
  },
  {
    id: "after_llm_call",
    label: "After each LLM call",
    description:
      "(file hook only — author via ~/.magi/settings.json) Per-LLM-call inspection; redundant with pre_final's egress gate.",
    tier: "tier3",
    disabledReason:
      "No custom_rule gate yet — file hooks via ~/.magi/settings.json instead.",
  },
  {
    id: "on_session_start",
    label: "When a session starts",
    description:
      "(file hook only — author via ~/.magi/settings.json) No runtime emitter; CC SessionStart hook fires from the loader.",
    tier: "tier3",
    disabledReason:
      "No custom_rule gate yet — file hooks via ~/.magi/settings.json instead.",
  },
  {
    id: "on_session_stop",
    label: "When a session stops",
    description:
      "(file hook only — author via ~/.magi/settings.json) No runtime emitter; CC Stop hook fires from the loader.",
    tier: "tier3",
    disabledReason:
      "No custom_rule gate yet — file hooks via ~/.magi/settings.json instead.",
  },
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


// PR-F-UX3 — tool-bearing lifecycles get a third sub-fieldset inside
// the Trigger step (Tool target). The standalone TargetStep was removed
// in F-UX3 to collapse the wizard back to a constant 6 steps. The
// helper below is reused by TriggerStep so the predicate stays in one
// place; it is also the gate stepIsComplete("trigger") consults.
function lifecycleHasToolTarget(lifecycle: Lifecycle): boolean {
  return lifecycle === "before_tool_use" || lifecycle === "after_tool_use";
}


function TriggerStep({
  draft,
  update,
  tools,
}: {
  draft: Draft;
  update: (patch: Partial<Draft>) => void;
  tools: ToolItem[];
}): React.ReactElement {
  const showToolTarget = lifecycleHasToolTarget(draft.lifecycle);
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <h2 className="text-lg font-bold text-foreground">When should this policy fire?</h2>
        <p className="text-xs text-secondary">
          Three axes: <em>when</em> in the agent's lifecycle, <em>on which
          kind of turn</em>, and (for tool-bearing lifecycles)
          <em> which tool(s)</em>. Pick one of each.
        </p>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Lifecycle event
        </legend>
        {LIFECYCLE_OPTIONS.map((opt) => {
          // Tier 3 entries render visible-but-disabled with an honest tooltip;
          // the operator sees the option exists in the runtime but learns it
          // needs a file hook authored via ~/.magi/settings.json instead.
          const isDisabled = opt.tier === "tier3";
          return (
            <RadioCard
              key={opt.id}
              checked={draft.lifecycle === (opt.id as Lifecycle)}
              onClick={() => update({ lifecycle: opt.id as Lifecycle })}
              label={opt.label}
              description={opt.description}
              disabled={isDisabled}
              disabledReason={opt.disabledReason}
            />
          );
        })}
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

      {showToolTarget ? (
        <fieldset className="space-y-2">
          <legend className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
            Tool target
          </legend>
          <p className="text-xs text-secondary">
            Which tool(s) does this policy apply to? Apply to every tool call,
            or narrow to a specific tool.
          </p>
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
          {draft.toolTarget === "specific" ? (
            <ToolNameSelect
              value={draft.toolName}
              onChange={(v) => update({ toolName: v })}
              tools={tools}
            />
          ) : null}
        </fieldset>
      ) : null}
    </div>
  );
}


// PR-F-UX3 — dropdown source for the tool-target sub-fieldset. Replaces
// the freeform TextField the F1.5 TargetStep used so a typo can no
// longer silently produce a no-match rule. Sorted by display name with
// the runtime's source/dangerous markers preserved as suffix hints; the
// stored value is the bare tool name (matches the backend tool_perm
// match.tool exact-string compare).
function ToolNameSelect({
  value,
  onChange,
  tools,
}: {
  value: string;
  onChange: (v: string) => void;
  tools: ToolItem[];
}): React.ReactElement {
  // Sort by name so the dropdown is stable across renders; the catalog
  // is already deduped by the customize-api fetcher.
  const sorted = [...tools].sort((a, b) => a.name.localeCompare(b.name));
  // If the current value is not in the catalog (e.g. wizard pre-filled
  // from a saved rule that referenced a removed/renamed tool), surface
  // it as a disambiguated option so the user can see it instead of the
  // selector silently snapping back to the placeholder.
  const valueInCatalog = sorted.some((t) => t.name === value);
  return (
    <label className="block">
      <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
        Tool name
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Tool name"
        className="mt-1 w-full rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
      >
        <option value="">Select a tool…</option>
        {!valueInCatalog && value ? (
          <option value={value}>{value} (not in catalog)</option>
        ) : null}
        {sorted.map((t) => {
          const suffix = t.dangerous ? " · dangerous" : "";
          return (
            <option key={t.name} value={t.name}>
              {t.name}
              {suffix}
            </option>
          );
        })}
      </select>
    </label>
  );
}


// ---------------------------------------------------------------------------
// Step — Condition kind (filtered by lifecycle + target)
// ---------------------------------------------------------------------------


function availableConditionKinds(
  lifecycle: Lifecycle,
  toolTarget: ToolTarget,
): ConditionKind[] {
  // PR-F-UX1 Tier 2 — both audit slots accept ``llm_criterion``. PR-F-MUT1
  // additionally exposes ``prompt_injection`` on on_user_prompt_submit (the
  // operator picks "append a section to the system prompt") while
  // on_subagent_stop stays llm_criterion-only because the turn has already
  // emitted (mutation has no honest target).
  if (lifecycle === "on_user_prompt_submit") {
    return ["llm_criterion", "prompt_injection"];
  }
  if (lifecycle === "on_subagent_stop") {
    return ["llm_criterion"];
  }
  // pre_final has no tool layer; target is ignored.
  if (lifecycle === "pre_final") {
    // PR-F3: field_constraint is the deterministic SHACL-via-picker path
    // and is the preferred default for evidence-shape rules — it sits
    // beside the raw `shacl` escape hatch (TTL textarea) for power users.
    // PR-F-UX5: ``verifier_passed`` joins beside ``evidence_ref`` so the
    // operator picks raw-evidence-record-present vs verdict-primitive-passed
    // as two distinct intents — both compile to ``deterministic_ref`` on
    // the backend, the split lives at the UX layer only.
    return ["evidence_ref", "verifier_passed", "shacl", "llm_criterion", "field_constraint"];
  }
  if (lifecycle === "before_tool_use") {
    if (toolTarget === "specific") {
      // tool_perm has no AND between tool name and url-shape matchers
      // (backend `tool_perm.py` only honors a single matcher key per rule),
      // so a per-tool rule can only fire unconditionally per call. Refusing
      // the AND combo here keeps the wizard from assembling a draft the
      // backend cannot save. The Specifics hint surfaces the same fact so
      // operators aren't left guessing where domain/path matchers went —
      // use target=any to author those.
      // PR-F-MUT1 — prompt_injection sits beside ``none`` for the per-tool
      // case: it appends a value to a chosen arg key on every call of the
      // chosen tool. condition.tool is auto-derived from draft.toolName.
      return ["none", "prompt_injection"];
    }
    // target=any: tool_perm has no wildcard matcher, so "no condition"
    // is omitted (no honest backend mapping). F6 adds path / path_allowlist
    // alongside domain / domain_allowlist — the backend tool_perm matcher
    // already supports both via match.path / match.pathAllowlist, firing
    // only for tools that surface a file/path argument.
    // PR-F-MUT1 — prompt_injection appears here too so an operator can
    // author "append X to <key> for any tool that surfaces <key>" without
    // pinning a single tool.
    return ["domain", "domain_allowlist", "path", "path_allowlist", "prompt_injection"];
  }
  // after_tool_use
  // PR-F-UX4 — liberalization: llm_criterion is now available under BOTH
  // target=any AND target=specific. The backend validator
  // (`magi_agent/customize/custom_rules.py:185`) requires a non-empty
  // `toolMatch` list on every after_tool_use llm_criterion rule, but it
  // does not care WHERE the wizard sourced that list from. When
  // target=specific the wizard auto-derives `toolMatch=[draft.toolName]`
  // in `customRulePayload`, hiding the duplicate-entry llmToolMatch text
  // field — same backend payload as the target=any path (one-tool list
  // with the chosen tool name) with no user re-typing.
  // PR-F-MUT2 — output_rewrite is the after-tool mutator: re.sub-based
  // redact of the tool result text BEFORE the model reads it. Available
  // under both tool-target modes; the wizard derives the toolMatch.include
  // filter from draft.toolName when target=specific.
  if (toolTarget === "specific") {
    return ["none", "regex", "llm_criterion", "output_rewrite"];
  }
  return ["none", "regex", "llm_criterion", "output_rewrite"];
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
    // PR-F-UX5 — labelled around the raw producer record (input shape) so the
    // operator picks "is a record of type X present?" distinctly from the
    // verdict-primitive form (verifier_passed).
    label: "Check evidence record present",
    description:
      "Raw evidence: fires when a producer-emitted record (e.g. evidence:git-diff) did NOT return ok this turn.",
  },
  verifier_passed: {
    // PR-F-UX5 — verdict-primitive picker. Compiles to the SAME backend
    // payload as evidence_ref (kind: deterministic_ref, payload: {ref}); the
    // split is a UX clarification so the operator sees raw-input vs verdict
    // as two distinct intents in the picker.
    label: "Check verifier / condition passed",
    description:
      "Verdict primitive: fires when a built-in verifier or a named user condition did NOT return ok this turn.",
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
  prompt_injection: {
    // PR-F-MUT1 — single meta entry; SpecificsStep branches on lifecycle to
    // render the right picker (tool-arg append vs system-prompt section
    // append). The Mutator trust badge (F-MUT3) makes it explicit that this
    // policy rewrites traffic.
    label: "Append context (mutator)",
    description:
      "Mutator: appends a value to a tool's argument (before tool use) or to the assembled system prompt (on user prompt submit). v1 is append-only.",
  },
  output_rewrite: {
    // PR-F-MUT2 — single meta entry; SpecificsStep renders the redact
    // picker (pattern + replacement + scope + isRegex). The Mutator trust
    // badge (F-MUT3) makes it explicit that this policy rewrites traffic.
    // ``summarize`` / ``replace`` modes are deferred to v2 with an
    // admin-tier flag.
    label: "Rewrite tool output (mutator)",
    description:
      "Mutator: redacts a pattern in a tool's output before the model reads it (regex or literal). v1 is redact-only — summarize / replace require a v2 admin-tier flag.",
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


/**
 * PR-F-UX5 — build picker options from a SINGLE catalog menu (evidenceMenu
 * OR judgmentMenu). The evidence-side caller also passes
 * ``catalog`` policy-derived ``evidenceTypes`` so user evidence refs
 * authored by other rules also appear in the picker; judgment-side callers
 * pass ``[]`` because authoring a verifier is not a user surface (verifiers
 * are runtime code per F-UX5 design principle 1).
 */
function buildRefOptionsFromMenu(
  menu: CustomizeCatalog["verification"]["evidenceMenu"],
  evidenceTypes: EvidenceTypeEntry[],
): RefOption[] {
  const out: RefOption[] = [];
  for (const item of menu) {
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
  evidenceRefOptions,
  judgmentRefOptions,
  liveCatalogTypes,
}: {
  draft: Draft;
  update: (patch: Partial<Draft>) => void;
  // PR-F-UX5 — split refOptions: evidence picker reads raw-evidence refs only;
  // verifier_passed picker reads verdict-primitive refs only. Field-constraint
  // picker (FieldConstraintPicker) keeps reading liveCatalogTypes which is
  // already evidence-shape-only (it filters by registeredFields presence).
  evidenceRefOptions: RefOption[];
  judgmentRefOptions: RefOption[];
  liveCatalogTypes: EvidenceLiveCatalogTypeEntry[];
}): React.ReactElement {
  // PR-F-UX2 — refs for cursor-aware chip insertion. One ref per chip-bearing
  // input; the chip click reads selectionStart from the right ref and splices
  // at the caret via :func:`insertAtCaret`.
  const regexInputRef = useRef<HTMLInputElement | null>(null);
  const criterionInputRef = useRef<HTMLInputElement | null>(null);
  const contentMatchInputRef = useRef<HTMLInputElement | null>(null);
  const shaclTextareaRef = useRef<HTMLTextAreaElement | null>(null);

  // PR-F-UX2 — resolve the runtime-fields tool target. When the wizard's
  // top-level Target step picked a specific tool, thread that name through
  // so ``tool_input.*`` expands to the real manifest input_schema; otherwise
  // pass null and the backend returns the generic marker + alias hints.
  const chipTool =
    draft.toolTarget === "specific" && draft.toolName.trim().length > 0
      ? draft.toolName.trim()
      : null;

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
        evidenceRefOptions.length === 0 ? (
          <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs text-secondary">
            No evidence records available in this runtime.
          </p>
        ) : (
          <div className="space-y-2">
            {evidenceRefOptions.map((opt) => (
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
      {/* PR-F-UX5 — verifier_passed picker reads judgmentMenu (verdict
          primitives). Same draft slot as evidence_ref (``evidenceRef``)
          because both compile to the same backend ``deterministic_ref``
          payload; the picker just narrows the visible inventory. */}
      {draft.conditionKind === "verifier_passed" ? (
        judgmentRefOptions.length === 0 ? (
          <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs text-secondary">
            No verifiers or named conditions available in this runtime.
          </p>
        ) : (
          <div className="space-y-2">
            {judgmentRefOptions.map((opt) => (
              <RadioCard
                key={opt.ref}
                checked={draft.evidenceRef === opt.ref}
                onClick={() => update({ evidenceRef: opt.ref })}
                label={opt.label}
                description={opt.description}
                badge={opt.origin === "user" ? "user" : "built-in"}
                monoLabel={opt.ref}
              />
            ))}
          </div>
        )
      ) : null}
      {draft.conditionKind === "shacl" ? (
        <div className="space-y-2">
          <RuntimeFieldChips
            lifecycle={draft.lifecycle}
            condition="shacl"
            tool={chipTool}
            onInsert={(token) =>
              insertAtCaret(shaclTextareaRef, draft.shapeTtl, token, (next) =>
                update({ shapeTtl: next }),
              )
            }
            label="Available evidence fields"
          />
          <label className="block">
            <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
              SHACL shape (Turtle)
            </span>
            <textarea
              ref={shaclTextareaRef}
              value={draft.shapeTtl}
              onChange={(e) => update({ shapeTtl: e.target.value })}
              rows={10}
              placeholder={SHACL_PLACEHOLDER}
              aria-label="SHACL shape"
              className="mt-1 w-full resize-y rounded-lg border border-primary/30 bg-white px-3 py-2 text-xs font-mono text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </label>
        </div>
      ) : null}
      {draft.conditionKind === "llm_criterion" ? (
        <div className="space-y-3">
          {/* PR-F6.5 BLOCKER fix — the backend validator
              (`magi_agent/customize/custom_rules.py:185`) REQUIRES a
              non-empty `toolMatch` list on every after_tool_use
              llm_criterion rule and the runtime gate matches by exact
              membership. Without this input the wizard always emitted a
              payload that PUT /custom-rules rejected with HTTP 400. Hidden
              on pre_final (no tool layer there).

              PR-F-UX4 — when toolTarget=specific the trigger step already
              named the tool, so the wizard auto-derives `toolMatch` from
              `draft.toolName` and renders a read-only chip here rather
              than asking the operator to retype it. The text input only
              appears under target=any where the multi-tool list is the
              only way to express a per-rule tool filter. */}
          {draft.lifecycle === "after_tool_use" && draft.toolTarget === "specific" ? (
            <div className="space-y-1">
              <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
                Tool match (from Trigger step)
              </span>
              <div className="mt-1 inline-flex items-center gap-2 rounded-lg border border-black/[0.10] bg-gray-50/80 px-3 py-1.5 text-xs font-mono text-foreground">
                Tool: {draft.toolName.trim() || "(none)"}
              </div>
              <p className="text-[11px] leading-relaxed text-secondary">
                Auto-derived from the Trigger step's tool pick. To match
                multiple tools, change Tool target to "Any tool" and supply
                a comma-separated list.
              </p>
            </div>
          ) : null}
          {draft.lifecycle === "after_tool_use" && draft.toolTarget !== "specific" ? (
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
          <div className="space-y-2">
            <RuntimeFieldChips
              lifecycle={draft.lifecycle}
              condition="llm_criterion"
              tool={chipTool}
              onInsert={(token) =>
                insertAtCaret(criterionInputRef, draft.criterion, token, (next) =>
                  update({ criterion: next }),
                )
              }
            />
            <TextField
              value={draft.criterion}
              onChange={(v) => update({ criterion: v })}
              label="LLM criterion (single sentence)"
              placeholder="The answer cites at least one source."
              inputRef={criterionInputRef}
            />
          </div>
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
                  <RuntimeFieldChips
                    lifecycle={draft.lifecycle}
                    condition="contentMatch"
                    tool={chipTool}
                    onInsert={(token) =>
                      insertAtCaret(
                        contentMatchInputRef,
                        draft.llmContentMatchPattern,
                        token,
                        (next) => update({ llmContentMatchPattern: next }),
                      )
                    }
                  />
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
                    inputRef={contentMatchInputRef}
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
          <RuntimeFieldChips
            lifecycle={draft.lifecycle}
            condition="regex"
            tool={chipTool}
            onInsert={(token) =>
              insertAtCaret(regexInputRef, draft.regexPattern, token, (next) =>
                update({ regexPattern: next }),
              )
            }
          />
          <TextField
            value={draft.regexPattern}
            onChange={(v) => update({ regexPattern: v })}
            label="Pattern"
            placeholder={draft.regexIsRegex ? "AKIA[0-9A-Z]{16}" : "secret"}
            mono
            inputRef={regexInputRef}
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
      {/* PR-F-MUT1 — prompt_injection picker. Two surfaces share the kind;
          SpecificsStep picks the right one based on the lifecycle the
          operator already selected upstream. */}
      {draft.conditionKind === "prompt_injection" ? (
        draft.lifecycle === "on_user_prompt_submit" ? (
          <PromptInjectionSystemPromptPicker draft={draft} update={update} />
        ) : (
          <PromptInjectionToolArgPicker draft={draft} update={update} />
        )
      ) : null}
      {/* PR-F-MUT2 — output_rewrite picker. Single surface (after_tool_use
          only). Renders pattern + replacement + scope + isRegex; the wizard
          derives toolMatch.include from draft.toolName when
          target=specific so the operator does not have to retype it. */}
      {draft.conditionKind === "output_rewrite" ? (
        <OutputRewriteRedactPicker draft={draft} update={update} />
      ) : null}
    </div>
  );
}


// ---------------------------------------------------------------------------
// PR-F-MUT1 — prompt_injection pickers
// ---------------------------------------------------------------------------


function PromptInjectionToolArgPicker({
  draft,
  update,
}: {
  draft: Draft;
  update: (patch: Partial<Draft>) => void;
}): React.ReactElement {
  return (
    <div className="space-y-3">
      <TextField
        value={draft.piTargetArgKey}
        onChange={(v) => update({ piTargetArgKey: v })}
        label="Tool argument key to append into"
        placeholder="command"
        mono
      />
      <label className="block">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Value to append (raw text)
        </span>
        <textarea
          value={draft.piValue}
          onChange={(e) => update({ piValue: e.target.value })}
          rows={3}
          placeholder=" --dry-run"
          aria-label="Value to append"
          className="mt-1 w-full resize-y rounded-lg border border-primary/30 bg-white px-3 py-2 text-xs font-mono text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </label>
      <div className="rounded-xl border border-black/[0.08] bg-gray-50/60 px-3 py-2.5 text-xs">
        <label className="flex items-start gap-2 text-foreground">
          <input
            type="checkbox"
            checked={draft.piConditionEnabled}
            onChange={(e) => update({ piConditionEnabled: e.target.checked })}
            className="mt-0.5 rounded border-black/[0.20] text-primary focus:ring-primary/30"
          />
          <span>
            <span className="font-semibold">
              Only fire when the existing argument matches a pattern
            </span>
            <span className="mt-0.5 block text-[11px] leading-relaxed text-secondary">
              Optional deterministic regex pre-filter on the inbound argument
              value. Leave off to fire on every matching tool call.
            </span>
          </span>
        </label>
        {draft.piConditionEnabled ? (
          <div className="mt-3 space-y-2 border-t border-black/[0.06] pt-3">
            <TextField
              value={draft.piConditionPattern}
              onChange={(v) => update({ piConditionPattern: v })}
              label="Pre-filter regex"
              placeholder="^rm"
              mono
            />
          </div>
        ) : null}
      </div>
      <p className="text-[11px] leading-relaxed text-secondary">
        v1 is append-only (the value is concatenated to the existing argument
        string). Replace mode is deferred to v2.
      </p>
    </div>
  );
}


function PromptInjectionSystemPromptPicker({
  draft,
  update,
}: {
  draft: Draft;
  update: (patch: Partial<Draft>) => void;
}): React.ReactElement {
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Target (auto)
        </span>
        <div className="mt-1 inline-flex items-center gap-2 rounded-lg border border-black/[0.10] bg-gray-50/80 px-3 py-1.5 text-xs font-mono text-foreground">
          system_prompt
        </div>
        <p className="text-[11px] leading-relaxed text-secondary">
          v1 only supports appending a section to the assembled system
          prompt. Replace mode is deferred to v2.
        </p>
      </div>
      <label className="block">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Section to append
        </span>
        <textarea
          value={draft.piValue}
          onChange={(e) => update({ piValue: e.target.value })}
          rows={5}
          placeholder="Always cite sources. Prefer concise, testable code."
          aria-label="Section to append"
          className="mt-1 w-full resize-y rounded-lg border border-primary/30 bg-white px-3 py-2 text-xs text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </label>
    </div>
  );
}


// ---------------------------------------------------------------------------
// PR-F-MUT2 — output_rewrite picker (after_tool_use redact mode)
// ---------------------------------------------------------------------------


function OutputRewriteRedactPicker({
  draft,
  update,
}: {
  draft: Draft;
  update: (patch: Partial<Draft>) => void;
}): React.ReactElement {
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Mode (auto)
        </span>
        <div className="mt-1 inline-flex items-center gap-2 rounded-lg border border-black/[0.10] bg-gray-50/80 px-3 py-1.5 text-xs font-mono text-foreground">
          redact
        </div>
        <p className="text-[11px] leading-relaxed text-secondary">
          v1 supports redact only — the matched substring is replaced with
          the replacement string. summarize / replace require a v2
          admin-tier flag.
        </p>
      </div>
      <TextField
        value={draft.orPattern}
        onChange={(v) => update({ orPattern: v })}
        label="Pattern to match in the tool output"
        placeholder={draft.orIsRegex ? "AKIA[0-9A-Z]{16}" : "AWS_SECRET"}
        mono
      />
      <TextField
        value={draft.orReplacement}
        onChange={(v) => update({ orReplacement: v })}
        label="Replacement string"
        placeholder="***"
        mono
      />
      <label className="flex items-center gap-2 text-xs text-secondary">
        <input
          type="checkbox"
          checked={draft.orIsRegex}
          onChange={(e) => update({ orIsRegex: e.target.checked })}
          className="rounded border-black/[0.20] text-primary focus:ring-primary/30"
        />
        Treat pattern as a regular expression (uncheck for a literal-string
        match)
      </label>
      <label className="block">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Scope
        </span>
        <select
          value={draft.orScope}
          onChange={(e) =>
            update({
              orScope: e.target.value as Draft["orScope"],
            })
          }
          aria-label="Rewrite scope"
          className="mt-1 w-full rounded-lg border border-primary/30 bg-white px-3 py-2 text-xs text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        >
          <option value="match_only">
            match_only — replace each match individually (default)
          </option>
          <option value="full_output">
            full_output — apply the substitution against the entire output
          </option>
        </select>
        <p className="mt-1 text-[11px] leading-relaxed text-secondary">
          In v1 both scopes route to <code>re.sub</code>, which only
          replaces matches; the axis exists so v2 can add a wrap-whole
          mode without re-shaping the persisted payload.
        </p>
      </label>
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


// PR-F-UX2: optional ``inputRef`` forwards the underlying <input> so the
// parent can read selectionStart/selectionEnd for cursor-aware chip
// insertion (RuntimeFieldChips). Backward-compatible: existing callers
// that don't pass ``inputRef`` are unaffected.
function TextField({
  value,
  onChange,
  label,
  placeholder,
  mono,
  inputRef,
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  placeholder?: string;
  mono?: boolean;
  inputRef?: React.Ref<HTMLInputElement>;
}): React.ReactElement {
  return (
    <label className="block">
      <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
        {label}
      </span>
      <input
        ref={inputRef}
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


// PR-F-UX2: cursor-aware chip insertion helper shared across the wizard's
// chip-bearing inputs. Reads the current selection from the input/textarea
// ref, splices the chip token at the caret, hands the new value to the
// draft updater, and restores the caret after React commits.
//
// Mirrors the pattern from apps/web/src/components/chat/chat-input.tsx
// (acceptSlash / acceptKb at lines 392-410 / 452-470) so the wizard's
// chip insertion feels identical to the chat input's slash/kb autocomplete.
function insertAtCaret(
  ref: React.RefObject<HTMLInputElement | HTMLTextAreaElement | null>,
  value: string,
  token: string,
  onChange: (next: string) => void,
): void {
  const el = ref.current;
  const start =
    el && typeof el.selectionStart === "number" ? el.selectionStart : value.length;
  const end =
    el && typeof el.selectionEnd === "number" ? el.selectionEnd : value.length;
  const next = value.slice(0, start) + token + value.slice(end);
  onChange(next);
  const caret = start + token.length;
  requestAnimationFrame(() => {
    if (!ref.current) return;
    try {
      ref.current.setSelectionRange(caret, caret);
      ref.current.focus();
    } catch {
      // setSelectionRange can throw if the element shape changed mid-flight;
      // failing the cursor restoration is non-fatal — the value is already
      // committed and the user can re-click to reposition.
    }
  });
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
  // PR-F-MUT3 — "mutate" appears in three lifecycle slots that carry a
  // mutator conditionKind today:
  //   * before_tool_use      → prompt_injection (append to tool args)
  //   * after_tool_use       → output_rewrite (redact tool result)
  //   * on_user_prompt_submit → prompt_injection (append system-prompt section)
  // The card is hidden on pre_final + on_subagent_stop because no mutator
  // hook is wired there (a turn that already emitted has no honest mutation
  // target). Selecting "mutate" snaps conditionKind to the matching kind in
  // reseedDownstream so the SpecificsStep renders the F-MUT picker.
  if (lifecycle === "before_tool_use") return ["block", "ask", "audit", "mutate"];
  if (lifecycle === "after_tool_use") return ["block", "audit", "strip", "mutate"];
  // PR-F-UX1 Tier 2 — both new slots are audit-only at the backend matrix.
  // Surfacing "block" here would let the wizard assemble a draft the backend
  // ``_LEGAL`` table rejects (cleaner to refuse the action here than at PUT).
  // PR-F-MUT3 — on_user_prompt_submit additionally accepts "mutate" because
  // prompt_injection is wired there (system-prompt section append). The
  // sibling slot on_subagent_stop stays audit-only (no mutator hook).
  if (lifecycle === "on_user_prompt_submit") {
    return ["audit", "mutate"];
  }
  if (lifecycle === "on_subagent_stop") {
    return ["audit"];
  }
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
  // PR-F-MUT3 — friendly grouping card that surfaces the two mutator
  // conditionKinds (prompt_injection + output_rewrite) as a first-class
  // action archetype. Selecting it sets conditionKind based on the active
  // lifecycle (handled by reseedDownstream) so the SpecificsStep picker
  // appears next. The label is intentionally "Inject / Rewrite" so the
  // operator sees the two concrete shapes the choice covers — the trust
  // badge in Review then renders Mutator (amber-yellow) honestly.
  mutate: {
    id: "mutate",
    label: "Inject / Rewrite (mutator)",
    description:
      "Inject a value into a tool call or system prompt, or rewrite a tool's output before the model reads it. Modifies traffic — the trust badge will show Mutator.",
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
    case "verifier_passed": {
      // PR-F-UX5 — verdict-primitive phrasing. Same draft slot as evidence_ref
      // (the wizard reuses ``evidenceRef`` for both pickers since storage is
      // shared); the trigger sentence flips to "verifier" wording.
      const ref = refOptions.find((r) => r.ref === draft.evidenceRef);
      return `When verifier "${ref?.label ?? (draft.evidenceRef || "…")}" did NOT return ok`;
    }
    case "shacl":
      return "When the SHACL shape does NOT conform on any evidence record";
    case "field_constraint":
      return fieldConstraintTriggerPhrase(draft);
    case "prompt_injection":
      // PR-F-MUT1 — mutator action ("append") is unconditional within its
      // matched scope (per-tool or per-prompt-submit). Phrase as the trigger
      // surface so the Action-step header still reads naturally.
      if (draft.lifecycle === "on_user_prompt_submit") {
        return "On every user prompt submission";
      }
      return `When ${lowerHead(targetPhrase)} is invoked`;
    case "output_rewrite":
      // PR-F-MUT2 — phrasing surfaces the pattern that drives the redact.
      // Unconditional within the matched scope: every tool result whose
      // text matches the pattern is rewritten before the model reads it.
      return `When ${lowerHead(targetPhrase)} contains "${draft.orPattern || "…"}"`;
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
  // PR-F-UX1 Tier 2 — surface the new lifecycle slots in plain English so the
  // ArchetypeStep header and the Review-step sentence stay honest.
  if (draft.lifecycle === "on_user_prompt_submit") {
    return "When the user submits a prompt";
  }
  if (draft.lifecycle === "on_subagent_stop") {
    return "When a subagent finishes a turn";
  }
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
          {draft.lifecycle !== "pre_final"
            && draft.lifecycle !== "on_user_prompt_submit"
            && draft.lifecycle !== "on_subagent_stop" ? (
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
  // PR-F-UX1 Tier 2 — describe the two new audit-only lifecycle slots.
  if (draft.lifecycle === "on_user_prompt_submit") {
    return "When the user submits a prompt";
  }
  if (draft.lifecycle === "on_subagent_stop") {
    return "When a subagent finishes a turn";
  }
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
    case "mutate":
      // PR-F-MUT3 — the SpecificsStep picker emits the exact mutation verb
      // (append / redact / inject); this verb sits in the Review summary
      // sentence and stays honest about the trust-class without restating
      // the specifics (those render via conditionClause below).
      return draft.lifecycle === "after_tool_use"
        ? "rewrite the tool output before the model reads it"
        : "inject context into the agent's next call";
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
    case "verifier_passed": {
      // PR-F-UX5 — verdict-primitive phrasing mirrors evidence_ref but is
      // labelled around the verifier (judgment), not the raw record.
      const ref = refOptions.find((r) => r.ref === draft.evidenceRef);
      return `verifier "${ref?.label ?? draft.evidenceRef}" did not return ok`;
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
    case "prompt_injection":
      // PR-F-MUT1 — review-summary phrasing. Two surfaces, one kind: pick the
      // tool-arg vs system-prompt form based on the lifecycle the operator
      // selected upstream.
      if (draft.lifecycle === "on_user_prompt_submit") {
        return `append "${draft.piValue}" as a new system-prompt section`;
      }
      {
        const target = draft.piTargetArgKey || "(unset)";
        const tool =
          draft.toolTarget === "specific" && draft.toolName.trim().length > 0
            ? ` on "${draft.toolName.trim()}"`
            : "";
        const cond =
          draft.piConditionEnabled
          && draft.piConditionPattern.trim().length > 0
            ? ` (only when arg matches /${draft.piConditionPattern.trim()}/)`
            : "";
        return `append "${draft.piValue}" to tool arg "${target}"${tool}${cond}`;
      }
    case "output_rewrite": {
      // PR-F-MUT2 — review-summary phrasing. Mode is locked to redact in
      // v1; surface the pattern + replacement so the operator sees the
      // exact mutation before activating.
      const tool =
        draft.toolTarget === "specific" && draft.toolName.trim().length > 0
          ? ` (only for "${draft.toolName.trim()}")`
          : "";
      const verb = draft.orIsRegex ? "regex" : "literal";
      return `redact ${verb} "${draft.orPattern || "(unset)"}" → "${draft.orReplacement}" in tool output${tool}`;
    }
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
      // PR-F-UX3 — tool-target gate folded into the Trigger step. For
      // tool-bearing lifecycles, the merged gate is (lifecycle && scope
      // && (target=any || (target=specific && toolName non-empty))).
      // For non-tool-bearing lifecycles (pre_final / on_user_prompt_submit
      // / on_subagent_stop) the tool-target axis does not render and
      // reseedDownstream forces toolTarget="any" so the gate simplifies
      // to (lifecycle && scope).
      if (!draft.lifecycle || !draft.scope) return false;
      if (!lifecycleHasToolTarget(draft.lifecycle)) return true;
      return (
        draft.toolTarget === "any"
        || (draft.toolTarget === "specific" && draft.toolName.trim().length > 0)
      );
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
        case "verifier_passed":
          // PR-F-UX5 — both kinds share the ``evidenceRef`` draft slot since
          // they compile to the same backend ``deterministic_ref`` payload.
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
          //
          // PR-F-UX4: when toolTarget=specific, the toolMatch list is
          // auto-derived from draft.toolName by customRulePayload (the
          // llmToolMatch text input is hidden in SpecificsStep). The
          // trigger step already enforces a non-empty toolName for
          // target=specific, so the after_tool_use gate is satisfied
          // automatically — the per-field gate here only needs to assert
          // the llmToolMatch list when target=any.
          return (
            draft.criterion.trim().length > 0
            && (draft.lifecycle !== "after_tool_use"
              || draft.toolTarget === "specific"
              || splitToolMatchList(draft.llmToolMatch).length > 0)
            && (!draft.llmContentMatchEnabled
              || draft.llmContentMatchPattern.trim().length > 0)
          );
        case "regex":
          return draft.regexPattern.trim().length > 0;
        case "field_constraint":
          return fieldConstraintIsComplete(draft);
        case "prompt_injection":
          // PR-F-MUT1 — the lifecycle decides which fields are required.
          // on_user_prompt_submit needs only piValue (target is locked);
          // before_tool_use needs both piTargetArgKey + piValue.
          if (draft.lifecycle === "on_user_prompt_submit") {
            return draft.piValue.length > 0;
          }
          return (
            draft.piTargetArgKey.trim().length > 0
            && draft.piValue.length > 0
          );
        case "output_rewrite":
          // PR-F-MUT2 — pattern + replacement are the only required fields
          // (mode is locked to "redact" in v1; scope/isRegex have safe
          // defaults; toolMatch is optional).
          return (
            draft.orPattern.trim().length > 0
            && draft.orReplacement.length > 0
          );
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
  // PR-F-MUT1 — prompt_injection routes to its own backend kind regardless of
  // lifecycle. Must come BEFORE the before_tool_use → tool_perm fallback so
  // an operator authoring an inject-on-shell_exec rule doesn't silently
  // downcast to a tool_perm deny.
  if (draft.conditionKind === "prompt_injection") return "prompt_injection";
  // PR-F-MUT2 — output_rewrite routes to its own backend kind. Same
  // precedence concern as prompt_injection: must precede any
  // lifecycle-keyed fallback so the wizard's mutator pick lands on the
  // right kind end-to-end.
  if (draft.conditionKind === "output_rewrite") return "output_rewrite";
  if (draft.lifecycle === "before_tool_use") {
    // before_tool authoring always routes to tool_perm: target=specific
    // sets match.tool; target=any with domain* sets the url-shape matcher.
    return "tool_perm";
  }
  // PR-F-UX5 — evidence_ref + verifier_passed BOTH compile to the same
  // backend ``deterministic_ref`` kind (with payload {ref}). The UX split is
  // purely a clarification of intent (raw evidence record vs verdict
  // primitive); persisted shape is identical, so existing custom_rules.py
  // validator + persisted rules round-trip unchanged.
  if (draft.conditionKind === "evidence_ref") return "deterministic_ref";
  if (draft.conditionKind === "verifier_passed") return "deterministic_ref";
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
  // PR-F-MUT1 — prompt_injection is a mutator: the backend ``_LEGAL`` matrix
  // restricts it to action=audit at both lifecycle slots (block has no
  // honest semantics — the mutation already happened by the time the audit
  // record is written). Force audit here so an operator who picked any
  // archetype upstream still produces a valid rule.
  if (draft.conditionKind === "prompt_injection") return "audit";
  // PR-F-MUT2 — output_rewrite is also audit-only at the backend
  // ``_LEGAL`` matrix (same fail-honest reason: by the time the rewrite
  // event is recorded, the mutation already happened). Force audit so any
  // archetype the operator picked upstream resolves to a valid action.
  if (draft.conditionKind === "output_rewrite") return "audit";
  switch (draft.archetype) {
    case "block":
      return "block";
    case "ask":
      return "ask_approval";
    case "audit":
      return "audit";
    case "strip":
      return "override";
    case "mutate":
      // PR-F-MUT3 — defensive fallback. When the operator picks the
      // "Inject / Rewrite" card, reseedDownstream snaps conditionKind to
      // prompt_injection / output_rewrite, both of which are intercepted by
      // the early-return mutator branches above. This case only fires if
      // an upstream caller hands customRuleAction a draft with
      // archetype="mutate" but a non-mutator conditionKind (today
      // unreachable; staying audit keeps the resulting rule honest with
      // the backend ``_LEGAL`` matrix).
      return "audit";
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
  // PR-F-MUT2 — output_rewrite payload. Single lifecycle slot
  // (after_tool_use); shape matches the backend
  // validate_output_rewrite_payload contract:
  //   {mode: "redact", pattern, replacement, scope, isRegex,
  //    toolMatch?: {include?: [str]}}
  // toolMatch.include is auto-derived from draft.toolName when
  // target=specific so the operator does not have to retype the tool name.
  if (draft.conditionKind === "output_rewrite") {
    const payload: Record<string, unknown> = {
      mode: "redact",
      pattern: draft.orPattern.trim(),
      replacement: draft.orReplacement,
      scope: draft.orScope,
      isRegex: draft.orIsRegex,
    };
    if (
      draft.toolTarget === "specific"
      && draft.toolName.trim().length > 0
    ) {
      payload.toolMatch = { include: [draft.toolName.trim()] };
    }
    return payload;
  }
  // PR-F-MUT1 — prompt_injection payload. Branches on lifecycle because the
  // two slots have different required-field shapes (see backend
  // validate_prompt_injection_payload):
  //   before_tool_use      → {mode, target_arg_key, value, condition?}
  //   on_user_prompt_submit → {mode, target=system_prompt, value, condition?}
  if (draft.conditionKind === "prompt_injection") {
    const value = draft.piValue;
    if (draft.lifecycle === "on_user_prompt_submit") {
      return {
        mode: "append",
        target: "system_prompt",
        value,
      };
    }
    // before_tool_use shape. Auto-derive condition.tool from draft.toolName
    // when target=specific so the operator does not have to retype it.
    const payload: Record<string, unknown> = {
      mode: "append",
      target_arg_key: draft.piTargetArgKey.trim(),
      value,
    };
    const condition: Record<string, unknown> = {};
    if (
      draft.toolTarget === "specific"
      && draft.toolName.trim().length > 0
    ) {
      condition.tool = draft.toolName.trim();
    }
    if (
      draft.piConditionEnabled
      && draft.piConditionPattern.trim().length > 0
    ) {
      condition.regex = draft.piConditionPattern.trim();
    }
    if (Object.keys(condition).length > 0) {
      payload.condition = condition;
    }
    return payload;
  }

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
    case "verifier_passed":
      // PR-F-UX5 — same backend payload for both UX kinds (kind:
      // deterministic_ref, payload: {ref}). The split lives only in the
      // wizard picker (evidenceMenu vs judgmentMenu); on disk the rules
      // are indistinguishable.
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
        //
        // PR-F-UX4: when toolTarget=specific, auto-derive the list from
        // draft.toolName so the operator does not have to retype the tool
        // name into the llmToolMatch field. SpecificsStep correspondingly
        // hides the llmToolMatch input under target=specific and renders
        // a read-only "Tool: <name>" chip instead. This is the per-combo
        // auto-derivation that F-UX4 unlocks: backend payload is identical
        // (one-tool list) but the wizard stops asking the same question
        // twice.
        payload.toolMatch =
          draft.toolTarget === "specific" && draft.toolName.trim().length > 0
            ? [draft.toolName.trim()]
            : splitToolMatchList(draft.llmToolMatch);
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
