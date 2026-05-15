import { z } from "zod";

// ── Server-side environment variables ────────────────────────────────────────
// Validated lazily on first access. Only import this module from server code
// (API routes, server actions, server components).
//
// In development/build, missing vars log a warning but don't crash.
// At runtime in production, missing required vars throw at first access.

const serverEnvSchema = z.object({
  // Stripe
  STRIPE_SECRET_KEY: z.string().startsWith("sk_"),
  STRIPE_WEBHOOK_SECRET: z.string().startsWith("whsec_"),
  STRIPE_PRO_PRICE_ID: z.string().startsWith("price_"),
  STRIPE_PRO_PLUS_PRICE_ID: z.string().startsWith("price_"),
  STRIPE_BYOK_PRICE_ID: z.string().startsWith("price_"),
  STRIPE_PRO_YEARLY_PRICE_ID: z.string().startsWith("price_").optional(),
  STRIPE_PRO_PLUS_YEARLY_PRICE_ID: z.string().startsWith("price_").optional(),
  // max + flex plans added after initial schema; optional so existing
  // deploys without these keys keep booting.
  STRIPE_MAX_PRICE_ID: z.string().startsWith("price_").optional(),
  STRIPE_FLEX_PRICE_ID: z.string().startsWith("price_").optional(),
  STRIPE_MAX_YEARLY_PRICE_ID: z.string().startsWith("price_").optional(),
  STRIPE_FLEX_YEARLY_PRICE_ID: z.string().startsWith("price_").optional(),

  // Supabase
  NEXT_PUBLIC_SUPABASE_URL: z.string().url(),
  SUPABASE_SERVICE_ROLE_KEY: z.string().min(1),
  // Browser-safe anon key — used by push_messages Realtime subscription.
  // Reads are gated by RLS on push_messages (user can only SELECT their
  // own rows, matched by auth.jwt() ->> 'sub'). Optional so existing
  // deploys without Phase 1 push messaging keep booting.
  NEXT_PUBLIC_SUPABASE_ANON_KEY: z.string().min(1).optional(),

  // Privy
  NEXT_PUBLIC_PRIVY_APP_ID: z.string().min(1),
  PRIVY_APP_SECRET: z.string().min(1),
  PRIVY_AUTHORIZATION_KEY_ID: z.string().min(1).optional(),
  PRIVY_AUTHORIZATION_KEY_PRIVATE: z.string().min(1).optional(),

  // Encryption (64 hex chars = 32 bytes AES-256 key)
  ENCRYPTION_KEY: z.string().length(64),

  // Internal services
  INTERNAL_SERVICE_TOKEN: z.string().min(1).optional(),
  CRON_SECRET: z.string().min(1).optional(),

  // Admin
  ADMIN_USER_IDS: z.string().optional(),

  // Blockchain / payouts
  PAYOUT_WALLET_PRIVATE_KEY: z.string().optional(),
  BASE_RPC_URL: z.string().url().optional(),

  // Container images (dev provisioning)
  GATEWAY_IMAGE: z.string().optional(),
  NODE_HOST_IMAGE: z.string().optional(),
  ROUTER_IMAGE: z.string().optional(),
  GHCR_USERNAME: z.string().optional(),
  GHCR_TOKEN: z.string().optional(),
  KUBECONFIG_CONTENT: z.string().optional(),

  // Sentry
  NEXT_PUBLIC_SENTRY_DSN: z.string().url().optional(),
  SENTRY_AUTH_TOKEN: z.string().optional(),

  // PostHog
  NEXT_PUBLIC_POSTHOG_KEY: z.string().optional(),
  NEXT_PUBLIC_POSTHOG_HOST: z.string().url().optional(),

  // AgentMail
  AGENTMAIL_API_KEY: z.string().min(1).optional(),

  // Anthropic (for AI policy generation)
  ANTHROPIC_API_KEY: z.string().min(1).optional(),
});

type ServerEnv = z.infer<typeof serverEnvSchema>;

let _validated = false;

/** Convert empty strings to undefined so zod's .optional() works with .env files.
 *  Also strips surrounding quotes and whitespace that can sneak in from env var UIs. */
function cleanEnv(): Record<string, string | undefined> {
  const cleaned: Record<string, string | undefined> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value === "" || value === undefined) {
      cleaned[key] = undefined;
    } else {
      // Strip surrounding quotes and whitespace
      cleaned[key] = value.replace(/^["'\s]+|["'\s]+$/g, "");
    }
  }
  return cleaned;
}

function validateOnce(): void {
  if (_validated) return;
  _validated = true;

  const result = serverEnvSchema.safeParse(cleanEnv());
  if (!result.success) {
    const missing = result.error.issues.map(
      (i) => `  ${i.path.join(".")}: ${i.message}`
    );
    const msg = `[config] Missing or invalid environment variables:\n${missing.join("\n")}`;

    // During next build (NEXT_PHASE=phase-production-build), warn only.
    // At runtime in production, throw to fail fast on misconfiguration.
    const isBuildPhase = process.env.NEXT_PHASE === "phase-production-build";
    if (process.env.NODE_ENV === "production" && !isBuildPhase) {
      console.error(msg);
      throw new Error(msg);
    }
    // In development / build: warn only
    console.warn(msg);
  }
}

// Lazy proxy — validation runs on first property access, not at import time.
// Reads from process.env directly so missing optional vars don't throw.
export const env: ServerEnv = new Proxy({} as ServerEnv, {
  get(_target, prop: string) {
    validateOnce();
    return process.env[prop];
  },
});
