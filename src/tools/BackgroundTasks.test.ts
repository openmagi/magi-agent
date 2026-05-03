/**
 * BackgroundTasks end-to-end tests — T2-10.
 *
 * Exercises the SpawnAgent `deliver="background"` path wired with a
 * BackgroundTaskRegistry + the four task tools (TaskList/TaskGet/
 * TaskOutput/TaskStop). Uses the same mockLLMClient / fakeAgent shape
 * as SpawnAgent.test.ts.
 */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, it, expect } from "vitest";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type {
  LLMEvent,
  LLMStreamRequest,
} from "../transport/LLMClient.js";
import {
  makeSpawnAgentTool,
  runChildAgentLoop,
  type SpawnAgentInput,
  type SpawnAgentOutput,
} from "./SpawnAgent.js";
import { BackgroundTaskRegistry } from "../tasks/BackgroundTaskRegistry.js";
import { makeTaskListTool, type TaskListOutput } from "./TaskList.js";
import { makeTaskGetTool, type TaskGetOutput } from "./TaskGet.js";
import { makeTaskOutputTool, type TaskOutputOutput } from "./TaskOutput.js";
import { makeTaskStopTool, type TaskStopOutput } from "./TaskStop.js";

interface MockScript {
  rounds: LLMEvent[][];
}

function mockLLMClient(script: MockScript): {
  stream: (req: LLMStreamRequest) => AsyncGenerator<LLMEvent, void, void>;
} {
  let roundIdx = 0;
  async function* stream(
    _req: LLMStreamRequest,
  ): AsyncGenerator<LLMEvent, void, void> {
    const round = script.rounds[roundIdx++] ?? [
      {
        kind: "message_end",
        stopReason: "end_turn",
        usage: { inputTokens: 0, outputTokens: 0 },
      },
    ];
    for (const evt of round) yield evt;
  }
  return { stream };
}

function fakeAgent(
  tools: Tool[],
  script: MockScript,
): { agent: Parameters<typeof makeSpawnAgentTool>[0] } {
  const llm = mockLLMClient(script);
  const toolMap = new Map<string, Tool>(tools.map((t) => [t.name, t]));
  const config = {
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
      return runChildAgentLoop(agent as never, opts);
    },
  } as const;
  return { agent: agent as never };
}

function makeCtx(overrides: Partial<ToolContext> = {}): ToolContext {
  return {
    botId: "bot_test",
    sessionKey: "agent:main:test:1",
    turnId: "turn_0",
    workspaceRoot: "/tmp/ws",
    abortSignal: new AbortController().signal,
    emitProgress: () => {},
    emitAgentEvent: () => {},
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
}

async function sleep(ms: number): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
}

