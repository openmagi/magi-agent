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
import { renderCliHelp, renderCliWelcome, renderPrompt } from "./terminalUi.js";
import { TerminalSseWriter } from "./terminalWriter.js";

// ANSI helpers
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";
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
  const sessionKey = "agent:local:cli:interactive";

  console.log(
    renderCliWelcome({
      agentName,
      provider: config.llm.provider,
      model: agentConfig.model,
      workspaceRoot: agentConfig.workspaceRoot,
      sessionKey,
    }),
  );

  const agent = new Agent(agentConfig);
  try {
    await agent.start();
  } catch (err) {
    console.error(`Failed to start agent: ${(err as Error).message}`);
    process.exit(1);
  }

  const channelRef: ChannelRef = { type: "app", channelId: "cli" };
  const session = await agent.getOrCreateSession(sessionKey, channelRef);

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: renderPrompt(),
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
    if (text === "/help") {
      console.log(renderCliHelp());
      rl.prompt();
      return;
    }
    if (text === "/clear") {
      console.clear();
      console.log(
        renderCliWelcome({
          agentName,
          provider: config.llm.provider,
          model: agentConfig.model,
          workspaceRoot: agentConfig.workspaceRoot,
          sessionKey,
        }),
      );
      rl.prompt();
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
