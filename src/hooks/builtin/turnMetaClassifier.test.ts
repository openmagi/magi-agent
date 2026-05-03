import { describe, expect, it } from "vitest";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";
import type { HookContext } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import {
  getOrClassifyFinalAnswerMeta,
  getOrClassifyRequestMeta,
  hashMetaInput,
  parseFinalAnswerMetaOutput,
  parseRequestMetaOutput,
} from "./turnMetaClassifier.js";

function mockLlm(text: string, calls: { count: number }): LLMClient {
  return {
    stream: () =>
      (async function* () {
        calls.count += 1;
        yield { kind: "text_delta" as const, delta: text };
        yield { kind: "message_end" as const };
      })(),
  } as unknown as LLMClient;
}

function ctx(store: ExecutionContractStore, llm: LLMClient): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey: "session",
    turnId: "turn-1",
    llm,
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "gpt-5.5",
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
    executionContract: store,
  };
}

describe("turn meta classifier parsing", () => {
  it("parses request meta as one JSON classifier payload", () => {
    const result = parseRequestMetaOutput(
      JSON.stringify({
        turnMode: { label: "coding", confidence: 0.92 },
        skipTdd: true,
        implementationIntent: true,
        documentOrFileOperation: false,
        deterministic: {
          requiresDeterministic: true,
          kinds: ["clock", "calculation"],
          reason: "Needs exact 30-day average.",
          suggestedTools: ["Clock", "Calculation"],
          acceptanceCriteria: ["Use runtime date.", "Compute average with code."],
        },
        fileDelivery: {
          intent: "deliver_existing",
          path: "reports/final.docx",
          wantsChatDelivery: true,
          wantsKbDelivery: false,
          wantsFileOutput: true,
        },
      }),
    );

    expect(result.turnMode).toEqual({ label: "coding", confidence: 0.92 });
    expect(result.skipTdd).toBe(true);
    expect(result.deterministic.kinds).toEqual(["clock", "calculation"]);
    expect(result.fileDelivery).toMatchObject({
      intent: "deliver_existing",
      path: "reports/final.docx",
      wantsChatDelivery: true,
    });
  });

  it("parses final-answer meta as one JSON classifier payload", () => {
    const result = parseFinalAnswerMetaOutput(
      JSON.stringify({
        internalReasoningLeak: true,
        lazyRefusal: false,
        selfClaim: true,
        deferralPromise: false,
        assistantClaimsFileCreated: true,
        assistantClaimsChatDelivery: true,
        assistantClaimsKbDelivery: false,
        assistantReportsDeliveryFailure: false,
        reason: "The answer claims a file was sent.",
      }),
    );

    expect(result).toMatchObject({
      internalReasoningLeak: true,
      selfClaim: true,
      assistantClaimsFileCreated: true,
      assistantClaimsChatDelivery: true,
      assistantReportsDeliveryFailure: false,
    });
  });
});

describe("turn meta classifier caching", () => {
  it("reuses request classification stored on the execution contract", async () => {
    const store = new ExecutionContractStore({ now: () => 123 });
    const calls = { count: 0 };
    const context = ctx(
      store,
      mockLlm(
        JSON.stringify({
          turnMode: { label: "coding", confidence: 0.9 },
          skipTdd: false,
          implementationIntent: true,
          documentOrFileOperation: false,
          deterministic: {
            requiresDeterministic: false,
            kinds: [],
            reason: "No exact numeric work.",
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
        }),
        calls,
      ),
    );

    const first = await getOrClassifyRequestMeta(context, {
      userMessage: "implement the endpoint",
    });
    const second = await getOrClassifyRequestMeta(context, {
      userMessage: "implement the endpoint",
    });

    expect(first.turnMode.label).toBe("coding");
    expect(second.turnMode.label).toBe("coding");
    expect(calls.count).toBe(1);
  });

  it("reuses final-answer classification for multiple beforeCommit gates", async () => {
    const store = new ExecutionContractStore({ now: () => 456 });
    const calls = { count: 0 };
    const context = ctx(
      store,
      mockLlm(
        JSON.stringify({
          internalReasoningLeak: false,
          lazyRefusal: false,
          selfClaim: false,
          deferralPromise: false,
          assistantClaimsFileCreated: false,
          assistantClaimsChatDelivery: true,
          assistantClaimsKbDelivery: false,
          assistantReportsDeliveryFailure: false,
          reason: "The answer says a file was sent.",
        }),
        calls,
      ),
    );

    const input = {
      userMessage: "send me final.docx",
      assistantText: "I sent final.docx in chat.",
    };
    const first = await getOrClassifyFinalAnswerMeta(context, input);
    const second = await getOrClassifyFinalAnswerMeta(context, input);

    expect(first.assistantClaimsChatDelivery).toBe(true);
    expect(second.assistantClaimsChatDelivery).toBe(true);
    expect(calls.count).toBe(1);
  });

  it("does not reuse stale final-answer classification for a changed draft", async () => {
    const store = new ExecutionContractStore({ now: () => 789 });
    const hash = hashMetaInput("old answer");
    store.recordFinalAnswerClassification({
      turnId: "turn-1",
      inputHash: hash,
      source: "llm_classifier",
      result: {
        internalReasoningLeak: false,
        lazyRefusal: false,
        selfClaim: false,
        deferralPromise: false,
        assistantClaimsFileCreated: false,
        assistantClaimsChatDelivery: false,
        assistantClaimsKbDelivery: false,
        assistantReportsDeliveryFailure: false,
        reason: "old draft",
      },
    });
    const calls = { count: 0 };
    const context = ctx(
      store,
      mockLlm(
        JSON.stringify({
          internalReasoningLeak: false,
          lazyRefusal: false,
          selfClaim: false,
          deferralPromise: true,
          assistantClaimsFileCreated: false,
          assistantClaimsChatDelivery: false,
          assistantClaimsKbDelivery: false,
          assistantReportsDeliveryFailure: false,
          reason: "new draft defers.",
        }),
        calls,
      ),
    );

    const result = await getOrClassifyFinalAnswerMeta(context, {
      userMessage: "make a report",
      assistantText: "I will send it later.",
    });

    expect(result.deferralPromise).toBe(true);
    expect(calls.count).toBe(1);
  });
});
