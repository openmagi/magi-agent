"use client";

import { useCallback, useEffect, useState } from "react";
import { useAgentFetch } from "./local-api";

/**
 * Types and data hook for the local runtime customization surface.
 *
 * Mirrors the `GET /v1/app/customize` contract served by the local Python
 * runtime. The catalog enumerates everything the runtime can do (recipes,
 * harness presets, hooks, tools); the overrides record the locally configured
 * deltas on top of the defaults. Note the asymmetry intentionally preserved
 * from the backend contract: the catalog uses camelCase `harnessPresets` while
 * the overrides file uses snake_case `harness_presets`.
 */

export interface RecipeItem {
  id: string;
  title: string;
  description: string;
  category: string;
  source: string;
  enabled: boolean;
  /**
   * Pack IDs that map this recipe to a UI surface (Phase 3 wiring). Empty
   * tuple means the recipe is not surfaced in any pack — the Customize hub
   * UI greys these out as "no live effect".
   */
  packIds: string[];
}

export interface HarnessPresetItem {
  id: string;
  title: string;
  category: string;
  /** WHEN-group for the modal: always-on | coding | research | delivery. */
  domain: string;
  /** Raw runtime fire-at points (beforeToolUse / afterTurnEnd / ...). */
  hookPoints: string[];
  /** Concrete one-line description of what the gate checks. */
  description: string;
  /** Enforcement mechanism tier: `deterministic` | `always-on` | null (preview). */
  tier: "deterministic" | "always-on" | null;
  /** How the toggle acts: `opt-out` | `opt-in` | null (not wired). */
  optMethod: "opt-out" | "opt-in" | null;
  /** Catalog default state in the live runtime. */
  defaultEnabled: boolean;
  /**
   * Honest enforcement status:
   * - `enforcing`  — toggling this preset changes runtime behavior now.
   * - `always-on`  — enforced elsewhere (security/PermissionGate); not togglable here.
   * - `capability` — a real runtime capability gated by an env flag (not a
   *                  pre-final verification gate, so not a Customize toggle).
   * - `preview`    — surfaced for parity but not yet wired to a runtime gate.
   */
  enforcement: "enforcing" | "always-on" | "capability" | "preview";
  /** Evaluation strategies the runtime supports for this preset. */
  supportedModes: string[];
  /** Legacy field kept for back-compat; prefer `defaultEnabled`. */
  enabled?: boolean;
}

export interface HookItem {
  name: string;
  point: string;
  title: string;
  category: string;
  alwaysOn: boolean;
  enabled: boolean;
}

export interface ToolItem {
  name: string;
  description: string;
  enabled: boolean;
  source: string;
  dangerous: boolean;
}

/** Producer-backed deterministic check a custom rule may require (WHAT-menu). */
export interface CustomRuleMenuItem {
  ref: string;
  label: string;
  evidenceType: string;
  tier: string;
  firesAt: string;
  allowedActions: string[];
}

/**
 * An in-context control-plane *behavior* toggle (facts-survey replan, goal
 * nudge, etc.). Orthogonal to the verification gate layer: each maps to a
 * single `MAGI_*_ENABLED` flag that the lab/dogfood profile seeds ON, so this
 * is the only surface that can turn the behavior off.
 */
export interface ControlPlaneBehaviorItem {
  id: string;
  env_var: string;
  label: string;
  description: string;
  /** Current effective state (env-flag truthiness) — shown when no explicit override is set. */
  enabled: boolean;
}

/**
 * A user-disableable first-party (builtin) *policy* toggle
 * (verify-before-replying, …). Each maps a builtin policy id to its master
 * `MAGI_*_ENABLED` flag; a toggle here projects an opt-out. Floor policies
 * (e.g. source_citation, whose gate can BLOCK) are deliberately NOT in this
 * list, so they cannot be disabled through this surface.
 */
export interface BuiltinPolicyToggleItem {
  id: string;
  env_var: string;
  label: string;
  description: string;
  /** Current effective (profile-aware) state — reports ON even when the flag is unset-but-default-ON. */
  enabled: boolean;
}

/**
 * A unified Policy summary from the `GET /v1/app/customize` catalog (PR-1 U3).
 *
 * A *policy* is the user's unit of intent — a named 1..N-rule bundle. The
 * Policies surface renders one card per entry; member rules live in the
 * drill-down (looked up by id from the flat rule-row list).
 *
 * `enabledState` is derived from member custom rules' `enabled` flags:
 *   - `"on"`  — every stored member is enabled
 *   - `"off"` — every stored member is disabled
 *   - `"mixed"` — some on, some off (render an indeterminate toggle + note)
 *   - `"managed"` — NO members are stored custom rules (builtin-native or
 *     dashboard-check producers): there is nothing on the per-rule `enabled`
 *     axis to cascade, so the card renders a static pill, NOT a Switch.
 */
export interface PolicyCatalogEntry {
  id: string;
  displayName: string;
  /** The user's own natural-language sentence; may be empty. */
  intent: string;
  /** Member rule ids (join into the flat rule-row list for the drill-down). */
  ruleIds: string[];
  origin: "user" | "builtin";
  /** False for floor policies (source_citation) — render always-on, no toggle. */
  userDisableable: boolean;
  /** Advisory review verdict; `"unreviewed"` when never reviewed. */
  reviewVerdict: string;
  /** True when the policy binds a producer → gate pair (render the relationship). */
  hasBinding: boolean;
  enabledState: "on" | "off" | "mixed" | "managed";
  /**
   * PR-3 — routing discriminator for the policy-level toggle:
   *   - `"policy"`        — store-backed user policy; cascade onto member custom
   *     rules (`PATCH /v1/app/policies/{id}`).
   *   - `"builtinPolicy"` — first-party policy (verify_before_replying,
   *     source_citation); opt-out routes to `PATCH .../builtin-policies/{id}`.
   *   - `"controlPlane"`  — one of the 4 in-context control-plane *behaviors*
   *     (facts-replan, goal-loop, tool-synthesis-nudge, empty-response-recovery)
   *     adapted read-time into a 1-rule nudge card; toggle routes to
   *     `PATCH .../control-plane/{id}`.
   * Optional so a pre-PR-3 backend still type-checks (absent → `"policy"`).
   */
  source?: "policy" | "builtinPolicy" | "controlPlane";
  /**
   * PR-3 — lightweight action label for adapter entries that have no member
   * rules to derive a strongest-action chip from (the control-plane nudges).
   * `"nudge"` lets the card render a NUDGE chip without member rules.
   */
  actionHint?: string;
}

