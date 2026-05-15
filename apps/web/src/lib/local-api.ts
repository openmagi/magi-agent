// Local agent API client
const DEFAULT_AGENT_URL = typeof window !== "undefined"
  ? window.localStorage.getItem("magi:agentUrl") || window.location.origin
  : "http://localhost:8080";

export function getAgentUrl(): string {
  return typeof window !== "undefined"
    ? window.localStorage.getItem("magi:agentUrl") || window.location.origin
    : DEFAULT_AGENT_URL;
}

export function getToken(): string {
  return typeof window !== "undefined"
    ? window.localStorage.getItem("magi:token") || ""
    : "";
}

export async function agentFetch(path: string, init?: RequestInit): Promise<Response> {
  const url = `${getAgentUrl()}${path}`;
  const token = getToken();
  return fetch(url, {
    ...init,
    headers: {
      ...init?.headers,
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
}
