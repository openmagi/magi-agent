import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { ServerResponse } from "node:http";
import { Turn } from "./Turn.js";
import { Transcript } from "./storage/Transcript.js";
import { SseWriter } from "./transport/SseWriter.js";
import type { LLMEvent, LLMStreamRequest } from "./transport/LLMClient.js";
import type { UserMessage } from "./util/types.js";
import type { Session } from "./Session.js";
import type { AuditLog } from "./storage/AuditLog.js";
import { ControlEventLedger } from "./control/ControlEventLedger.js";

interface ScriptedTurn {
  blocks: Array<{ type: "text"; text: string }>;
  stopReason: "end_turn";
}

function* scriptedEvents(turn: ScriptedTurn): Generator<LLMEvent, void, void> {
  let idx = 0;
  for (const block of turn.blocks) {
    yield { kind: "text_delta", blockIndex: idx, delta: block.text };
    yield { kind: "block_stop", blockIndex: idx };
    idx += 1;
  }
  yield {
    kind: "message_end",
    stopReason: turn.stopReason,
    usage: { inputTokens: 5, outputTokens: 5 },
  };
}

class ScriptedLLM {
  readonly calls: LLMStreamRequest[] = [];
  constructor(private readonly script: ScriptedTurn[]) {}

  async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    this.calls.push(req);
    const next = this.script.shift();
    if (!next) throw new Error("ScriptedLLM exhausted");
    for (const evt of scriptedEvents(next)) yield evt;
  }
}

class FakeSse extends SseWriter {
  readonly agentEvents: unknown[] = [];
  constructor() {
    super({
      writeHead: () => {},
      write: () => true,
      end: () => {},
    } as unknown as ServerResponse);
  }
  override agent(event: unknown): void {
    this.agentEvents.push(event);
  }
  override legacyDelta(): void {}
  override legacyFinish(): void {}
  override start(): void {}
  override end(): void {}
}

async function makeFixture(opts: {
  model?: string;
  withIdentity?: boolean;
}): Promise<{
  turn: Turn;
  llm: ScriptedLLM;
  sse: FakeSse;
}> {
  const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "turn-cache-"));
  const sessionsDir = path.join(workspaceRoot, "core-agent", "sessions");
  await fs.mkdir(sessionsDir, { recursive: true });

  if (opts.withIdentity) {
    await fs.mkdir(path.join(workspaceRoot, "workspace"), { recursive: true });
    await fs.writeFile(
      path.join(workspaceRoot, "workspace", "soul.md"),
      "You are a helpful assistant.",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "workspace", "identity.md"),
      "Agent Kevin",
    );
  }

  const llm = new ScriptedLLM([
    { blocks: [{ type: "text", text: "Hello." }], stopReason: "end_turn" },
  ]);

  const transcript = new Transcript(sessionsDir, "agent:main:app:general:1");
  const controlEvents = new ControlEventLedger({
    rootDir: sessionsDir,
    sessionKey: "agent:main:app:general:1",
    transcript,
  });

  const hooks = {
    runPre: async (_point: string, args: unknown) => ({
      action: "continue" as const,
      args,
    }),
    runPost: async () => {},
    list: () => [],
  };

  const auditLog: Pick<AuditLog, "append"> = { append: async () => {} };
  const model = opts.model ?? "claude-opus-4-7";
  const agentConfig = {
    botId: "bot-cache",
    userId: "user-cache",
    workspaceRoot,
    gatewayToken: "test",
    apiProxyUrl: "http://localhost",
    chatProxyUrl: "http://localhost",
    redisUrl: "redis://localhost",
    model,
  };
  const agentStub = {
    config: agentConfig,
    resolveRuntimeModel: async () => model,
    hooks,
    tools: {
      list: () => [],
      resolve: () => undefined,
    },
    llm,
    workspace: { loadIdentity: async () => ({}) },
    auditLog,
    contextEngine: {
      maybeCompact: async () => null,
      buildMessagesFromTranscript: () => [],
    },
    intent: { classify: async () => ["general"] },
    sessionsDir,
  };

  const sessionStub = {
    meta: {
      sessionKey: "agent:main:app:general:1",
      botId: "bot-cache",
      channel: { type: "web" as const, channelId: "ch-1" },
      createdAt: Date.now(),
      lastActivityAt: Date.now(),
    },
    transcript,
    controlEvents,
    agent: agentStub,
    budgetExceeded: () => ({ exceeded: false as const }),
    budgetStats: () => ({ turns: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 }),
    maxTurns: 100,
    maxCostUsd: 10,
    setActiveSse: () => {},
    hasPendingInjections: () => false,
    getStructuredOutputContract: () => null,
  };

  const sse = new FakeSse();
  const userMessage: UserMessage = {
    text: "Hello",
    receivedAt: Date.now(),
  };

  const turn = new Turn(
    sessionStub as unknown as Session,
    userMessage,
    "turn-cache-1",
    sse,
    "direct",
  );
  return { turn, llm, sse };
}

