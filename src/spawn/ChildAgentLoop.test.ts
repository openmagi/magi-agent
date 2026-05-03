/**
 * ChildAgentLoop unit tests — exercises the mini-agent loop with a
 * stubbed LLMClient. These tests pin the child-specific invariants that
 * are NOT covered by the Turn suite (children have no transcript / SSE /
 * hook chain).
 */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type { LLMEvent, LLMStreamRequest } from "../transport/LLMClient.js";
import { Workspace } from "../storage/Workspace.js";
import {
  CHILD_MAX_ITERATIONS,
  hasTrailingDeferralNarrative,
  runChildAgentLoop,
  selectChildTools,
  type SpawnChildOptions,
} from "./ChildAgentLoop.js";

describe("hasTrailingDeferralNarrative", () => {
  it("detects english 'Now let me write the report'", () => {
    expect(
      hasTrailingDeferralNarrative(
        "Collected all data. Now let me write the report.",
      ),
    ).toBe(true);
  });
  it("detects english 'I'll now generate the analysis'", () => {
    expect(
      hasTrailingDeferralNarrative("I'll now generate the analysis file."),
    ).toBe(true);
  });
  it("detects korean '이제 리포트 작성'", () => {
    expect(
      hasTrailingDeferralNarrative("데이터 수집 완료. 이제 리포트 작성하겠습니다."),
    ).toBe(true);
  });
  it("detects korean '결과를 정리'", () => {
    expect(
      hasTrailingDeferralNarrative("분석 끝. 이제 결과를 정리합니다."),
    ).toBe(true);
  });
  it("does NOT fire on already-complete plain text", () => {
    expect(
      hasTrailingDeferralNarrative("Here is the analysis: 아메리카노 224건, 매출 777,550원."),
    ).toBe(false);
  });
  it("does NOT fire on empty string", () => {
    expect(hasTrailingDeferralNarrative("")).toBe(false);
  });
  it("only inspects the trailing ~200 chars", () => {
    // "Now let me write" buried 500+ chars before end → ignored.
    const long =
      "Now let me write the report. " + "Analysis details: ".repeat(50);
    expect(hasTrailingDeferralNarrative(long)).toBe(false);
  });
});

interface MockScript {
  rounds: Array<LLMEvent[] | Error>;
}

function mockLLM(script: MockScript): {
  stream: (req: LLMStreamRequest) => AsyncGenerator<LLMEvent, void, void>;
  calls: LLMStreamRequest[];
} {
  const calls: LLMStreamRequest[] = [];
  let idx = 0;
  async function* stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    calls.push(req);
    const r = script.rounds[idx++] ?? [
      {
        kind: "message_end",
        stopReason: "end_turn",
        usage: { inputTokens: 0, outputTokens: 0 },
      },
    ];
    if (r instanceof Error) throw r;
    for (const e of r) yield e;
  }
  return { stream, calls };
}

function stubTool<TIn = unknown, TOut = unknown>(
  name: string,
  run: (input: TIn, ctx: ToolContext) => Promise<ToolResult<TOut>>,
): Tool<TIn, TOut> {
  return {
    name,
    description: `stub ${name}`,
    inputSchema: { type: "object" },
    permission: "meta",
    kind: "core",
    execute: run,
  };
}

function fakeAgent(tools: Tool[], script: MockScript): {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  agent: any;
  llmCalls: LLMStreamRequest[];
} {
  const llm = mockLLM(script);
  const map = new Map<string, Tool>(tools.map((t) => [t.name, t]));
  return {
    agent: {
      config: { model: "claude-opus-4-7" },
      llm: { stream: llm.stream },
      tools: {
        list: () => [...map.values()],
        resolve: (n: string) => map.get(n) ?? null,
      },
    },
    llmCalls: llm.calls,
  };
}

