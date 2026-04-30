import { describe, it, expect, beforeEach, afterEach } from "vitest";
import type { HookContext } from "../types.js";
import {
  makeCronDeliverySafetyHook,
  matchesRiskyDeliveryCommand,
} from "./cronDeliverySafety.js";

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

describe("cronDeliverySafety", () => {
  const originalEnv = process.env.CORE_AGENT_CHANNEL_DELIVERY_SAFETY;

  beforeEach(() => {
    delete process.env.CORE_AGENT_CHANNEL_DELIVERY_SAFETY;
  });

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.CORE_AGENT_CHANNEL_DELIVERY_SAFETY;
    } else {
      process.env.CORE_AGENT_CHANNEL_DELIVERY_SAFETY = originalEnv;
    }
  });

  it("detects risky direct channel delivery commands", () => {
    expect(matchesRiskyDeliveryCommand("curl https://api.telegram.org/botTOKEN/sendMessage")).toBe(true);
    expect(matchesRiskyDeliveryCommand("clawy cron add --target @user --announce 'done'")).toBe(true);
    expect(matchesRiskyDeliveryCommand("npm test")).toBe(false);
  });

  it("asks for permission before direct channel delivery", async () => {
    const hook = makeCronDeliverySafetyHook();
    const result = await hook.handler(
      {
        toolName: "Bash",
        toolUseId: "tool-1",
        input: { command: "curl https://api.telegram.org/botTOKEN/sendMessage" },
      },
      makeCtx(),
    );
    expect(result).toEqual({
      action: "permission_decision",
      decision: "ask",
      reason: expect.stringContaining("[CHANNEL_DELIVERY_SAFETY]"),
    });
  });

  it("continues for unrelated tools and commands", async () => {
    const hook = makeCronDeliverySafetyHook();
    const result = await hook.handler(
      {
        toolName: "Bash",
        toolUseId: "tool-1",
        input: { command: "npm test" },
      },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("respects CORE_AGENT_CHANNEL_DELIVERY_SAFETY=off", async () => {
    process.env.CORE_AGENT_CHANNEL_DELIVERY_SAFETY = "off";
    const hook = makeCronDeliverySafetyHook();
    const result = await hook.handler(
      {
        toolName: "Bash",
        toolUseId: "tool-1",
        input: { command: "curl https://api.telegram.org/botTOKEN/sendMessage" },
      },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });
});
