/**
 * magi-agent entrypoint.
 *
 * Status: Phase 0 — boots, serves /health, returns 501 elsewhere.
 *
 * Dual-mode:
 *   - Magi Cloud: BOT_ID env present → startFromEnv() (backward compat)
 *   - OSS:       CLI passes parsed config → startFromConfig()
 */

import { Agent } from "./Agent.js";
import { HttpServer } from "./transport/HttpServer.js";
import { bootstrapCoreAgent } from "./bootstrap.js";
import {
  loadRuntimeEnv,
  loadFromConfig,
  type MagiAgentConfig,
  type RuntimeEnv,
} from "./config/RuntimeEnv.js";

// ── Re-exports for programmatic use ─────────────────────────────
export { Agent } from "./Agent.js";
export type { AgentConfig } from "./Agent.js";
export { Session } from "./Session.js";
export type { SessionMeta } from "./Session.js";
export { loadRuntimeEnv, loadFromConfig } from "./config/RuntimeEnv.js";
export type { RuntimeEnv, MagiAgentConfig } from "./config/RuntimeEnv.js";

// ── Shared boot logic ───────────────────────────────────────────

async function boot(env: RuntimeEnv): Promise<void> {
  const agent = new Agent(env.agentConfig);
  const http = new HttpServer({
    port: env.port,
    agent,
    bearerToken: env.agentConfig.gatewayToken || undefined,
  });
  await bootstrapCoreAgent({ agent, http });

  console.log(
    `[magi-agent] botId=${env.agentConfig.botId} port=${env.port} phase=0 ready`,
  );

  const shutdown = async (signal: NodeJS.Signals): Promise<void> => {
    console.log(`[magi-agent] ${signal} received, shutting down`);
    try {
      await http.stop();
      await agent.stop();
      process.exit(0);
    } catch (err) {
      console.error("[magi-agent] shutdown error", err);
      process.exit(1);
    }
  };

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
}

// ── Public start functions ──────────────────────────────────────

/** Start from environment variables (Magi Cloud / K8s pod mode). */
export async function startFromEnv(): Promise<void> {
  const env = loadRuntimeEnv();
  await boot(env);
}

/** Start from a parsed YAML config object (OSS / CLI mode). */
export async function startFromConfig(config: MagiAgentConfig): Promise<void> {
  const env = loadFromConfig(config);
  await boot(env);
}

// ── Auto-start when BOT_ID is set (backward compat) ────────────
if (process.env.BOT_ID) {
  startFromEnv().catch((err) => {
    console.error("[magi-agent] fatal startup error", err);
    process.exit(1);
  });
}
