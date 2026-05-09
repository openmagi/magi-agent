/**
 * Unit tests for task lifecycle hooks (0.17.1).
 *
 * Covers:
 *   - classifyTaskShape KO + EN + ambiguous fallthrough
 *   - hook handler fail-open semantics
 *   - env-gate short-circuit (no IO when off)
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  _resetActivatedTurnsForTest,
  _resetTurnStartForTest,
  classifyTaskShape,
  makeTaskLifecycleHook,
} from "./taskLifecycle.js";
import type { HookContext } from "../types.js";
import type { LLMMessage } from "../../transport/LLMClient.js";

const origEnv = { ...process.env };

let root: string;

beforeEach(async () => {
  root = await fs.mkdtemp(path.join(os.tmpdir(), "tasklife-hook-"));
  _resetActivatedTurnsForTest();
  _resetTurnStartForTest();
  // 0.17.3: lifecycle hooks now short-circuit when the workspace lives
  // under an OS temp dir (so prod doesn't race with afterEach cleanup in
  // un-related suites). Tests that want to exercise the IO path must opt
  // in explicitly — this `on` override disables the test-workspace guard
  // inside makeTaskLifecycleHook.
  process.env.CORE_AGENT_TASK_LIFECYCLE = "on";
});

afterEach(async () => {
  await fs.rm(root, { recursive: true, force: true }).catch(() => undefined);
  process.env = { ...origEnv };
});

function makeCtx(overrides: Partial<HookContext> = {}): HookContext {
  return {
    botId: "bot-t",
    userId: "user-t",
    sessionKey: "sess-t",
    turnId: "turn-t1",
    llm: {} as never,
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
    ...overrides,
  };
}

describe("classifyTaskShape — heuristic", () => {
  it("classifies Korean imperative as task", () => {
    expect(classifyTaskShape("POS 매출 분석해줘")).toBe("task");
  });

  it("classifies English imperative as task", () => {
    expect(classifyTaskShape("Please analyze the sales report")).toBe("task");
  });

  it("classifies Korean question as question", () => {
    expect(classifyTaskShape("오늘 날씨가 어때?")).toBe("question");
  });

  it("classifies English question as question", () => {
    expect(classifyTaskShape("What is the capital of France?")).toBe("question");
  });

  it("returns ambiguous for mid-sentence verbs that are neither imperative nor interrogative", () => {
    expect(classifyTaskShape("I was thinking about the project today")).toBe(
      "ambiguous",
    );
  });

  it("returns chat for greetings / very short utterances", () => {
    expect(classifyTaskShape("안녕")).toBe("chat");
    expect(classifyTaskShape("hi")).toBe("chat");
  });
});

describe("task-lifecycle-detect hook", () => {
  it("appends a queue entry when the message classifies as task", async () => {
    const { detect } = makeTaskLifecycleHook({ workspaceRoot: root });
    const res = await detect.handler(
      { userMessage: "POS 매출 분석해줘" },
      makeCtx({ turnId: "t-detect" }),
    );
    expect(res).toEqual({ action: "continue" });
    const queue = await fs.readFile(path.join(root, "TASK-QUEUE.md"), "utf8");
    expect(queue).toContain("turnId=t-detect");
    expect(queue).toContain("POS 매출 분석해줘");
  });

  it("does NOT append for chat / question classifications", async () => {
    const { detect } = makeTaskLifecycleHook({ workspaceRoot: root });
    await detect.handler(
      { userMessage: "hi" },
      makeCtx({ turnId: "t-chat" }),
    );
    await detect.handler(
      { userMessage: "what is 2+2?" },
      makeCtx({ turnId: "t-q" }),
    );
    const exists = await fs
      .access(path.join(root, "TASK-QUEUE.md"))
      .then(() => true)
      .catch(() => false);
    expect(exists).toBe(false);
  });

  it("fail-open when disk write fails (points to a non-directory root)", async () => {
    const filePath = path.join(root, "not-a-dir.txt");
    await fs.writeFile(filePath, "x");
    const { detect } = makeTaskLifecycleHook({ workspaceRoot: filePath });
    const res = await detect.handler(
      { userMessage: "analyze this data now" },
      makeCtx({ turnId: "t-fail" }),
    );
    // Must not throw; returns continue.
    expect(res).toEqual({ action: "continue" });
  });
});

describe("task-lifecycle env gate", () => {
  it("performs no IO when CORE_AGENT_TASK_LIFECYCLE=off", async () => {
    process.env.CORE_AGENT_TASK_LIFECYCLE = "off";
    const { detect, activate, resolve } = makeTaskLifecycleHook({
      workspaceRoot: root,
    });
    const ctx = makeCtx({ turnId: "t-off" });

    await detect.handler({ userMessage: "analyze the data" }, ctx);
    const baseArgs = {
      messages: [] as LLMMessage[],
      tools: [],
      system: "",
      iteration: 1,
    };
    await activate.handler(baseArgs, ctx);
    await resolve.handler(
      {
        userMessage: "analyze the data",
        assistantText: "done",
        status: "committed",
      },
      ctx,
    );

    const files = await fs.readdir(root);
    expect(files).toHaveLength(0);
  });

  it("performs no IO when memoryMode is incognito", async () => {
    const { detect, activate, resolve } = makeTaskLifecycleHook({
      workspaceRoot: root,
    });
    const ctx = makeCtx({ turnId: "t-incognito", memoryMode: "incognito" });

    await detect.handler({ userMessage: "analyze the data" }, ctx);
    await activate.handler(
      {
        messages: [] as LLMMessage[],
        tools: [],
        system: "",
        iteration: 0,
      },
      ctx,
    );
    await resolve.handler(
      {
        userMessage: "analyze the data",
        assistantText: "done",
        status: "committed",
      },
      ctx,
    );

    const files = await fs.readdir(root);
    expect(files).toHaveLength(0);
  });

  it("performs no IO when memoryMode is read_only", async () => {
    const { detect, activate, resolve } = makeTaskLifecycleHook({
      workspaceRoot: root,
    });
    const ctx = makeCtx({ turnId: "t-read-only", memoryMode: "read_only" });

    await detect.handler({ userMessage: "analyze the data" }, ctx);
    await activate.handler(
      {
        messages: [] as LLMMessage[],
        tools: [],
        system: "",
        iteration: 0,
      },
      ctx,
    );
    await resolve.handler(
      {
        userMessage: "analyze the data",
        assistantText: "done",
        status: "committed",
      },
      ctx,
    );

    const files = await fs.readdir(root);
    expect(files).toHaveLength(0);
  });
});

describe("task-lifecycle-resolve hook", () => {
  it("skips daily log write for aborted turns", async () => {
    const { resolve } = makeTaskLifecycleHook({ workspaceRoot: root });
    const res = await resolve.handler(
      {
        userMessage: "analyze something",
        assistantText: "partial...",
        status: "aborted",
        reason: "client disconnect",
      },
      makeCtx({ turnId: "t-abort" }),
    );
    expect(res).toEqual({ action: "continue" });
    const memoryDir = path.join(root, "memory");
    const exists = await fs
      .access(memoryDir)
      .then(() => true)
      .catch(() => false);
    expect(exists).toBe(false);
  });
});