afterEach(() => {
  delete process.env.MAGI_CACHE_PROMPT;
  delete process.env.MAGI_PRIORITY_CONTEXT;
});

describe("Cache-optimized prompt (MAGI_CACHE_PROMPT)", () => {
  it("sends system as string when MAGI_CACHE_PROMPT is off (default)", async () => {
    const { turn, llm } = await makeFixture({ model: "claude-opus-4-7" });
    await turn.execute();
    expect(llm.calls.length).toBeGreaterThanOrEqual(1);
    const call = llm.calls[0]!;
    expect(typeof call.system).toBe("string");
  });

  it("sends system as array with cache_control when MAGI_CACHE_PROMPT=1 and Anthropic model", async () => {
    process.env.MAGI_CACHE_PROMPT = "1";
    const { turn, llm } = await makeFixture({ model: "claude-opus-4-7" });
    await turn.execute();
    expect(llm.calls.length).toBeGreaterThanOrEqual(1);
    const call = llm.calls[0]!;
    expect(Array.isArray(call.system)).toBe(true);
    const blocks = call.system as Array<{
      type: string;
      text: string;
      cache_control?: { type: string };
    }>;
    expect(blocks.length).toBeGreaterThanOrEqual(1);
    for (const block of blocks) {
      expect(block.type).toBe("text");
      expect(typeof block.text).toBe("string");
    }
  });

  it("sends system as string when MAGI_CACHE_PROMPT=1 but model is non-Anthropic", async () => {
    process.env.MAGI_CACHE_PROMPT = "1";
    const { turn, llm } = await makeFixture({ model: "gpt-5.5" });
    await turn.execute();
    expect(llm.calls.length).toBeGreaterThanOrEqual(1);
    const call = llm.calls[0]!;
    expect(typeof call.system).toBe("string");
  });

  it("cache_control ephemeral is set on non-last blocks", async () => {
    process.env.MAGI_CACHE_PROMPT = "1";
    const { turn, llm } = await makeFixture({ model: "claude-sonnet-4-6" });
    await turn.execute();
    const call = llm.calls[0]!;
    expect(Array.isArray(call.system)).toBe(true);
    const blocks = call.system as Array<{
      type: string;
      text: string;
      cache_control?: { type: string };
    }>;
    if (blocks.length > 1) {
      for (let i = 0; i < blocks.length - 1; i++) {
        expect(blocks[i]!.cache_control).toEqual({ type: "ephemeral" });
      }
      expect(blocks[blocks.length - 1]!.cache_control).toBeUndefined();
    }
  });

  it("recognizes anthropic/ prefixed model names", async () => {
    process.env.MAGI_CACHE_PROMPT = "1";
    const { turn, llm } = await makeFixture({
      model: "anthropic/claude-opus-4-6",
    });
    await turn.execute();
    const call = llm.calls[0]!;
    expect(Array.isArray(call.system)).toBe(true);
  });
});
