import { describe, it, expect, vi } from "vitest";
import { makeHipocampusCompactorHook, type CompactionEngine, type QmdManager } from "./hipocampusCompactor.js";
import type { HookContext } from "../types.js";

function makeCtx(sessionKey: string, overrides: Partial<HookContext> = {}): HookContext {
  return {
    botId: "test-bot",
    userId: "test-user",
    sessionKey,
    turnId: `turn-${Math.random().toString(36).slice(2, 8)}`,
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    abortSignal: new AbortController().signal,
    deadlineMs: 30000,
    ...overrides,
  };
}

function makeEngine(result: Partial<Awaited<ReturnType<CompactionEngine["run"]>>> = {}): CompactionEngine {
  return {
    run: vi.fn(async () => ({
      skipped: false,
      compacted: true,
      ...result,
    })),
  };
}

function makeQmd(): QmdManager {
  return {
    reindex: vi.fn(async () => {}),
  };
}

describe("hipocampusCompactor", () => {
  it("returns a registered hook with correct metadata", () => {
    const hook = makeHipocampusCompactorHook(makeEngine(), makeQmd());
    expect(hook.name).toBe("builtin:hipocampus-compactor");
    expect(hook.point).toBe("beforeTurnStart");
    expect(hook.priority).toBe(99);
    expect(hook.blocking).toBe(false);
  });

  it("runs compaction on first turn of session", async () => {
    const engine = makeEngine({ compacted: true });
    const qmd = makeQmd();
    const hook = makeHipocampusCompactorHook(engine, qmd);
    const ctx = makeCtx("session-1");

    await hook.handler({ userMessage: "hello" }, ctx);

    expect(engine.run).toHaveBeenCalledTimes(1);
    expect(qmd.reindex).toHaveBeenCalledTimes(1);
    expect(ctx.log).toHaveBeenCalledWith("info", "hipocampus compaction completed", expect.objectContaining({
      sessionKey: "session-1",
      compacted: true,
    }));
  });

  it("skips on subsequent turns of same session", async () => {
    const engine = makeEngine({ compacted: true });
    const qmd = makeQmd();
    const hook = makeHipocampusCompactorHook(engine, qmd);

    // First turn — should run
    const ctx1 = makeCtx("session-1");
    await hook.handler({ userMessage: "first" }, ctx1);
    expect(engine.run).toHaveBeenCalledTimes(1);

    // Second turn of same session — should skip
    const ctx2 = makeCtx("session-1");
    await hook.handler({ userMessage: "second" }, ctx2);
    expect(engine.run).toHaveBeenCalledTimes(1); // still 1
    expect(ctx2.log).toHaveBeenCalledWith("info", "hipocampus compactor: session already seen, skipping", expect.objectContaining({
      sessionKey: "session-1",
    }));
  });

  it("runs for different sessions", async () => {
    const engine = makeEngine({ compacted: true });
    const qmd = makeQmd();
    const hook = makeHipocampusCompactorHook(engine, qmd);

    await hook.handler({ userMessage: "hello" }, makeCtx("session-1"));
    await hook.handler({ userMessage: "hello" }, makeCtx("session-2"));

    expect(engine.run).toHaveBeenCalledTimes(2);
  });

  it("does not throw when compaction fails", async () => {
    const engine: CompactionEngine = {
      run: vi.fn(async () => { throw new Error("compaction engine exploded"); }),
    };
    const qmd = makeQmd();
    const hook = makeHipocampusCompactorHook(engine, qmd);
    const ctx = makeCtx("session-fail");

    // Should not throw
    await hook.handler({ userMessage: "hello" }, ctx);

    expect(ctx.log).toHaveBeenCalledWith("warn", "hipocampus compaction failed", expect.objectContaining({
      error: expect.stringContaining("compaction engine exploded"),
    }));
    expect(qmd.reindex).not.toHaveBeenCalled();
  });

  it("skips reindex when compaction was skipped (cooldown)", async () => {
    const engine = makeEngine({ skipped: true, compacted: false });
    const qmd = makeQmd();
    const hook = makeHipocampusCompactorHook(engine, qmd);
    const ctx = makeCtx("session-cooldown");

    await hook.handler({ userMessage: "hello" }, ctx);

    expect(engine.run).toHaveBeenCalledTimes(1);
    expect(qmd.reindex).not.toHaveBeenCalled();
    expect(ctx.log).toHaveBeenCalledWith("info", "hipocampus compaction completed", expect.objectContaining({
      skipped: true,
      compacted: false,
    }));
  });
});
