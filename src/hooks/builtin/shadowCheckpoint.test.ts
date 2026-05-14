import { describe, it, expect, vi, beforeEach } from "vitest";
import { makeShadowCheckpointHook } from "./shadowCheckpoint.js";
import type { HookContext } from "../types.js";
import type { ToolResult } from "../../Tool.js";

function makeCtx(overrides?: Partial<HookContext>): HookContext {
  return {
    botId: "bot-1",
    userId: "user-1",
    sessionKey: "s-1",
    turnId: "t-1",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    agentModel: "claude-opus-4-7",
    abortSignal: new AbortController().signal,
    deadlineMs: 5000,
    ...overrides,
  };
}

function makeResult(status: string): ToolResult {
  return { status: status as ToolResult["status"], durationMs: 10 };
}

describe("shadowCheckpoint hook", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
  });

  it("is named builtin:shadow-checkpoint", () => {
    const hook = makeShadowCheckpointHook({
      workspaceRoot: "/tmp/test",
      enabled: false,
    });
    expect(hook.name).toBe("builtin:shadow-checkpoint");
    expect(hook.point).toBe("afterToolUse");
    expect(hook.priority).toBe(90);
    expect(hook.blocking).toBe(false);
  });

  it("skips when not enabled", async () => {
    const hook = makeShadowCheckpointHook({
      workspaceRoot: "/tmp/test",
      enabled: false,
    });
    const ctx = makeCtx();

    await hook.handler(
      {
        toolName: "FileEdit",
        toolUseId: "tu-1",
        input: { path: "test.txt" },
        result: makeResult("ok"),
      },
      ctx,
    );

    // No log calls means no checkpoint attempt
    expect(ctx.log).not.toHaveBeenCalled();
  });

  it("skips read-only tools (FileRead)", async () => {
    const hook = makeShadowCheckpointHook({
      workspaceRoot: "/tmp/test",
      enabled: true,
    });
    const ctx = makeCtx();

    await hook.handler(
      {
        toolName: "FileRead",
        toolUseId: "tu-1",
        input: {},
        result: makeResult("ok"),
      },
      ctx,
    );

    expect(ctx.log).not.toHaveBeenCalled();
  });

  it("skips read-only tools (Glob)", async () => {
    const hook = makeShadowCheckpointHook({
      workspaceRoot: "/tmp/test",
      enabled: true,
    });
    const ctx = makeCtx();

    await hook.handler(
      {
        toolName: "Glob",
        toolUseId: "tu-1",
        input: {},
        result: makeResult("ok"),
      },
      ctx,
    );

    expect(ctx.log).not.toHaveBeenCalled();
  });

  it("skips read-only tools (Grep)", async () => {
    const hook = makeShadowCheckpointHook({
      workspaceRoot: "/tmp/test",
      enabled: true,
    });
    const ctx = makeCtx();

    await hook.handler(
      {
        toolName: "Grep",
        toolUseId: "tu-1",
        input: {},
        result: makeResult("ok"),
      },
      ctx,
    );

    expect(ctx.log).not.toHaveBeenCalled();
  });

  it("skips read-only tools (Browser)", async () => {
    const hook = makeShadowCheckpointHook({
      workspaceRoot: "/tmp/test",
      enabled: true,
    });
    const ctx = makeCtx();

    await hook.handler(
      {
        toolName: "Browser",
        toolUseId: "tu-1",
        input: {},
        result: makeResult("ok"),
      },
      ctx,
    );

    expect(ctx.log).not.toHaveBeenCalled();
  });

  it("skips on tool error result", async () => {
    const hook = makeShadowCheckpointHook({
      workspaceRoot: "/tmp/test",
      enabled: true,
    });
    const ctx = makeCtx();

    await hook.handler(
      {
        toolName: "FileEdit",
        toolUseId: "tu-1",
        input: { path: "test.txt" },
        result: makeResult("error"),
      },
      ctx,
    );

    expect(ctx.log).not.toHaveBeenCalled();
  });

  it("respects MAGI_CHECKPOINT env var", () => {
    vi.stubEnv("MAGI_CHECKPOINT", "1");
    const hook = makeShadowCheckpointHook({
      workspaceRoot: "/tmp/test",
    });
    // We can't easily test that it's enabled without a real workspace,
    // but the hook should be constructed without errors
    expect(hook.name).toBe("builtin:shadow-checkpoint");
  });

  it("has 5s timeout", () => {
    const hook = makeShadowCheckpointHook({
      workspaceRoot: "/tmp/test",
      enabled: false,
    });
    expect(hook.timeoutMs).toBe(5_000);
  });
});
