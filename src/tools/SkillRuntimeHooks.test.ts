import { describe, expect, it } from "vitest";
import { HookRegistry } from "../hooks/HookRegistry.js";
import type { HookContext } from "../hooks/types.js";
import {
  normalizeSkillRuntimeHooks,
  registerSkillRuntimeHooks,
} from "./SkillRuntimeHooks.js";

function makeCtx(): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey: "session",
    turnId: "turn",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 1_000,
  };
}

describe("SkillRuntimeHooks", () => {
  it("normalizes valid runtime hook declarations", () => {
    const result = normalizeSkillRuntimeHooks("guarded-skill", [
      {
        name: "ask-bash",
        point: "beforeToolUse",
        if: "Bash(*)",
        decision: "ask",
        reason: "Confirm shell command.",
        priority: 45,
      },
    ]);

    expect(result.issues).toEqual([]);
    expect(result.hooks).toEqual([
      {
        skillName: "guarded-skill",
        name: "ask-bash",
        point: "beforeToolUse",
        if: "Bash(*)",
        action: "permission_decision",
        decision: "ask",
        reason: "Confirm shell command.",
        priority: 45,
        blocking: true,
        trustSource: "static",
      },
    ]);
  });

  it("rejects runtime hooks without an if rule", () => {
    const result = normalizeSkillRuntimeHooks("wide-skill", [
      { point: "beforeToolUse", decision: "deny" },
    ]);

    expect(result.hooks).toEqual([]);
    expect(result.issues[0]?.reason).toBe("`if` rule is required");
  });

  it("registers declarations as HookRegistry hooks", async () => {
    const registry = new HookRegistry();
    const { hooks } = normalizeSkillRuntimeHooks("guarded-skill", [
      {
        name: "deny-bash",
        point: "beforeToolUse",
        if: "Bash(*)",
        decision: "deny",
        reason: "Bash disabled by guarded-skill.",
      },
    ]);

    const count = registerSkillRuntimeHooks(registry, hooks);
    expect(count).toBe(1);
    expect(registry.list("beforeToolUse").map((h) => h.name)).toEqual([
      "skill:guarded-skill:deny-bash",
    ]);

    const denied = await registry.runPre(
      "beforeToolUse",
      { toolName: "Bash", toolUseId: "tu_1", input: { command: "date" } },
      makeCtx(),
    );
    expect(denied.action).toBe("block");
    if (denied.action === "block") {
      expect(denied.reason).toContain("[PERMISSION:DENY]");
    }

    const allowed = await registry.runPre(
      "beforeToolUse",
      { toolName: "FileRead", toolUseId: "tu_2", input: { path: "x" } },
      makeCtx(),
    );
    expect(allowed.action).toBe("continue");
  });
});
