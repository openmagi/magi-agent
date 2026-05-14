import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { ServerResponse } from "node:http";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Agent } from "./Agent.js";
import { Session, type SessionMeta } from "./Session.js";
import type { LLMEvent, LLMStreamRequest } from "./transport/LLMClient.js";
import { SseWriter } from "./transport/SseWriter.js";

class CaptureSse extends SseWriter {
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

class ThrowingLlm {
  readonly calls: LLMStreamRequest[] = [];

  async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    this.calls.push(req);
    throw new Error("http_503: upstream provider error");
  }
}

class ScriptedLlm {
  readonly calls: LLMStreamRequest[] = [];

  constructor(private readonly replies: string[]) {}

  async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    this.calls.push(req);
    const next = this.replies.shift();
    if (next === undefined) throw new Error("ScriptedLlm exhausted");
    if (next.length > 0) {
      yield { kind: "text_delta", blockIndex: 0, delta: next };
      yield { kind: "block_stop", blockIndex: 0 };
    }
    yield {
      kind: "message_end",
      stopReason: "end_turn",
      usage: { inputTokens: 5, outputTokens: 5 },
    };
  }
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

function eventTypes(events: readonly unknown[]): string[] {
  return events
    .map((event) =>
      event && typeof event === "object"
        ? String((event as { type?: unknown }).type ?? "")
        : "",
    )
    .filter(Boolean);
}

describe("Session terminal abort fallback", () => {
  let workspaceRoot: string;
  let sessionsDir: string;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "session-terminal-fallback-"));
    sessionsDir = path.join(workspaceRoot, "core-agent", "sessions");
    await fs.mkdir(sessionsDir, { recursive: true });
  });

  afterEach(async () => {
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  function makeSession(
    llm: ThrowingLlm | ScriptedLlm,
    opts: { beforeCommitBlockReason?: string } = {},
  ): Session {
    const sessionMeta: SessionMeta = {
      sessionKey: "agent:main:app:general:1",
      botId: "bot-terminal-fallback",
      channel: { type: "app", channelId: "general" },
      createdAt: Date.now(),
      lastActivityAt: Date.now(),
    };
    const agent = {
      config: {
        botId: "bot-terminal-fallback",
        userId: "user-terminal-fallback",
        workspaceRoot,
        gatewayToken: "tok",
        apiProxyUrl: "http://api",
        chatProxyUrl: "http://chat",
        redisUrl: "redis://r",
        model: "claude-opus-4-7",
      },
      slashCommands: { resolve: () => null },
      hooks: {
        runPre: async (point: string, args: unknown) => {
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
      },
      tools: { list: () => [], resolve: () => null },
      intent: { classify: async () => ["general"] },
      workspace: { loadIdentity: async () => ({}) },
      auditLog: { append: vi.fn(async () => undefined) },
      llm,
      router: null,
      sessionsDir,
      contextEngine: {
        assertCompactionFeasible: () => undefined,
        maybeCompact: async () => undefined,
        buildMessagesFromTranscript: () => [],
      },
      nextTurnId: () => "turn-terminal-fallback-1",
      registerTurn: vi.fn(),
      unregisterTurn: vi.fn(),
    } as unknown as Agent;
    return new Session(sessionMeta, agent);
  }

  it("emits user-visible fallback text when the LLM stream fails before any answer", async () => {
    const session = makeSession(new ThrowingLlm());
    const sse = new CaptureSse();

    const result = await session.runTurn(
      { text: "Run the benchmark", receivedAt: Date.now() },
      sse,
    );

    expect(result.meta.status).toBe("aborted");
    expect(visibleTextAfterLastClear(sse.agentEvents)).toContain(
      "No final answer was produced",
    );
    expect(eventTypes(sse.agentEvents)).toContain("turn_end");
  });

  it("does not leave a verifier-blocked draft invisible after retry produces no text", async () => {
    const session = makeSession(new ScriptedLlm(["Unsupported claim.", ""]));
    const sse = new CaptureSse();

    const result = await session.runTurn(
      { text: "Answer with evidence", receivedAt: Date.now() },
      sse,
    );

    expect(result.meta.status).toBe("aborted");
    const visible = visibleTextAfterLastClear(sse.agentEvents);
    expect(visible).toContain("No final answer was produced");
    expect(visible).not.toContain("Unsupported claim");
    expect(eventTypes(sse.agentEvents)).toContain("turn_end");
  });

  it("shows a public research-proof verifier notice instead of leaking verifier internals", async () => {
    const session = makeSession(new ScriptedLlm(["Unsupported claim.", ""]), {
      beforeCommitBlockReason:
        "[RULE:CLAIM_CITATION_REQUIRED] Research claims still lack inspected-source citations.",
    });
    const sse = new CaptureSse();

    const result = await session.runTurn(
      { text: "Answer with evidence", receivedAt: Date.now() },
      sse,
    );

    expect(result.meta.status).toBe("aborted");
    const visible = visibleTextAfterLastClear(sse.agentEvents);
    expect(visible).toContain("source-verified final answer");
    expect(visible).not.toContain("CLAIM_CITATION_REQUIRED");
    expect(visible).not.toContain("No final answer was produced");
    expect(visible).not.toContain("Unsupported claim");
    expect(eventTypes(sse.agentEvents)).toContain("turn_end");
  });
});
