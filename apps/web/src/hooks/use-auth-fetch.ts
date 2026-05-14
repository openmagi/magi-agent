export function useAuthFetch(): typeof fetch {
  return ((input: RequestInfo | URL, init: RequestInit = {}) => {
    const token = window.localStorage.getItem("magi.agent.app.token");
    const headers = new Headers(init.headers);
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    const target =
      typeof input === "string" && input.startsWith("/v1/")
        ? new URL(
            input,
            window.localStorage.getItem("magi.agent.app.agentUrl") || window.location.origin,
          ).toString()
        : input;
    return fetch(target, { ...init, headers });
  }) as typeof fetch;
}
