/**
 * `magi-agent start` — interactive terminal mode.
 *
 * Loads magi-agent.yaml, creates an Agent + Session, then enters a
 * readline loop: user types a message, the agent streams its response
 * to stdout, repeat. Ctrl+C exits gracefully.
 */

import readline from "node:readline";
import path from "node:path";
import { loadConfig } from "./config.js";
import { Agent, type AgentConfig } from "../Agent.js";
import { Session } from "../Session.js";
import type { AgentEvent, SseWriter } from "../transport/SseWriter.js";
import type { UserMessage, ChannelRef } from "../util/types.js";
import { createProvider } from "../llm/createProvider.js";
import { registerConfiguredModelCapability } from "../config/registerConfiguredModelCapability.js";

// ANSI helpers
const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";

class TerminalSseWriter {
  private ended = false;
  private inThinking = false;

  start(): void {}

  agent(event: AgentEvent): void {
    if (this.ended) return;

    switch (event.type) {
      case "text_delta":
        if (this.inThinking) {
          process.stdout.write(`${RESET}\n`);
          this.inThinking = false;
        }
        process.stdout.write(event.delta);
        break;

      case "thinking_delta":
        if (!this.inThinking) {
          process.stdout.write(`${DIM}`);
          this.inThinking = true;
        }
        process.stdout.write(event.delta);
        break;

      case "tool_start":
        if (this.inThinking) {
          process.stdout.write(`${RESET}\n`);
          this.inThinking = false;
        }
        process.stdout.write(
          `${DIM}[tool] ${event.name}${event.input_preview ? ` ${event.input_preview}` : ""}${RESET}\n`,
        );
        break;

      case "tool_end":
        process.stdout.write(
          `${DIM}[tool] ${event.id} ${event.status} (${event.durationMs}ms)${RESET}\n`,
        );
        break;

      case "error":
        process.stdout.write(
          `\n${YELLOW}Error [${event.code}]: ${event.message}${RESET}\n`,
        );
        break;

      case "turn_end":
        if (this.inThinking) {
          process.stdout.write(`${RESET}`);
          this.inThinking = false;
        }
        break;

      default:
        break;
    }
  }

  legacyDelta(_content: string): void {}

  legacyFinish(): void {}

  end(): void {
    if (this.ended) return;
    this.ended = true;
    if (this.inThinking) {
      process.stdout.write(`${RESET}`);
      this.inThinking = false;
    }
    process.stdout.write("\n");
  }
}

const DEFAULT_MODELS: Record<string, string> = {
  anthropic: "claude-sonnet-4-6",
  openai: "gpt-5.4",
  google: "gemini-2.5-flash",
  "openai-compatible": "llama3.1",
};

function cleanToken(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : undefined;
}

function buildAgentConfig(
  config: ReturnType<typeof loadConfig>,
): AgentConfig {
  const workspace = config.workspace
    ? path.resolve(config.workspace)
    : path.resolve("./workspace");

  const model = config.llm.model ?? DEFAULT_MODELS[config.llm.provider] ?? "claude-sonnet-4-6";
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
    botId: "cli",
    userId: "cli-user",
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

export async function runStart(): Promise<void> {
  let config;
  try {
    config = loadConfig();
  } catch (err) {
    console.error(`${(err as Error).message}`);
    process.exit(1);
  }

  const agentName = config.identity?.name ?? "Magi";
  const agentConfig = buildAgentConfig(config);

  console.log("");
  console.log(`${BOLD}${agentName}${RESET}`);
  console.log(`${DIM}Model: ${config.llm.provider}/${agentConfig.model}${RESET}`);
  console.log(`${DIM}Workspace: ${agentConfig.workspaceRoot}${RESET}`);
  console.log(`${DIM}Type your message and press Enter. Ctrl+C to exit.${RESET}`);
  console.log("");

  const agent = new Agent(agentConfig);
  try {
    await agent.start();
  } catch (err) {
    console.error(`Failed to start agent: ${(err as Error).message}`);
    process.exit(1);
  }

  const channelRef: ChannelRef = { type: "app", channelId: "cli" };
  const session = await agent.getOrCreateSession("cli:interactive", channelRef);

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: `${GREEN}>${RESET} `,
  });

  let shuttingDown = false;
  const shutdown = async (): Promise<void> => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`\n${DIM}Shutting down...${RESET}`);
    rl.close();
    try {
      await agent.stop();
    } catch {
      // swallow
    }
    process.exit(0);
  };

  process.on("SIGINT", () => void shutdown());
  process.on("SIGTERM", () => void shutdown());

  rl.prompt();

  rl.on("line", async (line) => {
    const text = line.trim();
    if (!text) {
      rl.prompt();
      return;
    }

    if (text === "/exit" || text === "/quit") {
      await shutdown();
      return;
    }

    const userMessage: UserMessage = {
      text,
      receivedAt: Date.now(),
    };

    const writer = new TerminalSseWriter();

    try {
      writer.start();
      console.log("");
      await session.runTurn(
        userMessage,
        writer as unknown as SseWriter,
      );
      writer.end();
    } catch (err) {
      writer.end();
      console.error(
        `${YELLOW}Turn failed: ${(err as Error).message}${RESET}`,
      );
    }

    console.log("");
    rl.prompt();
  });

  rl.on("close", () => {
    if (!shuttingDown) {
      void shutdown();
    }
  });
}
