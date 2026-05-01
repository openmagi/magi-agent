import { describe, expect, it } from "vitest";
import type { HookContext } from "../types.js";
import type { LLMToolDef } from "../../transport/LLMClient.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import {
  CRON_META_ALLOWED_PARENT_TOOLS,
  makeCronMetaOrchestratorHooks,
} from "./cronMetaOrchestrator.js";

function makeCtx(overrides: Partial<HookContext> = {}): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "agent:cron:app:general:01CRON",
    turnId: "turn-test",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
    ...overrides,
  };
}

function tool(name: string): LLMToolDef {
  return {
    name,
    description: `${name} tool`,
    input_schema: { type: "object", properties: {} },
  };
}

describe("cronMetaOrchestrator", () => {
  it("injects the cron meta contract and exposes only parent meta tools", async () => {
    const hooks = makeCronMetaOrchestratorHooks();
    const result = await hooks.beforeLLMCall.handler(
      {
        messages: [{ role: "user", content: "daily report" }],
        tools: [
          tool("SpawnAgent"),
          tool("TaskBoard"),
          tool("Bash"),
          tool("FileRead"),
          tool("KnowledgeSearch"),
          tool("CronUpdate"),
        ],
        system: "base system",
        iteration: 0,
      },
      makeCtx(),
    );

    expect(result?.action).toBe("replace");
    if (result?.action !== "replace") throw new Error("expected replace");
    expect(result.value.system).toContain("<cron-meta-orchestrator>");
    expect(result.value.system).toContain("SpawnAgent");
    expect(result.value.tools.map((t) => t.name)).toEqual([
      "SpawnAgent",
      "TaskBoard",
      "CronUpdate",
    ]);
  });

  it("does not duplicate the cron meta contract on later LLM iterations", async () => {
    const hooks = makeCronMetaOrchestratorHooks();
    const alreadyInjected =
      "base system\n\n<cron-meta-orchestrator>\nexisting\n</cron-meta-orchestrator>";

    const result = await hooks.beforeLLMCall.handler(
      {
        messages: [{ role: "user", content: "daily report" }],
        tools: [tool("SpawnAgent"), tool("Bash")],
        system: alreadyInjected,
        iteration: 1,
      },
      makeCtx(),
    );

    expect(result?.action).toBe("replace");
    if (result?.action !== "replace") throw new Error("expected replace");
    expect(
      (result.value.system.match(/<cron-meta-orchestrator>/g) ?? []).length,
    ).toBe(1);
  });

  it("blocks direct work tools in the cron parent turn", async () => {
    const hooks = makeCronMetaOrchestratorHooks();
    const result = await hooks.beforeToolUse.handler(
      {
        toolName: "Bash",
        toolUseId: "tool-1",
        input: { command: "integration.sh google-calendar/events" },
      },
      makeCtx(),
    );

    expect(result).toEqual({
      action: "block",
      reason: expect.stringContaining("[CRON_META_ORCHESTRATOR]"),
    });
  });

  it("allows SpawnAgent and cron management tools in the cron parent turn", async () => {
    const hooks = makeCronMetaOrchestratorHooks();

    for (const toolName of CRON_META_ALLOWED_PARENT_TOOLS) {
      const result = await hooks.beforeToolUse.handler(
        { toolName, toolUseId: `tool-${toolName}`, input: {} },
        makeCtx(),
      );
      expect(result).toEqual({ action: "continue" });
    }
  });

  it("does not block child-agent tools spawned from a cron turn", async () => {
    const hooks = makeCronMetaOrchestratorHooks();
    const result = await hooks.beforeToolUse.handler(
      {
        toolName: "Bash",
        toolUseId: "tool-child",
        input: { command: "npm test" },
      },
      makeCtx({ turnId: "turn-parent::spawn::task-123" }),
    );

    expect(result).toEqual({ action: "continue" });
  });

  it("does not affect normal interactive sessions", async () => {
    const hooks = makeCronMetaOrchestratorHooks();
    const result = await hooks.beforeLLMCall.handler(
      {
        messages: [{ role: "user", content: "analyze this" }],
        tools: [tool("SpawnAgent"), tool("Bash")],
        system: "base system",
        iteration: 0,
      },
      makeCtx({ sessionKey: "agent:main:app:general:1" }),
    );

    expect(result).toEqual({ action: "continue" });
  });

  it("blocks cron completion claims when the parent never spawned a child", async () => {
    const hooks = makeCronMetaOrchestratorHooks();
    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "예약 작업을 완료했고 결과를 확인했습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "daily report",
        retryCount: 0,
      },
      makeCtx(),
    );

    expect(result).toEqual({
      action: "block",
      reason: expect.stringContaining("[RETRY:CRON_META_ORCHESTRATOR]"),
    });
  });

  it("allows cron parent completion after a successful SpawnAgent result", async () => {
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-test",
        toolUseId: "spawn-1",
        name: "SpawnAgent",
        input: { persona: "worker", prompt: "daily report", deliver: "return" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-test",
        toolUseId: "spawn-1",
        status: "ok",
        output: JSON.stringify({
          taskId: "task-1",
          status: "ok",
          finalText: "report complete",
          toolCallCount: 3,
        }),
      },
    ];
    const hooks = makeCronMetaOrchestratorHooks();
    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "예약 작업을 완료했고 결과를 확인했습니다.",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "daily report",
        retryCount: 0,
      },
      makeCtx({ transcript }),
    );

    expect(result).toEqual({ action: "continue" });
  });

  it("uses the transcript reader delegate for cron commit checks", async () => {
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-test",
        toolUseId: "spawn-1",
        name: "SpawnAgent",
        input: { persona: "worker", prompt: "daily report", deliver: "return" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-test",
        toolUseId: "spawn-1",
        status: "ok",
        output: JSON.stringify({ status: "ok", toolCallCount: 1 }),
      },
    ];
    const hooks = makeCronMetaOrchestratorHooks({
      agent: {
        readSessionTranscript: async () => transcript,
      },
    });
    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "예약 작업을 완료했습니다.",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "daily report",
        retryCount: 0,
      },
      makeCtx(),
    );

    expect(result).toEqual({ action: "continue" });
  });

  it("does not require a child spawn for explicit non-completion reports", async () => {
    const hooks = makeCronMetaOrchestratorHooks();
    const result = await hooks.beforeCommit.handler(
      {
        assistantText:
          "예약 작업을 실행하지 못했습니다. 필요한 승인 정보가 없어 완료로 표시하지 않습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "daily report",
        retryCount: 0,
      },
      makeCtx(),
    );

    expect(result).toEqual({ action: "continue" });
  });
});
