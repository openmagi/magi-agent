import { describe, expect, it } from "vitest";
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
import type { StructuredOutputSpec } from "./structured/StructuredOutputContract.js";

interface ScriptedTurn {
  blocks: Array<
    | { type: "text"; text: string }
    | { type: "tool_use"; id: string; name: string; input: unknown }
  >;
  stopReason: "end_turn" | "tool_use" | "max_tokens";
}

function* scriptedEvents(turn: ScriptedTurn): Generator<LLMEvent, void, void> {
  let idx = 0;
  for (const block of turn.blocks) {
    if (block.type === "text") {
      yield { kind: "text_delta", blockIndex: idx, delta: block.text };
    } else {
      yield { kind: "tool_use_start", blockIndex: idx, id: block.id, name: block.name };
      yield {
        kind: "tool_use_input_delta",
        blockIndex: idx,
        partial: JSON.stringify(block.input),
      };
    }
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
  finished = 0;
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
  override legacyFinish(): void {
    this.finished += 1;
  }
  override start(): void {}
  override end(): void {}
}

function visibleTextAfterLastClear(events: readonly unknown[]): string {
  let start = 0;
  events.forEach((event, index) => {
    if (
      event &&
      typeof event === "object" &&
      (event as { type?: unknown }).type === "response_clear"
    ) {
      start = index + 1;
    }
  });

  return events
    .slice(start)
    .filter(
      (event): event is { type: "text_delta"; delta: string } =>
        !!event &&
        typeof event === "object" &&
        (event as { type?: unknown }).type === "text_delta" &&
        typeof (event as { delta?: unknown }).delta === "string",
    )
    .map((event) => event.delta)
    .join("");
}

async function makeFixture(script: ScriptedTurn[] = [
  { blocks: [{ type: "text", text: "Unsupported claim." }], stopReason: "end_turn" },
  { blocks: [{ type: "text", text: "Unsupported claim again." }], stopReason: "end_turn" },
  { blocks: [{ type: "text", text: "Verified answer." }], stopReason: "end_turn" },
], opts: {
  structuredOutputContract?: StructuredOutputSpec;
  runtimeModel?: string;
  blockRetryPreLlm?: boolean;
  beforeCommitBlockReason?: string;
} = {}): Promise<{
  turn: Turn;
  llm: ScriptedLLM;
  transcript: Transcript;
  controlEvents: ControlEventLedger;
  sse: FakeSse;
}> {
  const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "turn-retry-"));
  const sessionsDir = path.join(workspaceRoot, "core-agent", "sessions");
  await fs.mkdir(sessionsDir, { recursive: true });

  const llm = new ScriptedLLM(script);
  const transcript = new Transcript(sessionsDir, "agent:main:app:general:1");
  const controlEvents = new ControlEventLedger({
    rootDir: sessionsDir,
    sessionKey: "agent:main:app:general:1",
    transcript,
  });

  const hooks = {
    runPre: async (point: string, args: unknown) => {
      if (
        opts.blockRetryPreLlm === true &&
        point === "beforeLLMCall" &&
        JSON.stringify(args).includes("unsupported claim")
      ) {
        return { action: "block" as const, reason: "retry preflight timeout" };
      }
      if (
        point === "beforeCommit" &&
        (args as { assistantText?: string }).assistantText?.includes("Unsupported")
      ) {
        return {
          action: "block" as const,
          reason: opts.beforeCommitBlockReason ?? "unsupported claim",
        };
      }
      return { action: "continue" as const, args };
    },
    runPost: async () => {},
    list: () => [],
  };

  const auditLog: Pick<AuditLog, "append"> = {
    append: async () => {},
  };
  const agentConfig = {
    botId: "bot-retry",
    userId: "user-retry",
    workspaceRoot,
    gatewayToken: "test",
    apiProxyUrl: "http://localhost",
    chatProxyUrl: "http://localhost",
    redisUrl: "redis://localhost",
    model: "claude-opus-4-7",
  };
  const agentStub = {
    config: agentConfig,
    resolveRuntimeModel: async () => opts.runtimeModel ?? agentConfig.model,
    hooks,
    tools: {
      list: () => [
        {
          name: "Echo",
          kind: "builtin" as const,
          permission: "read" as const,
          description: "Echo test tool",
          inputSchema: { type: "object", properties: {} },
          execute: async (input: unknown) => ({
            status: "ok" as const,
            durationMs: 1,
            output: JSON.stringify(input),
          }),
        },
      ],
      resolve: (name: string) =>
        name === "Echo"
          ? {
              name: "Echo",
              kind: "builtin" as const,
              permission: "read" as const,
              description: "Echo test tool",
              inputSchema: { type: "object", properties: {} },
              execute: async (input: unknown) => ({
                status: "ok" as const,
                durationMs: 1,
                output: JSON.stringify(input),
              }),
            }
          : undefined,
    },
    intent: { classify: async () => ["general"] },
    workspace: { loadIdentity: async () => ({}) },
    auditLog,
    llm,
    sessionsDir,
    contextEngine: {
      maybeCompact: async () => {},
      buildMessagesFromTranscript: () => [],
    },
  };
  const sessionStub = {
    meta: {
      sessionKey: "agent:main:app:general:1",
      botId: "bot-retry",
      channel: { type: "app" as const, channelId: "general" },
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
    getStructuredOutputContract: () => opts.structuredOutputContract ?? null,
  };
  const userMessage: UserMessage = {
    text: "answer with evidence",
    receivedAt: Date.now(),
  };
  const sse = new FakeSse();

  return {
    turn: new Turn(
      sessionStub as unknown as Session,
      userMessage,
      "turn-retry-1",
      sse,
      "direct",
    ),
    llm,
    transcript,
    controlEvents,
    sse,
  };
}

