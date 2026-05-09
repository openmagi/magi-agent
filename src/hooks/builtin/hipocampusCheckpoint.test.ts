import { describe, it, expect, vi } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { makeHipocampusCheckpointHook } from "./hipocampusCheckpoint.js";
import type { HookContext } from "../types.js";

function makeCtx(overrides: Partial<HookContext> = {}): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm: { stream: vi.fn() } as unknown as HookContext["llm"],
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
    ...overrides,
  };
}

describe("hipocampusCheckpoint", () => {
  it("skips long-term memory writes in incognito memory mode", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "hipocampus-checkpoint-"));
    try {
      const hook = makeHipocampusCheckpointHook(root);
      await hook.handler(
        {
          userMessage: "please analyze this",
          assistantText: "x".repeat(500),
          toolCallCount: 0,
          toolNames: [],
          filesChanged: [],
          startedAt: 1713800000000,
          endedAt: 1713800001000,
        },
        makeCtx({ memoryMode: "incognito" }),
      );

      await expect(fs.access(path.join(root, "memory"))).rejects.toBeTruthy();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("skips long-term memory writes in read-only memory mode", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "hipocampus-checkpoint-"));
    try {
      const hook = makeHipocampusCheckpointHook(root);
      await hook.handler(
        {
          userMessage: "please analyze this",
          assistantText: "x".repeat(500),
          toolCallCount: 0,
          toolNames: [],
          filesChanged: [],
          startedAt: 1713800000000,
          endedAt: 1713800001000,
        },
        makeCtx({ memoryMode: "read_only" }),
      );

      await expect(fs.access(path.join(root, "memory"))).rejects.toBeTruthy();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });
});
