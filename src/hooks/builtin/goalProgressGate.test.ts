import { describe, expect, it, vi } from "vitest";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";
import type { HookContext } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import {
  countFailedToolResultsThisTurn,
  countSuccessfulToolResultsThisTurn,
  countToolCallsThisTurn,
  makeGoalProgressGateHook,
} from "./goalProgressGate.js";

function mockLlm(payloads: unknown[]): LLMClient {
  let i = 0;
  return {
    stream: () =>
      (async function* () {
        const payload = payloads[Math.min(i, payloads.length - 1)];
        i += 1;
        yield { kind: "text_delta" as const, delta: JSON.stringify(payload) };
        yield { kind: "message_end" as const };
      })(),
  } as unknown as LLMClient;
}

function makeCtx(input: {
  transcript: HookContext["transcript"];
  llm?: LLMClient;
}): HookContext {
  return {
    botId: "bot-1",
    userId: "user-1",
    sessionKey: "agent:main:app:general:1",
    turnId: "t1",
    llm: input.llm ?? mockLlm([]),
    transcript: input.transcript,
    emit: vi.fn(),
    log: vi.fn(),
    agentModel: "claude-opus-4-7",
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
    executionContract: new ExecutionContractStore({ now: () => 1 }),
  } as unknown as HookContext;
}

