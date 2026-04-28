/**
 * autoApproval unit tests — T2-08.
 *
 * Covers:
 *  - mode=default → hook is a no-op (returns continue)
 *  - mode=auto + dangerous tool → permission_decision=ask
 *  - mode=auto + safe tool → permission_decision=approve
 *  - mode=auto + unknown tool → permission_decision=ask (fail-safe)
 *  - mode=plan / mode=bypass → hook is a no-op (continue)
 */

import { describe, it, expect } from "vitest";
import {
  makeAutoApprovalHook,
  type AutoApprovalAgent,
} from "./autoApproval.js";
import type { HookContext } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import type { AgentEvent } from "../../transport/SseWriter.js";
import type { Tool } from "../../Tool.js";
import type { PermissionMode } from "../../Session.js";

function makeCtx(sessionKey: string): {
  ctx: HookContext;
  emitted: AgentEvent[];
  logs: Array<{ level: string; msg: string; data?: object }>;
} {
  const emitted: AgentEvent[] = [];
  const logs: Array<{ level: string; msg: string; data?: object }> = [];
  const ctx: HookContext = {
    botId: "bot-test",
    userId: "user-test",
    sessionKey,
    turnId: "turn-test",
    llm: {} as unknown as LLMClient,
    transcript: [],
    emit: (e) => emitted.push(e),
    log: (level, msg, data) => logs.push({ level, msg, data }),
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
  };
  return { ctx, emitted, logs };
}

function makeTool(name: string, dangerous: boolean): Tool {
  return {
    name,
    description: `${name} test tool`,
    inputSchema: { type: "object" as const, properties: {} },
    permission: dangerous ? "execute" : "read",
    dangerous,
    execute: async () => ({ status: "ok" as const, durationMs: 1 }),
  } as Tool;
}

function makeAgentDelegate(
  mode: PermissionMode,
  tools: Record<string, Tool>,
): AutoApprovalAgent {
  return {
    getSessionPermissionMode: () => mode,
    resolveTool: (name: string) => tools[name] ?? null,
  };
}

describe("auto-approval hook (T2-08)", () => {
  it("is a no-op when mode=default", async () => {
    const hook = makeAutoApprovalHook({
      agent: makeAgentDelegate("default", {
        FileRead: makeTool("FileRead", false),
      }),
    });
    const { ctx } = makeCtx("s1");
    const result = await hook.handler(
      { toolName: "FileRead", toolUseId: "t1", input: {} },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("mode=default + dangerous tool → permission_decision ask", async () => {
    const hook = makeAutoApprovalHook({
      agent: makeAgentDelegate("default", {
        Bash: makeTool("Bash", true),
      }),
    });
    const { ctx } = makeCtx("s1");
    const result = await hook.handler(
      { toolName: "Bash", toolUseId: "t1", input: {} },
      ctx,
    );
    expect(result).toMatchObject({
      action: "permission_decision",
      decision: "ask",
    });
  });

  it("mode=auto + dangerous tool → permission_decision ask", async () => {
    const hook = makeAutoApprovalHook({
      agent: makeAgentDelegate("auto", {
        Bash: makeTool("Bash", true),
      }),
    });
    const { ctx } = makeCtx("s1");
    const result = await hook.handler(
      { toolName: "Bash", toolUseId: "t1", input: {} },
      ctx,
    );
    expect(result).toMatchObject({
      action: "permission_decision",
      decision: "ask",
    });
  });

  it("mode=auto + safe tool → permission_decision approve", async () => {
    const hook = makeAutoApprovalHook({
      agent: makeAgentDelegate("auto", {
        FileRead: makeTool("FileRead", false),
      }),
    });
    const { ctx } = makeCtx("s1");
    const result = await hook.handler(
      { toolName: "FileRead", toolUseId: "t1", input: {} },
      ctx,
    );
    expect(result).toMatchObject({
      action: "permission_decision",
      decision: "approve",
    });
  });

  it("mode=auto + unknown tool → permission_decision ask (fail-safe)", async () => {
    const hook = makeAutoApprovalHook({
      agent: makeAgentDelegate("auto", {}),
    });
    const { ctx } = makeCtx("s1");
    const result = await hook.handler(
      { toolName: "MysteryTool", toolUseId: "t1", input: {} },
      ctx,
    );
    expect(result).toMatchObject({
      action: "permission_decision",
      decision: "ask",
    });
  });

  it("mode=plan → no-op (plan-mode tool filter handles access)", async () => {
    const hook = makeAutoApprovalHook({
      agent: makeAgentDelegate("plan", {
        FileRead: makeTool("FileRead", false),
      }),
    });
    const { ctx } = makeCtx("s1");
    const result = await hook.handler(
      { toolName: "FileRead", toolUseId: "t1", input: {} },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("mode=bypass → no-op (Turn skips beforeToolUse chain entirely)", async () => {
    const hook = makeAutoApprovalHook({
      agent: makeAgentDelegate("bypass", {
        Bash: makeTool("Bash", true),
      }),
    });
    const { ctx } = makeCtx("s1");
    const result = await hook.handler(
      { toolName: "Bash", toolUseId: "t1", input: {} },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("treats dangerous=undefined as safe (approve)", async () => {
    const implicitSafe: Tool = {
      name: "TaskBoard",
      description: "no dangerous field",
      inputSchema: { type: "object" as const, properties: {} },
      permission: "read",
      execute: async () => ({ status: "ok" as const, durationMs: 1 }),
    } as Tool;
    const hook = makeAutoApprovalHook({
      agent: makeAgentDelegate("auto", { TaskBoard: implicitSafe }),
    });
    const { ctx } = makeCtx("s1");
    const result = await hook.handler(
      { toolName: "TaskBoard", toolUseId: "t1", input: {} },
      ctx,
    );
    expect(result).toMatchObject({
      action: "permission_decision",
      decision: "approve",
    });
  });
});
