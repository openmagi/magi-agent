/**
 * Local agent API client for OSS magi-agent.
 *
 * All dashboard components use `agentFetch` instead of cloud-specific
 * `useAuthFetch`. In the packaged OSS app the browser and runtime are served
 * from the same origin; optional env overrides are still supported for custom
 * deployments.
 */

import { getLocalAccessToken, getLocalAgentBaseUrl } from "./local-auth";

function joinUrl(base: string, path: string): string {
  if (!base) return path;
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

/**
 * Fetch wrapper that targets the local runtime and attaches the loopback token
 * when available. Chat/control routes accept the bearer token; local admin
 * surfaces such as tools, plugins, and learning governance require the same
 * value as `x-gateway-token`.
 */
export async function agentFetch(
  path: string,
  options?: RequestInit,
): Promise<Response> {
  const url = path.startsWith("http")
    ? path
    : joinUrl(await getLocalAgentBaseUrl(), path);
  const headers = new Headers(options?.headers);
  const token = await getLocalAccessToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (token && !headers.has("x-gateway-token")) {
    headers.set("x-gateway-token", token);
  }
  return fetch(url, { ...options, headers });
}

/**
 * React hook that returns agentFetch — keeps the call-site signature
 * identical to the old `useAuthFetch()` hook so component changes are
 * minimal (import swap only).
 */
export function useAgentFetch(): typeof agentFetch {
  return agentFetch;
}
