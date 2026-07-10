/**
 * API client for the dashboard pack-kind builder surface.
 *
 * Mirrors the `GET/PUT/DELETE /v1/app/packs/dashboard/checks` + `GET
 * /v1/app/packs/dashboard/menu` contract served by the local Python runtime
 * (`magi_agent/transport/packs_dashboard.py`). Self-host only — the runtime
 * returns 410 when `MAGI_DASHBOARD_PACK_AUTHORING_ENABLED` is OFF.
 *
 * A "check" is a UI-friendly after-tool match condition (producer side) plus
 * the validator that requires the matching evidence be absent (`block`) or only
 * audits it (`audit`). The wire shape uses camelCase (`isRegex`) — the backend
 * pydantic models serialize `by_alias`.
 */

export type DashboardScope = "always" | "coding" | "research" | "delivery";
export type DashboardAction = "block" | "audit";

/** The after-tool content match condition. */
export interface DashboardTriggerMatch {
  pattern: string;
  /** When true, `pattern` is a regular expression; otherwise a substring. */
  isRegex: boolean;
}

/** Tool name + trigger that fires the check on `on_after_tool`. */
export interface DashboardTrigger {
  tool: string;
  /**
   * A result-text match. Optional now that an arguments-based
   * `domainAllowlist` trigger exists (mirrors the backend
   * `DashboardTrigger.match: DashboardTriggerMatch | None`); at least one of
   * `match` / `domainAllowlist` is present.
   */
  match?: DashboardTriggerMatch | null;
  /**
   * An ARGUMENTS-based domain allowlist. When set, the check fires on the
   * tool's URL-argument host (not the result text) — a deterministic source
   * credibility signal. Mirrors the backend `domain_allowlist` field.
   */
  domainAllowlist?: string[];
}

/** A single dashboard-authored custom check. */
export interface DashboardCheck {
  id: string;
  label: string;
  scope: DashboardScope;
  enabled: boolean;
  trigger: DashboardTrigger;
  action: DashboardAction;
}

/** Response of `GET`/`PUT`/`DELETE` on the checks collection. */
export interface DashboardChecksResponse {
  enabled: boolean;
  packs_root: string;
  checks: DashboardCheck[];
}

/** Response of `GET /v1/app/packs/dashboard/menu`. */
export interface DashboardPacksMenuResponse {
  tools: string[];
}

type AgentFetch = (path: string, init?: RequestInit) => Promise<Response>;

const CHECKS_PATH = "/v1/app/packs/dashboard/checks";
const MENU_PATH = "/v1/app/packs/dashboard/menu";

/**
 * Loads the authored checks via `GET /v1/app/packs/dashboard/checks`.
 * Throws on non-2xx (e.g. 410 when authoring is disabled) so the caller can
 * surface the disabled state.
 */
export async function getDashboardChecks(
  fetch: AgentFetch,
): Promise<DashboardChecksResponse> {
  const res = await fetch(CHECKS_PATH);
  if (!res.ok) throw new Error(`Failed to load dashboard checks (${res.status})`);
  return (await res.json()) as DashboardChecksResponse;
}

/**
 * Loads the tool catalog menu via `GET /v1/app/packs/dashboard/menu`.
 * Throws on non-2xx so the caller can surface the disabled state.
 */
export async function getDashboardPacksMenu(
  fetch: AgentFetch,
): Promise<DashboardPacksMenuResponse> {
  const res = await fetch(MENU_PATH);
  if (!res.ok) throw new Error(`Failed to load dashboard pack menu (${res.status})`);
  return (await res.json()) as DashboardPacksMenuResponse;
}

/**
 * Creates/updates a check via `PUT /v1/app/packs/dashboard/checks/{id}`.
 * The server validates (400 on bad shape). Returns the refreshed collection.
 */
export async function putDashboardCheck(
  fetch: AgentFetch,
  check: DashboardCheck,
): Promise<DashboardChecksResponse> {
  const res = await fetch(`${CHECKS_PATH}/${encodeURIComponent(check.id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(check),
  });
  if (!res.ok) {
    const detail: unknown = await res.json().catch(() => null);
    const errors =
      detail && typeof detail === "object" && "errors" in detail
        ? (detail as { errors?: unknown }).errors
        : undefined;
    const msg = Array.isArray(errors) ? errors.join("; ") : `(${res.status})`;
    throw new Error(`Failed to save dashboard check ${msg}`);
  }
  return (await res.json()) as DashboardChecksResponse;
}

/**
 * Deletes a check via `DELETE /v1/app/packs/dashboard/checks/{id}`.
 * Returns the refreshed collection. Throws on non-2xx.
 */
export async function deleteDashboardCheck(
  fetch: AgentFetch,
  id: string,
): Promise<DashboardChecksResponse> {
  const res = await fetch(`${CHECKS_PATH}/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete dashboard check (${res.status})`);
  return (await res.json()) as DashboardChecksResponse;
}
