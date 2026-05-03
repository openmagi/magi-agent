import { describe, it, expect, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type { LLMEvent, LLMStreamRequest } from "../transport/LLMClient.js";
import { ControlEventLedger } from "../control/ControlEventLedger.js";
import { Workspace } from "../storage/Workspace.js";
import { createChildAgentHarness } from "./ChildAgentHarness.js";
import { runChildAgentLoop, type SpawnChildOptions } from "./ChildAgentLoop.js";

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

interface MockScript {
  rounds: LLMEvent[][];
}

function mockLLM(script: MockScript): {
  stream: (req: LLMStreamRequest) => AsyncGenerator<LLMEvent, void, void>;
} {
  let idx = 0;
  async function* stream(_req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    const round = script.rounds[idx++] ?? [
      {
        kind: "message_end",
        stopReason: "end_turn",
        usage: { inputTokens: 0, outputTokens: 0 },
      },
    ];
    for (const event of round) yield event;
  }
  return { stream };
}

function stubTool<TIn = unknown, TOut = unknown>(
  name: string,
  run: (input: TIn, ctx: ToolContext) => Promise<ToolResult<TOut>>,
): Tool<TIn, TOut> {
  return {
    name,
    description: `stub ${name}`,
    inputSchema: { type: "object" },
    permission: "execute",
    kind: "core",
    execute: run,
  };
}

function fakeAgent(tools: Tool[], script: MockScript): unknown {
  const llm = mockLLM(script);
  const map = new Map<string, Tool>(tools.map((tool) => [tool.name, tool]));
  return {
    config: { model: "claude-opus-4-7" },
    llm: { stream: llm.stream },
    tools: {
      list: () => [...map.values()],
      resolve: (name: string) => map.get(name) ?? null,
    },
  };
}

async function makeHarnessedOptions(
  overrides: Partial<SpawnChildOptions> = {},
): Promise<{
  ledger: ControlEventLedger;
  opts: SpawnChildOptions;
}> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "child-permissions-"));
  roots.push(root);
  const spawnDir = path.join(root, ".spawn", "child-1");
  await fs.mkdir(spawnDir, { recursive: true });
  const ledger = new ControlEventLedger({
    rootDir: root,
    sessionKey: "agent:main:test:child-permissions",
  });
  const lifecycle = createChildAgentHarness({
    taskId: "child-1",
    parentTurnId: "turn-parent",
    prompt: "do work",
    emitControlEvent: (event) => ledger.append(event),
  });

  const opts: SpawnChildOptions = {
    parentSessionKey: "agent:main:test:child-permissions",
    parentTurnId: "turn-parent",
    parentSpawnDepth: 0,
    persona: "child",
    prompt: "do work",
    timeoutMs: 5000,
    abortSignal: new AbortController().signal,
    botId: "bot_test",
    workspaceRoot: root,
    spawnDir,
    spawnWorkspace: new Workspace(spawnDir),
    taskId: "child-1",
    lifecycle,
    ...overrides,
  };
  return { ledger, opts };
}

describe("ChildAgentLoop lifecycle permissions", () => {
  it("persists child tool request and permission decision before denied execution", async () => {
    let executed = 0;
    const bash = stubTool("Bash", async () => {
      executed++;
      return { status: "ok", output: "ran", durationMs: 0 };
    });
    const agent = fakeAgent([bash as Tool], {
      rounds: [
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu-1", name: "Bash" },
          {
            kind: "tool_use_input_delta",
            blockIndex: 0,
            partial: JSON.stringify({ command: 'rm -rf "$(pwd)"' }),
          },
          {
            kind: "message_end",
            stopReason: "tool_use",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
        [
          { kind: "text_delta", blockIndex: 0, delta: "blocked" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    });
    const { ledger, opts } = await makeHarnessedOptions({
      workspacePolicy: "isolated",
    });

    await opts.lifecycle?.started();
    const result = await runChildAgentLoop(agent as never, opts);

    expect(result.status).toBe("ok");
    expect(executed).toBe(0);
    const events = await ledger.readAll();
    expect(events.map((event) => event.type)).toContain("child_tool_request");
    expect(events.map((event) => event.type)).toContain("child_permission_decision");
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "child_permission_decision",
        taskId: "child-1",
        decision: "deny",
      }),
    );
  });

  it("denies child Bash commands that escape the spawn workspace", async () => {
    let executed = 0;
    const bash = stubTool("Bash", async () => {
      executed++;
      return { status: "ok", output: "secret", durationMs: 0 };
    });
    const agent = fakeAgent([bash as Tool], {
      rounds: [
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu-escape", name: "Bash" },
          {
            kind: "tool_use_input_delta",
            blockIndex: 0,
            partial: JSON.stringify({ command: "cat ../../parent-secret.txt" }),
          },
          {
            kind: "message_end",
            stopReason: "tool_use",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
        [
          { kind: "text_delta", blockIndex: 0, delta: "blocked" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    });
    const { ledger, opts } = await makeHarnessedOptions({
      workspacePolicy: "isolated",
    });

    await opts.lifecycle?.started();
    await runChildAgentLoop(agent as never, opts);

    expect(executed).toBe(0);
    const events = await ledger.readAll();
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "child_permission_decision",
        taskId: "child-1",
        decision: "deny",
        reason: "child Bash cannot reference parent directories",
      }),
    );
  });

  it("allows trusted child Bash through the parent permission plane", async () => {
    let executed = 0;
    const bash = stubTool("Bash", async () => {
      executed++;
      return { status: "ok", output: "ran", durationMs: 0 };
    });
    const agent = fakeAgent([bash as Tool], {
      rounds: [
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu-trusted", name: "Bash" },
          {
            kind: "tool_use_input_delta",
            blockIndex: 0,
            partial: JSON.stringify({ command: "cat ../../parent-secret.txt" }),
          },
          {
            kind: "message_end",
            stopReason: "tool_use",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
        [
          { kind: "text_delta", blockIndex: 0, delta: "ran" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    });
    const { ledger, opts } = await makeHarnessedOptions();

    await opts.lifecycle?.started();
    await runChildAgentLoop(agent as never, opts);

    expect(executed).toBe(1);
    const events = await ledger.readAll();
    expect(events).not.toContainEqual(
      expect.objectContaining({
        type: "child_permission_decision",
        taskId: "child-1",
        decision: "deny",
        reason: "child Bash cannot reference parent directories",
      }),
    );
  });

  it("persists child_cancelled when the parent abort signal is already cancelled", async () => {
    const controller = new AbortController();
    controller.abort();
    const agent = fakeAgent([], {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "should not run" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    });
    const { ledger, opts } = await makeHarnessedOptions({
      abortSignal: controller.signal,
    });

    await opts.lifecycle?.started();
    const result = await runChildAgentLoop(agent as never, opts);

    expect(result.status).toBe("aborted");
    const events = await ledger.readAll();
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "child_cancelled",
        taskId: "child-1",
        reason: "parent aborted",
      }),
    );
  });
});
