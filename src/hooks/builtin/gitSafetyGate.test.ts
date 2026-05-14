import { describe, expect, it } from "vitest";
import type { HookContext } from "../types.js";
import { makeGitSafetyGateHook } from "./gitSafetyGate.js";

function hookContext(): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey: "session",
    turnId: "turn-1",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5000,
  };
}

describe("git safety gate", () => {
  it("blocks destructive git reset commands before Bash execution", async () => {
    const hook = makeGitSafetyGateHook();

    const out = await hook.handler(
      {
        toolName: "Bash",
        toolUseId: "bash-1",
        input: { command: "git reset --hard HEAD~1" },
      },
      hookContext(),
    );

    expect(out).toEqual({
      action: "block",
      reason: expect.stringContaining("git reset --hard"),
    });
  });

  it("allows read-only git inspection commands", async () => {
    const hook = makeGitSafetyGateHook();

    const out = await hook.handler(
      {
        toolName: "Bash",
        toolUseId: "bash-1",
        input: { command: "git status --short" },
      },
      hookContext(),
    );

    expect(out).toEqual({ action: "continue" });
  });
});
