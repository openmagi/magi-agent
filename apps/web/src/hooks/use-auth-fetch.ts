export function useAuthFetch(): typeof fetch {
  return ((input: RequestInfo | URL, init: RequestInit = {}) => {
    const token = window.localStorage.getItem("magi.agent.app.token");
    const headers = new Headers(init.headers);
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    return fetch(input, { ...init, headers });
  }) as typeof fetch;
}