export interface CustomizeCatalog {
  verification: {
    recipes: RecipeItem[];
    harnessPresets: HarnessPresetItem[];
    hooks: HookItem[];
    /**
     * Producer-backed deterministic checks the custom-rule builder may
     * require. Union of {@link evidenceMenu} + {@link judgmentMenu}.
     *
     * @deprecated PR-F-UX5 — prefer ``evidenceMenu`` / ``judgmentMenu`` so
     * the raw-evidence vs verdict-primitive distinction is visible in the UI.
     * Retained as the back-compat union so existing consumers (NL compiler
     * tests, third-party authoring surfaces) keep working.
     */
    customRuleMenu: CustomRuleMenuItem[];
    /**
     * PR-F-UX5 — raw-evidence ref descriptors (``evidence:*``). These are the
     * producer records a deterministic rule operates against (the inputs,
     * not the verdicts). Source for the wizard's "Check evidence record
     * present" picker AND the field-constraint type picker (verifiers have
     * no traversable fields).
     */
    evidenceMenu: CustomRuleMenuItem[];
    /**
     * PR-F-UX5 — verdict-primitive ref descriptors. Built-in verifier outputs
     * (``verifier:*`` refs and unprefixed named judgments such as
     * ``fact_grounding``). Source for the wizard's "Check verifier /
     * condition passed" picker; the Conditions tab merges these with
     * user-authored named conditions under an origin badge.
     */
    judgmentMenu: CustomRuleMenuItem[];
  };
  tools: ToolItem[];
  controlPlane: ControlPlaneBehaviorItem[];
  /** User-disableable first-party policies (verify-before-replying). Floors excluded. */
  builtinPolicies: BuiltinPolicyToggleItem[];
  /**
   * Unified Policies surface (PR-1 U3): the full list of policies (user +
   * first-party builtin) with membership, origin, review verdict, binding
   * flag, and derived `enabledState`. The Policies card list renders from
   * this; drill-down member rows come from the flat rule-row list.
   *
   * Optional at the type level so a pre-U3 backend (no `policies` key) still
   * type-checks; the UI treats an absent value as an empty list.
   */
  policies?: PolicyCatalogEntry[];
}

/** A structured custom verification rule (spec §9.1). P1 builds deterministic_ref.
 *
 *  PR-F-UX6: optional `groupId` — rules sharing a non-empty groupId are
 *  surfaced in the dashboard as one logical hybrid policy (the operator
 *  sees one row + can drill into the composing primitives). Backend gates
 *  still evaluate each rule independently. */
export interface CustomRule {
  id?: string;
  scope: string;
  enabled: boolean;
  what: { kind: string; payload: Record<string, unknown> };
  firesAt: string;
  action: string;
  projection?: string[];
  groupId?: string;
}

export interface CustomizeOverrides {
  verification: {
    recipes: string[];
    harness_presets: string[];
    /** Explicit per-preset enable state (tri-state: present true/false, or absent → catalog default). */
    preset_overrides: Record<string, boolean>;
    hooks: Record<string, boolean>;
    modes: Record<string, string>;
    custom_rules: CustomRule[];
    /** PR-C2 approved SeamSpec docs. Empty when the seam-spec flag is OFF or no spec has been saved yet. */
    seam_specs?: SeamSpecDoc[];
    /** PR-F7 cost budgets. Keys: maxToolCallsPerTurn / maxStepsBrakeHard / loopGuardHardThreshold; values are positive integers. */
    budgets?: VerificationBudgets;
  };
  tools: Record<string, boolean>;
  /** Free-text USER-RULES.md body injected into the system prompt. */
  user_rules: string;
  /**
   * Explicit per-behavior enable state for control-plane loop controls
   * (tri-state: present true/false, or absent → profile default). An explicit
   * value wins over the lab/dogfood env seed.
   */
  control_plane: Record<string, boolean>;
  /**
   * Explicit per-builtin-policy enable state (tri-state: present true/false, or
   * absent → default). An explicit `false` opts out of a default-ON first-party
   * policy; only ids in the curated catalog project onto their master flag.
   */
  builtin_policies: Record<string, boolean>;
  /**
   * Egress-guard destination allowlist + enforcement mode (U4). The
   * `allowlist` is a list of host patterns (exact host or single-suffix
   * wildcard, e.g. `*.github.com`); `mode` mirrors `MAGI_EGRESS_GUARD_MODE`
   * (`audit` | `block`, or `""` for the profile default). The whole `~/.magi`
   * directory is agent-write-protected, so this surface is the operator's
   * sanctioned edit path and every write emits a config-change audit row.
   */
  egress_guard?: { allowlist: string[]; mode: string };
}

/**
 * PR-F7 — operator-authored per-bot cost budgets.
 *
 * Each value is a positive integer. The backend applier projects the dashboard
 * save onto the live MAGI_* env at turn entry via ``setdefault``, so an
 * explicit operator env (k8s / shell export / dogfood profile) always wins.
 * The GET endpoint returns ``effectiveEnv`` so the dashboard can flag "your
 * save is dormant because this env is pinned elsewhere".
 */
export interface VerificationBudgets {
  /** -> MAGI_TOOL_MAX_CALLS_PER_TURN (default 64; range 1..4096) */
  maxToolCallsPerTurn?: number;
  /** -> MAGI_MAX_STEPS_BRAKE_HARD (sentinel; no numeric flag registered today) */
  maxStepsBrakeHard?: number;
  /** -> MAGI_LOOP_GUARD_HARD_THRESHOLD (default 5) */
  loopGuardHardThreshold?: number;
}

/**
 * Response shape from `GET /v1/app/customize/budgets`. The `effectiveEnv`
 * mirror is keyed by the same logical budget names so the UI can show the
 * resolved env value (or null when unset) right next to the input.
 */
export interface BudgetsResponse {
  budgets: VerificationBudgets;
  /** The currently-set MAGI_* env value for each budget (null when unset). */
  effectiveEnv: { [K in keyof VerificationBudgets]?: string | null };
  /** Static budget-name -> MAGI_* env-name vocabulary (so the UI does not hardcode it). */
  envMap: Record<string, string>;
}

export interface CustomizeResponse {
  overrides: CustomizeOverrides;
  catalog: CustomizeCatalog;
}

