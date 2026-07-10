/**
 * Unified rule-row model — PR-E1 (renamed PR-2 "policies-first surface").
 *
 * Historical note: this type was originally called ``Policy``, which collided
 * with the real Policy entity (a named 1..N-rule user-intent unit persisted in
 * ``customize.json > policies:{}``). The word "Policy" now belongs to that
 * entity + its :class:`PolicyCardList` surface; the flat, per-store row this
 * module unifies is a :class:`RuleRow` (the atomic implementation detail that
 * lives inside a policy's drill-down).
 *
 * Kevin's 2026-06-22 architecture call: the user has one mental concept
 * (a "policy"), but the backend persists rules in four disjoint shapes:
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
  | "capability_scope"  // narrows the spawned-child toolset (F4)
  // F-MUT1 — first mutator kind. Rewrites inbound data (tool args before
  // dispatch, or system-prompt sections at on_user_prompt_submit). Maps to
  // the Mutator trust class via :func:`trustClassForPolicy` (amber-yellow
  // badge styled by F-MUT3 in trust-badge.tsx).
  | "prompt_injection"
  // F-MUT2 — second mutator kind. Rewrites a tool's output text AFTER
  // dispatch but BEFORE the model reads it (re.sub-based redact). Same
  // Mutator trust class as ``prompt_injection`` — surfaces "this policy
  // modifies traffic" via :func:`trustClassForPolicy`.
  | "output_rewrite"
  // F-EXEC1 — operator-authored subprocess action. Runs an operator-
  // written shell script (bash/sh) at the chosen lifecycle slot; exit
  // code 0 = pass, non-zero blocks at pre_final / before_tool_use.
  // Maps to the new ``operator_defined`` trust class via
  // :func:`trustClassForPolicy` — the wizard surfaces an explicit "magi
  // does not verify the script" warning before activation. F-EXEC3 ships
  // the dedicated amber-red badge palette; until then the badge falls
  // back to a placeholder rendering for this literal.
  | "shell_command"
  // F-EXEC2 — operator-authored subprocess VERIFIER. Same payload shape
  // as ``shell_command`` (source + timeout + env_vars + shell) but the
  // runtime treats the script output as a verdict: stdout JSON
  // ``{passed, reason?}`` (preferred) or exit code 0 = passed
  // (fallback). Same ``operator_defined`` trust class as
  // ``shell_command``; the wizard surfaces the same "magi does not
  // verify the script" warning before activation.
  | "shell_check"
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


/**
 * A single unified rule row (formerly ``Policy``). One per backend store
 * record; a policy's drill-down renders a subset of these keyed by rule id.
 */