async function makeOpts(overrides: Partial<SpawnChildOptions> = {}): Promise<{
  opts: SpawnChildOptions;
  spawnDir: string;
  cleanup: () => Promise<void>;
}> {
  const tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "child-loop-"));
  const spawnDir = path.join(tmpRoot, ".spawn", "task_1");
  await fs.mkdir(spawnDir, { recursive: true });
  const opts: SpawnChildOptions = {
    parentSessionKey: "agent:main:test:1",
    parentTurnId: "turn_0",
    parentSpawnDepth: 0,
    persona: "child",
    prompt: "do work",
    timeoutMs: 5000,
    abortSignal: new AbortController().signal,
    botId: "bot_test",
    workspaceRoot: tmpRoot,
    spawnDir,
    spawnWorkspace: new Workspace(spawnDir),
    taskId: "task_1",
    ...overrides,
  };
  return {
    opts,
    spawnDir,
    cleanup: () => fs.rm(tmpRoot, { recursive: true, force: true }),
  };
}

describe("ChildAgentLoop — selectChildTools", () => {
  it("inherits all tools when no filters given", () => {
    const parent = [stubTool("A", async () => ({ status: "ok", durationMs: 0 }))];
    expect(selectChildTools(parent as Tool[]).length).toBe(1);
  });

  it("filters by allowed_tools name", () => {
    const parent = [
      stubTool("Keep", async () => ({ status: "ok", durationMs: 0 })),
      stubTool("Drop", async () => ({ status: "ok", durationMs: 0 })),
    ] as Tool[];
    const filtered = selectChildTools(parent, ["Keep"]);
    expect(filtered.map((t) => t.name)).toEqual(["Keep"]);
  });

  it("filters by skill tags", () => {
    const parent = [
      {
        ...stubTool("Legal", async () => ({ status: "ok", durationMs: 0 })),
        kind: "skill" as const,
        tags: ["law"],
      },
      {
        ...stubTool("Food", async () => ({ status: "ok", durationMs: 0 })),
        kind: "skill" as const,
        tags: ["restaurant"],
      },
    ] as Tool[];
    const filtered = selectChildTools(parent, undefined, ["law"]);
    expect(filtered.map((t) => t.name)).toEqual(["Legal"]);
  });
});

