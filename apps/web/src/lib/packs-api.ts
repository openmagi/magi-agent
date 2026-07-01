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