export interface RuleRow {
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


function presetToRow(
  preset: HarnessPresetItem,
  presetOverrides: Record<string, boolean>,
): RuleRow {
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


function customRuleToRow(rule: CustomRule): RuleRow {
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
  if (kind === "prompt_injection") {
    // F-MUT1 — round-trip a persisted prompt_injection rule into a Policy
    // condition. Branches on payload shape (target_arg_key → before-tool;
    // target → on-user-prompt-submit) so the dashboard surfaces the right
    // sentence without re-loading the rule's lifecycle slot.
    const mode = typeof payload.mode === "string" ? payload.mode : "append";
    const value = typeof payload.value === "string" ? payload.value : "";
    const valuePreview = value.length > 60 ? `${value.slice(0, 60)}…` : value;
    if (typeof payload.target_arg_key === "string") {
      const argKey = payload.target_arg_key;
      return {
        kind,
        summary: `${mode} "${valuePreview}" to tool arg "${argKey}"`,
        payload: { mode, target_arg_key: argKey, value, condition: payload.condition },
      };
    }
    const target =
      typeof payload.target === "string" ? payload.target : "system_prompt";
    return {
      kind,
      summary: `${mode} "${valuePreview}" to ${target}`,
      payload: { mode, target, value, condition: payload.condition },
    };
  }
  if (kind === "output_rewrite") {
    // F-MUT2 — round-trip a persisted output_rewrite rule into a Policy
    // condition. Mode is locked to "redact" in v1; pattern + replacement
    // ride along verbatim so the dashboard sentence reads honestly without
    // re-loading the rule's lifecycle slot.
    const mode = typeof payload.mode === "string" ? payload.mode : "redact";
    const pattern = typeof payload.pattern === "string" ? payload.pattern : "";
    const replacement =
      typeof payload.replacement === "string" ? payload.replacement : "";
    const patternPreview =
      pattern.length > 60 ? `${pattern.slice(0, 60)}…` : pattern;
    return {
      kind,
      summary: `${mode} /${patternPreview}/ → "${replacement}"`,
      payload: {
        mode,
        pattern,
        replacement,
        scope: payload.scope ?? "match_only",
        isRegex: payload.isRegex ?? true,
        toolMatch: payload.toolMatch,
      },
    };
  }
  if (kind === "shell_command" || kind === "shell_check") {
    // F-EXEC1 (shell_command) + F-EXEC2 (shell_check) — round-trip a
    // persisted operator shell rule into a Policy condition. Both kinds
    // share the same ``ShellPayload`` shape; only the runtime semantics
    // differ (action vs verifier). The summary mirrors the wizard's
    // Review-step phrasing so the dashboard reads consistently.
    const source = typeof payload.source === "string" ? payload.source : "inline";
    const inline = typeof payload.inline === "string" ? payload.inline : "";
    const path = typeof payload.path === "string" ? payload.path : "";
    const shell = typeof payload.shell === "string" ? payload.shell : "bash";
    const timeout =
      typeof payload.timeout_seconds === "number"
        ? payload.timeout_seconds
        : 30;
    const sourceLabel =
      source === "inline"
        ? "inline script"
        : `file ${path || "(unset)"}`;
    const kindLabel = kind === "shell_check" ? "shell verifier" : "shell hook";
    return {
      kind,
      summary: `Run operator ${kindLabel} (${sourceLabel}, ${shell}, ${timeout}s timeout)`,
      payload: {
        source,
        inline,
        path,
        shell,
        timeout_seconds: timeout,
        env_vars: Array.isArray(payload.env_vars) ? payload.env_vars : [],
      },
    };
  }
  if (kind === "capability_scope") {
    // F4 — narrows the spawned-child toolset. Payload v1 (mirrors
    // magi_agent/customize/capability_scope.py): denyTools (list[str]),
    // maxPermissionClass ("readonly" | "safe_write" | null), tightenOnly (true).
    const denyToolsRaw = payload.denyTools;
    const denyTools = Array.isArray(denyToolsRaw)
      ? (denyToolsRaw.filter((t) => typeof t === "string") as string[])
      : [];
    const maxClass =
      typeof payload.maxPermissionClass === "string"
        ? (payload.maxPermissionClass as string)
        : null;
    const parts: string[] = [];
    if (denyTools.length > 0) {
      parts.push(`denies ${denyTools.join(", ")}`);
    }
    if (maxClass) {
      parts.push(`max class ${maxClass}`);
    }
    const summary =
      parts.length > 0
        ? `Caps spawned subagents (${parts.join(" / ")})`
        : "Caps spawned subagents";
    return {
      kind,
      summary,
      payload: { denyTools, maxPermissionClass: maxClass },
    };
  }
  return { kind: "evidence_ref", summary: "(unknown condition)" };
}


function dashboardCheckToRow(check: DashboardCheck): RuleRow {
  const tool = check.trigger.tool;
  const match = check.trigger.match;
  const domainAllowlist = Array.isArray(check.trigger.domainAllowlist)
    ? check.trigger.domainAllowlist
    : [];

  // Two trigger shapes share the after-tool check: the historic result-text
  // ``match`` and the newer arguments-based ``domainAllowlist`` (which leaves
  // ``match`` null). Guard the null so a domain-allowlist check does not crash
  // the whole Customize page on ``match.pattern``.
  let description: string;
  let condition: PolicyCondition;
  if (match) {
    const { pattern, isRegex } = match;
    description = `After-tool: ${tool} matches ${isRegex ? "regex" : "literal"} "${pattern}"`;
    condition = {
      kind: "regex",
      summary: `${tool} result ${isRegex ? "matches regex" : "contains"} "${pattern}"`,
      payload: { pattern, isRegex, tools: [tool] },
    };
  } else {
    const domains = domainAllowlist.join(", ");
    description = `After-tool: ${tool} fetches a source in [${domains}]`;
    condition = {
      kind: "regex",
      summary: `${tool} fetches a source domain in [${domains}]`,
      payload: { domainAllowlist, tools: [tool] },
    };
  }

  return {
    id: `dashboard_check:${check.id}`,
    name: check.label,
    description,
    origin: "user",
    source: "dashboard_check",
    state: check.enabled ? "enabled" : "disabled",
    when: { scope: check.scope, firesAt: "after_tool_use" },
    condition,
    action: check.action,
    togglable: true,
    editable: true,
    deletable: true,
    rawSource: { kind: "dashboard_check", check },
  };
}


function seamSpecToRows(spec: SeamSpecDoc): RuleRow[] {
  return spec.actions.map((action, idx) => seamActionToRow(spec, action, idx));
}


function seamActionToRow(
  spec: SeamSpecDoc,
  action: SeamSpecAction,
  idx: number,
): RuleRow {
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


export function unifyRuleRows(args: {
  catalog: CustomizeCatalog;
  overrides: CustomizeOverrides;
  dashboardChecks: DashboardCheck[];
}): RuleRow[] {
  const { catalog, overrides, dashboardChecks } = args;
  const out: RuleRow[] = [];
  for (const preset of catalog.verification.harnessPresets) {
    out.push(presetToRow(preset, overrides.verification.preset_overrides));
  }
  for (const rule of overrides.verification.custom_rules) {
    out.push(customRuleToRow(rule));
  }
  for (const spec of overrides.verification.seam_specs ?? []) {
    for (const p of seamSpecToRows(spec)) out.push(p);
  }
  for (const check of dashboardChecks) {
    out.push(dashboardCheckToRow(check));
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


// ---------------------------------------------------------------------------
// PR-F5 — Trust-class derivation
// ---------------------------------------------------------------------------


/**
 * Honesty taxonomy bucket attached to each :class:`Policy` for badging.
 *
 * The four buckets surface to operators how a policy is enforced at runtime
 * so they can read the table without guessing whether a rule is byte-stable
 * verifier code, a prompt nudge, or something in between:
 *
 *  * ``deterministic`` — runtime gate; the model cannot opt out. Backed by
 *    verifier code (``deterministic_ref`` / ``shacl_constraint`` /
 *    ``field_constraint`` / ``tool_perm`` / ``seam_action``) or a
 *    ``dashboard_check`` whose action is ``block`` / ``audit``.
 *  * ``advisory`` — LLM critic or system-prompt inject. Result may vary
 *    between runs (``llm_criterion`` policies and the Guidance textarea).
 *  * ``hybrid`` — deterministic match that also rewrites the tool output
 *    (e.g. a ``dashboard_check`` with action ``override`` / strip). No
 *    code path produces this today; the mapping is forward-compatible so
 *    adding such an action later lights the badge automatically.
 *  * ``preview`` — visible but not wired (preset enforcement = ``preview``).
 */
export type TrustClass =
  | "deterministic"
  | "advisory"
  | "hybrid"
  | "preview"
  | "mutator"
  // F-EXEC1 — fifth class. Operator-authored shell-script policies
  // (``shell_command`` kind today; F-EXEC2 adds ``shell_check``). magi
  // does NOT verify the script — the operator owns the trust boundary.
  // F-EXEC3 ships the dedicated amber-red palette + Terminal icon; until
  // then trust-badge.tsx falls back to its existing palette for any
  // unknown variant so this addition does not break the v1 badge.
  | "operator_defined";


/**
 * Structural input for :func:`trustClassForPolicy`.
 *
 * Accepts either a full unified :class:`Policy` or a synthesized bag from
 * row adapters in ``rules-table.tsx`` that build a policy-like view from
 * ``HarnessPresetItem`` / ``CustomRule`` / ``DashboardCheck`` / ``SeamSpec``
 * without round-tripping through :func:`unifyPolicies`.
 *
 * ``condition.kind`` is typed as ``string`` (not :type:`PolicyConditionKind`)
 * because adapter call sites may pass an unknown / future kind (e.g.
 * ``rule.what?.kind ?? "rule"``); any unrecognized kind falls through to
 * ``deterministic`` so a new backend kind never silently downgrades the
 * trust badge.
 */
export interface PolicyTrustInput {
  /** Drives the ``"none"`` + ``"preview"`` → ``preview`` bucket. Optional;
   *  rows that don't carry state (default) read as enabled-ish. */
  state?: string;
  /** Used by the ``regex`` (``dashboard_check``) projection to distinguish
   *  override / strip → ``hybrid`` from block / audit → ``deterministic``. */
  action?: string;
  /** Optional source tag carried by row adapters for documentation; not
   *  read by the mapping today (the mapping is condition-driven). */
  source?: string;
  condition: { kind: string };
}


/**
 * Derive the trust-class bucket for a unified :class:`Policy` (or any
 * structurally similar :type:`PolicyTrustInput` synthesized by a row
 * adapter — see ``rules-table.tsx`` and ``trust-badge.tsx``).
 *
 * This is the **single source of truth** for the trust-class taxonomy
 * across the customize surface. The ``trust-badge.tsx`` module re-exports
 * this function so existing call sites that import from ``./trust-badge``
 * continue to work; both imports resolve to this implementation.
 *
 * Mapping table (verified against backend KIND + ROUTED_KIND set,
 * not the stale spec — see discovery notes attached to PR-F5):
 *
 * | policy.condition.kind | additional signal     | trust class      |
 * |-----------------------|-----------------------|------------------|
 * | ``evidence_ref``      | —                     | ``deterministic``|
 * | ``shacl_constraint``  | —                     | ``deterministic``|
 * | ``tool_perm``         | —                     | ``deterministic``|
 * | ``seam_action``       | —                     | ``deterministic``|
 * | ``llm_criterion``     | —                     | ``advisory``     |
 * | ``regex``             | action ``override``   | ``hybrid``       |
 * | ``regex``             | action ``block``/``audit`` | ``deterministic`` |
 * | ``none``              | preset ``preview``    | ``preview``      |
 * | ``none``              | otherwise             | ``deterministic``|
 * | (unknown / future)    | —                     | ``deterministic``|
 *
 * Notes
 * -----
 *  * The frontend renames the backend ``deterministic_ref`` kind to
 *    ``evidence_ref`` at the adapter boundary
 *    (:func:`customRuleToPolicy` → :func:`customRuleCondition`). The map
 *    above keys on the frontend name (``evidence_ref``) accordingly.
 *  * The backend ``field_constraint`` ROUTED_KIND is lifted to
 *    ``shacl_constraint`` at the transport layer
 *    (``_lift_field_constraint_to_shacl`` in ``transport/customize.py``),
 *    so the runtime gate only ever sees ``shacl_constraint`` and both
 *    authoring surfaces collapse onto the same Deterministic bucket.
 *  * ``user_rules`` is a top-level overrides string (Guidance textarea),
 *    NOT a :class:`Policy` — its trust-class badge is hard-coded
 *    ``advisory`` inside ``guidance-panel.tsx`` rather than going through
 *    this function.
 *  * ``dashboard_check`` action ``override`` is not a real backend
 *    variant today (the type is the closed set ``block | audit``); the
 *    branch is wired so a forthcoming ``override`` / strip action lights
 *    up the Hybrid badge without further changes here.
 *  * Unknown / future ``condition.kind`` values fall through to
 *    ``deterministic`` rather than blowing up — this keeps the customize
 *    surface forward-compatible while still surfacing the safer (more
 *    enforcing) default in the badge.
 */
export function trustClassForPolicy(policy: PolicyTrustInput): TrustClass {
  const kind = policy.condition.kind;
  switch (kind) {
    case "llm_criterion":
      return "advisory";
    case "evidence_ref":
    case "shacl_constraint":
    case "tool_perm":
    case "seam_action":
      return "deterministic";
    case "regex":
      // dashboard_check projection — distinguished by action.
      // Today the action is closed { block, audit } (both Deterministic);
      // a future "override" / strip action would mutate tool output and
      // therefore reads as Hybrid.
      return policy.action === "override" ? "hybrid" : "deterministic";
    case "prompt_injection":
    case "output_rewrite":
      // F-MUT1 (prompt_injection) + F-MUT2 (output_rewrite) — both kinds
      // are explicit mutators. They rewrite/augment traffic the model
      // sees, so the operator must see "this policy modifies traffic"
      // before activating. F-MUT3 lights the amber-yellow palette in
      // trust-badge.tsx; until then the badge falls through to its
      // default rendering for these literals.
      return "mutator";
    case "shell_command":
    case "shell_check":
      // F-EXEC1 (shell_command) + F-EXEC2 (shell_check) — both kinds
      // share the same operator_defined trust class. The script body is
      // never verified by magi; the operator owns the trust boundary.
      // F-EXEC3 ships the dedicated amber-red badge palette + Terminal
      // icon; until then trust-badge.tsx falls back to its existing
      // palette so the badge still renders (any unknown variant
      // defaults to the "deterministic" visual rather than crashing).
      return "operator_defined";
    case "none":
      // Built-in preset_seam: fall through to the preset's enforcement
      // metadata. Preview presets are inert; everything else is enforced
      // by the runtime → Deterministic.
      return policy.state === "preview" ? "preview" : "deterministic";
    default:
      // Unknown / future kinds (or row-adapter fallbacks like
      // ``rule.what?.kind ?? "rule"``) — fall through to the safer
      // Deterministic bucket. A new :type:`PolicyConditionKind` should
      // be added to the switch above; this fallback prevents silent
      // crashes if the rollout order slips.
      return "deterministic";
  }
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
export function extractEvidenceTypes(policies: RuleRow[]): EvidenceTypeEntry[] {
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
 * PR-F-UX5 — derive a :class:`NamedConditionEntry` per built-in verdict
 * primitive (``catalog.verification.judgmentMenu``) so the Conditions tab can
 * surface built-in verifiers and user-authored named conditions in a single
 * list with origin badges.
 *
 * The synthesised ``ownerPolicyId`` / ``ownerPolicyName`` point to a virtual
 * ``builtin:<ref>`` owner so the Conditions tab's "from policy" copy can show
 * "built-in" without claiming a real :class:`Policy` exists. Built-in rows
 * are read-only; editing a verifier requires runtime code changes (the F-UX5
 * design treats producers/verifiers as code, not authoring surface).
 *
 * Each entry's ``kind`` is ``evidence_ref`` (matches how the wizard's
 * ``verifier_passed`` UX kind compiles to the same backend
 * ``deterministic_ref`` payload) so the Conditions tab labelling reads the
 * same as a user-authored deterministic_ref rule referencing the same ref.
 */
export function extractBuiltinJudgmentRefs(
  catalog: CustomizeCatalog,
): NamedConditionEntry[] {
  const items = catalog.verification.judgmentMenu ?? [];
  return items.map((item) => ({
    key: `builtin:${item.ref}`,
    kind: "evidence_ref" as const,
    summary: item.label,
    ownerPolicyId: `builtin:${item.ref}`,
    ownerPolicyName: item.ref,
    origin: "builtin" as const,
    payload: { ref: item.ref },
  }));
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
export function extractNamedConditions(policies: RuleRow[]): NamedConditionEntry[] {
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
