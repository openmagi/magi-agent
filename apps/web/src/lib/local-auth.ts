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

/**
 * First-run setup signal emitted by the backend at `/app/bootstrap.json`.
 *
 * Additive (PR1.1): older runtimes omit it, in which case the onboarding wizard
 * treats setup as not needed. `needed` is true only when the wizard flag is ON
 * and no provider is configured.
 */
export interface LocalBootstrapSetup {
  needed: boolean;
  hasProvider: boolean;
  providers: string[];
}

/**
 * Default-OFF feature toggles the dashboard reads to hide gated surfaces
 * (e.g. the chat Audit tab) when their backend flag is off. Older runtimes
 * omit `features`, in which case every gated surface stays hidden.
 */
export interface LocalBootstrapFeatures {
  auditPanel?: boolean;
}

export interface LocalBootstrap {
  ok?: boolean;
  agentUrl?: string;
  tokenRequired?: boolean;
  token?: string;
  setup?: LocalBootstrapSetup;
  features?: LocalBootstrapFeatures;
}

let bootstrapPromise: Promise<LocalBootstrap | null> | null = null;

export async function loadLocalBootstrap(): Promise<LocalBootstrap | null> {
  if (!bootstrapPromise) {
    bootstrapPromise = fetch("/app/bootstrap.json", { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) return null;
        return (await res.json()) as LocalBootstrap;
      })
      .catch(() => null);
  }
  return bootstrapPromise;
}

/** Always returns a local placeholder user. */
export function useLocalUser(): { user: LocalUser; ready: boolean } {
  return { user: { id: "local" }, ready: true };
}

/** Privy-compatible local auth shape used by reused cloud dashboard components. */
export function useLocalPrivy(): {
  user: LocalUser;
  ready: boolean;
  authenticated: boolean;
  getAccessToken: () => Promise<string | null>;
  logout: () => Promise<void>;
} {
  return {
    user: { id: "local" },
    ready: true,
    authenticated: true,
    getAccessToken: getLocalAccessToken,
    logout: async () => {},
  };
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
  const envToken = process.env.NEXT_PUBLIC_AGENT_TOKEN;
  if (envToken) return envToken;
  const bootstrap = await loadLocalBootstrap();
  return bootstrap?.token ?? null;
}

export async function getLocalAgentBaseUrl(): Promise<string> {
  const envUrl = process.env.NEXT_PUBLIC_AGENT_URL;
  if (envUrl) return envUrl.replace(/\/+$/, "");
  if (typeof window === "undefined") return "";
  const bootstrap = await loadLocalBootstrap();
  return (bootstrap?.agentUrl ?? "").replace(/\/+$/, "");
}

export function resetLocalBootstrapCacheForTests(): void {
  bootstrapPromise = null;
}
