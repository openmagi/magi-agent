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

export interface CustomizeCatalog {
  verification: {
    recipes: RecipeItem[];
    harnessPresets: HarnessPresetItem[];
    hooks: HookItem[];
    customRuleMenu: CustomRuleMenuItem[];
  };
  tools: ToolItem[];
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
  };
  tools: Record<string, boolean>;
  /** Free-text USER-RULES.md body injected into the system prompt. */
  user_rules: string;
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