const actionRequestMeta = {
  turnMode: { label: "other", confidence: 0.93 },
  skipTdd: false,
  implementationIntent: false,
  documentOrFileOperation: false,
  documentExport: { strategy: "none", confidence: 0, renderParityRequired: false, nativeTemplateRequired: false, docxMode: null, reason: "No document export routing requested." },
  deterministic: {
    requiresDeterministic: false,
    kinds: [],
    reason: "No exact computation.",
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
  goalProgress: {
    requiresAction: true,
    actionKinds: ["browser_interaction"],
    reason: "The user asked the agent to interact with the browser.",
  },
};

const noActionRequestMeta = {
  ...actionRequestMeta,
  goalProgress: {
    requiresAction: false,
    actionKinds: [],
    reason: "The user asked a conversational question.",
  },
};

const earlyGiveUpMeta = {
  internalReasoningLeak: false,
  lazyRefusal: false,
  selfClaim: false,
  deferralPromise: false,
  assistantClaimsFileCreated: false,
  assistantClaimsChatDelivery: false,
  assistantClaimsKbDelivery: false,
  assistantReportsDeliveryFailure: false,
  assistantReportsDeliveryUnverified: false,
  assistantGivesUpEarly: true,
  assistantClaimsActionWithoutEvidence: false,
  assistantEndsWithUnexecutedPlan: false,
  assistantNeedsMoreRuntimeWork: false,
  reason: "The draft asks the user to choose after one failed click.",
};

const actionClaimMeta = {
  ...earlyGiveUpMeta,
  assistantGivesUpEarly: false,
  assistantClaimsActionWithoutEvidence: true,
  reason: "The draft claims debugging happened.",
};

const neutralFinalMeta = {
  ...earlyGiveUpMeta,
  assistantGivesUpEarly: false,
  assistantClaimsActionWithoutEvidence: false,
  assistantEndsWithUnexecutedPlan: false,
  assistantNeedsMoreRuntimeWork: false,
  reason: "The draft does not claim runtime action.",
};

const planOnlyMeta = {
  ...earlyGiveUpMeta,
  assistantGivesUpEarly: false,
  assistantClaimsActionWithoutEvidence: false,
  assistantEndsWithUnexecutedPlan: true,
  reason: "The draft only says it will start the subagents instead of returning results.",
};

const runtimeWorkStillNeededMeta = {
  ...earlyGiveUpMeta,
  assistantGivesUpEarly: false,
  assistantClaimsActionWithoutEvidence: false,
  assistantEndsWithUnexecutedPlan: false,
  assistantNeedsMoreRuntimeWork: true,
  reason: "The draft says it will now call KnowledgeSearch and Browser before it can answer.",
};

describe("goalProgressGate helpers", () => {
  it("counts tool calls and current-turn success/failure results", () => {
    const transcript = [
      { kind: "tool_call", turnId: "t1", toolUseId: "a", name: "Browser" },
      {
        kind: "tool_result",
        turnId: "t1",
        toolUseId: "a",
        status: "error",
        isError: true,
      },
      { kind: "tool_call", turnId: "t1", toolUseId: "b", name: "Browser" },
      {
        kind: "tool_result",
        turnId: "t1",
        toolUseId: "b",
        status: "ok",
        isError: false,
      },
      { kind: "tool_call", turnId: "t2", toolUseId: "c", name: "Browser" },
      {
        kind: "tool_result",
        turnId: "t2",
        toolUseId: "c",
        status: "error",
        isError: true,
      },
    ];

    expect(countToolCallsThisTurn(transcript, "t1")).toBe(2);
    expect(countFailedToolResultsThisTurn(transcript, "t1")).toBe(1);
    expect(countSuccessfulToolResultsThisTurn(transcript, "t1")).toBe(1);
  });
});

describe("goalProgressGate hook", () => {
  it("blocks a goal-oriented turn that gives up after one failed tool attempt", async () => {
    const hook = makeGoalProgressGateHook();
    const ctx = makeCtx({
      transcript: [
        { kind: "tool_call", turnId: "t1", toolUseId: "click-1", name: "Browser" },
        {
          kind: "tool_result",
          turnId: "t1",
          toolUseId: "click-1",
          status: "error",
          output: "Element not found",
          isError: true,
        },
      ] as HookContext["transcript"],
      llm: mockLlm([actionRequestMeta, earlyGiveUpMeta]),
    });

    const result = await hook.handler(
      {
        assistantText:
          "한 번 클릭했는데 안 됩니다. A로 마지막 시도하거나 B로 포기할 수 있습니다. 어떤 방향으로 진행할까요?",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "인간이 인터랙션하는 것처럼 천천히 진행해",
        retryCount: 0,
      },
      ctx,
    );

    expect(result?.action).toBe("block");
    expect(result?.reason).toContain("[RETRY:GOAL_PROGRESS_REQUIRED]");
  });

  it("allows a hard blocker report after the retry budget is exhausted", async () => {
    const hook = makeGoalProgressGateHook();
    const ctx = makeCtx({
      transcript: [
        { kind: "tool_call", turnId: "t1", toolUseId: "click-1", name: "Browser" },
        {
          kind: "tool_result",
          turnId: "t1",
          toolUseId: "click-1",
          status: "error",
          output: "Element not found",
          isError: true,
        },
      ] as HookContext["transcript"],
      llm: mockLlm([actionRequestMeta, earlyGiveUpMeta]),
    });

    const result = await hook.handler(
      {
        assistantText: "도구가 한 번 실패했습니다. 진행 방향을 선택해주세요.",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "인간이 인터랙션하는 것처럼 천천히 진행해",
        retryCount: 1,
      },
      ctx,
    );

    expect(result?.action ?? "continue").toBe("continue");
  });

  it("blocks early give-up when no tool attempt was made", async () => {
    const hook = makeGoalProgressGateHook();
    const ctx = makeCtx({
      transcript: [],
      llm: mockLlm([actionRequestMeta, earlyGiveUpMeta]),
    });

    const result = await hook.handler(
      {
        assistantText:
          "직접 접속이 어려우니 스크린샷이나 소스코드를 공유해주시면 리뷰하겠습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "이 사이트 접속해서 버튼 눌러봐",
        retryCount: 0,
      },
      ctx,
    );

    expect(result?.action).toBe("block");
    expect(result?.reason).toContain("[RETRY:GOAL_PROGRESS_REQUIRED]");
  });

  it("blocks action claims when no current-turn tool evidence exists", async () => {
    const hook = makeGoalProgressGateHook();
    const ctx = makeCtx({
      transcript: [],
      llm: mockLlm([actionRequestMeta, actionClaimMeta]),
    });

    const result = await hook.handler(
      {
        assistantText:
          "디버깅 결과를 도구 호출 기록 기준으로 보고드립니다. 실제로 한 것은 다음과 같습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "왜 안 했는지 디버깅해봐",
        retryCount: 0,
      },
      ctx,
    );

    expect(result?.action).toBe("block");
    expect(result?.reason).toContain("[RETRY:GOAL_PROGRESS_ACTION_EVIDENCE]");
  });

  it("blocks goal-oriented turns that end with a plan instead of executing the next action", async () => {
    const hook = makeGoalProgressGateHook();
    const ctx = makeCtx({
      transcript: [
        {
          kind: "tool_call",
          turnId: "t1",
          toolUseId: "write-context",
          name: "FileWrite",
        },
        {
          kind: "tool_result",
          turnId: "t1",
          toolUseId: "write-context",
          status: "ok",
          output: JSON.stringify({ path: "workspace/context.md" }),
          isError: false,
        },
      ] as HookContext["transcript"],
      llm: mockLlm([actionRequestMeta, planOnlyMeta]),
    });

    const result = await hook.handler(
      {
        assistantText:
          "컨텍스트 파일이 준비되었습니다. 이제 낙관 파트너와 회의 파트너를 Opus 4.6으로 병렬 디스패치하겠습니다.",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "투심위를 진행해서 최종 리포트를 줘",
        retryCount: 0,
      },
      ctx,
    );

    expect(result?.action).toBe("block");
    expect(result?.reason).toContain("[RETRY:GOAL_PROGRESS_EXECUTE_NEXT]");
  });

  it("blocks immediate tool-action promises even when the latest user message is only a status follow-up", async () => {
    const hook = makeGoalProgressGateHook();
    const ctx = makeCtx({
      transcript: [],
      llm: mockLlm([noActionRequestMeta, planOnlyMeta]),
    });

    const result = await hook.handler(
      {
        assistantText:
          "아니요, 아직 하지 못했습니다. 지금 당장 KnowledgeSearch와 Browser 도구를 호출하여 지침과 접속 상태를 확인하겠습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "했음?",
        retryCount: 0,
      },
      ctx,
    );

    expect(result?.action).toBe("block");
    expect(result?.reason).toContain("[RETRY:GOAL_PROGRESS_EXECUTE_NEXT]");
  });

  it("blocks drafts classified as still needing runtime work even without narrower plan-only flags", async () => {
    const hook = makeGoalProgressGateHook();
    const ctx = makeCtx({
      transcript: [],
      llm: mockLlm([noActionRequestMeta, runtimeWorkStillNeededMeta]),
    });

    const result = await hook.handler(
      {
        assistantText:
          "이번에는 KnowledgeSearch와 Browser 도구를 실제로 호출해서 확인하겠습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "이번엔 정말 했어?",
        retryCount: 0,
      },
      ctx,
    );

    expect(result?.action).toBe("block");
    expect(result?.reason).toContain("[RETRY:GOAL_PROGRESS_EXECUTE_NEXT]");
  });

  it("blocks subagent work orders that only announce dispatch without any tool progress", async () => {
    const hook = makeGoalProgressGateHook();
    const ctx = makeCtx({
      transcript: [],
      llm: mockLlm([
        {
          ...actionRequestMeta,
          deterministic: {
            requiresDeterministic: true,
            kinds: ["calculation"],
            reason: "The user requested exact arithmetic.",
            suggestedTools: ["Calculation"],
            acceptanceCriteria: ["Compute 1+1 with deterministic evidence."],
          },
          documentOrFileOperation: true,
          documentExport: { strategy: "none", confidence: 0, renderParityRequired: false, nativeTemplateRequired: false, docxMode: null, reason: "No document export routing requested." },
          planning: {
            need: "task_board",
            reason: "The user asked for coordinated subagent work and a file deliverable.",
            suggestedStrategy: "Track dispatch, validation, report creation, and delivery.",
          },
          goalProgress: {
            requiresAction: true,
            actionKinds: ["subagent_dispatch", "calculation", "file_delivery"],
            reason: "The request explicitly asks the agent to run subagents and return a file.",
          },
        },
        planOnlyMeta,
      ]),
    });

    const result = await hook.handler(
      {
        assistantText:
          "I'll spawn 4 subagents with different SOTA LLMs to compute 1+1, then cross-validate and deliver the result as a markdown file.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage:
          "Spawn 4 subagents with different SOTA LLMs, compute 1+1 on each, cross-validate the results, and return the final answer as a .md file.",
        retryCount: 0,
      },
      ctx,
    );

    expect(result?.action).toBe("block");
    expect(result?.reason).toContain("[RETRY:GOAL_PROGRESS_EXECUTE_NEXT]");
  });

  it("keeps repeated plan-only endings retryable so the turn controller can keep steering tool use", async () => {
    const hook = makeGoalProgressGateHook();
    const ctx = makeCtx({
      transcript: [
        {
          kind: "tool_call",
          turnId: "t1",
          toolUseId: "write-context",
          name: "FileWrite",
        },
        {
          kind: "tool_result",
          turnId: "t1",
          toolUseId: "write-context",
          status: "ok",
          output: JSON.stringify({ path: "workspace/context.md" }),
          isError: false,
        },
      ] as HookContext["transcript"],
      llm: mockLlm([actionRequestMeta, planOnlyMeta]),
    });

    const result = await hook.handler(
      {
        assistantText:
          "죄송합니다. 실제로 서브에이전트를 띄우겠습니다.",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "투심위를 진행해서 최종 리포트를 줘",
        retryCount: 1,
      },
      ctx,
    );

    expect(result?.action).toBe("block");
    expect(result?.reason).toContain("[RETRY:GOAL_PROGRESS_EXECUTE_NEXT]");
    expect(result?.reason).not.toContain("[RULE:");
  });

  it("does not block conversational requests", async () => {
    const hook = makeGoalProgressGateHook();
    const ctx = makeCtx({
      transcript: [],
      llm: mockLlm([noActionRequestMeta, neutralFinalMeta]),
    });

    const result = await hook.handler(
      {
        assistantText: "그건 실행 환경과 도구의 차이 때문에 생길 수 있습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "이 현상에 대해 어떻게 생각해?",
        retryCount: 0,
      },
      ctx,
    );

    expect(result?.action ?? "continue").toBe("continue");
  });
});
