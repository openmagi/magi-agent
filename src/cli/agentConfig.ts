import path from "node:path";
import type { AgentConfig } from "../Agent.js";
import { registerConfiguredModelCapability } from "../config/registerConfiguredModelCapability.js";
import { createProvider } from "../llm/createProvider.js";
import type { MagiAgentConfig } from "./config.js";

const DEFAULT_MODELS: Record<string, string> = {
  anthropic: "claude-sonnet-4-6",
  openai: "gpt-5.4",
  google: "gemini-2.5-flash",
  "openai-compatible": "llama3.1",
};

export function cleanToken(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : undefined;
}

export function buildCliAgentConfig(
  config: MagiAgentConfig,
  opts: {
    botId: string;
    userId?: string;
  },
): AgentConfig {
  const workspace = config.workspace
    ? path.resolve(config.workspace)
    : path.resolve("./workspace");

  const model =
    config.llm.model ??
    DEFAULT_MODELS[config.llm.provider] ??
    "claude-sonnet-4-6";
  registerConfiguredModelCapability(model, config.llm.capabilities);

  const provider = createProvider({
    provider: config.llm.provider,
    apiKey: config.llm.apiKey,
    baseUrl: config.llm.baseUrl,
    defaultModel: model,
  });

  const agentGatewayToken =
    cleanToken(config.server?.gatewayToken) ??
    cleanToken(process.env.MAGI_AGENT_SERVER_TOKEN) ??
    cleanToken(config.llm.apiKey) ??
    "local-dev";

  return {
    botId: opts.botId,
    userId: opts.userId ?? "cli-user",
    workspaceRoot: workspace,
    gatewayToken: agentGatewayToken,
    apiProxyUrl: config.llm.baseUrl ?? "https://api.anthropic.com",
    model,
    llmProvider: provider,
    agentName: config.identity?.name,
    agentInstructions: config.identity?.instructions,
    telegramBotToken: config.channels?.telegram?.token,
    discordBotToken: config.channels?.discord?.token,
  };
}
