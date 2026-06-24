/**
 * Unified Policy model — PR-E1.
 *
 * Kevin's 2026-06-22 architecture call: the user has one mental concept
 * ("Policy"), but the backend persists it in four disjoint shapes:
 *
 *   1. ``HarnessPresetItem`` (built-in policy template) + per-id
 *      ``preset_overrides[id]`` toggle (built-in policy)
 *   2. ``CustomRule`` (user custom policy with verifier inline)
 *   3. ``DashboardCheck`` (user after-tool policy with regex inline)
 *   4. ``SeamSpecDoc`` action (user override of a built-in policy template)
 *
 * The frontend collapses all four into a single :class:`Policy` view so the
 * Policies table renders one list with consistent toggle / edit / delete
 * affordances. Writes still go to the matching backend PUT route
 * (no backend migration here).
 *
 * Reusable byproducts (Evidence types + Conditions) are EXTRACTED from
 * this same :class:`Policy[]` list — there is no separate store for them.
 * A policy that emits an evidence ref auto-publishes that ref to the
 * Evidence types catalog; a policy whose condition carries a SHACL shape
 * / LLM criterion / regex pattern / tool-match pattern auto-publishes
 * that condition payload to the Conditions catalog. Both surfaces are
 * read-only — editing happens through the originating policy.
 */

import type {
  CustomRule,
  CustomizeCatalog,
  CustomizeOverrides,
  HarnessPresetItem,
  SeamSpecAction,
  SeamSpecDoc,
} from "./customize-api";
import type { DashboardCheck } from "./packs-dashboard-api";


// ---------------------------------------------------------------------------
// Unified Policy shape
// ---------------------------------------------------------------------------


export type PolicyOrigin = "builtin" | "user";


export type PolicySource =
  | "preset_seam"     // built-in PresetSeam (toggle in preset_overrides)
  | "custom_rule"     // user CustomRule (verifier inline)
  | "dashboard_check" // user DashboardCheck (after-tool regex)
  | "seam_spec";      // user SeamSpec doc (built-in override)


export type PolicyState =
  | "enabled"
  | "disabled"
  | "always-on"  // security/permission — toggle is locked
  | "preview";   // shipped but not wired


export type PolicyConditionKind =
  | "evidence_ref"      // require a named evidence ref to be present + ok
  | "shacl_constraint"  // SHACL shape against evidence records
  | "llm_criterion"     // LLM critic over text/state
  | "regex"             // regex match on a tool result or output
  | "tool_perm"         // tool name / domain match before invocation
  | "seam_action"       // built-in preset seam rewire (compound)
  | "none";             // built-in preview / always-on, condition is implicit


export interface PolicyCondition {
  kind: PolicyConditionKind;
  /** Compact 1-line summary of the condition for table rendering. */
  summary: string;
  /** Raw payload bag — shape depends on ``kind``. Consumed by the Conditions
   *  catalog extractor. */
  payload?: Record<string, unknown>;
}


export interface Policy {
  /** Stable key for React + de-dup. Format: ``<source>:<source-specific-id>``. */
  id: string;
  /** Human label. Built-in: HarnessPresetItem.title. User: rule id / label. */
  name: string;
  /** One-line description. Built-in copy or user-supplied. */
  description: string;
  origin: PolicyOrigin;
  source: PolicySource;
  /** Tri-state: enabled / disabled / always-on / preview. */
  state: PolicyState;
  /** When this policy fires (scope + lifecycle event). */
  when: { scope: string; firesAt: string };
  /** What the policy checks. ``kind="none"`` for built-in always-on. */
  condition: PolicyCondition;
  /** Runtime action on condition match/fail. */
  action: string;
  /** Affordance permissions — drives Edit / Toggle / Delete button visibility. */
  togglable: boolean;
  editable: boolean;
  deletable: boolean;
  /** Source-specific raw object the activate / save / delete routes need. */
  rawSource:
    | { kind: "preset_seam"; preset: HarnessPresetItem }
    | { kind: "custom_rule"; rule: CustomRule }
    | { kind: "dashboard_check"; check: DashboardCheck }
    | { kind: "seam_spec"; spec: SeamSpecDoc; actionIndex: number };
}


// ---------------------------------------------------------------------------
// Adapters: backend shape → Policy
// ---------------------------------------------------------------------------


function presetToPolicy(
  preset: HarnessPresetItem,
  presetOverrides: Record<string, boolean>,
): Policy {
  const enabledByOverride = presetOverrides[preset.id];
  const enabled =
    typeof enabledByOverride === "boolean" ? enabledByOverride : preset.defaultEnabled;
  const state: PolicyState =
    preset.enforcement === "always-on"
      ? "always-on"
      : preset.enforcement === "preview"
        ? "preview"
        : enabled
          ? "enabled"
          : "disabled";
  return {
    id: `preset_seam:${preset.id}`,
    name: preset.title,
    description: preset.description,
    origin: "builtin",
    source: "preset_seam",
    state,
    when: {
      scope: preset.domain,
      firesAt: preset.hookPoints[0] ?? "pre_final",
    },
    condition: { kind: "none", summary: preset.description },
    action: preset.enforcement === "always-on" ? "always-on" : "block",
    togglable: preset.enforcement === "enforcing",
    editable: preset.enforcement === "enforcing", // built-in editable via SeamSpec
    deletable: false,
    rawSource: { kind: "preset_seam", preset },
  };
}


