/**
 * Server-side config for OSS magi-agent.
 * No Stripe/Privy/Supabase env vars required.
 */

import { z } from "zod";

const serverEnvSchema = z.object({
  // Agent runtime
  NEXT_PUBLIC_AGENT_URL: z.string().optional(),
  NEXT_PUBLIC_AGENT_TOKEN: z.string().optional(),

  // Anthropic (for AI features like NL hook config)
  ANTHROPIC_API_KEY: z.string().min(1).optional(),
});

type ServerEnv = z.infer<typeof serverEnvSchema>;

export const env: ServerEnv = new Proxy({} as ServerEnv, {
  get(_target, prop: string) {
    return process.env[prop];
  },
});
