/**
 * `magi-agent serve` — HTTP API server mode.
 *
 * Loads magi-agent.yaml, creates an Agent + HttpServer, and starts
 * serving on the specified port. This is the config-file-driven
 * equivalent of the env-var-based `src/index.ts` entrypoint.
 */

import { loadConfig } from "./config.js";
import { Agent, type AgentConfig } from "../Agent.js";
import { HttpServer } from "../transport/HttpServer.js";
import { buildCliAgentConfig, cleanToken } from "./agentConfig.js";

const DIM = "\x1b[2m";
const BOLD = "\x1b[1m";
const RESET = "\x1b[0m";
const GREEN = "\x1b[32m";

const DEFAULT_PORT = 8080;

export function resolveHttpBearerToken(
  config: ReturnType<typeof loadConfig>,
  agentConfig: AgentConfig,
): string | undefined {
  const hasExplicitServerToken =
    !!config.server &&
    Object.prototype.hasOwnProperty.call(config.server, "gatewayToken");
  const configuredServerToken = cleanToken(config.server?.gatewayToken);
  if (configuredServerToken) {
    return configuredServerToken;
  }
  if (hasExplicitServerToken) {
    throw new Error(
      "server.gatewayToken is configured but empty. Set MAGI_AGENT_SERVER_TOKEN or remove the server.gatewayToken field.",
    );
  }
  const envServerToken = cleanToken(process.env.MAGI_AGENT_SERVER_TOKEN);
  if (envServerToken) {
    return envServerToken;
  }
  if (config.llm.provider === "openai-compatible" && !cleanToken(config.llm.apiKey)) {
    return undefined;
  }
  return cleanToken(agentConfig.gatewayToken);
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
  const agentConfig = buildCliAgentConfig(config, { botId: "cli-serve" });
  let httpBearerToken: string | undefined;
  try {
    httpBearerToken = resolveHttpBearerToken(config, agentConfig);
  } catch (err) {
    console.error((err as Error).message);
    process.exit(1);
  }
  const agentName = config.identity?.name ?? "Magi";

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
    ...(httpBearerToken ? { bearerToken: httpBearerToken } : {}),
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
  console.log(`${DIM}App: http://localhost:${listenPort}/app${RESET}`);
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
