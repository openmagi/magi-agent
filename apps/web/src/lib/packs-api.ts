/**
 * Installed-pack inventory client (PR-P3): thin wrapper over
 * `GET /v1/app/packs`. Read-only: lists installed packs (first-party + user)
 * and what each contributes (`provides`), so the Packs tab can show pack
 * CONTENTS instead of just a pack id. Served by the local runtime
 * (`magi_agent/transport/customize.py`); reached via `agentFetch`.
 */

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

/** One ref a pack contributes. `type` is a ProvidesType (tool / validator /
 * harness / control_plane / evidence_producer / recipe / connector / role /
 * loop_policy / schedule_policy / memory_strategy / callback). */
export interface PackProvide {
  type: string;
  ref: string;
}

export interface PackInfo {
  packId: string;
  displayName: string;
  description: string;
  version: string;
  origin: "first_party" | "user";
  defaultEnabled: boolean;
  enabled: boolean;
  provides: PackProvide[];
}

export interface PacksResponse {
  packs: PackInfo[];
}

export async function getPacks(fetch: Fetcher): Promise<PacksResponse> {
  const res = await fetch("/v1/app/packs");
  if (!res.ok) throw new Error(`Failed to load packs (${res.status})`);
  return (await res.json()) as PacksResponse;
}

/**
 * Install (`enabled=true`) or remove (`enabled=false`) a pack via
 * `POST /v1/app/packs/{id}/state`. The runtime persists a dashboard override
 * (never rewrites the operator's config.toml), so "Remove" is reversible:
 * installing again restores it, so first-party packs stay recoverable. Returns
 * the updated inventory. Throws on non-2xx so the caller can surface it.
 */
export async function setPackState(
  fetch: Fetcher,
  packId: string,
  enabled: boolean,
): Promise<PacksResponse> {
  const res = await fetch(`/v1/app/packs/${encodeURIComponent(packId)}/state`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error(`Failed to update pack (${res.status})`);
  return (await res.json()) as PacksResponse;
}
