import { describe, expect, it } from "vitest";
import type { HookContext } from "../types.js";
import type { RuntimePolicySnapshot } from "../../policy/policyTypes.js";
import {
  judgeResponseLanguage,
  makeResponseLanguageGateHook,
  parseLanguageVerdict,
} from "./responseLanguageGate.js";

function makeSnapshot(
  language: RuntimePolicySnapshot["policy"]["responseMode"]["language"],
): RuntimePolicySnapshot {
  return {
    policy: {
      approval: { explicitConsentForExternalActions: true },
      verification: {
        requireCompletionEvidence: true,
        honorTaskContractVerificationMode: true,
      },
      delivery: { requireDeliveredArtifactsBeforeCompletion: true },
      async: { requireRealNotificationMechanism: true },
      retry: { retryTransientToolFailures: true, defaultBackoffSeconds: [0, 10, 30] },
      responseMode: language ? { language } : {},
      citations: {},
      harnessRules: [],
    },
    status: {
      executableDirectives: [],
      userDirectives: language ? [`response.language=${language}`] : [],
      harnessDirectives: [],
      advisoryDirectives: [],
      warnings: [],
    },
  };
}

function makeCtx(
  events: unknown[] = [],
  llmOutput = "PASS",
  onRequest?: (req: unknown) => void,
): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey: "session",
    turnId: "turn",
    llm: {
      stream: async function* (req: unknown) {
        onRequest?.(req);
        yield { kind: "text_delta", blockIndex: 0, delta: llmOutput } as const;
        yield {
          kind: "message_end",
          stopReason: "end_turn",
          usage: { inputTokens: 1, outputTokens: 1 },
        } as const;
      },
    } as HookContext["llm"],
    transcript: [],
    emit: (event) => events.push(event),
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
  };
}

describe("responseLanguageGate", () => {
  it("parses PASS and FAIL verifier verdicts", () => {
    expect(parseLanguageVerdict("PASS")).toEqual({ pass: true, detail: "PASS" });
    expect(parseLanguageVerdict("FAIL: mostly English")).toEqual({
      pass: false,
      detail: "FAIL: mostly English",
    });
    expect(parseLanguageVerdict("unclear")).toEqual({
      pass: true,
      detail: "unclear",
    });
  });

  it("passes target language and exception guidance to the LLM verifier", async () => {
    const calls: unknown[] = [];
    const ctx = makeCtx([], "PASS", (req) => calls.push(req));

    const result = await judgeResponseLanguage(ctx, {
      language: "ko",
      userMessage: "한국어로 이 논문 요약해줘",
      assistantText: "이 논문은 Transformer 모델을 소개합니다. Title: Attention Is All You Need.",
    });

    expect(result.pass).toBe(true);
    const request = calls[0] as { messages: Array<{ content: string }> };
    expect(request.messages[0]?.content).toContain("target language policy: ko");
    expect(request.messages[0]?.content).toContain("quoted source titles");
    expect(request.messages[0]?.content).toContain("language learning");
  });

  it("blocks fixed-language responses when the draft visibly uses another language", async () => {
    const events: unknown[] = [];
    const hook = makeResponseLanguageGateHook({
      policy: {
        current: async () => makeSnapshot("ko"),
      },
    });

    const result = await hook.handler(
      {
        userMessage: "요약해줘",
        assistantText: "This report is about market structure.",
        toolCallCount: 0,
        toolReadHappened: false,
        retryCount: 0,
      },
      makeCtx(events, "FAIL: answer body is English"),
    );

    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("[RETRY:RESPONSE_LANGUAGE:ko]");
      expect(result.reason).toContain("assistant main prose appears to be English");
    }
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "rule_check",
        ruleId: "response-language-gate",
        verdict: "violation",
      }),
    );
  });

  it("continues when auto language policy verifier allows an explicit cross-language request", async () => {
    const hook = makeResponseLanguageGateHook({
      policy: {
        current: async () => makeSnapshot("auto"),
      },
    });

    const result = await hook.handler(
      {
        userMessage: "영어 이메일 초안 작성해줘",
        assistantText: "Dear Alex,\n\nThank you for your time today.",
        toolCallCount: 0,
        toolReadHappened: false,
        retryCount: 0,
      },
      makeCtx([], "PASS"),
    );

    expect(result).toEqual({ action: "continue" });
  });

  it("blocks Korean main prose for an English latest user message under auto policy even if the LLM verifier misses it", async () => {
    const events: unknown[] = [];
    const hook = makeResponseLanguageGateHook({
      policy: {
        current: async () => makeSnapshot("auto"),
      },
    });

    const result = await hook.handler(
      {
        userMessage:
          "Spawn 4 subagents with different SOTA LLM models, calculate 1+1, and cross-validate them each other. Send me the final result in .md file.",
        assistantText:
          "[META: intent=execution, domain=AI orchestration]\n4개의 서로 다른 SOTA 모델 서브에이전트를 병렬 디스패치합니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        retryCount: 0,
      },
      makeCtx(events, "PASS"),
    );

    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("[RETRY:RESPONSE_LANGUAGE:en]");
      expect(result.reason).toContain("latest user message is English");
    }
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "rule_check",
        ruleId: "response-language-gate",
        verdict: "violation",
      }),
    );
  });

  it("does not run when no response language policy exists", async () => {
    let called = false;
    const hook = makeResponseLanguageGateHook({
      policy: {
        current: async () => makeSnapshot(undefined),
      },
    });
    const ctx = makeCtx([], "FAIL", () => {
      called = true;
    });

    const result = await hook.handler(
      {
        userMessage: "hello",
        assistantText: "hello",
        toolCallCount: 0,
        toolReadHappened: false,
        retryCount: 0,
      },
      ctx,
    );

    expect(result).toEqual({ action: "continue" });
    expect(called).toBe(false);
  });
});
