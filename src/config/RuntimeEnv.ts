/**
 * Environment reading with validation. Centralised so no module reads
 * process.env ad-hoc (and so env-var collisions like the 2026-04-19
 * API_PROXY_PORT K8s-injection bug are caught at boot, not runtime).
 */

import type { AgentConfig } from "../Agent.js";
import type { PermissionMode } from "../Session.js";
import { createProvider } from "../llm/createProvider.js";
import { isRouterKeyword, type RoutingMode } from "../routing/types.js";

function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v || v.length === 0) {
    throw new Error(`Missing required env: ${name}`);
  }
  return v;
}

function optionalEnv(name: string): string | undefined {
  const v = process.env[name];
  return v && v.length > 0 ? v : undefined;
}

function parseIntSafe(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n) || n <= 0 || n >= 65536) {
    console.warn(
      `[env] ${name}=${JSON.stringify(raw)} invalid, using ${fallback}`,
    );
    return fallback;
  }
  return n;
}

function parsePermissionMode(raw: string | undefined): PermissionMode | undefined {
  if (
    raw === "default" ||
    raw === "plan" ||
    raw === "auto" ||
    raw === "bypass"
  ) {
    return raw;
  }
  return undefined;
}

function parseRoutingMode(model: string): RoutingMode {
  const explicit = optionalEnv("CORE_AGENT_ROUTING_MODE");
  if (explicit === "off" || explicit === "hosted-proxy" || explicit === "direct") {
    return explicit;
  }
  return isRouterKeyword(model) ? "hosted-proxy" : "off";
}

function directProvidersFromEnv(): AgentConfig["directProviders"] {
  return {
    ...(optionalEnv("ANTHROPIC_API_KEY")
      ? {
          anthropic: {
            kind: "anthropic" as const,
            baseUrl: optionalEnv("ANTHROPIC_BASE_URL") ?? "https://api.anthropic.com",
            apiKey: optionalEnv("ANTHROPIC_API_KEY") ?? "",
          },
        }
      : {}),
    ...(optionalEnv("OPENAI_API_KEY")
      ? {
          openai: {
            kind: "openai-compatible" as const,
            baseUrl: optionalEnv("OPENAI_BASE_URL") ?? "https://api.openai.com",
            apiKey: optionalEnv("OPENAI_API_KEY") ?? "",
          },
        }
      : {}),
    ...(optionalEnv("FIREWORKS_API_KEY")
      ? {
          fireworks: {
            kind: "openai-compatible" as const,
            baseUrl: optionalEnv("FIREWORKS_BASE_URL") ?? "https://api.fireworks.ai/inference",
            apiKey: optionalEnv("FIREWORKS_API_KEY") ?? "",
          },
        }
      : {}),
    ...(optionalEnv("GOOGLE_API_KEY")
      ? {
          google: {
            kind: "openai-compatible" as const,
            baseUrl:
              optionalEnv("GOOGLE_BASE_URL") ??
              "https://generativelanguage.googleapis.com/v1beta/openai",
            apiKey: optionalEnv("GOOGLE_API_KEY") ?? "",
          },
        }
      : {}),
  };
}

export interface RuntimeEnv {
  port: number;
  agentConfig: AgentConfig;
}

// ── OSS config-file types ───────────────────────────────────────

export interface ClawyAgentConfig {
  llm: {
    provider: "anthropic" | "openai" | "google";
    apiKey: string;
    model?: string;
    baseUrl?: string;
  };
  workspace?: string;
  identity?: {
    name?: string;
    instructions?: string;
  };
  channels?: {
    telegram?: { token: string };
    discord?: { token: string };
    webhook?: { url: string; secret?: string };
  };
  hooks?: {
    builtin?: Record<string, boolean>;
  };
  memory?: {
    enabled?: boolean;
    compaction?: boolean;
  };
}

const DEFAULT_MODELS: Record<string, string> = {
  anthropic: "claude-sonnet-4-6",
  openai: "gpt-5.4",
  google: "gemini-2.5-flash",
};

/**
 * Build a RuntimeEnv from a parsed YAML config (OSS / open-source mode).
 *
 * Creates an LLMProvider from the config and injects it into AgentConfig
 * so the Agent uses direct provider API calls instead of api-proxy.
 */
export function loadFromConfig(config: ClawyAgentConfig): RuntimeEnv {
  const model = config.llm.model ?? DEFAULT_MODELS[config.llm.provider] ?? "claude-sonnet-4-6";

  const provider = createProvider({
    provider: config.llm.provider,
    apiKey: config.llm.apiKey,
    baseUrl: config.llm.baseUrl,
    defaultModel: model,
  });

  const agentConfig: AgentConfig = {
    botId: "local",
    userId: "local",
    workspaceRoot: config.workspace ?? "./workspace",
    model,

    // For Anthropic direct mode, apiKey doubles as gatewayToken (same x-api-key header).
    // For non-Anthropic, these are unused because llmProvider handles routing.
    gatewayToken: config.llm.apiKey,
    apiProxyUrl: config.llm.baseUrl ?? "https://api.anthropic.com",

    // Multi-provider: inject the OSS provider so LLMClient delegates to it
    llmProvider: provider,

    // OSS identity
    agentName: config.identity?.name ?? "Clawy Agent",
    agentInstructions: config.identity?.instructions,

    // Channel tokens
    telegramBotToken: config.channels?.telegram?.token,
    discordBotToken: config.channels?.discord?.token,
  };

  return { port: 8080, agentConfig };
}

/** Load RuntimeEnv from environment variables (Clawy Pro mode). */
export function loadRuntimeEnv(): RuntimeEnv {
  const port = parseIntSafe("CORE_AGENT_PORT", 8080);
  const model = optionalEnv("CORE_AGENT_MODEL") ?? "claude-opus-4-7";
  const routingMode = parseRoutingMode(model);

  const agentConfig: AgentConfig = {
    botId: requireEnv("BOT_ID"),
    userId: requireEnv("USER_ID"),
    workspaceRoot:
      optionalEnv("CORE_AGENT_WORKSPACE") ?? "/home/ocuser/.clawy/workspace",
    gatewayToken: requireEnv("GATEWAY_TOKEN"),
    codexAccessToken: optionalEnv("CODEX_ACCESS_TOKEN"),
    codexRefreshToken: optionalEnv("CODEX_REFRESH_TOKEN"),
    apiProxyUrl: requireEnv("CORE_AGENT_API_PROXY_URL"),
    chatProxyUrl: optionalEnv("CORE_AGENT_CHAT_PROXY_URL"),
    redisUrl: optionalEnv("CORE_AGENT_REDIS_URL"),
    model,
    defaultPermissionMode:
      parsePermissionMode(optionalEnv("CORE_AGENT_PERMISSION_MODE")) ?? "bypass",
    routingMode,
    routingProfileId: optionalEnv("CORE_AGENT_ROUTING_PROFILE") ?? "standard",
    directProviders: routingMode === "direct" ? directProvidersFromEnv() : undefined,
    telegramBotToken: optionalEnv("TELEGRAM_BOT_TOKEN"),
    discordBotToken: optionalEnv("DISCORD_BOT_TOKEN"),
    webAppPushEndpointUrl: optionalEnv("WEBAPP_PUSH_URL"),
    webAppPushHmacKey: optionalEnv("WEBAPP_PUSH_HMAC_KEY"),
  };

  return { port, agentConfig };
}