interface UseCustomizeResult {
  data: CustomizeResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Persists a single tool enable/disable toggle via `PATCH /v1/app/customize/tools/{name}`.
 *
 * Returns the updated `CustomizeOverrides` on success so the caller can
 * reconcile local state from the backend's authoritative view.
 * Throws on non-2xx responses so the caller can surface the error and revert.
 */
export async function patchToolOverride(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  name: string,
  enabled: boolean,
): Promise<CustomizeOverrides> {
  const res = await fetch(`/v1/app/customize/tools/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error(`Failed to update tool (${res.status})`);
  const data = (await res.json()) as { overrides: CustomizeOverrides };
  return data.overrides;
}

/**
 * Persists a control-plane behavior toggle via
 * `PATCH /v1/app/customize/control-plane/{behaviorId}`.
 *
 * Records an explicit tri-state in `control_plane` (so an opt-out of a
 * profile-seeded behavior persists) and projects it onto the live process env
 * so the next turn honors it. Returns the updated overrides; throws on non-2xx
 * so the caller can surface the error and revert.
 */
export async function patchControlPlaneOverride(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  behaviorId: string,
  enabled: boolean,
): Promise<CustomizeOverrides> {
  const res = await fetch(
    `/v1/app/customize/control-plane/${encodeURIComponent(behaviorId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );
  if (!res.ok) throw new Error(`Failed to update behavior (${res.status})`);
  const data = (await res.json()) as { overrides: CustomizeOverrides };
  return data.overrides;
}

/**
 * Persists a first-party (builtin) policy opt-out via
 * `PATCH /v1/app/customize/builtin-policies/{policyId}`.
 *
 * Records an explicit tri-state in `builtin_policies` (so the opt-out of a
 * default-ON policy persists) and projects it onto the live process env so the
 * next turn's gate honors it. A floor policy (not user-disableable) 404s.
 * Returns the updated overrides; throws on non-2xx so the caller can revert.
 */
export async function patchBuiltinPolicyOverride(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  policyId: string,
  enabled: boolean,
): Promise<CustomizeOverrides> {
  const res = await fetch(
    `/v1/app/customize/builtin-policies/${encodeURIComponent(policyId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );
  if (!res.ok) throw new Error(`Failed to update built-in policy (${res.status})`);
  const data = (await res.json()) as { overrides: CustomizeOverrides };
  return data.overrides;
}

/** The egress-guard allowlist + mode read from `GET .../egress-allowlist` (U4). */
export interface EgressAllowlistState {
  allowlist: string[];
  mode: string;
}

/**
 * Reads the egress-guard allowlist + mode via `GET /v1/app/customize/egress-allowlist`.
 * Throws on non-2xx so the caller can surface the error.
 */
export async function getEgressAllowlist(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
): Promise<EgressAllowlistState> {
  const res = await fetch(`/v1/app/customize/egress-allowlist`);
  if (!res.ok) throw new Error(`Failed to load egress allowlist (${res.status})`);
  const data = (await res.json()) as { allowlist?: string[]; mode?: string };
  return { allowlist: data.allowlist ?? [], mode: data.mode ?? "" };
}

/**
 * Persists the egress-guard allowlist via `PUT /v1/app/customize/egress-allowlist`.
 *
 * Each entry must be an exact host or a single-suffix wildcard (`*.github.com`);
 * the backend validates + normalizes (lowercase, de-dup) and returns the stored
 * list. Throws on non-2xx (e.g. an invalid host pattern) so the caller can
 * surface the error and revert.
 */
export async function putEgressAllowlist(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  allowlist: string[],
): Promise<string[]> {
  const res = await fetch(`/v1/app/customize/egress-allowlist`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ allowlist }),
  });
  if (!res.ok) throw new Error(`Failed to save egress allowlist (${res.status})`);
  const data = (await res.json()) as { allowlist: string[] };
  return data.allowlist;
}

/**
 * Persists the egress-guard enforcement mode via `PUT /v1/app/customize/egress-mode`.
 *
 * `mode` is `"audit"` (observe-only, records destinations) or `"block"` (denies
 * a non-allowlisted destination). The backend projects the value onto the live
 * `MAGI_EGRESS_GUARD_MODE` env so the next turn honors it without a restart.
 * Throws on non-2xx.
 */
export async function putEgressMode(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  mode: "audit" | "block",
): Promise<string> {
  const res = await fetch(`/v1/app/customize/egress-mode`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (!res.ok) throw new Error(`Failed to save egress mode (${res.status})`);
  const data = (await res.json()) as { mode: string };
  return data.mode;
}

/**
 * Persists a verification preset/recipe/hook toggle via
 * `PATCH /v1/app/customize/verification/{kind}/{id}`.
 *
 * For `harness_presets` this records an explicit tri-state in `preset_overrides`
 * (so opt-out of a default-on gate persists). Returns the updated overrides.
 * Throws on non-2xx so the caller can surface the error and revert.
 */
export async function patchVerificationOverride(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  kind: "recipes" | "harness_presets" | "hooks",
  id: string,
  enabled: boolean,
  mode?: string,
): Promise<CustomizeOverrides> {
  const body: { enabled: boolean; mode?: string } = { enabled };
  if (mode) body.mode = mode;
  const res = await fetch(
    `/v1/app/customize/verification/${kind}/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw new Error(`Failed to update verification rule (${res.status})`);
  const data = (await res.json()) as { overrides: CustomizeOverrides };
  return data.overrides;
}

/**
 * F-UX10 (2026-06-24): convenience wrapper around `patchVerificationOverride`
 * targeting the recipes allowlist. Identical wire shape — exists so the
 * Recipes tab handler does not hardcode the `"recipes"` kind string at every
 * callsite (and so tests can spy on a single named export).
 *
 * Allowlist semantics: an empty `verification.recipes[]` means "no user
 * override" (all recipes effectively enabled, byte-identical to legacy).
 * A non-empty list is an explicit allowlist — recipe ids NOT in the list have
 * their mapped pack's evidence/validator refs filtered out at assembly time.
 *
 * The backend 404s on unknown recipe ids so a typo cannot silently land in
 * the persisted list.
 */
export async function patchRecipeOverride(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  recipeId: string,
  enabled: boolean,
): Promise<CustomizeOverrides> {
  return patchVerificationOverride(fetch, "recipes", recipeId, enabled);
}

/**
 * Persists the USER-RULES.md body via `PUT /v1/app/customize/rules`.
 * Returns the updated overrides. Throws on non-2xx.
 */
export async function putRules(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  text: string,
): Promise<CustomizeOverrides> {
  const res = await fetch(`/v1/app/customize/rules`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(`Failed to save rules (${res.status})`);
  const data = (await res.json()) as { overrides: CustomizeOverrides };
  return data.overrides;
}

/**
 * Policy-envelope fields threaded through a custom-rule save (PR-4 authoring
 * consolidation). When a rule is authored from a natural-language flow, the
 * client passes the user's ORIGINAL sentence as `intent` plus a
 * compiler-suggested (or derived) `displayName`; the server's auto-promoted
 * 1-rule Policy carries both, so the policy card shows the user's own words.
 * These are Policy fields, not rule fields — the server strips them from the
 * persisted rule shape.
 */
export interface CustomRulePolicyEnvelope {
  displayName?: string;
  intent?: string;
}

/**
 * Creates/updates a structured custom rule via `PUT /v1/app/customize/custom-rules`.
 * The server validates (400 on bad shape) and assigns an id. Returns overrides.
 *
 * `policyEnvelope` (optional) threads displayName/intent for the
 * auto-promoted 1-rule Policy; omit it on flows with no NL sentence
 * (Guided/Raw), where the server falls back to the rule id — honest, not
 * fabricated.
 */
export async function putCustomRule(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  rule: CustomRule,
  policyEnvelope?: CustomRulePolicyEnvelope,
): Promise<CustomizeOverrides> {
  const body: Record<string, unknown> = { ...rule };
  if (policyEnvelope?.displayName) body.displayName = policyEnvelope.displayName;
  if (policyEnvelope?.intent) body.intent = policyEnvelope.intent;
  const res = await fetch(`/v1/app/customize/custom-rules`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const msg = Array.isArray(detail?.details) ? detail.details.join("; ") : `(${res.status})`;
    throw new Error(`Failed to save custom rule ${msg}`);
  }
  const data = (await res.json()) as { overrides: CustomizeOverrides };
  return data.overrides;
}

// ---------------------------------------------------------------------------
// SHACL compile-preview API types and client
// ---------------------------------------------------------------------------

/** A single sample-record outcome from the SHACL compile preview. */
export interface ShaclPreviewCase {
  conforms: boolean | null;
  status: string;
  violations: unknown[];
}

/** LLM review of the compiled SHACL shape vs the natural-language intent. */
export interface ShaclReview {
  verdict: string;
  issues: string[];
  confidence: number;
}

/**
 * A single turn in a conversational compile session.
 * Used to carry prior context to the compiler across multiple rounds.
 */
export interface ConversationTurn {
  role: "user" | "assistant";
  content: string;
}

/**
 * Response from `POST /v1/app/customize/custom-rules/compile`.
 * Preview-only — never saves; the caller must call `putCustomRule` after
 * the user explicitly approves the compiled shape.
 *
 * When the compiler needs clarification instead of returning a shape,
 * `clarifyingQuestions` is present, `ok` is false, `shapeTtl` is null,
 * and `error` is explicitly null (not undefined).
 */
export interface ShaclCompileResponse {
  ok: boolean;
  shapeTtl?: string;
  review?: ShaclReview;
  explanation?: string;
  previewCases?: ShaclPreviewCase[];
  previewTruncated?: boolean;
  /** Present when the compiler asks for clarification instead of returning a shape. */
  clarifyingQuestions?: string[];
  /** Compile error message, or explicitly null on the clarifyingQuestions branch. */
  error?: string | null;
}

/**
 * Sends a natural-language constraint description to the local runtime for
 * SHACL compilation and preview.
 *
 * Mirrors the fetch + error-handling pattern of `putCustomRule`:
 * - On non-OK HTTP status: returns `{ ok: false, error }` — does NOT throw.
 * - On network error: returns `{ ok: false, error }` — does NOT throw.
 * - On success: returns the `ShaclCompileResponse` from the server.
 *
 * The UI is responsible for displaying the error; this function is safe to
 * await without a try/catch.
 *
 * When `priorTurns` is provided and non-empty, it is included in the POST body
 * to carry conversational context to the compiler. When omitted or empty, the
 * `priorTurns` key is NOT included in the body (existing callers are byte-identical
 * at the wire level).
 */
export async function compileCustomRule(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  nlText: string,
  sampleRecords?: unknown[],
  priorTurns?: ConversationTurn[],
): Promise<ShaclCompileResponse> {
  try {
    const bodyPayload: {
      nlText: string;
      sampleRecords?: unknown[];
      priorTurns?: ConversationTurn[];
    } = { nlText, sampleRecords };
    if (priorTurns !== undefined && priorTurns.length > 0) {
      bodyPayload.priorTurns = priorTurns;
    }
    const res = await fetch(`/v1/app/customize/custom-rules/compile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bodyPayload),
    });
    if (!res.ok) {
      let backendError = `Compile request failed (${res.status})`;
      try {
        const errBody = (await res.json()) as { error?: string };
        if (typeof errBody.error === "string" && errBody.error.length > 0) backendError = errBody.error;
      } catch { /* ignore JSON parse failure on error body */ }
      return { ok: false, error: backendError };
    }
    return (await res.json()) as ShaclCompileResponse;
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Network error";
    return { ok: false, error: message };
  }
}

// ---------------------------------------------------------------------------
// Conversational compile — POST /v1/app/customize/custom-rules/compile-interactive
// ---------------------------------------------------------------------------


/** One question the assistant surfaces to the operator on a turn. */
export interface InteractiveQuestionOption {
  value: string;
  label: string;
  hint?: string;
}


export interface InteractiveQuestion {
  id: string;
  prompt: string;
  kind: "single_select" | "multi_select" | "text";
  targets_field: string;
  options: InteractiveQuestionOption[] | null;
}


/** One assistant or user turn the wire carries. Local-only `questions`
 *  metadata stays on the client and is stripped before the next POST. */
export interface InteractiveHistoryTurn {
  role: "user" | "assistant";
  content: string;
}


/** Body sent on every conversational turn. */
export interface InteractiveCompileRequest {
  history: InteractiveHistoryTurn[];
  draft_so_far: Record<string, unknown> | null;
  answers: Record<string, string> | null;
}


/** Response mirrored verbatim from
 *  ``nl_compiler_interactive.InteractiveTurnResult.to_dict``. */
export interface InteractiveCompileResponse {
  /** Plain-English status line; always present on a 200 envelope. */
  assistant_message?: string;
  /** Server-side IR snapshot — the dashboard's live draft pane reads
   *  this directly to fill the right-hand summary. */
  draft?: Record<string, unknown> | null;
  /** Canonical field-name list the state machine still needs to fill. */
  missing_fields?: string[];
  /** 0..2 clarifying questions the operator picks/answers next turn. */
  questions?: InteractiveQuestion[];
  /** True iff the validator does NOT accept the draft yet. */
  needs_more?: boolean;
  /** True iff the runtime validator accepts the draft as-is. Save CTA
   *  on the dashboard's draft pane is gated on this flag. */
  ready_to_save?: boolean;
  /** Validator complaints surfaced for the Save CTA tooltip; plain-
   *  language scrubbed. */
  schema_issues?: string[];
  /** Disabled-feature envelope shape (mirrors the one-shot compile route)
   *  so the dashboard can render a fallback banner without branching on
   *  HTTP status. */
  ok?: boolean;
  error?: string;
}


/**
 * Sends one conversational turn to the magi-agent runtime. Mirrors the
 * fetch + error-handling pattern of ``compileCustomRule`` (one-shot):
 *
 * - HTTP 200: returns the wire envelope verbatim (caller renders).
 * - HTTP 4xx/5xx: returns ``{ok:false, error}`` synthesized from the
 *   upstream body — does NOT throw.
 * - Network failure: same envelope shape with a synthetic error string.
 *
 * The conversational state machine is server-driven; the client is
 * a thin shell that forwards ``(history, draft_so_far, answers)`` and
 * mirrors the response into local React state.
 */
export async function compileCustomRuleInteractive(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  body: InteractiveCompileRequest,
): Promise<InteractiveCompileResponse> {
  try {
    const res = await fetch(
      `/v1/app/customize/custom-rules/compile-interactive`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (!res.ok) {
      let backendError = `Compile-interactive failed (${res.status})`;
      try {
        const errBody = (await res.json()) as { error?: string };
        if (typeof errBody.error === "string" && errBody.error.length > 0) {
          backendError = errBody.error;
        }
      } catch {
        /* ignore JSON parse failure on error body */
      }
      return { ok: false, error: backendError };
    }
    return (await res.json()) as InteractiveCompileResponse;
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Network error";
    return { ok: false, error: message };
  }
}


// ---------------------------------------------------------------------------
// Conversational POLICY compile (multi-rule producer + gate + binding).
// POST /v1/app/policies/compile/interactive  (multi-turn)
// POST /v1/app/policies/from-plan            (persist an assembled plan)
// ---------------------------------------------------------------------------

/** Body sent on every conversational policy turn. */
export interface PolicyInteractiveRequest {
  history: InteractiveHistoryTurn[];
  paramsSoFar: Record<string, unknown> | null;
  answers: Record<string, string> | null;
}


/** Response mirrored from ``nl_policy_interactive.step_policy_compile``. */
export interface PolicyInteractiveResponse {
  assistant_message?: string;
  /** The params assembled so far (gatedTool / evidenceLabel / ...). */
  params?: Record<string, unknown>;
  /** The assembled producer+gate+binding plan; present only when
   *  ``ready_to_save`` is true. The Save CTA persists it via
   *  {@link savePolicyFromPlan}. */
  plan?: Record<string, unknown> | null;
  missing_params?: string[];
  questions?: InteractiveQuestion[];
  needs_more?: boolean;
  ready_to_save?: boolean;
  schema_issues?: string[];
  /** True when the assembled plan binds to a producer the operator already
   *  authored (emitting the same evidence type) instead of a fresh one. The
   *  human-readable note is appended to ``assistant_message`` by the backend. */
  producer_reused?: boolean;
  ok?: boolean;
  error?: string;
}


/** Result of persisting an assembled plan. */
export interface PolicyFromPlanResponse {
  ok?: boolean;
  policyId?: string;
  producerId?: string;
  gateId?: string;
  error?: string;
  message?: string;
}


/** The advisory verdict half of a policy review (mirrors
 *  ``policy_review.review_policy_plan``'s ``review`` field). */
export interface PolicyReviewVerdict {
  verdict?: "aligned" | "partial" | "misaligned" | "unknown";
  issues?: string[];
  confidence?: number;
  coverage?: string;
}


/** Response of ``POST /v1/app/policies/review``: deterministic integrity
 *  findings (the hard signal) + an advisory LLM verdict (guidance only). */
export interface PolicyReviewResponse {
  /** Deterministic structural findings; empty = structurally sound. */
  structural?: string[];
  structurallySound?: boolean;
  review?: PolicyReviewVerdict;
  ok?: boolean;
  error?: string;
}


/** One conversational policy turn. Same thin-shell contract as
 *  {@link compileCustomRuleInteractive}: 200 -> wire envelope verbatim;
 *  non-OK / network failure -> ``{ok:false, error}`` (never throws). */
export async function compilePolicyInteractive(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  body: PolicyInteractiveRequest,
): Promise<PolicyInteractiveResponse> {
  try {
    const res = await fetch(`/v1/app/policies/compile/interactive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let backendError = `Policy compile-interactive failed (${res.status})`;
      try {
        const errBody = (await res.json()) as { error?: string };
        if (typeof errBody.error === "string" && errBody.error.length > 0) {
          backendError = errBody.error;
        }
      } catch {
        /* ignore JSON parse failure on error body */
      }
      return { ok: false, ready_to_save: false, error: backendError };
    }
    return (await res.json()) as PolicyInteractiveResponse;
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Network error";
    return { ok: false, ready_to_save: false, error: message };
  }
}


/** Persists an assembled policy plan (producer + gate + Policy record).
 *  Same non-throwing envelope contract as the compile helpers. */
export async function savePolicyFromPlan(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  plan: Record<string, unknown>,
): Promise<PolicyFromPlanResponse> {
  try {
    const res = await fetch(`/v1/app/policies/from-plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan }),
    });
    if (!res.ok) {
      let backendError = `Save policy failed (${res.status})`;
      try {
        const errBody = (await res.json()) as { error?: string; message?: string };
        if (typeof errBody.message === "string" && errBody.message.length > 0) {
          backendError = errBody.message;
        } else if (typeof errBody.error === "string" && errBody.error.length > 0) {
          backendError = errBody.error;
        }
      } catch {
        /* ignore */
      }
      return { ok: false, error: backendError };
    }
    return (await res.json()) as PolicyFromPlanResponse;
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Network error";
    return { ok: false, error: message };
  }
}


/** Reviews an assembled policy plan: deterministic integrity findings plus an
 *  advisory LLM intent-coverage verdict. Advisory only, never blocks a save.
 *  Same non-throwing envelope contract as the compile/save helpers. */
export async function reviewPolicyPlan(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  plan: Record<string, unknown>,
): Promise<PolicyReviewResponse> {
  try {
    const res = await fetch(`/v1/app/policies/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan }),
    });
    if (!res.ok) {
      let backendError = `Policy review failed (${res.status})`;
      try {
        const errBody = (await res.json()) as { error?: string };
        if (typeof errBody.error === "string" && errBody.error.length > 0) {
          backendError = errBody.error;
        }
      } catch {
        /* ignore */
      }
      return { ok: false, error: backendError };
    }
    return (await res.json()) as PolicyReviewResponse;
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Network error";
    return { ok: false, error: message };
  }
}


/**
 * Toggles a USER policy on/off via `PATCH /v1/app/policies/{id}` `{enabled}`
 * (PR-1 U4). The server atomically cascades `enabled` to every member custom
 * rule and re-projects the verification overrides so the change takes effect
 * next turn. Throws on non-2xx so the caller can surface the error and revert:
 *   - 404 unknown policy id
 *   - 409 first-party (builtin) policy — those toggle via their own route
 *
 * The response carries the refreshed policy list; the caller should still
 * `reload()` the catalog to pick up the cascaded member-rule states.
 */
export async function patchPolicyEnabled(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  policyId: string,
  enabled: boolean,
): Promise<void> {
  const res = await fetch(`/v1/app/policies/${encodeURIComponent(policyId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) {
    if (res.status === 409) {
      throw new Error("This built-in policy is toggled from its own control.");
    }
    if (res.status === 404) throw new Error("Policy not found.");
    throw new Error(`Failed to update policy (${res.status})`);
  }
}

/** Body for {@link upsertPolicy} — the user-facing Policy envelope. */
export interface UpsertPolicyInput {
  displayName: string;
  intent?: string;
  ruleIds: string[];
}

/**
 * Creates/updates a Policy record via `PUT /v1/app/policies/{id}` (PR-4).
 *
 * Used by the NL hybrid-proposal activate path: after saving the N member
 * rules under one groupId (whose per-rule auto-promotion the server skips),
 * the client upserts ONE Policy carrying the operator's original sentence as
 * `intent` — so a hybrid composition renders as a single policy card instead
 * of shattering into member rows.
 */
export async function upsertPolicy(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  policyId: string,
  input: UpsertPolicyInput,
): Promise<void> {
  const res = await fetch(`/v1/app/policies/${encodeURIComponent(policyId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Failed to save policy (${res.status})`);
}

/**
 * Deletes a policy record via `DELETE /v1/app/policies/{id}`.
 *
 * NOTE: the backend route deletes ONLY the policy record — it does NOT cascade
 * to member custom rules. Per the magi-cp precedent (cascade delete), the
 * caller is responsible for deleting the member rules client-side (sequential
 * {@link deleteCustomRule} calls) so a delete does not orphan the members back
 * onto the surface as loose rows.
 */
export async function deletePolicy(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  policyId: string,
): Promise<void> {
  const res = await fetch(`/v1/app/policies/${encodeURIComponent(policyId)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete policy (${res.status})`);
}


/** Deletes a custom rule by id via `DELETE /v1/app/customize/custom-rules/{id}`. */
export async function deleteCustomRule(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  id: string,
): Promise<CustomizeOverrides> {
  const res = await fetch(`/v1/app/customize/custom-rules/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete custom rule (${res.status})`);
  const data = (await res.json()) as { overrides: CustomizeOverrides };
  return data.overrides;
}


// ---------------------------------------------------------------------------
// PR-C3 — SeamSpec NL builder API client (handoff §5)
// ---------------------------------------------------------------------------

/** One mutation against the static PRESET_SEAMS catalog. */
export interface SeamSpecAction {
  op: "add_seam" | "modify_seam";
  preset_id: string;
  controls_refs?: string[];
  runtime_default_on?: boolean;
  wiring?: "opt_in" | "opt_out";
  controls_kind?: "validator" | "evidence";
  supported_modes?: string[];
}

/** A persisted/in-flight SeamSpec document (matches the Python wire shape). */
export interface SeamSpecDoc {
  /** Server-assigned id once persisted; absent on first compile. */
  id?: string;
  spec_version: string;
  actions: SeamSpecAction[];
}

/** LLM critic verdict from the compile route. */
export interface SeamSpecReview {
  verdict: "aligned" | "mismatch" | "overbroad" | "underbroad" | "unknown";
  issues: string[];
  confidence: number;
}

/**
 * Response from `POST /v1/app/customize/seams/compile` (preview-only).
 *
 * On success: `ok: true`, `spec` is the compiled SeamSpec, `review` is the
 * LLM critic verdict, `schemaIssues` is the deterministic structural check.
 * On clarifying-questions: `ok: false`, `clarifyingQuestions` is the list,
 * `spec` is null, `error` is explicitly null (not undefined).
 * On compile failure: `ok: false`, `error` carries the reason.
 * Flag-OFF: `ok: false`, `error: "seam-spec compiler disabled"`.
 */
export interface SeamSpecCompileResponse {
  ok: boolean;
  spec?: SeamSpecDoc | null;
  review?: SeamSpecReview;
  schemaIssues?: string[];
  clarifyingQuestions?: string[];
  error?: string | null;
}

/**
 * Compiles a natural-language policy into a SeamSpec via
 * `POST /v1/app/customize/seams/compile`. Same error contract as
 * `compileCustomRule`: never throws on a 4xx/5xx or network error — returns
 * `{ ok: false, error }` so the UI can render without a try/catch.
 */
export async function compileSeamSpec(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  nlText: string,
  priorTurns?: ConversationTurn[],
): Promise<SeamSpecCompileResponse> {
  try {
    const bodyPayload: { nlText: string; priorTurns?: ConversationTurn[] } = { nlText };
    if (priorTurns !== undefined && priorTurns.length > 0) {
      bodyPayload.priorTurns = priorTurns;
    }
    const res = await fetch(`/v1/app/customize/seams/compile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bodyPayload),
    });
    if (!res.ok) {
      let backendError = `Compile request failed (${res.status})`;
      try {
        const errBody = (await res.json()) as { error?: string };
        if (typeof errBody.error === "string" && errBody.error.length > 0) backendError = errBody.error;
      } catch { /* ignore JSON parse failure on error body */ }
      return { ok: false, error: backendError };
    }
    return (await res.json()) as SeamSpecCompileResponse;
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Network error";
    return { ok: false, error: message };
  }
}

/**
 * Persists an approved SeamSpec via `PUT /v1/app/customize/seams`. The server
 * structurally re-validates and returns 422 with `schemaIssues` if the spec
 * still has issues; throws on any other non-OK status so the caller surfaces
 * unexpected failures rather than silently dropping them.
 */
export async function putSeamSpec(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  doc: SeamSpecDoc,
): Promise<{ id: string; overrides: CustomizeOverrides }> {
  const res = await fetch(`/v1/app/customize/seams`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(doc),
  });
  if (res.status === 422) {
    const body = (await res.json().catch(() => ({}))) as {
      error?: string;
      schemaIssues?: string[];
    };
    throw new Error(
      body.schemaIssues?.join("; ") ?? body.error ?? `Invalid spec (${res.status})`,
    );
  }
  if (!res.ok) throw new Error(`Failed to save seam spec (${res.status})`);
  const data = (await res.json()) as { id: string; overrides: CustomizeOverrides };
  return data;
}

/** Deletes a persisted SeamSpec by id via `DELETE /v1/app/customize/seams/{id}`. */
export async function deleteSeamSpec(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  id: string,
): Promise<CustomizeOverrides> {
  const res = await fetch(`/v1/app/customize/seams/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete seam spec (${res.status})`);
  const data = (await res.json()) as { overrides: CustomizeOverrides };
  return data.overrides;
}


// ---------------------------------------------------------------------------
// PR-F2 — Evidence live-catalog (input-space browser) API client
// ---------------------------------------------------------------------------

/**
 * Per-type entry returned by `GET /v1/app/customize/evidence/live-catalog`.
 *
 * - `registeredFields`: the static `_BUILTIN_FIELD_HINTS` vocabulary for the
 *   evidence type. Empty list = inert-producer (no field constraints
 *   authorable until the producer is extended; the UI must flag this state
 *   honestly rather than hide the type).
 * - `fieldsPopulatedRecently`: the subset of registered fields actually
 *   observed in `EvidenceRecord.fields` across the recent ledger sampling
 *   window. Drives the "Authorable now" badge together with `refsUsing`.
 * - `samplePopulationCount`: how many records (within the window) contributed
 *   to the populated-field union. Surfaced as honest signal of sample depth.
 * - `refsUsing`: named evidence refs (from `what_menu` etc.) that target this
 *   type and are currently rule-ready (producer active).
 * - `rulesReferencing`: count of user rules that name one of `refsUsing`.
 */
export interface EvidenceLiveCatalogTypeEntry {
  type: string;
  registeredFields: string[];
  fieldsPopulatedRecently: string[];
  samplePopulationCount: number;
  refsUsing: string[];
  rulesReferencing: number;
}

/**
 * Response from `GET /v1/app/customize/evidence/live-catalog`.
 *
 * Spec §5 PR-F2: read-only, fail-open. The server returns an empty
 * `evidenceTypes` list on ledger read error rather than 5xx. The client
 * mirrors that contract: on any fetch failure we return an empty catalog
 * so the UI degrades to the "no evidence types observed yet" empty state
 * rather than crashing.
 */
export interface EvidenceLiveCatalog {
  evidenceTypes: EvidenceLiveCatalogTypeEntry[];
  /** Human description of the sampling window (e.g. "last 100 turns"). */
  samplingWindow: string;
  /** ISO-8601 timestamp of when the snapshot was assembled. */
  asOf: string;
}

/**
 * Loads the evidence live catalog. Fail-open contract: any network/HTTP
 * error returns an empty catalog rather than throwing, so the consumer can
 * render the empty state without a try/catch wrapper.
 */
export async function getEvidenceLiveCatalog(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
): Promise<EvidenceLiveCatalog> {
  try {
    const res = await fetch(`/v1/app/customize/evidence/live-catalog`);
    if (!res.ok) {
      return { evidenceTypes: [], samplingWindow: "", asOf: "" };
    }
    return (await res.json()) as EvidenceLiveCatalog;
  } catch {
    return { evidenceTypes: [], samplingWindow: "", asOf: "" };
  }
}


// ---------------------------------------------------------------------------
// PR-F-UX2 (F8 core) — runtime-fields chip menu API client
// ---------------------------------------------------------------------------

/**
 * One chip in the wizard's variable picker. Mirrors the backend's
 * ``magi_agent.customize.runtime_fields.RuntimeField`` shape.
 */
export interface RuntimeFieldChip {
  /** Canonical variable name (e.g. ``session_id``, ``tool_input.url``, ``evidence:TestRun.fields.command``). */
  name: string;
  /** JSON-Schema-style type ("string", "bool", "object", ...). */
  type: string;
  /** Human description; may be empty for tool-input properties whose manifest has no description. */
  description: string;
}

/**
 * Response shape from `GET /v1/app/customize/runtime-fields`.
 *
 * Fail-open contract (matches the backend): an unknown (lifecycle, condition)
 * tuple returns ``{fields: [], context, source: 'unknown'}`` rather than
 * 4xx/5xx so the chip picker silently falls back to a plain text input.
 */
export interface RuntimeFieldsResponse {
  fields: RuntimeFieldChip[];
  /** Echo of the resolved tuple ("lifecycle/condition[/tool]"). */
  context: string;
  /** Provenance marker — "fields_for_context" on a hit, "unknown" on miss. */
  source: string;
}

/**
 * Fetch the variable chip menu for a (lifecycle, condition, tool?) tuple.
 *
 * - Read-only (GET) and fail-open: a fetch / HTTP error returns an empty
 *   chip list rather than throwing, so the consumer renders without a
 *   try/catch wrapper.
 * - Gated by ``MAGI_CUSTOMIZE_RUNTIME_FIELDS_ENDPOINT_ENABLED``; when OFF
 *   the endpoint responds 404 and the chip picker degrades to "no chips".
 */
export async function getRuntimeFields(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  args: { lifecycle: string; condition: string; tool?: string | null },
): Promise<RuntimeFieldsResponse> {
  const params = new URLSearchParams({
    lifecycle: args.lifecycle,
    condition: args.condition,
  });
  if (args.tool) {
    params.set("tool", args.tool);
  }
  const path = `/v1/app/customize/runtime-fields?${params.toString()}`;
  const empty: RuntimeFieldsResponse = {
    fields: [],
    context: `${args.lifecycle}/${args.condition}`,
    source: "unknown",
  };
  try {
    const res = await fetch(path);
    if (!res.ok) {
      return empty;
    }
    return (await res.json()) as RuntimeFieldsResponse;
  } catch {
    return empty;
  }
}


// ---------------------------------------------------------------------------
// PR-D1/D2 — Unified NL → rule compiler API client
// ---------------------------------------------------------------------------

/** The seven backing primitives the unified NL compiler can route to.
 *  `field_constraint` (F3) is the structured-picker form of a SHACL shape:
 *  the compiler emits a `{evidenceType, field, operator, value}` IR (with a
 *  cross-record `forEachExistsCovering` variant carrying `source` + `target`)
 *  and the frontend renders it as editable chips. The IR persists as a
 *  `shacl_constraint` on disk (deterministic synth at save), so no new
 *  storage path is introduced. */
export type RoutedKind =
  | "deterministic_ref"
  | "tool_perm"
  | "llm_criterion"
  | "shacl_constraint"
  | "field_constraint"
  | "seam_spec"
  | "custom_check"
  // PR-F4: spawn-time toolset cap. PR-F-UX6 architect may surface this
  // as a primitive in a hybrid composition (e.g. cap subagents + advisory
  // critic on the parent answer).
  | "capability_scope";

/** Honest-degrade payload returned by the NL compiler when the rule
 *  references an evidence field that no producer is known to emit. */
export interface MissingFieldEntry {
  evidenceType: string;
  field: string;
}

/** Same verdict shape as SHACL / SeamSpec reviewers. */
export interface RuleReview {
  verdict: "aligned" | "mismatch" | "overbroad" | "underbroad" | "unknown";
  issues: string[];
  confidence: number;
}

/**
 * Response from `POST /v1/app/customize/rules/compile` (preview-only).
 *
 * On success: `ok: true` + `routedKind` + `draft` (CustomRule | SeamSpecDoc |
 * DashboardCheck shape per kind) + `review` + `schemaIssues` + `explanation`.
 * On clarifying-questions: `ok: false`, `clarifyingQuestions` is the list,
 * `routedKind` / `draft` are null, `error` is explicitly null.
 * On compile failure: `ok: false`, `error` carries the reason.
 * Flag-OFF: `ok: false`, `error: "nl-rule compiler disabled"`.
 *
 * PR-F-UX6 interview-mode additions (sent only when
 * MAGI_CUSTOMIZE_NL_INTERVIEW_MODE_ENABLED is ON):
 *   - `mode: "interview"` + `questions: InterviewQuestion[]` — the compiler
 *     needs more input; render each question with a chip picker per
 *     `expects` tag.
 *   - `mode: "proposal"` + `proposal: ArchitectProposal` — the compiler
 *     proposes a single primitive OR a hybrid composition of N primitives
 *     sharing a logical groupId; render the ProposalCard.
 *   - Legacy callers without these fields keep working — `mode` is absent.
 */
export interface RuleCompileResponse {
  ok: boolean;
  routedKind?: RoutedKind | null;
  draft?: unknown;
  explanation?: string;
  review?: RuleReview;
  schemaIssues?: string[];
  clarifyingQuestions?: string[];
  error?: string | null;
  /** F3 honest-degrade: populated when `error === "field_not_in_catalog"`,
   *  listing the (evidenceType, field) tuples the producer does not emit.
   *  Used by the NL compose UI to render a red banner with a "Browse
   *  available fields" link and an "Author as advisory llm_criterion
   *  instead?" recovery action. */
  missingFields?: MissingFieldEntry[];
  /** Optional human-readable suggestion the backend pairs with
   *  `field_not_in_catalog` (e.g. "Browse available fields at Customize >
   *  Reusable evidence."). */
  suggestion?: string;
  /** PR-F-UX6: interview-mode response branch. Absent on legacy compile
   *  success / clarifying-questions / error responses. */
  mode?: "interview" | "proposal";
  /** PR-F-UX6: present when `mode === "interview"`. */
  questions?: InterviewQuestion[];
  /** PR-F-UX6: present when `mode === "interview"` or `mode === "proposal"` —
   *  the architect's structured intent map so the frontend can drop into
   *  the wizard with pre-filled state. */
  intent?: ArchitectIntent;
  /** PR-F-UX6: present when `mode === "proposal"`. */
  proposal?: ArchitectProposal;
}

/** PR-F-UX6 — the vocabulary of `expects` tags the architect may emit on
 *  each open question. Drives the per-question chip-picker component in
 *  the NL compose UI. */
export type ArchitectExpects =
  | "evidence_ref"
  | "verifier_ref"
  | "field"
  | "tool_name"
  | "lifecycle"
  | "scope"
  | "value"
  | "freeform";

/** PR-F-UX6 — one open question the architect needs answered before it can
 *  propose a primitive. */
export interface InterviewQuestion {
  question: string;
  expects: ArchitectExpects;
  /** Optional closed-set inventory the operator may pick from (e.g. the
   *  runtime tool names). Absent → freeform text input. */
  inventory?: string[];
}

/** PR-F-UX6 — structured intent map the architect produces in `discover_intent`. */
export interface ArchitectIntent {
  whatToCheck: string;
  whereInLifecycle: string;
  whatToDoOnFail: string;
  openQuestions: InterviewQuestion[];
  confidence: number;
}

/** PR-F-UX6 — trust-class taxonomy the architect declares per primitive in
 *  a proposal. Mirrors the frontend TrustBadge bucket names. */
export type ArchitectTrustClass = "deterministic" | "advisory";

/** PR-F-UX6 — one primitive within an architect proposal. `payload` is the
 *  same shape the legacy one-shot compiler emits for `kind` (so the same
 *  PUT routes accept it on activate). */
export interface ArchitectPrimitive {
  kind: RoutedKind;
  payload: unknown;
  trustClass: ArchitectTrustClass;
  rationale: string;
}

/** PR-F-UX6 — full architect proposal. `mode: "single"` → one primitive;
 *  `mode: "hybrid"` → N primitives composed and persisted under one
 *  logical groupId. */
export interface ArchitectProposal {
  mode: "single" | "hybrid";
  primitives: ArchitectPrimitive[];
  summary: string;
  explanation: string;
}

/**
 * Compiles a natural-language policy via `POST /v1/app/customize/rules/
 * compile`. Same error contract as `compileSeamSpec` / `compileCustomRule`:
 * never throws on a 4xx/5xx or network error.
 *
 * PR-F-UX6: `mode === "interview"` forces the architect interview path
 * even on well-formed inputs (the UI's "Refine" affordance). Omit the
 * arg to let the backend pick (legacy heuristic + flag-gated).
 */
export async function compileRule(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  nlText: string,
  priorTurns?: ConversationTurn[],
  mode?: "interview",
): Promise<RuleCompileResponse> {
  try {
    const bodyPayload: {
      nlText: string;
      priorTurns?: ConversationTurn[];
      mode?: "interview";
    } = { nlText };
    if (priorTurns !== undefined && priorTurns.length > 0) {
      bodyPayload.priorTurns = priorTurns;
    }
    if (mode !== undefined) {
      bodyPayload.mode = mode;
    }
    const res = await fetch(`/v1/app/customize/rules/compile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bodyPayload),
    });
    if (!res.ok) {
      let backendError = `Compile request failed (${res.status})`;
      try {
        const errBody = (await res.json()) as { error?: string };
        if (typeof errBody.error === "string" && errBody.error.length > 0) backendError = errBody.error;
      } catch { /* ignore JSON parse failure on error body */ }
      return { ok: false, error: backendError };
    }
    return (await res.json()) as RuleCompileResponse;
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Network error";
    return { ok: false, error: message };
  }
}

/**
 * Loads the local runtime customization snapshot from `/v1/app/customize`.
 *
 * Handles loading/error state and exposes a `reload` callback so the UI can
 * retry after a failed fetch. This phase is read-only — override mutations are
 * held in component state by the consumer and are not persisted here.
 */
export function useCustomize(): UseCustomizeResult {
  const agentFetch = useAgentFetch();
  const [data, setData] = useState<CustomizeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(() => {
    setReloadKey((value) => value + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    agentFetch("/v1/app/customize")
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Failed to load /v1/app/customize (${response.status})`);
        }
        const payload = (await response.json()) as CustomizeResponse;
        if (!cancelled) {
          setData(payload);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load /v1/app/customize",
          );
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [agentFetch, reloadKey]);

