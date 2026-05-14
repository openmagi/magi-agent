import { describe, it, expect, beforeEach, afterEach } from "vitest";
import type { HookContext } from "../types.js";
import {
  detectSecretExposure,
  makeSecretExposureGateHook,
} from "./secretExposureGate.js";

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
    userMessage: "show env",
    retryCount,
  };
}

describe("secretExposureGate", () => {
  const originalEnv = process.env.MAGI_SECRET_EXPOSURE;

  beforeEach(() => {
    delete process.env.MAGI_SECRET_EXPOSURE;
  });

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.MAGI_SECRET_EXPOSURE;
    } else {
      process.env.MAGI_SECRET_EXPOSURE = originalEnv;
    }
  });

  it("detects literal API tokens and env assignments", () => {
    expect(detectSecretExposure("OPENAI_API_KEY=sk-1234567890abcdef1234567890")).toBe(true);
    expect(detectSecretExposure("token ghp_1234567890abcdef1234567890abcdef1234")).toBe(true);
  });

  it("does not block env var names or masked references", () => {
    expect(detectSecretExposure("Set OPENAI_API_KEY in your environment.")).toBe(false);
    expect(detectSecretExposure("The token ends with ****1234.")).toBe(false);
  });

  it("blocks secret-looking output", async () => {
    const hook = makeSecretExposureGateHook();
    const result = await hook.handler(
      args("OPENAI_API_KEY=sk-1234567890abcdef1234567890"),
      makeCtx(),
    );
    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("[RETRY:SECRET_EXPOSURE]");
    }
  });

  it("respects MAGI_SECRET_EXPOSURE=off", async () => {
    process.env.MAGI_SECRET_EXPOSURE = "off";
    const hook = makeSecretExposureGateHook();
    const result = await hook.handler(
      args("OPENAI_API_KEY=sk-1234567890abcdef1234567890"),
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });
});
