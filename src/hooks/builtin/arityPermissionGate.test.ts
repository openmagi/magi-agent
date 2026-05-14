import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  makeArityPermissionGateHook,
  isArityPermissionEnabled,
  resolveArityRulesFromConfig,
} from "./arityPermissionGate.js";
import { HookContext } from "../types.js";

function makeCtx(overrides?: Partial<HookContext>): HookContext {
  return {
    botId: "test-bot",
    userId: "test-user",
    sessionKey: "test-session",
    turnId: "test-turn",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    agentModel: "claude-opus-4-6",
    classifierModel: "claude-haiku-4-5-20251001",
    abortSignal: new AbortController().signal,
    deadlineMs: 3000,
    ...overrides,
  } as HookContext;
}

describe("isArityPermissionEnabled", () => {
  const orig = process.env.MAGI_ARITY_PERMISSION;
  afterEach(() => {
    if (orig === undefined) delete process.env.MAGI_ARITY_PERMISSION;
    else process.env.MAGI_ARITY_PERMISSION = orig;
  });

  it("returns false when env is unset (default off)", () => {
    delete process.env.MAGI_ARITY_PERMISSION;
    expect(isArityPermissionEnabled()).toBe(false);
  });

  it("returns true when env is 'on'", () => {
    process.env.MAGI_ARITY_PERMISSION = "on";
    expect(isArityPermissionEnabled()).toBe(true);
  });

  it("returns true when env is '1'", () => {
    process.env.MAGI_ARITY_PERMISSION = "1";
    expect(isArityPermissionEnabled()).toBe(true);
  });

  it("returns false when env is 'off'", () => {
    process.env.MAGI_ARITY_PERMISSION = "off";
    expect(isArityPermissionEnabled()).toBe(false);
  });
});

describe("resolveArityRulesFromConfig", () => {
  it("returns null when config is null", () => {
    expect(resolveArityRulesFromConfig(null)).toBeNull();
  });

  it("returns null when arity_rules key is absent", () => {
    expect(resolveArityRulesFromConfig({ other: "value" })).toBeNull();
  });

  it("parses valid rules", () => {
    const config = {
      arity_rules: [
        { pattern: "git push *", action: "ask" },
        { pattern: "sudo *", action: "deny" },
      ],
    };
    const rules = resolveArityRulesFromConfig(config);
    expect(rules).toHaveLength(2);
    expect(rules?.[0]).toEqual({ pattern: "git push *", action: "ask" });
    expect(rules?.[1]).toEqual({ pattern: "sudo *", action: "deny" });
  });

  it("defaults action to ask when invalid", () => {
    const config = {
      arity_rules: [{ pattern: "rm *", action: "invalid" }],
    };
    const rules = resolveArityRulesFromConfig(config);
    expect(rules?.[0]?.action).toBe("ask");
  });

  it("skips entries without pattern", () => {
    const config = {
      arity_rules: [
        { action: "deny" },
        { pattern: "rm *", action: "ask" },
      ],
    };
    const rules = resolveArityRulesFromConfig(config);
    expect(rules).toHaveLength(1);
  });
});

describe("arityPermissionGate hook", () => {
  const orig = process.env.MAGI_ARITY_PERMISSION;

  beforeEach(() => {
    process.env.MAGI_ARITY_PERMISSION = "on";
  });

  afterEach(() => {
    if (orig === undefined) delete process.env.MAGI_ARITY_PERMISSION;
    else process.env.MAGI_ARITY_PERMISSION = orig;
  });

  it("has correct metadata", () => {
    const hook = makeArityPermissionGateHook({ workspaceRoot: "/tmp/test" });
    expect(hook.name).toBe("builtin:arity-permission-gate");
    expect(hook.point).toBe("beforeToolUse");
    expect(hook.priority).toBe(38);
    expect(hook.blocking).toBe(true);
  });

  it("continues when env is off", async () => {
    process.env.MAGI_ARITY_PERMISSION = "off";
    const hook = makeArityPermissionGateHook({ workspaceRoot: "/tmp/test" });
    const ctx = makeCtx();
    const result = await hook.handler(
      { toolName: "Bash", input: { command: "sudo rm -rf /" } },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("continues for non-Bash tools", async () => {
    const hook = makeArityPermissionGateHook({ workspaceRoot: "/tmp/test" });
    const ctx = makeCtx();
    const result = await hook.handler(
      { toolName: "FileWrite", input: { path: "/etc/passwd" } },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("continues for safe commands (no config file)", async () => {
    const hook = makeArityPermissionGateHook({ workspaceRoot: "/tmp/nonexistent-" + Date.now() });
    const ctx = makeCtx();
    const result = await hook.handler(
      { toolName: "Bash", input: { command: "echo hello" } },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("returns ask for git push (no config file = defaults)", async () => {
    const hook = makeArityPermissionGateHook({ workspaceRoot: "/tmp/nonexistent-" + Date.now() });
    const ctx = makeCtx();
    const result = await hook.handler(
      { toolName: "Bash", input: { command: "git push origin main" } },
      ctx,
    );
    expect(result).toEqual(
      expect.objectContaining({
        action: "permission_decision",
        decision: "ask",
      }),
    );
  });

  it("returns deny for sudo (no config file = defaults)", async () => {
    const hook = makeArityPermissionGateHook({ workspaceRoot: "/tmp/nonexistent-" + Date.now() });
    const ctx = makeCtx();
    const result = await hook.handler(
      { toolName: "Bash", input: { command: "sudo apt install vim" } },
      ctx,
    );
    expect(result).toEqual(
      expect.objectContaining({
        action: "permission_decision",
        decision: "deny",
      }),
    );
  });

  it("continues for empty command", async () => {
    const hook = makeArityPermissionGateHook({ workspaceRoot: "/tmp/nonexistent-" + Date.now() });
    const ctx = makeCtx();
    const result = await hook.handler(
      { toolName: "Bash", input: { command: "" } },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("continues when input is not an object", async () => {
    const hook = makeArityPermissionGateHook({ workspaceRoot: "/tmp/nonexistent-" + Date.now() });
    const ctx = makeCtx();
    const result = await hook.handler(
      { toolName: "Bash", input: "not an object" },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("emits rule_check event on non-allow result", async () => {
    const hook = makeArityPermissionGateHook({ workspaceRoot: "/tmp/nonexistent-" + Date.now() });
    const ctx = makeCtx();
    await hook.handler(
      { toolName: "Bash", input: { command: "git push origin main" } },
      ctx,
    );
    expect(ctx.emit).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "rule_check",
        ruleId: "arity-permission-gate",
      }),
    );
  });
});
