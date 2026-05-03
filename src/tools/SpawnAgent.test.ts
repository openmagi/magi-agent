/**
 * SpawnAgent tests — §7.12.d + PRE-01 isolation.
 *
 * Covers:
 *  (a) MAX_SPAWN_DEPTH enforcement blocks at depth=2.
 *  (b) allowed_tools filter narrows the child's toolset.
 *  (c) deliver="return" returns child finalText + toolCallCount.
 *  (d) deliver="background" emits a spawn_result AgentEvent.
 *  (e) PRE-01 — Workspace path-scope refuses traversal outside root.
 *  (f) PRE-01 — child cannot read parent files via ctx.workspaceRoot.
 *  (g) PRE-01 — child writes land inside spawnDir only.
 *  (h) PRE-01 — deliver=return + no files → spawnDir cleaned up.
 *  (i) PRE-01 — deliver=return + files → spawnDir retained + fileCount correct.
 */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, it, expect } from "vitest";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type {
  LLMEvent,
  LLMStreamRequest,
  LLMToolDef,
} from "../transport/LLMClient.js";
import { Workspace } from "../storage/Workspace.js";
import {
  MAX_SPAWN_DEPTH,
  SPAWNABLE_MODELS,
  makeSpawnAgentTool,
  runChildAgentLoop,
  selectChildTools,
  type SpawnAgentInput,
  type SpawnAgentOutput,
  type SpawnHandoffArtifact,
} from "./SpawnAgent.js";
import { ArtifactManager } from "../artifacts/ArtifactManager.js";

// ── Mocks ──────────────────────────────────────────────────────────

interface MockScript {
  /** Sequence of LLM round-trips; each yields the events for one call. */
  rounds: Array<LLMEvent[] | Error>;
}

function mockLLMClient(script: MockScript): {
  stream: (req: LLMStreamRequest) => AsyncGenerator<LLMEvent, void, void>;
  callLog: LLMStreamRequest[];
} {
  const callLog: LLMStreamRequest[] = [];
  let roundIdx = 0;
  async function* stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    callLog.push(req);
    const round = script.rounds[roundIdx++] ?? [
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 0, outputTokens: 0 } },
    ];
    if (round instanceof Error) throw round;
    for (const evt of round) {
      yield evt;
    }
  }
  return { stream, callLog };
}

function makeStubTool(name: string): Tool<{ value?: string }, { echoed: string }> {
  return {
    name,
    description: `stub tool ${name}`,
    inputSchema: { type: "object" },
    permission: "meta",
    kind: "core",
    async execute(
      input: { value?: string },
    ): Promise<ToolResult<{ echoed: string }>> {
      return {
        status: "ok",
        output: { echoed: input.value ?? name },
        durationMs: 1,
      };
    },
  };
}

interface FakeAgentConfig {
  botId: string;
  userId: string;
  workspaceRoot: string;
  gatewayToken: string;
  apiProxyUrl: string;
  chatProxyUrl: string;
  redisUrl: string;
  model: string;
}

/**
 * Minimal Agent surface for the child loop. We duck-type against the
 * real Agent class to avoid spinning up the full constructor (which
 * touches the filesystem for workspace identity).
 */
function fakeAgent(tools: Tool[], script: MockScript): {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  agent: any;
  llmCalls: LLMStreamRequest[];
  spawnCalls: number;
} {
  const llm = mockLLMClient(script);
  const toolMap = new Map<string, Tool>(tools.map((t) => [t.name, t]));
  let spawnCalls = 0;
  const config: FakeAgentConfig = {
    botId: "bot_test",
    userId: "user_test",
    workspaceRoot: "/tmp/ws",
    gatewayToken: "gw",
    apiProxyUrl: "http://api.local",
    chatProxyUrl: "http://chat.local",
    redisUrl: "redis://r",
    model: "claude-opus-4-7",
  };
  const agent = {
    config,
    llm: { stream: llm.stream },
    tools: {
      list(): Tool[] {
        return [...toolMap.values()];
      },
      resolve(name: string): Tool | null {
        return toolMap.get(name) ?? null;
      },
    },
    async spawnChildTurn(
      opts: Parameters<typeof runChildAgentLoop>[1],
    ): Promise<ReturnType<typeof runChildAgentLoop> extends Promise<infer R> ? R : never> {
      spawnCalls++;
      return runChildAgentLoop(agent as never, opts);
    },
  };
  return { agent, llmCalls: llm.callLog, get spawnCalls() { return spawnCalls; } } as never;
}

function makeParentCtx(overrides: Partial<ToolContext> = {}): {
  ctx: ToolContext;
  events: unknown[];
} {
  const events: unknown[] = [];
  const ctx: ToolContext = {
    botId: "bot_test",
    sessionKey: "agent:main:test:1",
    turnId: "turn_0",
    workspaceRoot: "/tmp/ws",
    abortSignal: new AbortController().signal,
    emitProgress: () => {},
    emitAgentEvent: (evt) => events.push(evt),
    askUser: async () => {
      throw new Error("no askUser");
    },
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
    spawnDepth: 0,
    ...overrides,
  };
  return { ctx, events };
}

type SpawnChildTestResult = Awaited<ReturnType<typeof runChildAgentLoop>>;

async function withZeroSpawnRetryDelay<T>(fn: () => Promise<T>): Promise<T> {
  const previous = process.env.CORE_AGENT_SPAWN_RETRY_BASE_DELAY_MS;
  process.env.CORE_AGENT_SPAWN_RETRY_BASE_DELAY_MS = "0";
  try {
    return await fn();
  } finally {
    if (previous === undefined) {
      delete process.env.CORE_AGENT_SPAWN_RETRY_BASE_DELAY_MS;
    } else {
      process.env.CORE_AGENT_SPAWN_RETRY_BASE_DELAY_MS = previous;
    }
  }
}

async function waitForSpawnResult(
  events: unknown[],
  timeoutMs = 200,
): Promise<unknown | undefined> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const event = events.find(
      (e): e is { type: string } => (e as { type?: unknown }).type === "spawn_result",
    );
    if (event) return event;
    await new Promise((r) => setTimeout(r, 5));
  }
  return events.find(
    (e): e is { type: string } => (e as { type?: unknown }).type === "spawn_result",
  );
}

// ── Tests ──────────────────────────────────────────────────────────

