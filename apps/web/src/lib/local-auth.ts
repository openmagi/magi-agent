/**
 * Stub auth utilities for OSS magi-agent.
 *
 * The cloud product uses Privy for auth. OSS runs locally with no
 * authentication requirement, so this module exports no-op stubs
 * that satisfy the type contracts used by shared components.
 */

/** Stub user object. */
export interface LocalUser {
  id: string;
}

/** Always returns a local placeholder user. */
export function useLocalUser(): { user: LocalUser; ready: boolean } {
  return { user: { id: "local" }, ready: true };
}

/** No-op — OSS has no login flow. */
export function useLocalLogin(): { login: () => void; logout: () => void } {
  return {
    login: () => {},
    logout: () => {},
  };
}

/** Stub getAccessToken for components that pass it as a prop. */
export async function getLocalAccessToken(): Promise<string | null> {
  return null;
}