describe("ChildAgentLoop — loop semantics", () => {
  let cleanups: Array<() => Promise<void>> = [];
  beforeEach(() => {
    cleanups = [];
  });
  afterEach(async () => {
    await Promise.all(cleanups.map((f) => f()));
  });

  it("stops on end_turn with accumulated text", async () => {
    const { agent } = fakeAgent([], {
      rounds: [
        [
          { kind: "text_delta", blockIndex: 0, delta: "hello " },
          { kind: "text_delta", blockIndex: 0, delta: "world" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 2 },
          },
        ],
      ],
    });
    const { opts, cleanup } = await makeOpts();
    cleanups.push(cleanup);

    const result = await runChildAgentLoop(agent, opts);
    expect(result.status).toBe("ok");
    expect(result.finalText).toBe("hello world");
    expect(result.toolCallCount).toBe(0);
  });

  it("adds a trusted-worker runtime contract to the child system prompt by default", async () => {
    const { agent, llmCalls } = fakeAgent([], {
      rounds: [
        [
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    });
    const { opts, cleanup } = await makeOpts();
    cleanups.push(cleanup);

    await runChildAgentLoop(agent, opts);

    const system = String(llmCalls[0]?.system ?? "");
    expect(system).toContain("trusted worker");
    expect(system).toContain("same workspace authority as the parent agent");
    expect(system).toContain(".spawn");
    expect(system).toContain("scratch");
  });

  it("adds an isolated runtime contract when workspace_policy is isolated", async () => {
    const { agent, llmCalls } = fakeAgent([], {
      rounds: [
        [
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    });
    const { opts, cleanup } = await makeOpts({ workspacePolicy: "isolated" });
    cleanups.push(cleanup);

    await runChildAgentLoop(agent, opts);

    const system = String(llmCalls[0]?.system ?? "");
    expect(system).toContain("isolated child worker");
    expect(system).toContain("scoped to the .spawn");
    expect(system).toContain("Do not assume access to parent workspace files");
  });

  it("passes the merged child abort signal into child LLM calls", async () => {
    const { agent, llmCalls } = fakeAgent([], {
      rounds: [
        [
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    });
    const parentAbort = new AbortController();
    const { opts, cleanup } = await makeOpts({ abortSignal: parentAbort.signal });
    cleanups.push(cleanup);

    await runChildAgentLoop(agent, opts);

    expect(llmCalls[0]?.signal).toBeInstanceOf(AbortSignal);
  });

  it("dispatches tool_use and tallies toolCallCount", async () => {
    let executed = 0;
    const tool = stubTool<{ v: string }, { echoed: string }>(
      "Echo",
      async (input, ctx) => {
        executed++;
        // PRE-01: child ctx.workspaceRoot must be the spawnDir, NOT parent root.
        expect(ctx.workspaceRoot).toBe(opts.spawnDir);
        expect(ctx.spawnDepth).toBe(opts.parentSpawnDepth + 1);
        return { status: "ok", output: { echoed: input.v }, durationMs: 0 };
      },
    );
    const { agent } = fakeAgent([tool as Tool], {
      rounds: [
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "Echo" },
          {
            kind: "tool_use_input_delta",
            blockIndex: 0,
            partial: JSON.stringify({ v: "hi" }),
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
    });
    const prepared = await makeOpts();
    const { opts } = prepared;
    cleanups.push(prepared.cleanup);

    const result = await runChildAgentLoop(agent, opts);
    expect(result.status).toBe("ok");
    expect(result.toolCallCount).toBe(1);
    expect(executed).toBe(1);
    expect(result.finalText).toBe("done");
  });

  it("runs beforeToolUse hooks and blocks child tools before execute", async () => {
    let executed = 0;
    const hookCalls: unknown[] = [];
    const tool = stubTool("Blocked", async () => {
      executed++;
      return { status: "ok", durationMs: 0 };
    });
    const { agent } = fakeAgent([tool as Tool], {
      rounds: [
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu_block", name: "Blocked" },
          { kind: "tool_use_input_delta", blockIndex: 0, partial: JSON.stringify({ x: 1 }) },
          {
            kind: "message_end",
            stopReason: "tool_use",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
        [
          { kind: "text_delta", blockIndex: 0, delta: "blocked handled" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    });
    agent.hooks = {
      list: () => [],
      runPre: async (point: string, args: unknown) => {
        hookCalls.push({ point, args });
        return { action: "block" as const, reason: "child boundary block" };
      },
      runPost: async () => {},
    };
    const { opts, cleanup } = await makeOpts();
    cleanups.push(cleanup);

    const result = await runChildAgentLoop(agent, opts);

    expect(result.status).toBe("ok");
    expect(result.toolCallCount).toBe(1);
    expect(result.finalText).toBe("blocked handled");
    expect(executed).toBe(0);
    expect(hookCalls).toHaveLength(1);
    expect(hookCalls[0]).toMatchObject({
      point: "beforeToolUse",
      args: { toolName: "Blocked", toolUseId: "tu_block", input: { x: 1 } },
    });
  });

  it("denies security-critical child shell commands before execution", async () => {
    let executed = 0;
    const tool = {
      ...stubTool("Bash", async () => {
        executed++;
        return { status: "ok", output: "ran", durationMs: 0 };
      }),
      permission: "execute" as const,
    };
    const { agent, llmCalls } = fakeAgent([tool as Tool], {
      rounds: [
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "Bash" },
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
    const prepared = await makeOpts();
    cleanups.push(prepared.cleanup);

    const result = await runChildAgentLoop(agent, prepared.opts);

    expect(result.status).toBe("ok");
    expect(result.toolCallCount).toBe(1);
    expect(executed).toBe(0);
    const retryMessages = JSON.stringify(llmCalls[1]?.messages ?? []);
    expect(retryMessages).toContain("permission_denied");
    expect(retryMessages).toContain("destructive rm -rf");
  });

  it("fails closed for dangerous child tools when no consent delegate is available", async () => {
    let executed = 0;
    const tool = {
      ...stubTool("Danger", async () => {
        executed++;
        return { status: "ok" as const, durationMs: 0, output: { ok: true } };
      }),
      permission: "execute" as const,
      dangerous: true,
    };
    const { agent, llmCalls } = fakeAgent([tool as Tool], {
      rounds: [
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu_danger", name: "Danger" },
          { kind: "tool_use_input_delta", blockIndex: 0, partial: "{}" },
          {
            kind: "message_end",
            stopReason: "tool_use",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
        [
          { kind: "text_delta", blockIndex: 0, delta: "danger denied" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    });
    const { opts, cleanup } = await makeOpts();
    cleanups.push(cleanup);

    const result = await runChildAgentLoop(agent, opts);

    expect(result.status).toBe("ok");
    expect(result.finalText).toBe("danger denied");
    expect(executed).toBe(0);
    const retryMessages = JSON.stringify(llmCalls[1]?.messages ?? []);
    expect(retryMessages).toContain("permission_denied");
    expect(retryMessages).toContain("permission required for Danger");
  });

  it("asks the parent delegate when a dangerous child tool requires permission", async () => {
    let executed = 0;
    const tool = {
      ...stubTool("Danger", async () => {
        executed++;
        return { status: "ok" as const, durationMs: 0, output: { ok: true } };
      }),
      permission: "execute" as const,
      dangerous: true,
    };
    const { agent } = fakeAgent([tool as Tool], {
      rounds: [
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu_danger", name: "Danger" },
          { kind: "tool_use_input_delta", blockIndex: 0, partial: "{}" },
          {
            kind: "message_end",
            stopReason: "tool_use",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
        [
          { kind: "text_delta", blockIndex: 0, delta: "danger approved" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    });
    const { opts, cleanup } = await makeOpts({
      askUser: async () => ({ selectedId: "approve" }),
    });
    cleanups.push(cleanup);

    const result = await runChildAgentLoop(agent, opts);

    expect(result.status).toBe("ok");
    expect(result.finalText).toBe("danger approved");
    expect(executed).toBe(1);
  });

  it("throws child partial result evidence when the LLM stream aborts after tool use", async () => {
    const tool = stubTool<Record<string, unknown>, { ok: boolean }>(
      "WriteMarker",
      async (_input, ctx) => {
        await fs.writeFile(path.join(ctx.workspaceRoot, "marker.txt"), "x");
        return { status: "ok", output: { ok: true }, durationMs: 0 };
      },
    );
    const { agent } = fakeAgent([tool as Tool], {
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
    });
    const { opts, cleanup } = await makeOpts();
    cleanups.push(cleanup);

    await expect(runChildAgentLoop(agent, opts)).rejects.toMatchObject({
      partialResult: {
        status: "error",
        finalText: "",
        toolCallCount: 1,
        errorMessage: "aborted",
      },
    });
  });

  it("reports unknown tool via tool_result, does not throw", async () => {
    const { agent } = fakeAgent([], {
      rounds: [
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "Ghost" },
          { kind: "tool_use_input_delta", blockIndex: 0, partial: "{}" },
          {
            kind: "message_end",
            stopReason: "tool_use",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
        [
          { kind: "text_delta", blockIndex: 0, delta: "oh well" },
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    });
    const { opts, cleanup } = await makeOpts();
    cleanups.push(cleanup);

    const result = await runChildAgentLoop(agent, opts);
    expect(result.status).toBe("ok");
    expect(result.toolCallCount).toBe(1);
    expect(result.finalText).toBe("oh well");
  });

  it("allowedTools are intersected — child LLM sees only filtered toolDefs", async () => {
    const parent = [
      stubTool("Allowed", async () => ({ status: "ok", durationMs: 0 })),
      stubTool("Forbidden", async () => ({ status: "ok", durationMs: 0 })),
    ] as Tool[];
    const { agent, llmCalls } = fakeAgent(parent, {
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
    });
    const { opts, cleanup } = await makeOpts({ allowedTools: ["Allowed"] });
    cleanups.push(cleanup);

    await runChildAgentLoop(agent, opts);
    const defs = llmCalls[0]?.tools ?? [];
    expect(defs.map((t) => t.name)).toEqual(["Allowed"]);
  });

  it("system prompt contains persona + spawn depth marker", async () => {
    const { agent, llmCalls } = fakeAgent([], {
      rounds: [
        [
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 0, outputTokens: 0 },
          },
        ],
      ],
    });
    const { opts, cleanup } = await makeOpts({
      persona: "legal-researcher",
      parentSpawnDepth: 1,
    });
    cleanups.push(cleanup);

    await runChildAgentLoop(agent, opts);
    const system = llmCalls[0]?.system ?? "";
    expect(system).toContain("[Persona: legal-researcher]");
    // depth is incremented for the child's own frame.
    expect(system).toContain("depth=2");
  });

  it("system prompt deterministically includes runtime execution baseline", async () => {
    const { agent, llmCalls } = fakeAgent([], {
      rounds: [
        [
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 0, outputTokens: 0 },
          },
        ],
      ],
    });
    const { opts, cleanup } = await makeOpts({
      persona: "writer",
      prompt: "write the report",
    });
    cleanups.push(cleanup);

    await runChildAgentLoop(agent, opts);

    const system = llmCalls[0]?.system ?? "";
    expect(system).toContain("<agent_self_model>");
    expect(system).toContain("<runtime-evidence-policy>");
    expect(system).toContain("<execution-discipline-policy>");
    expect(system).toContain("smallest solution");
    expect(system).toContain("current-turn tool evidence");
    expect(system).toContain("<output-rules>");
    expect(system).toContain("The user can only see your TEXT output");
    expect(system).toContain("Do not operate as a meta-layer orchestrator");
  });

  it("respects CORE_AGENT_RELIABILITY_PROMPT=off for child reliability prompts", async () => {
    const original = process.env.CORE_AGENT_RELIABILITY_PROMPT;
    process.env.CORE_AGENT_RELIABILITY_PROMPT = "off";
    try {
      const { agent, llmCalls } = fakeAgent([], {
        rounds: [
          [
            {
              kind: "message_end",
              stopReason: "end_turn",
              usage: { inputTokens: 0, outputTokens: 0 },
            },
          ],
        ],
      });
      const { opts, cleanup } = await makeOpts({
        prompt: "빌드 에러 고쳐줘",
      });
      cleanups.push(cleanup);

      await runChildAgentLoop(agent, opts);
      const system = llmCalls[0]?.system ?? "";
      expect(system).toContain("<agent_self_model>");
      expect(system).toContain("<output-rules>");
      expect(system).not.toContain("<runtime-evidence-policy>");
      expect(system).not.toContain("<execution-discipline-policy>");
    } finally {
      if (original === undefined) {
        delete process.env.CORE_AGENT_RELIABILITY_PROMPT;
      } else {
        process.env.CORE_AGENT_RELIABILITY_PROMPT = original;
      }
    }
  });

  it("system prompt injects child-safe workspace docs without main meta-layer identity", async () => {
    const { agent, llmCalls } = fakeAgent([], {
      rounds: [
        [
          {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 0, outputTokens: 0 },
          },
        ],
      ],
    });
    const { opts, cleanup } = await makeOpts({
      persona: "executor",
      prompt: "execute delegated task",
    });
    cleanups.push(cleanup);
    await fs.mkdir(path.join(opts.workspaceRoot, "memory"), { recursive: true });
    await Promise.all([
      fs.writeFile(path.join(opts.workspaceRoot, "CLAUDE.md"), "claude environment rules", "utf8"),
      fs.writeFile(
        path.join(opts.workspaceRoot, "AGENTS.md"),
        [
          "<!-- hipocampus:protocol:start -->",
          "Hipocampus runtime contract",
          "<!-- hipocampus:protocol:end -->",
          "",
          "# AGENTS.md -- Meta-Layer Configuration",
          "",
          "## Runtime Environment",
          "cloud runtime facts",
          "",
          "## Subagent Dispatch (agent-run.sh)",
          "All complex tasks are executed via subagent. The meta layer routes.",
          "",
          "## File Permissions",
          "frozen file list",
        ].join("\n"),
        "utf8",
      ),
      fs.writeFile(path.join(opts.workspaceRoot, "MEMORY.md"), "legacy memory cache", "utf8"),
      fs.writeFile(path.join(opts.workspaceRoot, "memory", "ROOT.md"), "root memory index", "utf8"),
      fs.writeFile(path.join(opts.workspaceRoot, "LEARNING.md"), "learning protocol", "utf8"),
      fs.writeFile(path.join(opts.workspaceRoot, "DISCIPLINE.md"), "discipline rules", "utf8"),
      fs.writeFile(
        path.join(opts.workspaceRoot, "EXECUTION.md"),
        [
          "# EXECUTION.md",
          "execution rules",
          "",
          "## Multi-Agent Orchestration",
          "clawy-agent --agent specialist",
        ].join("\n"),
        "utf8",
      ),
      fs.writeFile(
        path.join(opts.workspaceRoot, "EXECUTION-TOOLS.md"),
        [
          "# EXECUTION-TOOLS.md",
          "execution tool rules",
          "",
          "## Agent Runner — Universal Subagent",
          "Spawn subagents with any model.",
          "",
          "## Coding Agent — Complex Code Generation",
          "delegate to the coding agent",
        ].join("\n"),
        "utf8",
      ),
      fs.writeFile(path.join(opts.workspaceRoot, "TOOLS.md"), "tools reference", "utf8"),
      fs.writeFile(path.join(opts.workspaceRoot, "SOUL.md"), "MAIN META SOUL MUST NOT ENTER CHILD", "utf8"),
      fs.writeFile(path.join(opts.workspaceRoot, "USER-RULES.md"), "- Always cite evidence", "utf8"),
    ]);

    await runChildAgentLoop(agent, opts);

    const system = llmCalls[0]?.system ?? "";
    expect(system).toContain('<subagent_workspace_context source="parent-workspace">');
    expect(system).toContain("# CLAUDE.md");
    expect(system).toContain("claude environment rules");
    expect(system).toContain("# AGENTS.md");
    expect(system).toContain("Hipocampus runtime contract");
    expect(system).toContain("cloud runtime facts");
    expect(system).toContain("frozen file list");
    expect(system).toContain("# MEMORY.md");
    expect(system).toContain("legacy memory cache");
    expect(system).toContain("# memory/ROOT.md");
    expect(system).toContain("root memory index");
    expect(system).toContain("# LEARNING.md");
    expect(system).toContain("# DISCIPLINE.md");
    expect(system).toContain("# EXECUTION.md");
    expect(system).toContain("execution rules");
    expect(system).toContain("# EXECUTION-TOOLS.md");
    expect(system).toContain("execution tool rules");
    expect(system).toContain("<runtime_policy");
    expect(system).toContain("citations.require_sources=true");
    expect(system).toContain("You are the spawned child agent, not the main meta-layer agent");
    expect(system).toContain("Do not spawn, route to, or manage further subagents");
    expect(system).not.toContain("All complex tasks are executed via subagent");
    expect(system).not.toContain("clawy-agent --agent specialist");
    expect(system).not.toContain("Spawn subagents with any model");
    expect(system).not.toContain("delegate to the coding agent");
    expect(system).not.toContain("MAIN META SOUL MUST NOT ENTER CHILD");
  });

  it("parent abort yields status='aborted'", async () => {
    const controller = new AbortController();
    // First round does a tool_use so the loop has to iterate again;
    // we abort between iterations so the top-of-loop guard fires.
    const tool = stubTool("Noop", async () => {
      // Abort from inside the tool — propagates to the parent signal
      // before the next iteration's guard check.
      controller.abort();
      return { status: "ok", output: { ok: true }, durationMs: 0 };
    });
    const { agent } = fakeAgent([tool as Tool], {
      rounds: [
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu", name: "Noop" },
          { kind: "tool_use_input_delta", blockIndex: 0, partial: "{}" },
          {
            kind: "message_end",
            stopReason: "tool_use",
            usage: { inputTokens: 1, outputTokens: 1 },
          },
        ],
      ],
    });
    const { opts, cleanup } = await makeOpts({ abortSignal: controller.signal });
    cleanups.push(cleanup);

    const result = await runChildAgentLoop(agent, opts);
    expect(result.status).toBe("aborted");
  });

  it("timeout yields status='error' with errorMessage='child timeout'", async () => {
    const { agent } = fakeAgent([], {
      // Empty rounds so the second iteration starts AFTER the deadline.
      rounds: [
        [
          {
            kind: "message_end",
            stopReason: "tool_use",
            usage: { inputTokens: 0, outputTokens: 0 },
          },
        ],
      ],
    });
    const { opts, cleanup } = await makeOpts({ timeoutMs: 5 });
    cleanups.push(cleanup);

    // After the first round finishes with tool_use + zero tool_use blocks
    // the loop treats that as "ok" (no tool_uses). To actually exercise
    // the deadline path, make the first round yield a tool_use we can
    // stall on via timer. Simpler: wait a tick then call and rely on the
    // `Date.now() > deadline` check firing on the 2nd iter. We arm that
    // by scripting a tool_use → no handler registered → loops again.
    const result = await runChildAgentLoop(agent, {
      ...opts,
      // Deadline already in the past by the time we enter iter=0.
      timeoutMs: -1,
    });
    expect(result.status).toBe("error");
    expect(result.errorMessage).toBe("child timeout");
  });

  it("iteration cap (CHILD_MAX_ITERATIONS) bounds runaway loops", async () => {
    // Every round emits a tool_use for a known tool that returns ok;
    // the loop should hit the cap after CHILD_MAX_ITERATIONS rounds.
    const tool = stubTool("Noop", async () => ({
      status: "ok",
      output: { ok: true },
      durationMs: 0,
    }));
    const infiniteRounds: LLMEvent[][] = Array.from(
      { length: CHILD_MAX_ITERATIONS + 2 },
      () => [
        { kind: "tool_use_start", blockIndex: 0, id: "tu", name: "Noop" },
        { kind: "tool_use_input_delta", blockIndex: 0, partial: "{}" },
        {
          kind: "message_end",
          stopReason: "tool_use",
          usage: { inputTokens: 1, outputTokens: 1 },
        },
      ],
    );
    const { agent } = fakeAgent([tool as Tool], { rounds: infiniteRounds });
    const { opts, cleanup } = await makeOpts();
    cleanups.push(cleanup);

    const result = await runChildAgentLoop(agent, opts);
    expect(result.status).toBe("error");
    expect(result.errorMessage).toContain("exceeded");
    expect(result.toolCallCount).toBe(CHILD_MAX_ITERATIONS);
  });

  it("child askUser throws (askUser unavailable in spawned child)", async () => {
    const tool = stubTool("AskIt", async (_input, ctx) => {
      try {
        await ctx.askUser({
          question: "why?",
        } as unknown as Parameters<typeof ctx.askUser>[0]);
        return { status: "ok", durationMs: 0 };
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        return {
          status: "error",
          errorCode: "ask_unavailable",
          errorMessage: msg,
          durationMs: 0,
        };
      }
    });
    const { agent } = fakeAgent([tool as Tool], {
      rounds: [
        [
          { kind: "tool_use_start", blockIndex: 0, id: "tu", name: "AskIt" },
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
    });
    const { opts, cleanup } = await makeOpts();
    cleanups.push(cleanup);

    const result = await runChildAgentLoop(agent, opts);
    // The tool swallowed the askUser throw and returned status:error.
    // The child loop itself still finished normally.
    expect(result.status).toBe("ok");
    expect(result.toolCallCount).toBe(1);
  });
});
