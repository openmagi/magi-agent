// Stub auth for local mode - no login required
export function getLocalAuth(): { userId: string; isAuthenticated: boolean } {
  return { userId: "local", isAuthenticated: true };
}

export function useLocalAuth(): { userId: string; isAuthenticated: boolean; loading: false } {
  return { userId: "local", isAuthenticated: true, loading: false };
}
