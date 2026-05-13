import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import type { HookContext } from "../types.js";
import { makeFocusChainInjectorHook } from "./focusChainInjector.js";

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

const baseArgs = {
  messages: [],
  tools: [],
  system: "base system prompt",
  iteration: 0,
};

let tmpDir: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "focus-chain-injector-"));
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe("focusChainInjector", () => {
  it("injects <focus_chain> block when focus-chain.json exists", async () => {
    const coreAgentDir = path.join(tmpDir, ".core-agent");
    await fs.mkdir(coreAgentDir, { recursive: true });
    await fs.writeFile(
      path.join(coreAgentDir, "focus-chain.json"),
      JSON.stringify({
        currentStep: "3/7: FileEdit 적용 중",
        completedSteps: ["1/7: 요구사항 분석", "2/7: 테스트 작성"],
        pendingSteps: ["4/7: 린트 수정", "5/7: 빌드 확인", "6/7: 문서 업데이트", "7/7: PR 생성"],
        updatedAt: Date.now(),
      }),
    );

    const hook = makeFocusChainInjectorHook({ workspaceRoot: tmpDir });
    const result = await hook.handler(baseArgs, makeCtx());

    expect(result).toBeDefined();
    expect((result as { action: string }).action).toBe("replace");
    const replaced = result as { action: "replace"; value: typeof baseArgs };
    expect(replaced.value.system).toContain("<focus_chain>");
    expect(replaced.value.system).toContain("3/7");
    expect(replaced.value.system).toContain("FileEdit 적용 중");
    expect(replaced.value.system).toContain("요구사항 분석");
    expect(replaced.value.system).toContain("린트 수정");
    expect(replaced.value.system).toContain("</focus_chain>");
  });

  it("returns continue when focus-chain.json does not exist", async () => {
    const hook = makeFocusChainInjectorHook({ workspaceRoot: tmpDir });
    const result = await hook.handler(baseArgs, makeCtx());
    expect(result).toEqual({ action: "continue" });
  });

  it("skips when iteration > 0", async () => {
    const coreAgentDir = path.join(tmpDir, ".core-agent");
    await fs.mkdir(coreAgentDir, { recursive: true });
    await fs.writeFile(
      path.join(coreAgentDir, "focus-chain.json"),
      JSON.stringify({
        currentStep: "1/3: test",
        completedSteps: [],
        pendingSteps: ["2/3: b", "3/3: c"],
        updatedAt: Date.now(),
      }),
    );

    const hook = makeFocusChainInjectorHook({ workspaceRoot: tmpDir });
    const result = await hook.handler(
      { ...baseArgs, iteration: 1 },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("returns continue on invalid JSON in file", async () => {
    const coreAgentDir = path.join(tmpDir, ".core-agent");
    await fs.mkdir(coreAgentDir, { recursive: true });
    await fs.writeFile(
      path.join(coreAgentDir, "focus-chain.json"),
      "this is not json{{{",
    );

    const hook = makeFocusChainInjectorHook({ workspaceRoot: tmpDir });
    const ctx = makeCtx();
    const result = await hook.handler(baseArgs, ctx);
    expect(result).toEqual({ action: "continue" });
  });

  it("handles blockers in the injected block", async () => {
    const coreAgentDir = path.join(tmpDir, ".core-agent");
    await fs.mkdir(coreAgentDir, { recursive: true });
    await fs.writeFile(
      path.join(coreAgentDir, "focus-chain.json"),
      JSON.stringify({
        currentStep: "2/4: 빌드 중",
        completedSteps: ["1/4: 분석"],
        pendingSteps: ["3/4: 테스트", "4/4: 배포"],
        blockers: ["TS 에러 3개", "린트 실패"],
        updatedAt: Date.now(),
      }),
    );

    const hook = makeFocusChainInjectorHook({ workspaceRoot: tmpDir });
    const result = await hook.handler(baseArgs, makeCtx());
    const replaced = result as { action: "replace"; value: typeof baseArgs };
    expect(replaced.value.system).toContain("Blockers:");
    expect(replaced.value.system).toContain("TS 에러 3개");
  });

  it("focus-chain.json on disk survives simulated compaction (file-based state independence)", async () => {
    const coreAgentDir = path.join(tmpDir, ".core-agent");
    await fs.mkdir(coreAgentDir, { recursive: true });
    const focusData = {
      currentStep: "5/7: 빌드 확인",
      completedSteps: ["1/7: a", "2/7: b", "3/7: c", "4/7: d"],
      pendingSteps: ["6/7: e", "7/7: f"],
      updatedAt: Date.now(),
    };
    await fs.writeFile(
      path.join(coreAgentDir, "focus-chain.json"),
      JSON.stringify(focusData),
    );

    // Simulate compaction: new hook instance, new context, fresh messages
    const hook = makeFocusChainInjectorHook({ workspaceRoot: tmpDir });
    const compactedArgs = {
      messages: [{ role: "user" as const, content: "compacted context" }],
      tools: [],
      system: "post-compaction system prompt",
      iteration: 0,
    };
    const result = await hook.handler(compactedArgs, makeCtx({ turnId: "new-turn" }));
    const replaced = result as { action: "replace"; value: typeof compactedArgs };
    expect(replaced.action).toBe("replace");
    expect(replaced.value.system).toContain("5/7");
    expect(replaced.value.system).toContain("빌드 확인");
  });

  it("has correct hook metadata", () => {
    const hook = makeFocusChainInjectorHook({ workspaceRoot: tmpDir });
    expect(hook.name).toBe("builtin:focus-chain-injector");
    expect(hook.point).toBe("beforeLLMCall");
    expect(hook.priority).toBe(10);
    expect(hook.blocking).toBe(false);
  });
});
