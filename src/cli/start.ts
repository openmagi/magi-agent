/**
 * `magi-agent start` — interactive terminal mode.
 *
 * Loads magi-agent.yaml, creates an Agent + Session, then enters a
 * readline loop: user types a message, the agent streams its response
 * to stdout, repeat. Ctrl+C exits gracefully.
 */

import readline from "node:readline";
import { loadConfig } from "./config.js";
import { Agent } from "../Agent.js";
import type { SseWriter } from "../transport/SseWriter.js";
import type { UserMessage, ChannelRef } from "../util/types.js";
import { buildCliAgentConfig } from "./agentConfig.js";
import { TerminalSseWriter } from "./terminalWriter.js";

// ANSI helpers
const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";

export async function runStart(): Promise<void> {
  let config;
  try {
    config = loadConfig();
  } catch (err) {
    console.error(`${(err as Error).message}`);
    process.exit(1);
  }

  const agentName = config.identity?.name ?? "Magi";
  const agentConfig = buildCliAgentConfig(config, { botId: "cli" });

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
