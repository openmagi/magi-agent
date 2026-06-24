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

export interface CustomizeCatalog {
  verification: {
    recipes: RecipeItem[];
    harnessPresets: HarnessPresetItem[];
    hooks: HookItem[];
    customRuleMenu: CustomRuleMenuItem[];
  };
  tools: ToolItem[];
  controlPlane: ControlPlaneBehaviorItem[];
}

/** A structured custom verification rule (spec §9.1). P1 builds deterministic_ref. */
export interface CustomRule {
  id?: string;
  scope: string;
  enabled: boolean;
  what: { kind: string; payload: Record<string, unknown> };
  firesAt: string;
  action: string;
  projection?: string[];
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
 * Creates/updates a structured custom rule via `PUT /v1/app/customize/custom-rules`.
 * The server validates (400 on bad shape) and assigns an id. Returns overrides.
 */
export async function putCustomRule(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  rule: CustomRule,
): Promise<CustomizeOverrides> {
  const res = await fetch(`/v1/app/customize/custom-rules`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rule),
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
// PR-D1/D2 — Unified NL → rule compiler API client
// ---------------------------------------------------------------------------

/** The six backing primitives the unified NL compiler can route to. */
export type RoutedKind =
  | "deterministic_ref"
  | "tool_perm"
  | "llm_criterion"
  | "shacl_constraint"
  | "seam_spec"
  | "custom_check";

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
}

/**
 * Compiles a natural-language policy via `POST /v1/app/customize/rules/
 * compile`. Same error contract as `compileSeamSpec` / `compileCustomRule`:
 * never throws on a 4xx/5xx or network error.
 */
export async function compileRule(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  nlText: string,
  priorTurns?: ConversationTurn[],
): Promise<RuleCompileResponse> {
  try {
    const bodyPayload: { nlText: string; priorTurns?: ConversationTurn[] } = { nlText };
    if (priorTurns !== undefined && priorTurns.length > 0) {
      bodyPayload.priorTurns = priorTurns;
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