describe("BackgroundTasks — T2-10 end-to-end", () => {
  let tmpRoot: string;
  let registry: BackgroundTaskRegistry;

  beforeEach(async () => {
    tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "bg-tasks-e2e-"));
    registry = new BackgroundTaskRegistry(tmpRoot);
  });
  afterEach(async () => {
    await fs.rm(tmpRoot, { recursive: true, force: true });
  });

  it("(1) background spawn creates a registry entry; TaskList + TaskGet return it", async () => {
    const script: MockScript = {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "bg done" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    };
    const { agent } = fakeAgent([], script);
    const spawnTool = makeSpawnAgentTool(agent, registry);
    const listTool = makeTaskListTool(registry);
    const getTool = makeTaskGetTool(registry);

    const ctx = makeCtx({ workspaceRoot: tmpRoot });
    const spawn = await spawnTool.execute(
      {
        persona: "bg",
        prompt: "work",
        deliver: "background",
        completion_contract: { required_evidence: "text" },
      } satisfies SpawnAgentInput,
      ctx,
    );
    expect(spawn.status).toBe("ok");
    const out = spawn.output as SpawnAgentOutput;
    expect(out.status).toBe("pending");
    const taskId = out.taskId;

    // Wait for the fire-and-forget promise to settle.
    await sleep(30);

    const list = await listTool.execute({}, ctx);
    expect(list.status).toBe("ok");
    const listOut = list.output as TaskListOutput;
    expect(listOut.tasks.map((t) => t.taskId)).toContain(taskId);

    const got = await getTool.execute({ taskId }, ctx);
    expect(got.status).toBe("ok");
    const record = got.output as TaskGetOutput;
    expect(record.taskId).toBe(taskId);
    expect(record.status).toBe("completed");
    expect(record.resultText).toBe("bg done");
  });

  it("(2) TaskOutput returns resultText + durationMs after completion", async () => {
    const script: MockScript = {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "finished output" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    };
    const { agent } = fakeAgent([], script);
    const spawnTool = makeSpawnAgentTool(agent, registry);
    const outputTool = makeTaskOutputTool(registry);
    const ctx = makeCtx({ workspaceRoot: tmpRoot });

    const spawn = await spawnTool.execute(
      {
        persona: "bg",
        prompt: "produce",
        deliver: "background",
        completion_contract: { required_evidence: "text" },
      },
      ctx,
    );
    const { taskId } = spawn.output as SpawnAgentOutput;
    await sleep(30);

    const out = await outputTool.execute({ taskId }, ctx);
    expect(out.status).toBe("ok");
    const body = out.output as TaskOutputOutput;
    expect(body.status).toBe("completed");
    expect(body.resultText).toBe("finished output");
    expect(body.durationMs).toBeGreaterThanOrEqual(0);
  });

  it("(3) TaskList filters by status", async () => {
    const script: MockScript = {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "ok" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    };
    const { agent } = fakeAgent([], script);
    const spawnTool = makeSpawnAgentTool(agent, registry);
    const listTool = makeTaskListTool(registry);
    const ctx = makeCtx({ workspaceRoot: tmpRoot });

    await spawnTool.execute(
      {
        persona: "bg",
        prompt: "a",
        deliver: "background",
        completion_contract: { required_evidence: "text" },
      },
      ctx,
    );
    await sleep(30);

    const completed = await listTool.execute({ status: "completed" }, ctx);
    const cOut = completed.output as TaskListOutput;
    expect(cOut.tasks.every((t) => t.status === "completed")).toBe(true);
    expect(cOut.tasks.length).toBeGreaterThan(0);

    const running = await listTool.execute({ status: "running" }, ctx);
    const rOut = running.output as TaskListOutput;
    expect(rOut.tasks.length).toBe(0);
  });

  it("(4) TaskStop aborts a running child — child sees the abort signal", async () => {
    // Stub tool that blocks until its abortSignal fires, so the child
    // stays "running" long enough for us to call TaskStop.
    const blockingTool: Tool<Record<string, unknown>, { aborted: boolean }> = {
      name: "Block",
      description: "blocks on abort",
      inputSchema: { type: "object" },
      permission: "meta",
      kind: "core",
      async execute(_input, toolCtx) {
        await new Promise<void>((resolve) => {
          if (toolCtx.abortSignal.aborted) {
            resolve();
            return;
          }
          toolCtx.abortSignal.addEventListener("abort", () => resolve(), {
            once: true,
          });
        });
        return {
          status: "aborted",
          output: { aborted: true },
          durationMs: 0,
        };
      },
    };

    const script: MockScript = {
      rounds: [
        // Round 1: child calls Block — which will pend until abort.
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "Block" },
          { kind: "tool_use_input_delta", blockIndex: 0, partial: "{}" },
          {
            kind: "message_end",
            stopReason: "tool_use",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
        // Round 2 (after abort clears the tool wait): child finishes.
        [
          { kind: "text_delta", blockIndex: 0, delta: "post-abort" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    };
    const { agent } = fakeAgent([blockingTool as Tool], script);
    const spawnTool = makeSpawnAgentTool(agent, registry);
    const stopTool = makeTaskStopTool(registry);
    const getTool = makeTaskGetTool(registry);
    const ctx = makeCtx({ workspaceRoot: tmpRoot });

    const spawn = await spawnTool.execute(
      {
        persona: "blocker",
        prompt: "block then be stopped",
        deliver: "background",
        allowed_tools: ["Block"],
      },
      ctx,
    );
    const { taskId } = spawn.output as SpawnAgentOutput;

    // Let the child reach the blocking tool before we stop it.
    await sleep(20);

    const stopResult = await stopTool.execute(
      { taskId, reason: "test_cancel" },
      ctx,
    );
    expect(stopResult.status).toBe("ok");
    const stopOut = stopResult.output as TaskStopOutput;
    expect(stopOut.stopped).toBe(true);

    // Let the child loop unwind.
    await sleep(40);

    const got = await getTool.execute({ taskId }, ctx);
    const rec = got.output as TaskGetOutput;
    // Registry flips to "aborted" synchronously on stop; the child's
    // eventual attachResult may try to overwrite, but we check the
    // registry error prefix either way.
    expect(["aborted", "failed"].includes(rec.status)).toBe(true);
    expect(rec.error).toBeDefined();
  });

  it("(5) TaskGet on unknown taskId returns not_found", async () => {
    const getTool = makeTaskGetTool(registry);
    const outputTool = makeTaskOutputTool(registry);
    const stopTool = makeTaskStopTool(registry);
    const ctx = makeCtx({ workspaceRoot: tmpRoot });

    const got = await getTool.execute({ taskId: "missing" }, ctx);
    expect(got.status).toBe("error");
    expect(got.errorCode).toBe("not_found");

    const out = await outputTool.execute({ taskId: "missing" }, ctx);
    expect(out.status).toBe("error");
    expect(out.errorCode).toBe("not_found");

    const stop = await stopTool.execute({ taskId: "missing" }, ctx);
    // stop returns ok with stopped=false when the task is unknown.
    expect(stop.status).toBe("ok");
    expect((stop.output as TaskStopOutput).stopped).toBe(false);
  });
});