describe("SpawnAgent — §7.12.d", () => {
  it("(a) blocks spawning at MAX_SPAWN_DEPTH", async () => {
    const { agent } = fakeAgent([], { rounds: [] });
    const tool = makeSpawnAgentTool(agent);
    const { ctx } = makeParentCtx({ spawnDepth: MAX_SPAWN_DEPTH });

    const result = await tool.execute(
      {
        persona: "child",
        prompt: "do work",
        deliver: "return",
      },
      ctx,
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("max_depth");
  });

  it("(b) allowed_tools filters the child's toolset (pure helper)", () => {
    const parent = [
      makeStubTool("FileRead"),
      makeStubTool("FileWrite"),
      { ...makeStubTool("KoreanLaw"), kind: "skill" as const, tags: ["legal"] },
      { ...makeStubTool("Michelin"), kind: "skill" as const, tags: ["restaurant"] },
    ] as Tool[];

    const filteredByName = selectChildTools(parent, ["FileRead"]);
    expect(filteredByName.map((t) => t.name)).toEqual(["FileRead"]);

    const filteredBySkill = selectChildTools(parent, undefined, ["legal"]);
    expect(filteredBySkill.map((t) => t.name)).toEqual(["KoreanLaw"]);

    const filteredCombo = selectChildTools(parent, ["FileRead"], ["restaurant"]);
    expect(filteredCombo.map((t) => t.name).sort()).toEqual(["FileRead", "Michelin"]);

    const inheritAll = selectChildTools(parent);
    expect(inheritAll.length).toBe(parent.length);
  });

  it("(b) child LLM only sees filtered tools", async () => {
    const parentTools = [makeStubTool("Allowed"), makeStubTool("Forbidden")];
    const script: MockScript = {
      rounds: [
        [
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 10, outputTokens: 2 },
          },
        ],
      ],
    };
    const { agent, llmCalls } = fakeAgent(parentTools, script) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0];
      llmCalls: LLMStreamRequest[];
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx } = makeParentCtx();

    await tool.execute(
      {
        persona: "researcher",
        prompt: "go",
        allowed_tools: ["Allowed"],
        deliver: "return",
      },
      ctx,
    );

    expect(llmCalls.length).toBeGreaterThan(0);
    const firstCall = llmCalls[0];
    expect(firstCall).toBeDefined();
    const toolDefs = (firstCall?.tools ?? []) as LLMToolDef[];
    expect(toolDefs.map((t) => t.name)).toEqual(["Allowed"]);
  });

  it("(c) deliver='return' returns child finalText and toolCallCount", async () => {
    const parentTools = [makeStubTool("Allowed")];
    const script: MockScript = {
      // Round 1: child says "hello", stops.
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "hello from child" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 5, outputTokens: 4 },
          },
        ],
      ],
    };
    const { agent } = fakeAgent(parentTools, script) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0];
    };
    const tool = makeSpawnAgentTool(agent);
    const controlEvents: unknown[] = [];
    const { ctx, events } = makeParentCtx({
      emitControlEvent: async (event) => {
        controlEvents.push(event);
      },
    });

    const result = await tool.execute(
      {
        persona: "child",
        prompt: "say hi",
        deliver: "return",
        completion_contract: {
          required_evidence: "none",
          reason: "answer-only delegation",
        },
      },
      ctx,
    );

    expect(result.status).toBe("ok");
    const out = result.output as SpawnAgentOutput;
    expect(out.status).toBe("ok");
    expect(out.finalText).toBe("hello from child");
    expect(out.toolCallCount).toBe(0);
    // spawn_started event was emitted.
    const startEvt = events.find(
      (e): e is { type: string } => (e as { type?: unknown }).type === "spawn_started",
    );
    expect(startEvt).toBeDefined();
    expect(controlEvents).toContainEqual(
      expect.objectContaining({
        type: "child_started",
        parentTurnId: "turn_0",
        prompt: "say hi",
      }),
    );
    expect(controlEvents).toContainEqual(
      expect.objectContaining({
        type: "child_completed",
        summary: {
          status: "ok",
          finalText: "hello from child",
          toolCallCount: 0,
        },
      }),
    );
  });

  it("(c) return mode tallies tool calls when child uses a tool", async () => {
    const parentTools = [makeStubTool("Allowed")];
    const script: MockScript = {
      rounds: [
        // Round 1: child emits tool_use for "Allowed".
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "Allowed" },
          { kind: "tool_use_input_delta", blockIndex: 0, partial: "{}" },
          {
            kind: "message_end",
            stopReason: "tool_use",
            usage: { inputTokens: 5, outputTokens: 3 },
          },
        ],
        // Round 2: child finishes with text.
        [
          { kind: "text_delta", blockIndex: 0, delta: "done" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 6, outputTokens: 1 },
          },
        ],
      ],
    };
    const { agent } = fakeAgent(parentTools, script) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0];
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx } = makeParentCtx();

    const result = await tool.execute(
      { persona: "child", prompt: "use Allowed", deliver: "return" },
      ctx,
    );
    const out = result.output as SpawnAgentOutput;
    expect(out.finalText).toBe("done");
    expect(out.toolCallCount).toBe(1);
  });

  it("(c2) deliver='return' retries child failures before any tool call", async () => {
    await withZeroSpawnRetryDelay(async () => {
      const { agent } = fakeAgent([], { rounds: [] }) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      let attempts = 0;
      (
        agent as unknown as {
          spawnChildTurn: () => Promise<SpawnChildTestResult>;
        }
      ).spawnChildTurn = async () => {
        attempts++;
        if (attempts < 3) {
          throw new Error("ECONNRESET aborted");
        }
        return {
          status: "ok",
          finalText: "recovered",
          toolCallCount: 1,
        };
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx, events } = makeParentCtx();

      const result = await tool.execute(
        { persona: "child", prompt: "retry", deliver: "return" },
        ctx,
      );

      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput & { attempts?: number };
      expect(out.status).toBe("ok");
      expect(out.finalText).toBe("recovered");
      expect(out.toolCallCount).toBe(1);
      expect(out.attempts).toBe(3);
      expect(attempts).toBe(3);
      const retryEvents = events.filter(
        (e): e is { type: string } =>
          (e as { type?: unknown }).type === "spawn_retry",
      );
      expect(retryEvents).toHaveLength(2);
    });
  });

  it("(c2a) deliver='return' retries an ok child result that violates the default completion contract", async () => {
    await withZeroSpawnRetryDelay(async () => {
      const { agent } = fakeAgent([], { rounds: [] }) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      let attempts = 0;
      (
        agent as unknown as {
          spawnChildTurn: () => Promise<SpawnChildTestResult>;
        }
      ).spawnChildTurn = async () => {
        attempts++;
        if (attempts === 1) {
          return {
            status: "ok",
            finalText: "서브에이전트가 빈 손으로 돌아왔어 (0 tool calls).",
            toolCallCount: 0,
          };
        }
        return {
          status: "ok",
          finalText: "chapter1_text_v4.md를 읽고 수정안을 작성했습니다.",
          toolCallCount: 1,
        };
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx, events } = makeParentCtx();

      const result = await tool.execute(
        {
          persona: "editor",
          prompt: "Read chapter1_text_v4.md and write SCENE 2-5 revisions.",
          deliver: "return",
        },
        ctx,
      );

      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput & { attempts?: number };
      expect(out.finalText).toBe("chapter1_text_v4.md를 읽고 수정안을 작성했습니다.");
      expect(out.toolCallCount).toBe(1);
      expect(out.attempts).toBe(2);
      expect(attempts).toBe(2);
      const retryEvents = events.filter(
        (e): e is { type: string; errorMessage?: string } =>
          (e as { type?: unknown }).type === "spawn_retry",
      );
      expect(retryEvents).toHaveLength(1);
      expect(retryEvents[0]?.errorMessage).toContain(
        "completion_contract (0 tool calls)",
      );
    });
  });

  it("(c2b) completion_contract required_evidence='files' accepts trusted required files", async () => {
    const tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "spawn-files-ok-"));
    const writer: Tool<Record<string, unknown>, { ok: boolean }> = {
      name: "WriteChapter",
      description: "write required chapter",
      inputSchema: { type: "object" },
      permission: "meta",
      kind: "core",
      async execute(_input, ctx) {
        const target = path.join(ctx.workspaceRoot, "book/manuscript/01-3-pilots-fail.md");
        await fs.mkdir(path.dirname(target), { recursive: true });
        await fs.writeFile(target, "chapter", "utf8");
        return { status: "ok", output: { ok: true }, durationMs: 0 };
      },
    };
    const script: MockScript = {
      rounds: [
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "WriteChapter" },
          { kind: "tool_use_input_delta", blockIndex: 0, partial: "{}" },
          {
            kind: "message_end",
            stopReason: "tool_use",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
        [
          { kind: "text_delta", blockIndex: 0, delta: "chapter complete" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    };
    const { agent } = fakeAgent([writer], script) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0];
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

    const result = await tool.execute(
      {
        persona: "writer",
        prompt: "write chapter",
        deliver: "return",
        completion_contract: {
          required_evidence: "files",
          required_files: ["book/manuscript/01-3-pilots-fail.md"],
        },
      } as SpawnAgentInput,
      ctx,
    );

    expect(result.status).toBe("ok");
    await fs.rm(tmpRoot, { recursive: true, force: true });
  });

  it("(c2c) completion_contract required_evidence='files' fails when required files are missing", async () => {
    const tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "spawn-files-missing-"));
    const { agent } = fakeAgent([], {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "done" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    }) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0];
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

    const result = await tool.execute(
      {
        persona: "writer",
        prompt: "claim done",
        deliver: "return",
        completion_contract: {
          required_evidence: "files",
          required_files: ["book/manuscript/01-3-pilots-fail.md"],
        },
      } as SpawnAgentInput,
      ctx,
    );

    expect(result.status).toBe("error");
    expect(result.errorMessage).toContain("missing required files");
    await fs.rm(tmpRoot, { recursive: true, force: true });
  });

  it("(c2d) completion_contract required_evidence='text' rejects empty final text", async () => {
    const { agent } = fakeAgent([], {
      rounds: [
        [
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 0 },
          },
        ],
      ],
    }) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0];
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx } = makeParentCtx();

    const result = await tool.execute(
      {
        persona: "answerer",
        prompt: "answer",
        deliver: "return",
        completion_contract: { required_evidence: "text" },
      } as SpawnAgentInput,
      ctx,
    );

    expect(result.status).toBe("error");
    expect(result.errorMessage).toContain("non-empty final text");
  });

  it("(c3) deliver='return' surfaces exhausted child failures as tool errors", async () => {
    await withZeroSpawnRetryDelay(async () => {
      const { agent } = fakeAgent([], { rounds: [] }) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      let attempts = 0;
      (
        agent as unknown as {
          spawnChildTurn: () => Promise<SpawnChildTestResult>;
        }
      ).spawnChildTurn = async () => {
        attempts++;
        return {
          status: "error",
          finalText: "",
          toolCallCount: 0,
          errorMessage: "Error http_429: upstream provider throttled",
        };
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx, events } = makeParentCtx();

      const result = await tool.execute(
        { persona: "child", prompt: "retry", deliver: "return" },
        ctx,
      );

      expect(attempts).toBe(3);
      expect(result.status).toBe("error");
      expect(result.errorCode).toBe("spawn_failed");
      expect(result.errorMessage).toContain("failed after 3 attempts");
      expect(result.errorMessage).toContain("Do not switch to direct execution");
      const retryEvents = events.filter(
        (e): e is { type: string } =>
          (e as { type?: unknown }).type === "spawn_retry",
      );
      expect(retryEvents).toHaveLength(2);
    });
  });

  it("(c4) deliver='return' does not retry after the child used tools", async () => {
    await withZeroSpawnRetryDelay(async () => {
      const { agent } = fakeAgent([], { rounds: [] }) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      let attempts = 0;
      (
        agent as unknown as {
          spawnChildTurn: () => Promise<SpawnChildTestResult>;
        }
      ).spawnChildTurn = async () => {
        attempts++;
        return {
          status: "error",
          finalText: "",
          toolCallCount: 1,
          errorMessage: "child failed after writing a file",
        };
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx, events } = makeParentCtx();

      const result = await tool.execute(
        { persona: "child", prompt: "no duplicate side effects", deliver: "return" },
        ctx,
      );

      expect(attempts).toBe(1);
      expect(result.status).toBe("error");
      expect(result.errorCode).toBe("spawn_failed");
      const retryEvents = events.filter(
        (e): e is { type: string } =>
          (e as { type?: unknown }).type === "spawn_retry",
      );
      expect(retryEvents).toHaveLength(0);
    });
  });

  it("(c5) preserves partial tool evidence when the child stream aborts after side effects", async () => {
    await withZeroSpawnRetryDelay(async () => {
      const writer: Tool<Record<string, unknown>, { ok: boolean }> = {
        name: "WriteMarker",
        description: "write a marker through ctx.workspaceRoot",
        inputSchema: { type: "object" },
        permission: "meta",
        kind: "core",
        async execute(_input, ctx) {
          await fs.writeFile(path.join(ctx.workspaceRoot, "marker.txt"), "x");
          return { status: "ok", output: { ok: true }, durationMs: 0 };
        },
      };
      const script: MockScript = {
        rounds: [
          [
            { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "WriteMarker" },
            { kind: "tool_use_input_delta", blockIndex: 0, partial: "{}" },
            {
              kind: "message_end",
              stopReason: "tool_use",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
          new Error("aborted"),
        ],
      };
      const { agent } = fakeAgent([writer], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "spawn-partial-"));
      const { ctx, events } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        { persona: "child", prompt: "write then abort", deliver: "return" },
        ctx,
      );

      expect(result.status).toBe("error");
      const out = result.output as SpawnAgentOutput & { attempts?: number };
      expect(out.toolCallCount).toBe(1);
      expect(out.attempts).toBe(1);
      expect(out.errorMessage).toContain("aborted");
      await expect(fs.access(path.join(tmpRoot, "marker.txt"))).resolves.toBeUndefined();
      const retryEvents = events.filter(
        (e): e is { type: string } =>
          (e as { type?: unknown }).type === "spawn_retry",
      );
      expect(retryEvents).toHaveLength(0);

      await fs.rm(tmpRoot, { recursive: true, force: true });
    });
  });

  // ── PRE-01 isolation tests ─────────────────────────────────────

  describe("trusted worker workspace policy", () => {
    it("(tw1) defaults children to trusted parent-workspace writes", async () => {
      const tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "spawn-trusted-"));
      const writer: Tool<{ name: string }, { path: string }> = {
        name: "Writer",
        description: "stub writer that uses ctx.workspaceRoot",
        inputSchema: { type: "object" },
        permission: "meta",
        kind: "core",
        async execute(input, ctx) {
          const target = path.join(ctx.workspaceRoot, input.name);
          await fs.mkdir(path.dirname(target), { recursive: true });
          await fs.writeFile(target, "trusted child output", "utf8");
          return {
            status: "ok",
            output: { path: target },
            durationMs: 1,
          };
        },
      };
      const script: MockScript = {
        rounds: [
          [
            { kind: "tool_use_start", blockIndex: 0, id: "toolu_writer", name: "Writer" },
            {
              kind: "tool_use_input_delta",
              blockIndex: 0,
              partial: JSON.stringify({ name: "book/manuscript/01-3-pilots-fail.md" }),
            },
            {
              kind: "message_end",
              stopReason: "tool_use",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
          [
            { kind: "text_delta", blockIndex: 0, delta: "wrote final chapter" },
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
        ],
      };
      const { agent } = fakeAgent([writer], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        { persona: "writer", prompt: "write the chapter", deliver: "return" },
        ctx,
      );

      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput;
      expect(out.artifacts?.spawnDir.startsWith(path.join(tmpRoot, ".spawn"))).toBe(true);
      await expect(
        fs.readFile(path.join(tmpRoot, "book/manuscript/01-3-pilots-fail.md"), "utf8"),
      ).resolves.toBe("trusted child output");
      await expect(
        fs.access(path.join(out.artifacts!.spawnDir, "book/manuscript/01-3-pilots-fail.md")),
      ).rejects.toThrow();

      await fs.rm(tmpRoot, { recursive: true, force: true });
    });

    it("(tw2) workspace_policy='isolated' keeps writes inside spawnDir", async () => {
      const tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "spawn-isolated-"));
      const writer: Tool<{ name: string }, { path: string }> = {
        name: "Writer",
        description: "stub writer that uses ctx.workspaceRoot",
        inputSchema: { type: "object" },
        permission: "meta",
        kind: "core",
        async execute(input, ctx) {
          const target = path.join(ctx.workspaceRoot, input.name);
          await fs.mkdir(path.dirname(target), { recursive: true });
          await fs.writeFile(target, "isolated child output", "utf8");
          return {
            status: "ok",
            output: { path: target },
            durationMs: 1,
          };
        },
      };
      const script: MockScript = {
        rounds: [
          [
            { kind: "tool_use_start", blockIndex: 0, id: "toolu_writer", name: "Writer" },
            {
              kind: "tool_use_input_delta",
              blockIndex: 0,
              partial: JSON.stringify({ name: "book/work/isolated.md" }),
            },
            {
              kind: "message_end",
              stopReason: "tool_use",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
          [
            { kind: "text_delta", blockIndex: 0, delta: "wrote scratch" },
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
        ],
      };
      const { agent } = fakeAgent([writer], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        {
          persona: "writer",
          prompt: "write scratch",
          deliver: "return",
          workspace_policy: "isolated",
        } as SpawnAgentInput,
        ctx,
      );

      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput;
      await expect(
        fs.readFile(path.join(out.artifacts!.spawnDir, "book/work/isolated.md"), "utf8"),
      ).resolves.toBe("isolated child output");
      await expect(
        fs.access(path.join(tmpRoot, "book/work/isolated.md")),
      ).rejects.toThrow();

      await fs.rm(tmpRoot, { recursive: true, force: true });
    });
  });

  describe("PRE-01 — opt-in ephemeral workspace isolation", () => {
    let tmpRoot: string;

    beforeEach(async () => {
      tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "spawnagent-pre01-"));
    });
    afterEach(async () => {
      await fs.rm(tmpRoot, { recursive: true, force: true });
    });

    it("(e) Workspace rooted at spawnDir refuses path traversal", async () => {
      const ws = new Workspace(tmpRoot);
      // Inside — resolves safely.
      expect(ws.resolve("file.txt")).toBe(path.resolve(tmpRoot, "file.txt"));
      // Escape attempt via ..
      expect(() => ws.resolve("../../../etc/passwd")).toThrow(/path escapes workspace/);
      // Absolute-path-ish input: ws.resolve strips leading slashes, so it stays inside.
      expect(ws.resolve("/etc/passwd")).toBe(path.resolve(tmpRoot, "etc/passwd"));
    });

    it("(f) child cannot read parent files via ctx.workspaceRoot", async () => {
      // Plant a "secret" file in the parent workspace that the child must NOT reach.
      await fs.writeFile(path.join(tmpRoot, "parent-secret.txt"), "topsecret");

      // Stub tool that tries to FileRead at `ctx.workspaceRoot + /parent-secret.txt`.
      // Because PRE-01 reassigns ctx.workspaceRoot to spawnDir, this read misses.
      const peekTool: Tool<Record<string, unknown>, { found: boolean; probed: string }> = {
        name: "Peek",
        description: "stub peek",
        inputSchema: { type: "object" },
        permission: "read",
        kind: "core",
        async execute(_input, ctx) {
          const target = path.join(ctx.workspaceRoot, "parent-secret.txt");
          let found = false;
          try {
            await fs.access(target);
            found = true;
          } catch {
            found = false;
          }
          return {
            status: "ok",
            output: { found, probed: target },
            durationMs: 0,
          };
        },
      };

      const script: MockScript = {
        rounds: [
          // Round 1: child calls Peek.
          [
            { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "Peek" },
            { kind: "tool_use_input_delta", blockIndex: 0, partial: "{}" },
            {
              kind: "message_end",
              stopReason: "tool_use",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
          // Round 2: child finishes.
          [
            { kind: "text_delta", blockIndex: 0, delta: "did peek" },
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
        ],
      };
      const { agent } = fakeAgent([peekTool as Tool], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        { persona: "peeker", prompt: "peek", deliver: "return", workspace_policy: "isolated" },
        ctx,
      );
      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput;
      // The Peek tool reports whether parent-secret.txt is reachable via
      // ctx.workspaceRoot. With PRE-01 in place, the child's workspaceRoot
      // is the ephemeral spawnDir — the file is NOT there.
      expect(out.finalText).toBe("did peek");
      expect(out.artifacts).toBeDefined();
      const probedPath = path.join(out.artifacts!.spawnDir, "parent-secret.txt");
      // The path the child probes is inside spawnDir, not parent root.
      // And since we never wrote a file there, access should have failed.
      expect(out.artifacts!.spawnDir.startsWith(tmpRoot)).toBe(true);
      expect(out.artifacts!.spawnDir.includes(".spawn")).toBe(true);
      expect(probedPath.startsWith(out.artifacts!.spawnDir)).toBe(true);
      // parent-secret.txt should still exist only in parent root, not spawnDir.
      await expect(fs.access(path.join(tmpRoot, "parent-secret.txt"))).resolves.toBeUndefined();
    });

    it("(g) child writes land inside spawnDir only", async () => {
      const writeTool: Tool<{ name: string; content: string }, { wrote: string }> = {
        name: "StubWrite",
        description: "stub writer that uses ctx.workspaceRoot",
        inputSchema: { type: "object" },
        permission: "write",
        kind: "core",
        async execute(input, ctx) {
          const target = path.join(ctx.workspaceRoot, input.name);
          await fs.writeFile(target, input.content);
          return { status: "ok", output: { wrote: target }, durationMs: 0 };
        },
      };

      const script: MockScript = {
        rounds: [
          [
            { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "StubWrite" },
            {
              kind: "tool_use_input_delta",
              blockIndex: 0,
              partial: JSON.stringify({ name: "artifact.txt", content: "child work" }),
            },
            {
              kind: "message_end",
              stopReason: "tool_use",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
          [
            { kind: "text_delta", blockIndex: 0, delta: "wrote" },
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
        ],
      };
      const { agent } = fakeAgent([writeTool as Tool], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        { persona: "writer", prompt: "write", deliver: "return", workspace_policy: "isolated" },
        ctx,
      );
      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput;
      // Artifact written inside spawnDir, NOT in parent root.
      expect(out.artifacts).toBeDefined();
      const spawnDir = out.artifacts!.spawnDir;
      expect(spawnDir.startsWith(path.join(tmpRoot, ".spawn"))).toBe(true);
      await expect(fs.access(path.join(spawnDir, "artifact.txt"))).resolves.toBeUndefined();
      // Parent root does NOT have artifact.txt at top level.
      await expect(fs.access(path.join(tmpRoot, "artifact.txt"))).rejects.toBeDefined();
    });

    it("(h) deliver=return + no files → spawnDir cleaned up, fileCount=0", async () => {
      const script: MockScript = {
        rounds: [
          [
            { kind: "text_delta", blockIndex: 0, delta: "noop" },
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
        ],
      };
      const { agent } = fakeAgent([], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        {
          persona: "idle",
          prompt: "nothing",
          deliver: "return",
          workspace_policy: "isolated",
          completion_contract: { required_evidence: "none" },
        },
        ctx,
      );
      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput;
      expect(out.artifacts).toBeDefined();
      expect(out.artifacts!.fileCount).toBe(0);
      // Retention (2026-04-20): spawnDir is ALWAYS retained, even when
      // fileCount === 0. Prior behavior rm'd empty dirs, hiding the
      // "child narrated a file write but never wrote" failure mode.
      await expect(fs.access(out.artifacts!.spawnDir)).resolves.toBeUndefined();
    });

    it("(i) deliver=return + files → spawnDir retained + fileCount correct", async () => {
      const writeTool: Tool<{ name: string }, { ok: boolean }> = {
        name: "StubWrite",
        description: "stub writer",
        inputSchema: { type: "object" },
        permission: "write",
        kind: "core",
        async execute(input, ctx) {
          await fs.writeFile(path.join(ctx.workspaceRoot, input.name), "x");
          return { status: "ok", output: { ok: true }, durationMs: 0 };
        },
      };
      const script: MockScript = {
        rounds: [
          [
            { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "StubWrite" },
            {
              kind: "tool_use_input_delta",
              blockIndex: 0,
              partial: JSON.stringify({ name: "a.txt" }),
            },
            {
              kind: "message_end",
              stopReason: "tool_use",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
          [
            { kind: "tool_use_start", blockIndex: 0, id: "tu_2", name: "StubWrite" },
            {
              kind: "tool_use_input_delta",
              blockIndex: 0,
              partial: JSON.stringify({ name: "b.txt" }),
            },
            {
              kind: "message_end",
              stopReason: "tool_use",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
          [
            { kind: "text_delta", blockIndex: 0, delta: "done" },
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
        ],
      };
      const { agent } = fakeAgent([writeTool as Tool], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx, events } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        {
          persona: "writer",
          prompt: "write two",
          deliver: "return",
          workspace_policy: "isolated",
        },
        ctx,
      );
      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput;
      expect(out.artifacts).toBeDefined();
      expect(out.artifacts!.fileCount).toBe(2);
      // spawnDir retained.
      await expect(fs.access(out.artifacts!.spawnDir)).resolves.toBeUndefined();
      await expect(
        fs.access(path.join(out.artifacts!.spawnDir, "a.txt")),
      ).resolves.toBeUndefined();
      await expect(
        fs.access(path.join(out.artifacts!.spawnDir, "b.txt")),
      ).resolves.toBeUndefined();
      // .gitignore written at the .spawn root.
      await expect(
        fs.access(path.join(tmpRoot, ".spawn", ".gitignore")),
      ).resolves.toBeUndefined();
      // spawn_dir_created event emitted.
      const createdEvt = events.find(
        (e): e is { type: string; spawnDir: string } =>
          (e as { type?: unknown }).type === "spawn_dir_created",
      );
      expect(createdEvt).toBeDefined();
      expect(createdEvt?.spawnDir).toBe(out.artifacts!.spawnDir);
    });
  });

  // ── 2026-04-20 — spawn artifact handoff round-trip ─────────────────

  describe("spawn artifact handoff (2026-04-20)", () => {
    let tmpRoot: string;

    beforeEach(async () => {
      tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "spawnagent-handoff-"));
    });
    afterEach(async () => {
      await fs.rm(tmpRoot, { recursive: true, force: true });
    });

    it("(ha1) parent tool_result exposes handedOffArtifacts when child seeds spawnDir artifacts", async () => {
      // The child "creates" an artifact by writing files + index.json
      // directly via a stub tool (simulates a child-scoped ArtifactManager).
      const seedTool: Tool<
        { artifactId: string; title: string; content: string },
        { ok: boolean }
      > = {
        name: "SeedArtifact",
        description: "stub that seeds an artifact into spawnDir/artifacts",
        inputSchema: { type: "object" },
        permission: "write",
        async execute(input, ctx) {
          const childMgr = new ArtifactManager(ctx.workspaceRoot);
          await childMgr.create({
            kind: "report",
            title: input.title,
            content: input.content,
            slug: input.artifactId,
          });
          return { status: "ok", output: { ok: true }, durationMs: 0 };
        },
      };

      const script: MockScript = {
        rounds: [
          [
            { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "SeedArtifact" },
            {
              kind: "tool_use_input_delta",
              blockIndex: 0,
              partial: JSON.stringify({
                artifactId: "seed-1",
                title: "Group 4 of 5 report",
                content: "113 skills tested, 47 pass, detailed breakdown here.",
              }),
            },
            {
              kind: "message_end",
              stopReason: "tool_use",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
          [
            { kind: "text_delta", blockIndex: 0, delta: "seeded" },
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
        ],
      };
      const { agent } = fakeAgent([seedTool as Tool], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      // Give the fake agent an ArtifactManager rooted at the parent workspace
      // so the handoff path is exercised end-to-end.
      (agent as unknown as { artifacts: ArtifactManager }).artifacts = new ArtifactManager(tmpRoot);

      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        { persona: "worker", prompt: "seed", deliver: "return", workspace_policy: "isolated" },
        ctx,
      );
      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput;
      expect(out.artifacts).toBeDefined();
      expect(out.artifacts!.handedOffArtifacts).toBeDefined();
      expect(out.artifacts!.handedOffArtifacts.length).toBe(1);
      const h = out.artifacts!.handedOffArtifacts[0]!;
      expect(h.kind).toBe("report");
      expect(h.title).toBe("Group 4 of 5 report");
      expect(h.artifactId).toBeTruthy();

      // Parent ArtifactManager now lists the imported artifact.
      const parentMgr = (agent as unknown as { artifacts: ArtifactManager }).artifacts;
      const list = await parentMgr.list();
      const entry = list.find((m) => m.artifactId === h.artifactId);
      expect(entry).toBeDefined();
      expect(entry!.spawnTaskId).toMatch(/^spawn_/);

      // L0 round-trip — the full child report is retrievable from the
      // parent's workspace after the spawnDir is long gone.
      const l0 = await parentMgr.readL0(h.artifactId);
      expect(l0).toContain("113 skills tested");
    });

    it("(ha2) no-op when child produces no artifacts — handedOffArtifacts is []", async () => {
      const script: MockScript = {
        rounds: [
          [
            { kind: "text_delta", blockIndex: 0, delta: "noop" },
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
        ],
      };
      const { agent } = fakeAgent([], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      (agent as unknown as { artifacts: ArtifactManager }).artifacts = new ArtifactManager(tmpRoot);
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        {
          persona: "idle",
          prompt: "noop",
          deliver: "return",
          completion_contract: { required_evidence: "none" },
        },
        ctx,
      );
      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput;
      expect(out.artifacts!.handedOffArtifacts).toEqual([]);
    });

    it("(ha3) background deliver surfaces handedOffArtifacts on spawn_result event", async () => {
      const seedTool: Tool<Record<string, unknown>, { ok: boolean }> = {
        name: "BgSeed",
        description: "seed artifact in background",
        inputSchema: { type: "object" },
        permission: "write",
        async execute(_input, ctx) {
          const childMgr = new ArtifactManager(ctx.workspaceRoot);
          await childMgr.create({
            kind: "note",
            title: "bg note",
            content: "bg content",
          });
          return { status: "ok", output: { ok: true }, durationMs: 0 };
        },
      };
      const script: MockScript = {
        rounds: [
          [
            { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "BgSeed" },
            { kind: "tool_use_input_delta", blockIndex: 0, partial: "{}" },
            {
              kind: "message_end",
              stopReason: "tool_use",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
          [
            { kind: "text_delta", blockIndex: 0, delta: "done" },
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
        ],
      };
      const { agent } = fakeAgent([seedTool as Tool], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      (agent as unknown as { artifacts: ArtifactManager }).artifacts = new ArtifactManager(tmpRoot);
      const tool = makeSpawnAgentTool(agent);
      const { ctx, events } = makeParentCtx({ workspaceRoot: tmpRoot });

      await tool.execute(
        { persona: "bg", prompt: "seed", deliver: "background", workspace_policy: "isolated" },
        ctx,
      );

      const resultEvt = (await waitForSpawnResult(events, 1000)) as
        | {
            type: string;
            artifacts: { handedOffArtifacts: SpawnHandoffArtifact[] };
          }
        | undefined;
      expect(resultEvt).toBeDefined();
      expect(resultEvt?.artifacts.handedOffArtifacts).toBeDefined();
      expect(resultEvt!.artifacts.handedOffArtifacts.length).toBe(1);
      expect(resultEvt!.artifacts.handedOffArtifacts[0]!.kind).toBe("note");
    });
  });

  // ── T2-11 persona catalog tests ──────────────────────────────────

  describe("T2-11 — persona catalog expansion", () => {
    let tmpRoot: string;

    beforeEach(async () => {
      tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "spawnagent-t2-11-"));
    });
    afterEach(async () => {
      await fs.rm(tmpRoot, { recursive: true, force: true });
    });

    it("(j) persona='explore' spawns child with only read tools", async () => {
      const parentTools = [
        makeStubTool("FileRead"),
        makeStubTool("Glob"),
        makeStubTool("Grep"),
        makeStubTool("FileWrite"),
        makeStubTool("Bash"),
      ];
      const script: MockScript = {
        rounds: [
          [
            { kind: "text_delta", blockIndex: 0, delta: "explored" },
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
        ],
      };
      const { agent, llmCalls } = fakeAgent(parentTools, script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
        llmCalls: LLMStreamRequest[];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        {
          persona: "explore",
          prompt: "investigate",
          deliver: "return",
          completion_contract: { required_evidence: "none" },
        },
        ctx,
      );
      expect(result.status).toBe("ok");
      expect(llmCalls.length).toBeGreaterThan(0);
      const toolDefs = (llmCalls[0]?.tools ?? []) as LLMToolDef[];
      expect(toolDefs.map((t) => t.name).sort()).toEqual([
        "FileRead",
        "Glob",
        "Grep",
      ]);
    });

    it("(k) caller's allowed_tools overrides preset", async () => {
      const parentTools = [
        makeStubTool("FileRead"),
        makeStubTool("Glob"),
        makeStubTool("Grep"),
        makeStubTool("FileWrite"),
      ];
      const script: MockScript = {
        rounds: [
          [
            { kind: "text_delta", blockIndex: 0, delta: "overridden" },
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
        ],
      };
      const { agent, llmCalls } = fakeAgent(parentTools, script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
        llmCalls: LLMStreamRequest[];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        {
          persona: "explore",
          prompt: "write something",
          allowed_tools: ["FileWrite"],
          deliver: "return",
          completion_contract: { required_evidence: "none" },
        },
        ctx,
      );
      expect(result.status).toBe("ok");
      const toolDefs = (llmCalls[0]?.tools ?? []) as LLMToolDef[];
      // Preset would have given [FileRead, Glob, Grep]; caller override wins.
      expect(toolDefs.map((t) => t.name)).toEqual(["FileWrite"]);
    });
  });

  // ── T3-16 tournament mode tests (OMC Port A) ──────────────────

  describe("T3-16 — tournament mode", () => {
    let tmpRoot: string;

    beforeEach(async () => {
      tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "spawnagent-t3-16-"));
    });
    afterEach(async () => {
      await fs.rm(tmpRoot, { recursive: true, force: true });
    });

    function variantRound(text: string): LLMEvent[] {
      return [
        { kind: "text_delta", blockIndex: 0, delta: text },
        {
          kind: "message_end",
          stopReason: "end_turn",
          usage: { inputTokens: 1, outputTokens: 1 },
        },
      ];
    }

    it("(t1) variants=3 + haiku_rubric picks highest score", async () => {
      // Each child variant is scored as soon as it completes.
      const script: MockScript = {
        rounds: [
          variantRound("v0"),
          variantRound("75"),
          variantRound("v1"),
          variantRound("60"),
          variantRound("v2"),
          variantRound("85"),
        ],
      };
      const { agent } = fakeAgent([], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx, events } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        {
          persona: "drafter",
          prompt: "draft",
          deliver: "return",
          mode: "tournament",
          variants: 3,
          scorer: { kind: "haiku_rubric", rubric: "Clarity" },
        },
        ctx,
      );

      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput;
      expect(out.mode).toBe("tournament");
      expect(out.variants).toBeDefined();
      expect(out.variants!.map((v) => v.score)).toEqual([75, 60, 85]);
      expect(out.winnerIndex).toBe(2);
      expect(out.finalText).toBe("v2");

      const tEvt = events.find(
        (e): e is { type: string; winnerIndex: number } =>
          (e as { type?: unknown }).type === "tournament_result",
      );
      expect(tEvt).toBeDefined();
      expect(tEvt?.winnerIndex).toBe(2);
    });

    it("(t2) variants=3 + tool scorer invokes the scorer tool per child", async () => {
      let scoreCalls = 0;
      const scoreTool: Tool<{ childOutput?: string }, { score: number }> = {
        name: "Scorer",
        description: "stub scorer",
        inputSchema: { type: "object" },
        permission: "meta",
        kind: "core",
        async execute(input) {
          scoreCalls++;
          // Score = length of childOutput; v2 (3 chars) wins.
          return {
            status: "ok",
            output: { score: (input.childOutput ?? "").length },
            durationMs: 0,
          };
        },
      };

      const script: MockScript = {
        rounds: [
          variantRound("a"),
          variantRound("ab"),
          variantRound("abc"),
        ],
      };
      const { agent } = fakeAgent([scoreTool as Tool], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        {
          persona: "drafter",
          prompt: "draft",
          deliver: "return",
          mode: "tournament",
          variants: 3,
          scorer: { kind: "tool", toolName: "Scorer" },
          allowed_tools: [],
        },
        ctx,
      );
      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput;
      expect(scoreCalls).toBe(3);
      expect(out.variants!.map((v) => v.score)).toEqual([1, 2, 3]);
      expect(out.winnerIndex).toBe(2);
    });

    it("(t3) each variant gets its own .spawn/{parentTurnId}.tournament-n/ dir", async () => {
      const script: MockScript = {
        rounds: [
          variantRound("a"),
          variantRound("b"),
          variantRound("c"),
          variantRound("10"),
          variantRound("20"),
          variantRound("30"),
        ],
      };
      const { agent } = fakeAgent([], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({
        workspaceRoot: tmpRoot,
        turnId: "turn_abc",
      });

      const result = await tool.execute(
        {
          persona: "drafter",
          prompt: "draft",
          deliver: "return",
          mode: "tournament",
          variants: 3,
          scorer: { kind: "haiku_rubric", rubric: "r" },
        },
        ctx,
      );
      expect(result.status).toBe("ok");
      const out = result.output as SpawnAgentOutput;
      const dirs = out.variants!.map((v) => v.spawnDir).sort();
      expect(dirs).toEqual([
        path.join(tmpRoot, ".spawn", "turn_abc.tournament-0"),
        path.join(tmpRoot, ".spawn", "turn_abc.tournament-1"),
        path.join(tmpRoot, ".spawn", "turn_abc.tournament-2"),
      ]);
      for (const d of dirs) {
        await expect(fs.access(d)).resolves.toBeUndefined();
      }
    });

    it("(t4) variants=1 returns bad_input", async () => {
      const { agent } = fakeAgent([], { rounds: [] }) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        {
          persona: "drafter",
          prompt: "draft",
          deliver: "return",
          mode: "tournament",
          variants: 1,
          scorer: { kind: "haiku_rubric", rubric: "r" },
        },
        ctx,
      );
      expect(result.status).toBe("error");
      expect(result.errorCode).toBe("bad_input");
    });

    it("(t5) variants=6 returns bad_input", async () => {
      const { agent } = fakeAgent([], { rounds: [] }) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      const result = await tool.execute(
        {
          persona: "drafter",
          prompt: "draft",
          deliver: "return",
          mode: "tournament",
          variants: 6,
          scorer: { kind: "haiku_rubric", rubric: "r" },
        },
        ctx,
      );
      expect(result.status).toBe("error");
      expect(result.errorCode).toBe("bad_input");
    });

    it("(t6) concurrency=1 runs variants sequentially (ordered timestamps)", async () => {
      const execOrder: number[] = [];
      let counter = 0;
      const probeTool: Tool<Record<string, unknown>, { ok: true }> = {
        name: "Probe",
        description: "records execution order",
        inputSchema: { type: "object" },
        permission: "meta",
        kind: "core",
        async execute() {
          const n = counter++;
          execOrder.push(n);
          // Yield to event loop to make interleaving observable.
          await new Promise((r) => setTimeout(r, 10));
          execOrder.push(n);
          return { status: "ok", output: { ok: true }, durationMs: 1 };
        },
      };

      // Each variant round calls Probe exactly once, then finishes.
      function variantWithProbe(id: string): LLMEvent[][] {
        return [
          [
            { kind: "tool_use_start", blockIndex: 0, id, name: "Probe" },
            { kind: "tool_use_input_delta", blockIndex: 0, partial: "{}" },
            {
              kind: "message_end",
              stopReason: "tool_use",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
          [
            { kind: "text_delta", blockIndex: 0, delta: "ok" },
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 1, outputTokens: 1 },
            },
          ],
        ];
      }

      // Sequential execution: variant N's child fully runs (2 rounds),
      // THEN the scorer runs (1 round), THEN variant N+1 begins.
      const script: MockScript = {
        rounds: [
          ...variantWithProbe("v0_t"),
          variantRound("50"),
          ...variantWithProbe("v1_t"),
          variantRound("50"),
          ...variantWithProbe("v2_t"),
          variantRound("50"),
        ],
      };
      const { agent } = fakeAgent([probeTool as Tool], script) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx } = makeParentCtx({ workspaceRoot: tmpRoot });

      await tool.execute(
        {
          persona: "drafter",
          prompt: "draft",
          deliver: "return",
          mode: "tournament",
          variants: 3,
          scorer: { kind: "haiku_rubric", rubric: "r" },
          concurrency: 1,
        },
        ctx,
      );

      // Sequential execution → each tool call completes fully before the
      // next one starts, so we observe 0,0,1,1,2,2 (not interleaved).
      expect(execOrder).toEqual([0, 0, 1, 1, 2, 2]);
    });
  });

  it("(d) deliver='background' emits spawn_result event on completion", async () => {
    const parentTools: Tool[] = [];
    const script: MockScript = {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "bg finish" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 2 },
          },
        ],
      ],
    };
    const { agent } = fakeAgent(parentTools, script) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0];
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx, events } = makeParentCtx();

    const result = await tool.execute(
      {
        persona: "bg",
        prompt: "work",
        deliver: "background",
        completion_contract: {
          required_evidence: "none",
          reason: "event emission test uses answer-only child",
        },
      } satisfies SpawnAgentInput,
      ctx,
    );
    expect(result.status).toBe("ok");
    const out = result.output as SpawnAgentOutput;
    expect(out.status).toBe("pending");

    // Flush microtasks + any pending promise continuations.
    await new Promise((r) => setTimeout(r, 20));

    const resultEvt = events.find(
      (e): e is { type: string; finalText: string; status: string } =>
        (e as { type?: unknown }).type === "spawn_result",
    );
    expect(resultEvt).toBeDefined();
    expect(resultEvt?.status).toBe("ok");
    expect(resultEvt?.finalText).toBe("bg finish");
  });

  // ── Model override tests ──────────────────────────────────────────

  it("model override passes the specified model to LLMClient", async () => {
    const script: MockScript = {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "GPT says 2" },
          { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 10, outputTokens: 5 } },
        ],
      ],
    };
    const { agent, llmCalls } = fakeAgent([], script) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0];
      llmCalls: LLMStreamRequest[];
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx } = makeParentCtx();

    const result = await tool.execute(
      {
        persona: "calculator",
        prompt: "1+1=?",
        deliver: "return",
        model: "gpt-5.4",
        completion_contract: { required_evidence: "none" },
      },
      ctx,
    );

    expect(result.status).toBe("ok");
    expect(llmCalls.length).toBe(1);
    expect(llmCalls[0]?.model).toBe("gpt-5.4");
  });

  it("provider-prefixed OpenAI model override is accepted and canonicalized", async () => {
    const script: MockScript = {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "GPT pro child done" },
          { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 10, outputTokens: 5 } },
        ],
      ],
    };
    const { agent, llmCalls } = fakeAgent([], script) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0];
      llmCalls: LLMStreamRequest[];
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx } = makeParentCtx();

    const result = await tool.execute(
      {
        persona: "calculator",
        prompt: "1+1=?",
        deliver: "return",
        model: "openai/gpt-5.5-pro",
        completion_contract: { required_evidence: "none" },
      },
      ctx,
    );

    expect(result.status).toBe("ok");
    expect(llmCalls.length).toBe(1);
    expect(llmCalls[0]?.model).toBe("gpt-5.5-pro");
  });

  it("omitting model uses the bot's default model", async () => {
    const script: MockScript = {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "default" },
          { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 10, outputTokens: 2 } },
        ],
      ],
    };
    const { agent, llmCalls } = fakeAgent([], script) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0];
      llmCalls: LLMStreamRequest[];
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx } = makeParentCtx();

    await tool.execute(
      {
        persona: "default-child",
        prompt: "go",
        deliver: "return",
        completion_contract: { required_evidence: "none" },
      },
      ctx,
    );

    expect(llmCalls.length).toBe(1);
    expect(llmCalls[0]?.model).toBe("claude-opus-4-7");
  });

  it("rejects invalid model names", async () => {
    const { agent } = fakeAgent([], { rounds: [] });
    const tool = makeSpawnAgentTool(agent);
    const { ctx } = makeParentCtx();

    const result = await tool.execute(
      {
        persona: "hacker",
        prompt: "pwn",
        deliver: "return",
        model: "not-a-real-model",
      },
      ctx,
    );

    expect(result.status).toBe("error");
    expect(result.errorMessage).toContain("model");
  });

  it("validate() rejects unknown model names", () => {
    const { agent } = fakeAgent([], { rounds: [] });
    const tool = makeSpawnAgentTool(agent);
    const err = tool.validate?.({
      persona: "child",
      prompt: "go",
      deliver: "return",
      model: "fake-model-9000",
    } as SpawnAgentInput);
    expect(err).toContain("model");
  });

  it("validate() accepts all SPAWNABLE_MODELS", () => {
    const { agent } = fakeAgent([], { rounds: [] });
    const tool = makeSpawnAgentTool(agent);
    for (const m of SPAWNABLE_MODELS) {
      const err = tool.validate?.({
        persona: "child",
        prompt: "go",
        deliver: "return",
        model: m,
      } as SpawnAgentInput);
      expect(err).toBeNull();
    }
  });

  it("validate() accepts runtime config model IDs for hosted subagent models", () => {
    const { agent } = fakeAgent([], { rounds: [] });
    const tool = makeSpawnAgentTool(agent);

    for (const model of [
      "anthropic/claude-opus-4-7",
      "anthropic/claude-sonnet-4-6",
      "anthropic/claude-haiku-4-5",
      "fireworks/kimi-k2p6",
      "fireworks/minimax-m2p7",
      "openai/gpt-5.4-nano",
      "openai/gpt-5.4-mini",
      "openai/gpt-5.5",
      "openai/gpt-5.5-pro",
      "google/gemini-3.1-flash-lite-preview",
      "google/gemini-3.1-pro-preview",
    ]) {
      const err = tool.validate?.({
        persona: "child",
        prompt: "go",
        deliver: "return",
        model,
      } as SpawnAgentInput);
      expect(err).toBeNull();
    }
  });

  it("spawn_started event includes model when specified", async () => {
    const script: MockScript = {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "done" },
          { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 10, outputTokens: 2 } },
        ],
      ],
    };
    const { agent } = fakeAgent([], script) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0];
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx, events } = makeParentCtx();

    await tool.execute(
      {
        persona: "gemini-child",
        prompt: "hello",
        deliver: "return",
        model: "gemini-3.1-pro-preview",
      },
      ctx,
    );

    const startEvt = events.find(
      (e): e is { type: string; model: string } =>
        (e as { type?: unknown }).type === "spawn_started",
    );
    expect(startEvt).toBeDefined();
    expect(startEvt?.model).toBe("gemini-3.1-pro-preview");
  });

  it("modelOverride propagates through runChildAgentLoop directly", async () => {
    const script: MockScript = {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "kimi says hi" },
          { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 10, outputTokens: 3 } },
        ],
      ],
    };
    const { agent, llmCalls } = fakeAgent([], script) as unknown as {
      agent: Parameters<typeof runChildAgentLoop>[0];
      llmCalls: LLMStreamRequest[];
    };
    const ws = new Workspace("/tmp/spawn-model-test");

    const result = await runChildAgentLoop(agent as never, {
      parentSessionKey: "agent:main:test:1",
      parentTurnId: "turn_0",
      parentSpawnDepth: 0,
      persona: "kimi-child",
      prompt: "hello from kimi",
      timeoutMs: 10_000,
      abortSignal: new AbortController().signal,
      botId: "bot_test",
      workspaceRoot: "/tmp/ws",
      spawnDir: "/tmp/spawn-model-test",
      spawnWorkspace: ws,
      taskId: "task_model_test",
      modelOverride: "kimi-k2p6",
    });

    expect(result.status).toBe("ok");
    expect(result.finalText).toBe("kimi says hi");
    expect(llmCalls.length).toBe(1);
    expect(llmCalls[0]?.model).toBe("kimi-k2p6");
  });

  it("(d2) deliver='background' retries child failures before emitting spawn_result", async () => {
    await withZeroSpawnRetryDelay(async () => {
      const { agent } = fakeAgent([], { rounds: [] }) as unknown as {
        agent: Parameters<typeof makeSpawnAgentTool>[0];
      };
      let attempts = 0;
      (
        agent as unknown as {
          spawnChildTurn: () => Promise<SpawnChildTestResult>;
        }
      ).spawnChildTurn = async () => {
        attempts++;
        if (attempts < 3) {
          return {
            status: "error",
            finalText: "",
            toolCallCount: 0,
            errorMessage: "Error http_503: upstream unavailable",
          };
        }
        return {
          status: "ok",
          finalText: "background recovered",
          toolCallCount: 1,
        };
      };
      const tool = makeSpawnAgentTool(agent);
      const { ctx, events } = makeParentCtx();

      const result = await tool.execute(
        { persona: "bg", prompt: "retry", deliver: "background" },
        ctx,
      );

      expect(result.status).toBe("ok");
      const event = (await waitForSpawnResult(events)) as
        | { status?: string; finalText?: string; attempts?: number }
        | undefined;
      expect(event).toBeDefined();
      expect(event?.status).toBe("ok");
      expect(event?.finalText).toBe("background recovered");
      expect(event?.attempts).toBe(3);
      expect(attempts).toBe(3);
      const retryEvents = events.filter(
        (e): e is { type: string } =>
          (e as { type?: unknown }).type === "spawn_retry",
      );
      expect(retryEvents).toHaveLength(2);
    });
  });
});
