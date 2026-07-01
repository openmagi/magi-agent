/**
 * Agent-mode (posture) CRUD client — thin wrappers over `/v1/app/modes`.
 *
 * A *mode* is a user-authored posture: a soft system prompt + a tool allow/deny
 * delta + scoped policy ids. These endpoints are served by the local runtime
 * (`magi_agent/transport/customize.py`); the dashboard reaches them via
 * `agentFetch`, which attaches the loopback gateway token.
 *
 * Wire shapes (authoritative = the FastAPI handlers):
 *   GET    /v1/app/modes            → { modes: AgentMode[], activeMode: string|null }
 *   PUT    /v1/app/modes/{id}       → { mode, modes, activeMode }   (400 on invalid)
 *   DELETE /v1/app/modes/{id}       → { modes, activeMode }
 *   POST   /v1/app/modes/active     → { activeMode }                (404 on unknown)
 */

import type { AgentMode } from "@/chat-core";

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

export interface ModesListResponse {
  modes: AgentMode[];
  activeMode: string | null;
}

/** Body accepted by PUT. The path `{id}` is authoritative for the mode id, so
 * this intentionally omits `id` — the caller passes it as the path segment. */
export interface AgentModeInput {
  displayName: string;
  systemPrompt?: string;
  toolDelta?: { exclude?: string[]; include?: string[] };
  scopedPolicyIds?: string[];
  permissionMode?: string | null;
}

async function parseJson<T>(res: Response): Promise<T> {
  return (await res.json()) as T;
}

export async function getModes(fetch: Fetcher): Promise<ModesListResponse> {
  const res = await fetch("/v1/app/modes");
  if (!res.ok) throw new Error(`Failed to load modes (${res.status})`);
  return parseJson<ModesListResponse>(res);
}

export async function putMode(
  fetch: Fetcher,
  modeId: string,
  input: AgentModeInput,
): Promise<{ mode: AgentMode; modes: AgentMode[]; activeMode: string | null }> {
  const res = await fetch(`/v1/app/modes/${encodeURIComponent(modeId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    // The backend returns { error: "invalid_mode" } on validation failure.
    const body = await res.json().catch(() => ({}));
    const reason = (body as { error?: string }).error ?? `http_${res.status}`;
    throw new Error(`Failed to save mode: ${reason}`);
  }
  return parseJson(res);
}

export async function deleteMode(
  fetch: Fetcher,
  modeId: string,
): Promise<ModesListResponse> {
  const res = await fetch(`/v1/app/modes/${encodeURIComponent(modeId)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete mode (${res.status})`);
  return parseJson<ModesListResponse>(res);
}

export async function setActiveMode(
  fetch: Fetcher,
  modeId: string | null,
): Promise<{ activeMode: string | null }> {
  const res = await fetch("/v1/app/modes/active", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ modeId }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const reason = (body as { error?: string }).error ?? `http_${res.status}`;
    throw new Error(`Failed to set active mode: ${reason}`);
  }
  return parseJson(res);
}
