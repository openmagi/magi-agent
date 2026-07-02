/**
 * Prebuilt (always-on) components client (PR-P4): thin wrapper over
 * `GET /v1/app/prebuilt-components`. Read-only: lists kernel-enforced behaviors
 * (read-before-write, path safety, receipts, ...) that gate every turn but had
 * no dashboard surface. Descriptive only; not togglable.
 */

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

export interface PrebuiltComponent {
  key: string;
  name: string;
  description: string;
  where: string;
  alwaysOn: boolean;
}

export interface PrebuiltComponentsResponse {
  components: PrebuiltComponent[];
}

export async function getPrebuiltComponents(
  fetch: Fetcher,
): Promise<PrebuiltComponentsResponse> {
  const res = await fetch("/v1/app/prebuilt-components");
  if (!res.ok) throw new Error(`Failed to load prebuilt components (${res.status})`);
  return (await res.json()) as PrebuiltComponentsResponse;
}
