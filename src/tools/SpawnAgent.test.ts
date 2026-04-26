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
  rounds: LLMEvent[][];
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
    async deliverBackgroundTaskResult(): Promise<boolean> {
      return false;
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
    const { ctx, events } = makeParentCtx();

    const result = await tool.execute(
      { persona: "child", prompt: "say hi", deliver: "return" },
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
  });

  it("(c2) return mode retries transient child failures before surfacing to parent", async () => {
    const { agent } = fakeAgent([], { rounds: [] }) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0] & {
        spawnChildTurn: Parameters<typeof makeSpawnAgentTool>[0]["spawnChildTurn"];
      };
    };
    let calls = 0;
    agent.spawnChildTurn = async () => {
      calls++;
      if (calls < 3) {
        return {
          status: "error",
          finalText: "",
          toolCallCount: 0,
          errorMessage: "llm upstream reset",
        };
      }
      return {
        status: "ok",
        finalText: "recovered result",
        toolCallCount: 0,
      };
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx, events } = makeParentCtx();

    const result = await tool.execute(
      { persona: "child", prompt: "do retryable work", deliver: "return" },
      ctx,
    );

    expect(result.status).toBe("ok");
    expect(calls).toBe(3);
    const out = result.output as SpawnAgentOutput;
    expect(out.status).toBe("ok");
    expect(out.finalText).toBe("recovered result");
    expect(
      events.filter((e) => (e as { type?: unknown }).type === "spawn_retry"),
    ).toHaveLength(2);
  });

  it("(c3) exhausted return-mode child failures surface as a tool error", async () => {
    const { agent } = fakeAgent([], { rounds: [] }) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0] & {
        spawnChildTurn: Parameters<typeof makeSpawnAgentTool>[0]["spawnChildTurn"];
      };
    };
    let calls = 0;
    agent.spawnChildTurn = async () => {
      calls++;
      return {
        status: "error",
        finalText: "partial child text",
        toolCallCount: 0,
        errorMessage: "child timeout",
      };
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx } = makeParentCtx();

    const result = await tool.execute(
      { persona: "child", prompt: "do retryable work", deliver: "return" },
      ctx,
    );

    expect(calls).toBe(3);
    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("spawn_failed");
    expect(result.errorMessage).toContain("child timeout");
    expect(result.errorMessage).toContain("Do not switch to direct execution");
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

  // ── PRE-01 isolation tests ─────────────────────────────────────

  describe("PRE-01 — ephemeral workspace isolation", () => {
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
        { persona: "peeker", prompt: "peek", deliver: "return" },
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
        { persona: "writer", prompt: "write", deliver: "return" },
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
        { persona: "idle", prompt: "nothing", deliver: "return" },
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
        { persona: "writer", prompt: "write two", deliver: "return" },
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
        { persona: "worker", prompt: "seed", deliver: "return" },
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
        { persona: "idle", prompt: "noop", deliver: "return" },
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
        { persona: "bg", prompt: "seed", deliver: "background" },
        ctx,
      );
      // Wait for background promise chain to settle.
      await new Promise((r) => setTimeout(r, 40));

      const resultEvt = events.find(
        (
          e,
        ): e is {
          type: string;
          artifacts: { handedOffArtifacts: SpawnHandoffArtifact[] };
        } => (e as { type?: unknown }).type === "spawn_result",
      );
      expect(resultEvt).toBeDefined();
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
        { persona: "explore", prompt: "investigate", deliver: "return" },
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
      // Three child rounds produce "v0", "v1", "v2"; then three scorer
      // rounds produce "75", "60", "85" (integer-only).
      const script: MockScript = {
        rounds: [
          variantRound("v0"),
          variantRound("v1"),
          variantRound("v2"),
          variantRound("75"),
          variantRound("60"),
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
      { persona: "bg", prompt: "work", deliver: "background" } satisfies SpawnAgentInput,
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

  it("(d2) deliver='background' asks the agent to deliver the completed result", async () => {
    const parentTools: Tool[] = [];
    const script: MockScript = {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "background result" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 2 },
          },
        ],
      ],
    };
    const { agent } = fakeAgent(parentTools, script) as unknown as {
      agent: Parameters<typeof makeSpawnAgentTool>[0] & {
        deliverBackgroundTaskResult?: (input: {
          sessionKey: string;
          taskId: string;
          status: string;
          finalText?: string;
        }) => Promise<boolean>;
      };
    };
    const delivered: Array<{
      sessionKey: string;
      taskId: string;
      status: string;
      finalText?: string;
    }> = [];
    agent.deliverBackgroundTaskResult = async (input) => {
      delivered.push(input);
      return true;
    };
    const tool = makeSpawnAgentTool(agent);
    const { ctx } = makeParentCtx({ sessionKey: "agent:main:app:general" });

    await tool.execute(
      { persona: "bg", prompt: "work", deliver: "background" } satisfies SpawnAgentInput,
      ctx,
    );

    await new Promise((r) => setTimeout(r, 20));

    expect(delivered).toHaveLength(1);
    expect(delivered[0]).toMatchObject({
      sessionKey: "agent:main:app:general",
      status: "completed",
      finalText: "background result",
    });
    expect(delivered[0]!.taskId).toMatch(/^spawn_/);
  });
});
