/**
 * Unit tests for the plan-mode auto-trigger beforeLLMCall hook.
 * Design ref: docs/plans/2026-04-20-superpowers-plugin-design.md design #1.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  isAutoTriggerEnabled,
  makePlanModeAutoTriggerHook,
  matchesImplementationIntent,
  type PlanModeAutoTriggerAgent,
} from "./planModeAutoTrigger.js";
import type { HookContext } from "../types.js";
import type { LLMMessage } from "../../transport/LLMClient.js";
import type { PermissionMode } from "../../Session.js";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";

function llmThatAnswers(
  answer: string,
  planningNeed:
    | "none"
    | "inline"
    | "task_board"
    | "approval_plan"
    | "pipeline_or_bulk" = answer === "YES" ? "approval_plan" : "none",
): HookContext["llm"] {
  return {
    stream: vi.fn(async function* (request: { system?: string }) {
      if (String(request.system ?? "").includes("runtime-control classifier")) {
        yield {
          kind: "text_delta" as const,
          delta: JSON.stringify({
            turnMode: {
              label: answer === "YES" ? "coding" : "other",
              confidence: 0.9,
            },
            skipTdd: false,
            implementationIntent: answer === "YES",
            documentOrFileOperation: false,
            documentExport: { strategy: "none", confidence: 0, renderParityRequired: false, nativeTemplateRequired: false, docxMode: null, reason: "No document export routing requested." },
            deterministic: {
              requiresDeterministic: false,
              kinds: [],
              reason: "No deterministic requirement.",
              suggestedTools: [],
              acceptanceCriteria: [],
            },
            fileDelivery: {
              intent: "none",
              path: null,
              wantsChatDelivery: false,
              wantsKbDelivery: false,
              wantsFileOutput: false,
            },
            planning: {
              need: planningNeed,
              reason:
                planningNeed === "none"
                  ? "No planning needed."
                  : "The request has enough scope to need runtime planning.",
              suggestedStrategy:
                planningNeed === "task_board"
                  ? "Create a TaskBoard checklist before execution."
                  : planningNeed === "approval_plan"
                    ? "Enter plan mode and submit a plan before execution."
                    : "Answer directly.",
            },
          }),
        };
        return;
      }
      yield { kind: "text_delta" as const, delta: answer };
    }),
  } as unknown as HookContext["llm"];
}

function makeCtx(
  sessionKey = "s1",
  classifierAnswer = "NO",
  planningNeed?: Parameters<typeof llmThatAnswers>[1],
): HookContext {
  const store = new ExecutionContractStore({ now: () => 1 });
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey,
    turnId: "turn-1",
    llm: llmThatAnswers(classifierAnswer, planningNeed),
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
    executionContract: store,
  };
}

function buildArgs(
  text: string,
  iteration = 0,
): {
  messages: LLMMessage[];
  tools: Array<{ name: string; description: string; input_schema: Record<string, unknown> }>;
  system: string;
  iteration: number;
} {
  return {
    messages: [
      {
        role: "user",
        content: [{ type: "text", text }],
      } as LLMMessage,
    ],
    tools: [],
    system: "you are a bot",
    iteration,
  };
}

function buildArgsWithTools(text: string) {
  return {
    ...buildArgs(text),
    tools: [
      { name: "Bash", description: "run shell", input_schema: { type: "object" } },
      { name: "FileWrite", description: "write file", input_schema: { type: "object" } },
      { name: "TaskBoard", description: "task board", input_schema: { type: "object" } },
      { name: "ExitPlanMode", description: "exit plan mode", input_schema: { type: "object" } },
      { name: "AskUserQuestion", description: "ask user", input_schema: { type: "object" } },
    ],
  };
}

function agentWith(mode: PermissionMode | null): PlanModeAutoTriggerAgent {
  return {
    getSessionPermissionMode: () => mode,
  };
}

describe("matchesImplementationIntent", () => {
  it("uses the classifier response for implementation intent", async () => {
    await expect(
      matchesImplementationIntent(
        "implement an endpoint for webhooks",
        makeCtx("s1", "YES"),
      ),
    ).resolves.toBe(true);
    await expect(
      matchesImplementationIntent("Build a new API route", makeCtx("s1", "YES")),
    ).resolves.toBe(true);
    await expect(
      matchesImplementationIntent(
        "Refactor the billing service",
        makeCtx("s1", "YES"),
      ),
    ).resolves.toBe(true);
  });

  it("returns false for non-yes classifier responses", async () => {
    await expect(
      matchesImplementationIntent("what's the weather today?", makeCtx("s1", "NO")),
    ).resolves.toBe(false);
    await expect(
      matchesImplementationIntent("hello world", makeCtx("s1", "maybe")),
    ).resolves.toBe(false);
    await expect(matchesImplementationIntent("", makeCtx("s1", "YES"))).resolves.toBe(
      false,
    );
  });
});

describe("isAutoTriggerEnabled", () => {
  it("defaults on when env unset", () => {
    expect(isAutoTriggerEnabled(undefined)).toBe(true);
  });
  it("off when explicitly disabled", () => {
    expect(isAutoTriggerEnabled("off")).toBe(false);
    expect(isAutoTriggerEnabled("false")).toBe(false);
    expect(isAutoTriggerEnabled("0")).toBe(false);
  });
});

describe("makePlanModeAutoTriggerHook", () => {
  const prevEnv = process.env.MAGI_PLAN_AUTOTRIGGER;
  beforeEach(() => {
    delete process.env.MAGI_PLAN_AUTOTRIGGER;
  });
  afterEach(() => {
    if (prevEnv === undefined) delete process.env.MAGI_PLAN_AUTOTRIGGER;
    else process.env.MAGI_PLAN_AUTOTRIGGER = prevEnv;
  });

  it("declares name, point, priority, non-blocking", () => {
    const hook = makePlanModeAutoTriggerHook({ agent: agentWith("default") });
    expect(hook.name).toBe("builtin:plan-mode-auto-trigger");
    expect(hook.point).toBe("beforeLLMCall");
    expect(hook.priority).toBe(8);
    expect(hook.blocking).toBe(false);
  });

  it("nudges when message has implementation intent", async () => {
    const hook = makePlanModeAutoTriggerHook({ agent: agentWith("default") });
    const result = await hook.handler(
      buildArgs("please implement a new hook for invoicing"),
      makeCtx("s1", "YES", "approval_plan"),
    );
    expect(result?.action).toBe("replace");
    if (result?.action !== "replace") throw new Error("expected replace");
    expect(result.value.system).toContain("planning_policy");
    expect(result.value.system).toContain("ExitPlanMode");
    expect(result.value.system).toContain("you are a bot"); // preserved
  });

  it("adds TaskBoard guidance when request meta asks for task_board planning", async () => {
    const hook = makePlanModeAutoTriggerHook({ agent: agentWith("default") });
    const result = await hook.handler(
      buildArgs("리서치하고 보고서로 정리해서 파일까지 만들어줘"),
      makeCtx("s1", "NO", "task_board"),
    );

    expect(result?.action).toBe("replace");
    if (result?.action !== "replace") throw new Error("expected replace");
    expect(result.value.system).toContain("planning_policy");
    expect(result.value.system).toContain("TaskBoard");
    expect(result.value.system).not.toContain("EnterPlanMode before using write");
  });

  it("enters plan mode and filters exposed tools for approval_plan planning", async () => {
    const enterPlanMode = vi.fn(async () => {});
    const hook = makePlanModeAutoTriggerHook({
      agent: {
        getSessionPermissionMode: () => "default",
        enterPlanMode,
      },
    });

    const result = await hook.handler(
      buildArgsWithTools("프로덕션 배포 스크립트 수정하고 배포해줘"),
      makeCtx("s1", "NO", "approval_plan"),
    );

    expect(enterPlanMode).toHaveBeenCalledWith("s1", "turn-1");
    expect(result?.action).toBe("replace");
    if (result?.action !== "replace") throw new Error("expected replace");
    expect(result.value.tools.map((tool) => tool.name)).toEqual([
      "TaskBoard",
      "ExitPlanMode",
      "AskUserQuestion",
    ]);
  });

  it("continues silently when no implementation intent matched", async () => {
    const hook = makePlanModeAutoTriggerHook({ agent: agentWith("default") });
    const result = await hook.handler(
      buildArgs("what's the weather today?"),
      makeCtx("s1", "NO"),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("does not nudge document regeneration requests even if classifier says yes", async () => {
    const hook = makePlanModeAutoTriggerHook({ agent: agentWith("default") });
    const ctx = makeCtx("s1", "YES", "none");
    const result = await hook.handler(
      buildArgs(
        "아니 docx랑 pdf를 md형식 그대로 내뱉으면 어떡하냐 agentic하게 해서 이쁘게 잘 만들어야지",
      ),
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(ctx.llm.stream).toHaveBeenCalledOnce();
  });

  it("skips when env gate is off", async () => {
    process.env.MAGI_PLAN_AUTOTRIGGER = "off";
    const hook = makePlanModeAutoTriggerHook({ agent: agentWith("default") });
    const result = await hook.handler(
      buildArgs("implement a new endpoint"),
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("skips when session is already in plan mode", async () => {
    const hook = makePlanModeAutoTriggerHook({ agent: agentWith("plan") });
    const result = await hook.handler(
      buildArgs("implement a new endpoint"),
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("fails open when agent throws", async () => {
    const hook = makePlanModeAutoTriggerHook({
      agent: {
        getSessionPermissionMode: () => {
          throw new Error("boom");
        },
      },
    });
    const ctx = makeCtx();
    const result = await hook.handler(
      buildArgs("implement an API endpoint"),
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(ctx.log).toHaveBeenCalledWith(
      "warn",
      "[plan-mode-auto-trigger] fail-open",
      expect.any(Object),
    );
  });

  it("does not nudge on iteration > 0", async () => {
    const hook = makePlanModeAutoTriggerHook({ agent: agentWith("default") });
    const result = await hook.handler(
      buildArgs("implement a new feature", 2),
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });
});