  return { data, loading, error, reload };
}


// ---------------------------------------------------------------------------
// PR-F7 — Customize budgets (cost vocabulary) API client
// ---------------------------------------------------------------------------

/**
 * Loads the persisted budgets + the resolved env snapshot via
 * `GET /v1/app/customize/budgets`. Throws on non-2xx so the budgets tab can
 * surface a "could not load" error and the user retries; not fail-open
 * because this is a settings-screen read where silent emptiness would
 * obscure a misconfigured runtime.
 */
export async function getBudgets(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
): Promise<BudgetsResponse> {
  const res = await fetch(`/v1/app/customize/budgets`);
  if (!res.ok) throw new Error(`Failed to load budgets (${res.status})`);
  return (await res.json()) as BudgetsResponse;
}

/**
 * Persists a new budgets dict via `PUT /v1/app/customize/budgets`. Unknown
 * keys / non-positive-int / boolean values are rejected by the server with
 * 400; the thrown Error includes the joined details so the UI can surface
 * "loopGuardHardThreshold: must be > 0 (got -1)" verbatim.
 *
 * Returns the post-save response (including the refreshed `effectiveEnv`) so
 * the budgets tab can reflect the operator-env precedence immediately.
 */
export async function putBudgets(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  budgets: VerificationBudgets,
): Promise<BudgetsResponse> {
  const res = await fetch(`/v1/app/customize/budgets`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ budgets }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const msg = Array.isArray(detail?.details)
      ? detail.details.join("; ")
      : `(${res.status})`;
    throw new Error(`Failed to save budgets ${msg}`);
  }
  return (await res.json()) as BudgetsResponse;
}
