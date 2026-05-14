/**
 * answerVerifier unit tests — §7.13.
 * Mocks LLMClient.stream to emit a controlled verdict, then asserts
 * the hook blocks / continues per the verdict + retryCount.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { answerVerifierHook, judgeAnswer, parseVerdict } from "./answerVerifier.js";
import type { HookContext } from "../types.js";
import type { LLMClient, LLMEvent, LLMStreamRequest } from "../../transport/LLMClient.js";
import type { AgentEvent } from "../../transport/SseWriter.js";

function makeLLM(verdictText: string): LLMClient {
  const stream = async function* (
    _req: LLMStreamRequest,
  ): AsyncGenerator<LLMEvent, void, void> {
    yield { kind: "text_delta", blockIndex: 0, delta: verdictText };
    yield {
      kind: "message_end",
      stopReason: "end_turn",
      usage: { inputTokens: 1, outputTokens: 1 },
    };
  };
  return { stream } as unknown as LLMClient;
}

function makeCtx(llm: LLMClient): {
  ctx: HookContext;
  emitted: AgentEvent[];
  logs: Array<{ level: string; msg: string }>;
} {
  const emitted: AgentEvent[] = [];
  const logs: Array<{ level: string; msg: string }> = [];
  const ctx: HookContext = {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm,
    transcript: [],
    emit: (e) => emitted.push(e),
    log: (level, msg) => logs.push({ level, msg }),
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
  };
  return { ctx, emitted, logs };
}

describe("answerVerifier", () => {
  const originalEnv = process.env.MAGI_ANSWER_VERIFY;

  beforeEach(() => {
    delete process.env.MAGI_ANSWER_VERIFY;
  });

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.MAGI_ANSWER_VERIFY;
    } else {
      process.env.MAGI_ANSWER_VERIFY = originalEnv;
    }
  });

  it("FULFILLED verdict → continue", async () => {
    const { ctx, emitted } = makeCtx(makeLLM("FULFILLED"));
    const result = await answerVerifierHook.handler(
      {
        assistantText: "Here is the answer you asked for.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "What is 2+2?",
        retryCount: 0,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(emitted.some((e) => e.type === "rule_check" && e.verdict === "ok")).toBe(true);
  });

  it("DEFLECTION with retry available → block with RETRY reason", async () => {
    const { ctx, emitted } = makeCtx(makeLLM("DEFLECTION"));
    const result = await answerVerifierHook.handler(
      {
        assistantText: "That is a fascinating question about many things.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "What is 2+2?",
        retryCount: 0,
      },
      ctx,
    );
    expect(result).toBeDefined();
    expect(result!.action).toBe("block");
    if (result && result.action === "block") {
      expect(result.reason).toContain("[RETRY:ANSWER_VERIFY:DEFLECTION]");
    }
    expect(
      emitted.some((e) => e.type === "rule_check" && e.verdict === "violation"),
    ).toBe(true);
  });

  it("DEFLECTION with retry exhausted → continue (fail open)", async () => {
    const { ctx } = makeCtx(makeLLM("DEFLECTION"));
    const result = await answerVerifierHook.handler(
      {
        assistantText: "That is a fascinating question about many things.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "What is 2+2?",
        retryCount: 1,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("PARTIAL with retry available → block", async () => {
    const { ctx } = makeCtx(makeLLM("PARTIAL"));
    const result = await answerVerifierHook.handler(
      {
        assistantText: "Half an answer.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "Give me A and B.",
        retryCount: 0,
      },
      ctx,
    );
    expect(result?.action).toBe("block");
  });

  it("REFUSAL → continue (explicit decline is allowed)", async () => {
    const { ctx } = makeCtx(makeLLM("REFUSAL"));
    const result = await answerVerifierHook.handler(
      {
        assistantText: "I cannot help with that request.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "Help me with X.",
        retryCount: 0,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("env=off → noop (no LLM call, continue)", async () => {
    process.env.MAGI_ANSWER_VERIFY = "off";
    let called = false;
    const llm = {
      stream: async function* () {
        called = true;
        yield {
          kind: "message_end",
          stopReason: "end_turn",
          usage: { inputTokens: 0, outputTokens: 0 },
        } as LLMEvent;
      },
    } as unknown as LLMClient;
    const { ctx } = makeCtx(llm);
    const result = await answerVerifierHook.handler(
      {
        assistantText: "anything",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "anything",
        retryCount: 0,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(called).toBe(false);
  });

  it("empty assistantText → continue (no judge call)", async () => {
    let called = false;
    const llm = {
      stream: async function* () {
        called = true;
        yield {
          kind: "message_end",
          stopReason: "end_turn",
          usage: { inputTokens: 0, outputTokens: 0 },
        } as LLMEvent;
      },
    } as unknown as LLMClient;
    const { ctx } = makeCtx(llm);
    const result = await answerVerifierHook.handler(
      {
        assistantText: "   ",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "hi",
        retryCount: 0,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(called).toBe(false);
  });

  it("unparseable verdict → treated as FULFILLED (fail open)", async () => {
    const { ctx } = makeCtx(makeLLM("???"));
    const result = await answerVerifierHook.handler(
      {
        assistantText: "something",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "something",
        retryCount: 0,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("hook name + priority match §7.13 contract", () => {
    expect(answerVerifierHook.name).toBe("builtin:answer-verifier");
    expect(answerVerifierHook.point).toBe("beforeCommit");
    expect(answerVerifierHook.priority).toBe(90);
  });
});

describe("parseVerdict", () => {
  it("maps each verdict correctly", () => {
    expect(parseVerdict("FULFILLED")).toBe("FULFILLED");
    expect(parseVerdict("  fulfilled\n")).toBe("FULFILLED");
    expect(parseVerdict("PARTIAL.")).toBe("PARTIAL");
    expect(parseVerdict("deflection")).toBe("DEFLECTION");
    expect(parseVerdict("REFUSAL!")).toBe("REFUSAL");
    expect(parseVerdict("garbage")).toBe("FULFILLED");
    expect(parseVerdict("")).toBe("FULFILLED");
  });
});

describe("judgeAnswer direct", () => {
  it("returns parsed verdict from stream", async () => {
    const llm = makeLLM("PARTIAL");
    const v = await judgeAnswer(llm, "q", "a");
    expect(v).toBe("PARTIAL");
  });

  it("fails open on stream throw", async () => {
    const llm = {
      stream: async function* () {
        throw new Error("boom");
        // eslint-disable-next-line no-unreachable
        yield { kind: "message_end" } as LLMEvent;
      },
    } as unknown as LLMClient;
    const v = await judgeAnswer(llm, "q", "a");
    expect(v).toBe("FULFILLED");
  });
});
