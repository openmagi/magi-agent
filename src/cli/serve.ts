/**
 * `clawy-agent serve` — HTTP API server mode.
 *
 * Loads clawy-agent.yaml, creates an Agent + HttpServer, and starts
 * serving on the specified port. This is the config-file-driven
 * equivalent of the env-var-based `src/index.ts` entrypoint.
 */

import path from "node:path";
import { loadConfig } from "./config.js";
import { Agent, type AgentConfig } from "../Agent.js";
import { HttpServer } from "../transport/HttpServer.js";
import { createProvider } from "../llm/createProvider.js";

const DIM = "\x1b[2m";
const BOLD = "\x1b[1m";
const RESET = "\x1b[0m";
const GREEN = "\x1b[32m";

const DEFAULT_PORT = 8080;

const DEFAULT_MODELS: Record<string, string> = {
  anthropic: "claude-sonnet-4-6",
  openai: "gpt-5.4",
  google: "gemini-2.5-flash",
};

function buildAgentConfig(
  config: ReturnType<typeof loadConfig>,
): AgentConfig {
  const workspace = config.workspace
    ? path.resolve(config.workspace)
    : path.resolve("./workspace");

  const model = config.llm.model ?? DEFAULT_MODELS[config.llm.provider] ?? "claude-sonnet-4-6";

  const provider = createProvider({
    provider: config.llm.provider,
    apiKey: config.llm.apiKey,
    baseUrl: config.llm.baseUrl,
    defaultModel: model,
  });

  return {
    botId: "cli-serve",
    userId: "cli-user",
    workspaceRoot: workspace,
    gatewayToken: config.llm.apiKey,
    apiProxyUrl: config.llm.baseUrl ?? "https://api.anthropic.com",
    model,
    llmProvider: provider,
    agentName: config.identity?.name,
    agentInstructions: config.identity?.instructions,
    telegramBotToken: config.channels?.telegram?.token,
    discordBotToken: config.channels?.discord?.token,
  };
}

export async function runServe(port?: number): Promise<void> {
  let config;
  try {
    config = loadConfig();
  } catch (err) {
    console.error(`${(err as Error).message}`);
    process.exit(1);
  }

  const listenPort = port ?? DEFAULT_PORT;
  const agentConfig = buildAgentConfig(config);
  const agentName = config.identity?.name ?? "Clawy Agent";

  const agent = new Agent(agentConfig);
  try {
    await agent.start();
  } catch (err) {
    console.error(`Failed to start agent: ${(err as Error).message}`);
    process.exit(1);
  }

  const http = new HttpServer({
    port: listenPort,
    agent,
    bearerToken: agentConfig.gatewayToken || undefined,
  });

  try {
    await http.start();
  } catch (err) {
    console.error(`Failed to start HTTP server: ${(err as Error).message}`);
    process.exit(1);
  }

  console.log("");
  console.log(`${BOLD}${agentName}${RESET} ${DIM}server mode${RESET}`);
  console.log(`${GREEN}Ready${RESET} on http://localhost:${listenPort}`);
  console.log(`${DIM}Model: ${config.llm.provider}/${agentConfig.model}${RESET}`);
  console.log(`${DIM}Workspace: ${agentConfig.workspaceRoot}${RESET}`);
  console.log("");

  const shutdown = async (signal: string): Promise<void> => {
    console.log(`\n${DIM}${signal} received, shutting down...${RESET}`);
    try {
      await http.stop();
      await agent.stop();
      process.exit(0);
    } catch (err) {
      console.error(`Shutdown error: ${(err as Error).message}`);
      process.exit(1);
    }
  };

  process.on("SIGTERM", () => void shutdown("SIGTERM"));
  process.on("SIGINT", () => void shutdown("SIGINT"));
}