function customRuleToPolicy(rule: CustomRule): Policy {
  const kind = (rule.what?.kind ?? "evidence_ref") as PolicyConditionKind;
  const payload = (rule.what?.payload ?? {}) as Record<string, unknown>;
  const condition = customRuleCondition(kind, payload);
  return {
    id: `custom_rule:${rule.id ?? Math.random().toString(36).slice(2)}`,
    name: rule.id ?? "(unnamed)",
    description: condition.summary,
    origin: "user",
    source: "custom_rule",
    state: rule.enabled ? "enabled" : "disabled",
    when: { scope: rule.scope ?? "always", firesAt: rule.firesAt ?? "pre_final" },
    condition,
    action: rule.action ?? "block",
    togglable: true,
    editable: true,
    deletable: !!rule.id,
    rawSource: { kind: "custom_rule", rule },
  };
}


function customRuleCondition(
  kind: PolicyConditionKind,
  payload: Record<string, unknown>,
): PolicyCondition {
  if (kind === "evidence_ref" || kind === "shacl_constraint" || kind === "llm_criterion" || kind === "tool_perm") {
    if (kind === "evidence_ref") {
      const ref = String(payload.ref ?? "");
      return { kind, summary: `Requires evidence: ${ref}`, payload: { ref } };
    }
    if (kind === "shacl_constraint") {
      const shape = String(payload.shapeTtl ?? "");
      const head = shape.slice(0, 50).replace(/\s+/g, " ");
      return {
        kind,
        summary: `SHACL shape${head ? `: ${head}…` : ""}`,
        payload: { shapeTtl: shape },
      };
    }
    if (kind === "llm_criterion") {
      const c = String(payload.criterion ?? "");
      return { kind, summary: `LLM critic: "${c}"`, payload: { criterion: c } };
    }
    // tool_perm
    const m = (payload.match ?? {}) as Record<string, unknown>;
    const verb = payload.decision === "ask" ? "Require approval for" : "Deny";
    let target = "";
    if (typeof m.tool === "string") target = `tool "${m.tool}"`;
    else if (typeof m.domain === "string") target = `domain ${m.domain}`;
    else if (Array.isArray(m.domainAllowlist))
      target = `outside [${(m.domainAllowlist as string[]).join(", ")}]`;
    return { kind, summary: `${verb} ${target}`, payload: { match: m, decision: payload.decision } };
  }
  return { kind: "evidence_ref", summary: "(unknown condition)" };
}


function dashboardCheckToPolicy(check: DashboardCheck): Policy {
  const pattern = check.trigger.match.pattern;
  const isRegex = check.trigger.match.isRegex;
  return {
    id: `dashboard_check:${check.id}`,
    name: check.label,
    description: `After-tool: ${check.trigger.tool} matches ${isRegex ? "regex" : "literal"} "${pattern}"`,
    origin: "user",
    source: "dashboard_check",
    state: check.enabled ? "enabled" : "disabled",
    when: { scope: check.scope, firesAt: "after_tool_use" },
    condition: {
      kind: "regex",
      summary: `${check.trigger.tool} result ${isRegex ? "matches regex" : "contains"} "${pattern}"`,
      payload: { pattern, isRegex, tools: [check.trigger.tool] },
    },
    action: check.action,
    togglable: true,
    editable: true,
    deletable: true,
    rawSource: { kind: "dashboard_check", check },
  };
}


function seamSpecToPolicies(spec: SeamSpecDoc): Policy[] {
  return spec.actions.map((action, idx) => seamActionToPolicy(spec, action, idx));
}


function seamActionToPolicy(
  spec: SeamSpecDoc,
  action: SeamSpecAction,
  idx: number,
): Policy {
  const ops = action.op === "add_seam" ? "Adds preset" : "Rewires preset";
  const wiringHint = action.wiring ? ` (${action.wiring})` : "";
  return {
    id: `seam_spec:${spec.id ?? "anon"}:${idx}`,
    name: `${ops} ${action.preset_id}${wiringHint}`,
    description:
      action.controls_refs
        ? `Controls refs: ${action.controls_refs.join(", ")}`
        : "",
    origin: "user",
    source: "seam_spec",
    state: "enabled",
    when: { scope: "always", firesAt: "registration" },
    condition: {
      kind: "seam_action",
      summary:
        action.op === "add_seam"
          ? `New preset "${action.preset_id}"`
          : `Edit preset "${action.preset_id}"`,
      payload: { ...action },
    },
    action: action.op,
    togglable: false,
    editable: true,
    deletable: !!spec.id,
    rawSource: { kind: "seam_spec", spec, actionIndex: idx },
  };
}


