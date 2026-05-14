/**
 * Tests for agentSelfModelHook (Layer 1 meta-cognitive scaffolding).
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  AGENT_SELF_MODEL_BLOCK,
  agentSelfModelHook,
} from "./agentSelfModel.js";
import type { HookContext } from "../types.js";

function makeCtx(): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm: {} as never,
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
  };
}

const baseArgs = {
  messages: [],
  tools: [],
  system: "you are a helpful assistant",
  iteration: 0,
};

afterEach(() => {
  delete process.env.CORE_AGENT_SELF_MODEL;
});

describe("agentSelfModelHook", () => {
  it("declares name, point, priority 0, non-blocking", () => {
    expect(agentSelfModelHook.name).toBe("builtin:agent-self-model");
    expect(agentSelfModelHook.point).toBe("beforeLLMCall");
    expect(agentSelfModelHook.priority).toBe(0);
    expect(agentSelfModelHook.blocking).toBe(false);
  });

  it("prepends the self-model block on iteration 0", async () => {
    const result = await agentSelfModelHook.handler(baseArgs, makeCtx());
    expect(result?.action).toBe("replace");
    if (result?.action !== "replace") throw new Error("expected replace");
    expect(result.value.system.startsWith("<agent_self_model>")).toBe(true);
    expect(result.value.system).toContain("you are a helpful assistant");
    expect(result.value.system).toContain("</agent_self_model>");
  });

  it("preserves messages + tools + iteration untouched", async () => {
    const args = {
      ...baseArgs,
      messages: [{ role: "user" as const, content: [{ type: "text" as const, text: "hi" }] }],
      tools: [{ name: "FileRead", description: "read", input_schema: {} }],
    };
    const result = await agentSelfModelHook.handler(args, makeCtx());
    if (result?.action !== "replace") throw new Error("expected replace");
    expect(result.value.messages).toBe(args.messages);
    expect(result.value.tools).toBe(args.tools);
    expect(result.value.iteration).toBe(0);
  });

  it("skips injection on iteration > 0 (block already in system)", async () => {
    const args = { ...baseArgs, iteration: 3 };
    const result = await agentSelfModelHook.handler(args, makeCtx());
    expect(result).toEqual({ action: "continue" });
  });

  it("is idempotent — does not double-inject when block already present", async () => {
    const args = {
      ...baseArgs,
      system: `${AGENT_SELF_MODEL_BLOCK}\n\nyou are helpful`,
    };
    const result = await agentSelfModelHook.handler(args, makeCtx());
    expect(result).toEqual({ action: "continue" });
  });

  it("respects CORE_AGENT_SELF_MODEL=off", async () => {
    process.env.CORE_AGENT_SELF_MODEL = "off";
    const result = await agentSelfModelHook.handler(baseArgs, makeCtx());
    expect(result).toEqual({ action: "continue" });
  });

  it("treats 'on' / '1' / 'true' / '' as enabled", async () => {
    for (const v of ["on", "1", "true", ""]) {
      process.env.CORE_AGENT_SELF_MODEL = v;
      const result = await agentSelfModelHook.handler(baseArgs, makeCtx());
      expect(result?.action).toBe("replace");
    }
  });

  it("block mentions workspace, qmd/KB, transcript — the three tiers", () => {
    expect(AGENT_SELF_MODEL_BLOCK).toContain("workspace");
    expect(AGENT_SELF_MODEL_BLOCK).toMatch(/qmd|KB/);
    expect(AGENT_SELF_MODEL_BLOCK).toContain("transcript");
  });

  it("block includes the 'tools are your eyes' reflex", () => {
    expect(AGENT_SELF_MODEL_BLOCK).toContain("tools are your eyes");
    expect(AGENT_SELF_MODEL_BLOCK).toMatch(/before answering/i);
  });

  it("block enumerates refusal patterns", () => {
    expect(AGENT_SELF_MODEL_BLOCK).toContain("Not in KB");
    expect(AGENT_SELF_MODEL_BLOCK).toContain("I don't have");
  });
});
