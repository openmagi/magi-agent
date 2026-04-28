import { describe, it, expect, beforeEach, afterEach } from "vitest";
import type { HookContext } from "../types.js";
import {
  makeOutputPurityGateHook,
  matchesInternalReasoningLeak,
} from "./outputPurityGate.js";

function makeCtx(): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
  };
}

function args(assistantText: string, retryCount = 0) {
  return {
    assistantText,
    toolCallCount: 0,
    toolReadHappened: false,
    userMessage: "question",
    retryCount,
  };
}

describe("outputPurityGate", () => {
  const originalEnv = process.env.CORE_AGENT_OUTPUT_PURITY;

  beforeEach(() => {
    delete process.env.CORE_AGENT_OUTPUT_PURITY;
  });

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.CORE_AGENT_OUTPUT_PURITY;
    } else {
      process.env.CORE_AGENT_OUTPUT_PURITY = originalEnv;
    }
  });

  it("detects internal planning leakage", () => {
    expect(matchesInternalReasoningLeak("We need answer. The user is asking for a plan.")).toBe(true);
    expect(matchesInternalReasoningLeak("I should inspect files first, then answer.")).toBe(true);
  });

  it("does not flag ordinary final-answer prose", () => {
    expect(matchesInternalReasoningLeak("확인한 결과, 이 설정은 현재 비활성화되어 있습니다.")).toBe(false);
  });

  it("blocks leaked internal reasoning", async () => {
    const hook = makeOutputPurityGateHook();
    const result = await hook.handler(
      args("We need answer. The user is asking for details.\n\nHere is the answer."),
      makeCtx(),
    );
    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("[RETRY:OUTPUT_PURITY]");
    }
  });

  it("fails open after retry budget is exhausted", async () => {
    const hook = makeOutputPurityGateHook();
    const result = await hook.handler(args("I should verify this first.", 1), makeCtx());
    expect(result).toEqual({ action: "continue" });
  });

  it("respects CORE_AGENT_OUTPUT_PURITY=off", async () => {
    process.env.CORE_AGENT_OUTPUT_PURITY = "off";
    const hook = makeOutputPurityGateHook();
    const result = await hook.handler(args("I should verify this first."), makeCtx());
    expect(result).toEqual({ action: "continue" });
  });
});
