import { afterEach, describe, expect, it, vi } from "vitest";
import {
  makeOutputDeliveryGateHook,
  matchesDeliveryFailureExplanation,
} from "./outputDeliveryGate.js";
import type { HookContext } from "../types.js";

function makeCtx(): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-1",
    llm: {} as never,
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    agentModel: "gpt-test",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
  };
}

function makeArgs(assistantText: string, retryCount = 0) {
  return {
    assistantText,
    toolCallCount: 0,
    toolReadHappened: false,
    userMessage: "generate a memo",
    retryCount,
  };
}

afterEach(() => {
  delete process.env.MAGI_OUTPUT_DELIVERY_GATE;
});

describe("matchesDeliveryFailureExplanation", () => {
  it("matches english and korean attachment failure reporting", () => {
    expect(matchesDeliveryFailureExplanation("Attachment upload failed, so I am leaving the file in workspace.")).toBe(true);
    expect(matchesDeliveryFailureExplanation("채팅 첨부 전송이 실패해서 파일 경로만 남깁니다.")).toBe(true);
    expect(matchesDeliveryFailureExplanation("Everything was delivered successfully.")).toBe(false);
  });
});

describe("outputDeliveryGate", () => {
  it("blocks when an output artifact from this turn is still undelivered", async () => {
    const hook = makeOutputDeliveryGateHook({
      agent: {
        listUndelivered: async () => [{ artifactId: "a1", filename: "memo.docx" }],
      },
    });

    const result = await hook.handler(makeArgs("작업 완료했습니다."), makeCtx());
    if (result?.action !== "block") throw new Error("expected block");
    expect(result.reason).toContain('memo.docx');
    expect(result.reason).toContain("FileDeliver");
  });

  it("allows when there are no undelivered artifacts", async () => {
    const hook = makeOutputDeliveryGateHook({
      agent: {
        listUndelivered: async () => [],
      },
    });

    const result = await hook.handler(makeArgs("작업 완료했습니다."), makeCtx());
    expect(result).toEqual({ action: "continue" });
  });

  it("allows when the assistant explicitly reports delivery failure", async () => {
    const hook = makeOutputDeliveryGateHook({
      agent: {
        listUndelivered: async () => [{ artifactId: "a1", filename: "memo.docx" }],
      },
    });

    const result = await hook.handler(
      makeArgs("채팅 첨부 전송이 실패해서 `memo.docx`는 workspace에만 남았습니다."),
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("respects MAGI_OUTPUT_DELIVERY_GATE=off", async () => {
    process.env.MAGI_OUTPUT_DELIVERY_GATE = "off";
    const hook = makeOutputDeliveryGateHook({
      agent: {
        listUndelivered: async () => [{ artifactId: "a1", filename: "memo.docx" }],
      },
    });

    const result = await hook.handler(makeArgs("작업 완료했습니다."), makeCtx());
    expect(result).toEqual({ action: "continue" });
  });
});
