import { describe, it, expect, vi } from "vitest";
import {
  matchesDeferral,
  countWorkToolsThisTurn,
  makeDeferralBlockerHook,
} from "./deferralBlocker.js";
import type { HookContext } from "../types.js";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";

function makeCtx(transcript: HookContext["transcript"]): HookContext {
  const llm = {
    async *stream() {
      yield {
        kind: "text_delta",
        delta: JSON.stringify({
          internalReasoningLeak: false,
          lazyRefusal: false,
          selfClaim: false,
          deferralPromise: true,
          assistantClaimsFileCreated: false,
          assistantClaimsChatDelivery: false,
          assistantClaimsKbDelivery: false,
          assistantReportsDeliveryFailure: false,
          reason: "test deferral",
        }),
      };
    },
  };
  return {
    botId: "bot-1",
    userId: "user-1",
    sessionKey: "agent:main:app:general:1",
    turnId: "t1",
    llm,
    transcript,
    emit: vi.fn(),
    log: vi.fn(),
    agentModel: "claude-opus-4-7",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
    executionContract: new ExecutionContractStore({ now: () => 1 }),
  } as unknown as HookContext;
}

describe("matchesDeferral (LLM-based)", () => {
  it("returns false for empty text", async () => {
    expect(await matchesDeferral("")).toBe(false);
  });

  it("returns false when no LLM context available (fail-open)", async () => {
    expect(await matchesDeferral("완료되면 결과 보내드리겠습니다")).toBe(false);
  });
});

describe("countWorkToolsThisTurn", () => {
  it("counts only WORK_TOOLS in the current turn", () => {
    const transcript = [
      { kind: "tool_call", turnId: "t1", name: "Bash" },
      { kind: "tool_call", turnId: "t1", name: "FileRead" },
      { kind: "tool_call", turnId: "t1", name: "SpawnAgent" },
      { kind: "tool_call", turnId: "t2", name: "Bash" },
    ];
    expect(countWorkToolsThisTurn(transcript, "t1")).toBe(2);
    expect(countWorkToolsThisTurn(transcript, "t2")).toBe(1);
  });
});

describe("deferralBlocker async delivery handoff", () => {
  it("allows a deferral-style status update when background task delivery is already scheduled", async () => {
    const hook = makeDeferralBlockerHook();
    const ctx = makeCtx([
      {
        kind: "tool_call",
        ts: 1,
        turnId: "t1",
        toolUseId: "spawn-1",
        name: "SpawnAgent",
        input: { deliver: "background", prompt: "deep research" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "t1",
        toolUseId: "spawn-1",
        status: "ok",
        output: JSON.stringify({ taskId: "spawn_123", status: "pending" }),
        isError: false,
      },
      {
        kind: "tool_call",
        ts: 3,
        turnId: "t1",
        toolUseId: "cron-1",
        name: "CronCreate",
        input: {
          expression: "*/3 * * * *",
          prompt: "Check task spawn_123 and deliver the result when complete",
        },
      },
      {
        kind: "tool_result",
        ts: 4,
        turnId: "t1",
        toolUseId: "cron-1",
        status: "ok",
        output: JSON.stringify({
          cron: {
            cronId: "01CRON",
            deliveryChannel: { type: "app", channelId: "general" },
            sessionKey: "agent:main:app:general:1",
          },
        }),
        isError: false,
      },
    ]);

    const result = await hook.handler(
      {
        assistantText: "리서치가 완료되면 문서로 변환해서 보내드리겠습니다.",
        toolCallCount: 2,
        toolReadHappened: false,
        userMessage: "딥리서치해서 docx/pdf로 보내줘",
        retryCount: 0,
      },
      ctx,
    );

    expect(result?.action ?? "continue").toBe("continue");
  });
});
