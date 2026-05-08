/**
 * `magi-agent run` — one-shot terminal mode.
 *
 * Runs a single prompt through the local runtime and prints streamed output
 * to stdout. If no argv prompt is supplied, piped stdin is used.
 */

import { Agent } from "../Agent.js";
import type { SseWriter } from "../transport/SseWriter.js";
import type { ChannelRef, UserMessage } from "../util/types.js";
import { buildCliAgentConfig } from "./agentConfig.js";
import { loadConfig } from "./config.js";
import { TerminalSseWriter } from "./terminalWriter.js";

const YELLOW = "\x1b[33m";
const RESET = "\x1b[0m";

export interface RunOneShotOptions {
  prompt?: string;
  sessionKey?: string;
  model?: string;
  planMode?: boolean;
}

export function resolveRunPrompt(
  argvPrompt: string | undefined,
  stdinText: string,
): string {
  const direct = argvPrompt?.trim();
  if (direct) return direct;

  const piped = stdinText.trim();
  if (piped) return piped;

  throw new Error(
    'No prompt supplied. Use `magi-agent run "your task"` or pipe text into `magi-agent run`.',
  );
}

async function readStdinIfPiped(): Promise<string> {
  if (process.stdin.isTTY) return "";

  let input = "";
  for await (const chunk of process.stdin) {
    input += typeof chunk === "string" ? chunk : Buffer.from(chunk).toString("utf8");
  }
  return input;
}

function normalizeSessionKey(value: string | undefined): string {
  const trimmed = value?.trim();
  if (!trimmed) return "agent:local:cli:default";
  if (trimmed.startsWith("agent:")) return trimmed;
  return `agent:local:cli:${trimmed}`;
}

export async function runOneShot(options: RunOneShotOptions): Promise<void> {
  let config;
  try {
    config = loadConfig();
  } catch (err) {
    console.error(`${(err as Error).message}`);
    process.exit(1);
  }

  let prompt: string;
  try {
    prompt = resolveRunPrompt(options.prompt, await readStdinIfPiped());
  } catch (err) {
    console.error((err as Error).message);
    process.exit(1);
  }

  const agent = new Agent(
    buildCliAgentConfig(config, {
      botId: "cli-run",
      userId: "cli-user",
    }),
  );

  try {
    await agent.start();
  } catch (err) {
    console.error(`Failed to start agent: ${(err as Error).message}`);
    process.exit(1);
  }

  const channelRef: ChannelRef = { type: "app", channelId: "cli" };
  const session = await agent.getOrCreateSession(
    normalizeSessionKey(options.sessionKey),
    channelRef,
  );
  const writer = new TerminalSseWriter();
  const userMessage: UserMessage = {
    text: prompt,
    receivedAt: Date.now(),
  };

  try {
    writer.start();
    await session.runTurn(userMessage, writer as unknown as SseWriter, {
      ...(options.planMode ? { planMode: true } : {}),
      ...(options.model ? { runtimeModelOverride: options.model } : {}),
    });
    writer.end();
  } catch (err) {
    writer.end();
    console.error(`${YELLOW}Turn failed: ${(err as Error).message}${RESET}`);
    process.exitCode = 1;
  } finally {
    await agent.stop().catch(() => {});
  }
}