// ---------------------------------------------------------------------------
// Public unifier
// ---------------------------------------------------------------------------


export function unifyPolicies(args: {
  catalog: CustomizeCatalog;
  overrides: CustomizeOverrides;
  dashboardChecks: DashboardCheck[];
}): Policy[] {
  const { catalog, overrides, dashboardChecks } = args;
  const out: Policy[] = [];
  for (const preset of catalog.verification.harnessPresets) {
    out.push(presetToPolicy(preset, overrides.verification.preset_overrides));
  }
  for (const rule of overrides.verification.custom_rules) {
    out.push(customRuleToPolicy(rule));
  }
  for (const spec of overrides.verification.seam_specs ?? []) {
    for (const p of seamSpecToPolicies(spec)) out.push(p);
  }
  for (const check of dashboardChecks) {
    out.push(dashboardCheckToPolicy(check));
  }
  return out;
}


// ---------------------------------------------------------------------------
// Reusable-byproducts extraction — Evidence types + Conditions
// ---------------------------------------------------------------------------


export interface EvidenceTypeEntry {
  ref: string;
  /** Human-readable label. For built-in refs we lean on the source policy's
   *  name; user evidence comes from the policy that emits it. */
  label: string;
  origin: PolicyOrigin;
  /** Policies that reference this evidence (consumers). */
  consumedBy: string[];
  /** Policies that emit this evidence (producers). */
  producedBy: string[];
}


export interface NamedConditionEntry {
  /** Stable key for React + de-dup. */
  key: string;
  kind: PolicyConditionKind;
  summary: string;
  /** Policy id this condition originated from — the source of truth. */
  ownerPolicyId: string;
  ownerPolicyName: string;
  origin: PolicyOrigin;
  payload?: Record<string, unknown>;
}


/**
 * Extract evidence refs that policies CONSUME via deterministic_ref
 * conditions. The output is a per-policy index, NOT a catalog of the
 * runtime's emit-able evidence types — that catalog comes from the
 * /v1/app/customize/evidence/live-catalog endpoint (F2) which surfaces
 * the producer-side schema directly.
 *
 * F2.5 fix: a prior implementation derived a fake ``preset:<id>`` entry
 * per built-in preset_seam policy under the comment "Surface the preset
 * id itself as a known 'rule' name so users see the inventory". Presets
 * are policies (gates) — not evidence emitters — so those entries made
 * the Evidence sub-tab a near-duplicate of the Policies sub-tab (38/38
 * matching counts) with "CONSUMED BY 0 / PRODUCED BY 0" on every row.
 * The derivation is gone; the Evidence tab now sources its catalog from
 * the live-catalog endpoint and uses this function only for the per-ref
 * consumer index.
 *
 * As Stage 5 adds a first-class ``emit_evidence`` action to Policy,
 * ``producedBy`` will fill in automatically; today it stays empty.
 */
export function extractEvidenceTypes(policies: Policy[]): EvidenceTypeEntry[] {
  const byRef = new Map<string, EvidenceTypeEntry>();
  const upsert = (ref: string, origin: PolicyOrigin): EvidenceTypeEntry => {
    let entry = byRef.get(ref);
    if (!entry) {
      entry = {
        ref,
        label: ref,
        origin,
        consumedBy: [],
        producedBy: [],
      };
      byRef.set(ref, entry);
    }
    // Promote origin to "user" if any consumer is user-defined.
    if (origin === "user") entry.origin = "user";
    return entry;
  };

  for (const policy of policies) {
    if (policy.condition.kind === "evidence_ref" && policy.condition.payload?.ref) {
      const ref = String(policy.condition.payload.ref);
      const entry = upsert(ref, policy.origin);
      entry.consumedBy.push(policy.id);
    }
  }

  return [...byRef.values()].sort((a, b) => a.ref.localeCompare(b.ref));
}


/**
 * Extract every NL-bound condition the user has authored so other policies
 * can reuse them via the wizard / NL compiler.
 *
 * Built-in PresetSeams have implicit conditions (their controls_refs check
 * is wired in the runtime) so they do NOT appear here; this catalog is
 * specifically the user-defined SHACL shapes / LLM criteria / regex
 * patterns / tool-match patterns that are reusable across other policies.
 */
export function extractNamedConditions(policies: Policy[]): NamedConditionEntry[] {
  const out: NamedConditionEntry[] = [];
  for (const policy of policies) {
    if (policy.origin !== "user") continue;
    const c = policy.condition;
    if (
      c.kind === "shacl_constraint"
      || c.kind === "llm_criterion"
      || c.kind === "regex"
      || c.kind === "tool_perm"
    ) {
      out.push({
        key: `${policy.id}:${c.kind}`,
        kind: c.kind,
        summary: c.summary,
        ownerPolicyId: policy.id,
        ownerPolicyName: policy.name,
        origin: policy.origin,
        payload: c.payload,
      });
    }
  }
  return out;
}