describe("Turn blocked-output retry", () => {
  it("resamples after a beforeCommit block and commits only the corrected draft", async () => {
    const { turn, llm, transcript, controlEvents } = await makeFixture();

    await turn.execute();
    const result = await turn.commitWithRetry();

    expect(result).toMatchObject({
      status: "committed",
      finalText: "Verified answer.",
    });
    expect(llm.calls).toHaveLength(3);
    expect(JSON.stringify(llm.calls[1]?.messages)).toContain("Unsupported claim.");
    expect(JSON.stringify(llm.calls[1]?.messages)).toContain("unsupported claim");
    expect(JSON.stringify(llm.calls[2]?.messages)).toContain("Unsupported claim again.");

    const entries = await transcript.readAll();
    const assistantTexts = entries
      .filter((entry) => entry.kind === "assistant_text")
      .map((entry) => (entry as { text: string }).text);
    expect(assistantTexts).toEqual(["Verified answer."]);
    const canonicalMessages = entries
      .filter((entry) => entry.kind === "canonical_message")
      .map((entry) => entry as { role: string; content: Array<{ type: string; text?: string }> });
    expect(canonicalMessages).toHaveLength(1);
    expect(canonicalMessages[0]?.role).toBe("assistant");
    expect(canonicalMessages[0]?.content).toEqual([
      { type: "text", text: "Verified answer." },
    ]);

    const events = await controlEvents.readByTurn("turn-retry-1");
    const retry = events.find((event) => event.type === "retry");
    expect(retry).toMatchObject({
      type: "retry",
      reason: "unsupported claim",
      attempt: 1,
    });
  });

  it("does not restore a verifier-blocked draft when retry preflight fails", async () => {
    const { turn, transcript, sse } = await makeFixture(undefined, {
      blockRetryPreLlm: true,
    });

    await turn.execute();
    await expect(turn.commitWithRetry()).rejects.toThrow("retry preflight timeout");

    const visible = visibleTextAfterLastClear(sse.agentEvents);
    expect(visible).toContain("runtime verifier blocked");
    expect(visible).not.toContain("Unsupported claim");

    const entries = await transcript.readAll();
    expect(entries.some((entry) => entry.kind === "assistant_text")).toBe(false);
  });

  it("does not resample when a sealed-files verifier blocks the commit", async () => {
    const { turn, llm, transcript, controlEvents, sse } = await makeFixture(undefined, {
      beforeCommitBlockReason:
        "[RULE:SEALED_FILES] Sealed files changed without explicit approval.",
    });

    await turn.execute();
    await expect(turn.commitWithRetry()).rejects.toThrow(
      "beforeCommit blocked: [RULE:SEALED_FILES]",
    );

    expect(llm.calls).toHaveLength(1);
    expect(
      sse.agentEvents.some(
        (event) =>
          !!event &&
          typeof event === "object" &&
          (event as { type?: unknown }).type === "retry",
      ),
    ).toBe(false);

    const events = await controlEvents.readByTurn("turn-retry-1");
    expect(events.some((event) => event.type === "retry")).toBe(false);
    expect(visibleTextAfterLastClear(sse.agentEvents)).toBe("");

    const entries = await transcript.readAll();
    expect(entries.some((entry) => entry.kind === "assistant_text")).toBe(false);
  });

  it("does not resample when a hook timeout blocks the commit", async () => {
    const { turn, llm, transcript } = await makeFixture(undefined, {
      beforeCommitBlockReason:
        "hook:builtin:self-claim-verifier threw: Error: hook timeout after 5000ms",
    });

    await turn.execute();
    await expect(turn.commitWithRetry()).rejects.toThrow(
      "beforeCommit blocked: hook:builtin:self-claim-verifier threw: Error: hook timeout",
    );

    expect(llm.calls).toHaveLength(1);
    const entries = await transcript.readAll();
    expect(entries.some((entry) => entry.kind === "assistant_text")).toBe(false);
  });

  it("uses the resolved turn model for normal and retry LLM calls", async () => {
    const { turn, llm } = await makeFixture(undefined, {
      runtimeModel: "openai/gpt-5.5",
    });

    await turn.execute();
    await turn.commitWithRetry();

    expect(llm.calls.map((call) => call.model)).toEqual([
      "openai/gpt-5.5",
      "openai/gpt-5.5",
      "openai/gpt-5.5",
    ]);
  });

  it("preserves prior tool_use/tool_result history when retrying a blocked final draft", async () => {
    const { turn, llm } = await makeFixture([
      {
        blocks: [
          { type: "tool_use", id: "tool_1", name: "Echo", input: { msg: "evidence" } },
        ],
        stopReason: "tool_use",
      },
      { blocks: [{ type: "text", text: "Unsupported claim." }], stopReason: "end_turn" },
      { blocks: [{ type: "text", text: "Verified answer." }], stopReason: "end_turn" },
    ]);

    await turn.execute();
    const result = await turn.commitWithRetry();

    expect(result).toMatchObject({ status: "committed", finalText: "Verified answer." });
    const retryMessages = llm.calls[2]?.messages ?? [];
    const serialized = JSON.stringify(retryMessages);
    expect(serialized).toContain("\"type\":\"tool_use\"");
    expect(serialized).toContain("\"type\":\"tool_result\"");
    expect(serialized.indexOf("\"type\":\"tool_use\"")).toBeLessThan(
      serialized.indexOf("\"type\":\"tool_result\""),
    );
  });

  it("drops unresolved final tool_use before blocked-commit retry replay", async () => {
    const { turn, llm } = await makeFixture([
      { blocks: [{ type: "text", text: "Unsupported partial 1" }], stopReason: "max_tokens" },
      { blocks: [{ type: "text", text: "Unsupported partial 2" }], stopReason: "max_tokens" },
      { blocks: [{ type: "text", text: "Unsupported partial 3" }], stopReason: "max_tokens" },
      {
        blocks: [
          { type: "text", text: "Unsupported final." },
          { type: "tool_use", id: "tool_orphan", name: "Echo", input: { msg: "late" } },
        ],
        stopReason: "max_tokens",
      },
      { blocks: [{ type: "text", text: "Verified answer." }], stopReason: "end_turn" },
    ]);

    await turn.execute();
    const result = await turn.commitWithRetry();

    expect(result).toMatchObject({ status: "committed", finalText: "Verified answer." });
    const retryMessages = JSON.stringify(llm.calls[4]?.messages ?? []);
    expect(retryMessages).toContain("Unsupported final.");
    expect(retryMessages).not.toContain("tool_orphan");
  });

  it("advances structured-output retry attempts and aborts only after exhaustion", async () => {
    const { turn, controlEvents } = await makeFixture([
      { blocks: [{ type: "text", text: "not json" }], stopReason: "end_turn" },
      { blocks: [{ type: "text", text: "{\"wrong\":true}" }], stopReason: "end_turn" },
      { blocks: [{ type: "text", text: "{\"wrong\":false}" }], stopReason: "end_turn" },
    ], {
      structuredOutputContract: {
        schemaName: "canary",
        schema: {
          type: "object",
          required: ["ok"],
          properties: { ok: { type: "boolean" } },
        },
        maxAttempts: 3,
      },
    });

    await turn.execute();
    await expect(turn.commitWithRetry()).rejects.toThrow("$.ok is required");
    expect(turn.meta.stopReason).toBe("structured_output_retry_exhausted");

    const statuses = (await controlEvents.readByTurn("turn-retry-1"))
      .filter((event) => event.type === "structured_output")
      .map((event) => event.status);
    expect(statuses).toEqual(["invalid", "invalid", "retry_exhausted"]);
  });
});
