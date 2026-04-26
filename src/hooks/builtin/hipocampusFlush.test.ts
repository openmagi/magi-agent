import { describe, it, expect, vi, beforeEach } from "vitest";
import { makeHipocampusFlushHook, flushMemory, type FlushDeps } from "./hipocampusFlush.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { HookContext } from "../types.js";

function makeTranscript(...turns: Array<{ turnId: string; user: string; assistant: string; ts?: number }>): TranscriptEntry[] {
  const entries: TranscriptEntry[] = [];
  for (const t of turns) {
    const ts = t.ts ?? Date.now();
    entries.push({ kind: "user_message", turnId: t.turnId, ts, text: t.user });
    entries.push({ kind: "assistant_text", turnId: t.turnId, ts: ts + 100, text: t.assistant });
  }
  return entries;
}

function makeDeps(): FlushDeps & { files: Map<string, string> } {
  const files = new Map<string, string>();
  return {
    files,
    readFile: vi.fn(async (p: string) => {
      const content = files.get(p);
      if (content === undefined) throw new Error("ENOENT");
      return content;
    }),
    writeFile: vi.fn(async (p: string, data: string) => {
      files.set(p, data);
    }),
    appendFile: vi.fn(async (p: string, data: string) => {
      files.set(p, (files.get(p) ?? "") + data);
    }),
    mkdir: vi.fn(async () => undefined),
  };
}

function makeCtx(overrides: Partial<HookContext> = {}): HookContext {
  return {
    botId: "test-bot",
    userId: "test-user",
    sessionKey: "test-session",
    turnId: "turn-1",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    abortSignal: new AbortController().signal,
    deadlineMs: 5000,
    ...overrides,
  };
}

describe("hipocampusFlush", () => {
  describe("flushMemory standalone", () => {
    it("flushes transcript turns to daily log", async () => {
      const deps = makeDeps();
      const transcript = makeTranscript(
        { turnId: "t1", user: "Hello", assistant: "Hi there", ts: 1713800000000 },
        { turnId: "t2", user: "How are you?", assistant: "I am well", ts: 1713800001000 },
      );

      const result = await flushMemory("/workspace", transcript, deps);

      expect(result.flushed).toBe(4); // 2 user + 2 assistant entries
      expect(result.lastTurnId).toBe("t2");

      // Check daily log was written
      const logKeys = [...deps.files.keys()].filter((k) => k.endsWith(".md") && !k.includes(".last-flushed"));
      expect(logKeys.length).toBe(1);
      const logContent = deps.files.get(logKeys[0])!;
      expect(logContent).toContain("**User:** Hello");
      expect(logContent).toContain("**Assistant:** Hi there");
      expect(logContent).toContain("**User:** How are you?");
      expect(logContent).toContain("**Assistant:** I am well");

      // Check marker was written
      const markerContent = deps.files.get("/workspace/memory/.last-flushed-turn");
      expect(markerContent).toBe("t2");
    });

    it("no-op when transcript is empty", async () => {
      const deps = makeDeps();
      const result = await flushMemory("/workspace", [], deps);

      expect(result.flushed).toBe(0);
      expect(result.lastTurnId).toBeNull();
      expect(deps.appendFile).not.toHaveBeenCalled();
    });

    it("appends to existing daily log", async () => {
      const deps = makeDeps();
      const logPath = "/workspace/memory/2024-04-22.md";
      deps.files.set(logPath, "## Existing entry\n\n---\n\n");

      const transcript = makeTranscript(
        { turnId: "t1", user: "New message", assistant: "New reply", ts: 1713800000000 },
      );

      await flushMemory("/workspace", transcript, deps);

      const logContent = deps.files.get(logPath);
      // Should still have the existing content since appendFile adds to it
      expect(logContent).toContain("## Existing entry");
      expect(logContent).toContain("**User:** New message");
    });

    it("does not duplicate on repeated flush (uses marker)", async () => {
      const deps = makeDeps();
      const transcript = makeTranscript(
        { turnId: "t1", user: "First", assistant: "Response 1", ts: 1713800000000 },
        { turnId: "t2", user: "Second", assistant: "Response 2", ts: 1713800001000 },
      );

      // First flush
      await flushMemory("/workspace", transcript, deps);
      expect(deps.appendFile).toHaveBeenCalledTimes(1);

      // Second flush with same transcript — marker says t2 already flushed
      const result2 = await flushMemory("/workspace", transcript, deps);
      expect(result2.flushed).toBe(0);
      expect(deps.appendFile).toHaveBeenCalledTimes(1); // no additional append
    });
  });

  describe("makeHipocampusFlushHook", () => {
    it("returns a registered hook with correct metadata", () => {
      const hook = makeHipocampusFlushHook("/workspace");
      expect(hook.name).toBe("builtin:hipocampus-flush");
      expect(hook.point).toBe("beforeCompaction");
      expect(hook.priority).toBe(1);
      expect(hook.blocking).toBe(true);
    });

    it("flushes via handler and logs result", async () => {
      const deps = makeDeps();
      const hook = makeHipocampusFlushHook("/workspace", deps);
      const ctx = makeCtx();
      const transcript = makeTranscript(
        { turnId: "t1", user: "Test", assistant: "Reply", ts: 1713800000000 },
      );

      await hook.handler({ transcript }, ctx);

      expect(ctx.log).toHaveBeenCalledWith("info", "hipocampus flush completed", expect.objectContaining({
        flushed: 2,
        lastTurnId: "t1",
      }));
    });

    it("logs warning on failure but does not throw", async () => {
      const deps = makeDeps();
      deps.mkdir = vi.fn(async () => { throw new Error("disk full"); });
      const hook = makeHipocampusFlushHook("/workspace", deps);
      const ctx = makeCtx();
      const transcript = makeTranscript(
        { turnId: "t1", user: "Test", assistant: "Reply", ts: 1713800000000 },
      );

      // Should not throw
      await hook.handler({ transcript }, ctx);

      expect(ctx.log).toHaveBeenCalledWith("warn", "hipocampus flush failed", expect.objectContaining({
        error: expect.stringContaining("disk full"),
      }));
    });
  });
});
