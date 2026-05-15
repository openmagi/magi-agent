/**
 * Local agent API client for OSS magi-agent.
 *
 * All dashboard components use `agentFetch` instead of cloud-specific
 * `useAuthFetch`. The base URL and optional token are resolved from
 * environment variables at build time.
 */

const AGENT_BASE_URL =
  process.env.NEXT_PUBLIC_AGENT_URL ?? "http://localhost:3001";
const AGENT_TOKEN = process.env.NEXT_PUBLIC_AGENT_TOKEN ?? "";

/**
 * Fetch wrapper that prepends the agent base URL and attaches the
 * bearer token when available. Drop-in replacement for the cloud
 * `useAuthFetch()` return value.
 */
export async function agentFetch(
  path: string,
  options?: RequestInit,
): Promise<Response> {
  const url = path.startsWith("http") ? path : `${AGENT_BASE_URL}${path}`;
  const headers: Record<string, string> = {
    ...(options?.headers as Record<string, string>),
  };
  if (AGENT_TOKEN) {
    headers.Authorization = `Bearer ${AGENT_TOKEN}`;
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
