import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";
import type { HookContext } from "../types.js";
import { makeMemoryContinuityGuardHook } from "./memoryContinuityGuard.js";

function makeCtx(contract?: ExecutionContractStore): {
  ctx: HookContext;
  emitted: Array<{ type: string; ruleId?: string; verdict?: string; detail?: string }>;
  logs: Array<{ level: string; msg: string; data?: object }>;
} {
  const emitted: Array<{ type: string; ruleId?: string; verdict?: string; detail?: string }> = [];
  const logs: Array<{ level: string; msg: string; data?: object }> = [];
  return {
    emitted,
    logs,
    ctx: {
      botId: "bot-test",
      userId: "user-test",
      sessionKey: "session-test",
      turnId: "turn-test",
      llm: {} as HookContext["llm"],
      transcript: [],
      emit: (event) => emitted.push(event as never),
      log: (level, msg, data) => logs.push({ level, msg, data }),
      agentModel: "test-model",
      abortSignal: new AbortController().signal,
      deadlineMs: 5_000,
      ...(contract ? { executionContract: contract } : {}),
    },
  };
}

function args(userMessage: string, assistantText: string, retryCount = 0) {
  return {
    assistantText,
    toolCallCount: 0,
    toolReadHappened: false,
    userMessage,
    retryCount,
  };
}

function contractWithBackgroundMemory(): ExecutionContractStore {
  const contract = new ExecutionContractStore({ now: () => 123 });
  contract.recordMemoryRecall({
    turnId: "turn-test",
    source: "qmd",
    path: "memory/old.md",
    continuity: "background",
    distinctivePhrases: ["한국식 vs 일본식 이름 선택"],
  });
  return contract;
}

describe("memoryContinuityGuard", () => {
  const originalEnv = process.env.CORE_AGENT_MEMORY_CONTINUITY_GUARD;

  beforeEach(() => {
    delete process.env.CORE_AGENT_MEMORY_CONTINUITY_GUARD;
  });

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.CORE_AGENT_MEMORY_CONTINUITY_GUARD;
    } else {
      process.env.CORE_AGENT_MEMORY_CONTINUITY_GUARD = originalEnv;
    }
  });

  it("blocks stale background memory promoted into a current decision question", async () => {
    const hook = makeMemoryContinuityGuardHook();
    const { ctx, emitted } = makeCtx(contractWithBackgroundMemory());

    const result = await hook.handler(
      args(
        "SYNC 분량 지금 어느 정도야?",
        "그런데 한국식 vs 일본식 이름 선택 문제는 어떻게 할까요?",
      ),
      ctx,
    );

    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("[RETRY:MEMORY_CONTINUITY]");
    }
    expect(emitted).toContainEqual(expect.objectContaining({
      type: "rule_check",
      ruleId: "memory-continuity-guard",
      verdict: "violation",
    }));
  });

  it("allows explicit callbacks to old topics", async () => {
    const hook = makeMemoryContinuityGuardHook();
    const { ctx } = makeCtx(contractWithBackgroundMemory());

    const result = await hook.handler(
      args(
        "아까 한국식 vs 일본식 이름 선택 문제 다시 보자",
        "한국식 vs 일본식 이름 선택은 일본식이 더 자연스럽습니다.",
      ),
      ctx,
    );

    expect(result).toEqual({ action: "continue" });
  });

  it("allows passive background references", async () => {
    const hook = makeMemoryContinuityGuardHook();
    const { ctx, emitted } = makeCtx(contractWithBackgroundMemory());

    const result = await hook.handler(
      args(
        "SYNC 분량 지금 어느 정도야?",
        "SYNC는 한국식 vs 일본식 이름 선택 논의도 있었지만, 현재 분량은 1-2장 수준입니다.",
      ),
      ctx,
    );

    expect(result).toEqual({ action: "continue" });
    expect(emitted).toContainEqual(expect.objectContaining({
      type: "rule_check",
      ruleId: "memory-continuity-guard",
      verdict: "ok",
    }));
  });

  it("continues when metadata is missing", async () => {
    const hook = makeMemoryContinuityGuardHook();
    const { ctx } = makeCtx();

    const result = await hook.handler(
      args(
        "SYNC 분량 지금 어느 정도야?",
        "그런데 한국식 vs 일본식 이름 선택 문제는 어떻게 할까요?",
      ),
      ctx,
    );

    expect(result).toEqual({ action: "continue" });
  });

  it("respects CORE_AGENT_MEMORY_CONTINUITY_GUARD=off", async () => {
    process.env.CORE_AGENT_MEMORY_CONTINUITY_GUARD = "off";
    const hook = makeMemoryContinuityGuardHook();
    const { ctx } = makeCtx(contractWithBackgroundMemory());

    const result = await hook.handler(
      args(
        "SYNC 분량 지금 어느 정도야?",
        "그런데 한국식 vs 일본식 이름 선택 문제는 어떻게 할까요?",
      ),
      ctx,
    );

    expect(result).toEqual({ action: "continue" });
  });
});
