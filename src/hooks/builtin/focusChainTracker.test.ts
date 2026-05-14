import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import type { HookContext } from "../types.js";
import { makeFocusChainTrackerHook } from "./focusChainTracker.js";

function makeCtx(overrides: Partial<HookContext> = {}): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm: {} as never,
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    agentModel: "claude-opus-4-6",
    classifierModel: "claude-haiku-4-5-20251001",
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
    ...overrides,
  };
}

let tmpDir: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "focus-chain-tracker-"));
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe("focusChainTracker", () => {
  it("writes focus-chain.json when task_progress is present in tool input", async () => {
    const hook = makeFocusChainTrackerHook({ workspaceRoot: tmpDir });
    const input = {
      path: "/some/file.ts",
      task_progress: {
        currentStep: "3/7: FileEdit 적용 중",
        completedSteps: ["1/7: 요구사항 분석", "2/7: 테스트 작성"],
        pendingSteps: ["4/7: 린트 수정", "5/7: 빌드 확인"],
      },
    };

    await hook.handler(
      {
        toolName: "FileEdit",
        toolUseId: "tu-1",
        input,
        result: { status: "success", content: "ok" },
      },
      makeCtx(),
    );

    const filePath = path.join(tmpDir, ".core-agent", "focus-chain.json");
    const raw = await fs.readFile(filePath, "utf-8");
    const data = JSON.parse(raw);
    expect(data.currentStep).toBe("3/7: FileEdit 적용 중");
    expect(data.completedSteps).toEqual(["1/7: 요구사항 분석", "2/7: 테스트 작성"]);
    expect(data.pendingSteps).toEqual(["4/7: 린트 수정", "5/7: 빌드 확인"]);
    expect(data.updatedAt).toBeDefined();
  });

  it("does not write when task_progress is absent — existing state preserved", async () => {
    const coreAgentDir = path.join(tmpDir, ".core-agent");
    await fs.mkdir(coreAgentDir, { recursive: true });
    const filePath = path.join(coreAgentDir, "focus-chain.json");
    const existing = JSON.stringify({ currentStep: "1/3: existing" });
    await fs.writeFile(filePath, existing);

    const hook = makeFocusChainTrackerHook({ workspaceRoot: tmpDir });
    await hook.handler(
      {
        toolName: "FileRead",
        toolUseId: "tu-2",
        input: { path: "/some/file.ts" },
        result: { status: "success", content: "ok" },
      },
      makeCtx(),
    );

    const raw = await fs.readFile(filePath, "utf-8");
    expect(raw).toBe(existing);
  });

  it("gracefully skips on malformed task_progress (not an object)", async () => {
    const hook = makeFocusChainTrackerHook({ workspaceRoot: tmpDir });
    const ctx = makeCtx();

    await hook.handler(
      {
        toolName: "Bash",
        toolUseId: "tu-3",
        input: { task_progress: "not-an-object" },
        result: { status: "success", content: "ok" },
      },
      ctx,
    );

    const filePath = path.join(tmpDir, ".core-agent", "focus-chain.json");
    await expect(fs.access(filePath)).rejects.toThrow();
  });

  it("gracefully skips when task_progress is missing currentStep", async () => {
    const hook = makeFocusChainTrackerHook({ workspaceRoot: tmpDir });
    const ctx = makeCtx();

    await hook.handler(
      {
        toolName: "Bash",
        toolUseId: "tu-4",
        input: {
          task_progress: {
            completedSteps: ["1/2: done"],
            pendingSteps: [],
          },
        },
        result: { status: "success", content: "ok" },
      },
      ctx,
    );

    const filePath = path.join(tmpDir, ".core-agent", "focus-chain.json");
    await expect(fs.access(filePath)).rejects.toThrow();
  });

  it("persists blockers when provided", async () => {
    const hook = makeFocusChainTrackerHook({ workspaceRoot: tmpDir });
    await hook.handler(
      {
        toolName: "Bash",
        toolUseId: "tu-5",
        input: {
          task_progress: {
            currentStep: "2/4: 빌드 중",
            completedSteps: ["1/4: 분석"],
            pendingSteps: ["3/4: 테스트"],
            blockers: ["TypeScript 에러 3개"],
          },
        },
        result: { status: "success", content: "ok" },
      },
      makeCtx(),
    );

    const filePath = path.join(tmpDir, ".core-agent", "focus-chain.json");
    const data = JSON.parse(await fs.readFile(filePath, "utf-8"));
    expect(data.blockers).toEqual(["TypeScript 에러 3개"]);
  });

  it("has correct hook metadata", () => {
    const hook = makeFocusChainTrackerHook({ workspaceRoot: tmpDir });
    expect(hook.name).toBe("builtin:focus-chain-tracker");
    expect(hook.point).toBe("afterToolUse");
    expect(hook.priority).toBe(91);
    expect(hook.blocking).toBe(false);
    expect(hook.timeoutMs).toBe(3_000);
  });
});
