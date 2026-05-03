import { describe, expect, it } from "vitest";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";
import {
  classifyExactnessNeed,
  makeDeterministicExactnessHook,
  parseExactnessClassifierOutput,
} from "./deterministicExactness.js";
import type { HookContext } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";

function mockLlm(text: string): LLMClient {
  return {
    stream: () =>
      (async function* () {
        yield { kind: "text_delta" as const, delta: text };
        yield { kind: "message_end" as const };
      })(),
  } as unknown as LLMClient;
}

function ctx(store: ExecutionContractStore, llmText: string): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey: "session",
    turnId: "turn-1",
    llm: mockLlm(llmText),
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "gpt-5.5",
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
    executionContract: store,
  };
}

describe("parseExactnessClassifierOutput", () => {
  it("parses strict JSON classifier output", () => {
    expect(
      parseExactnessClassifierOutput(
        JSON.stringify({
          requiresDeterministic: true,
          kinds: ["clock", "date_range", "calculation"],
          reason: "The answer requires an exact 30-day average.",
          suggestedTools: ["Clock", "DateRange", "Calculation"],
          acceptanceCriteria: ["Use runtime date", "Compute average with code"],
        }),
      ),
    ).toMatchObject({
      requiresDeterministic: true,
      kinds: ["clock", "date_range", "calculation"],
      suggestedTools: ["Clock", "DateRange", "Calculation"],
    });
  });

  it("defaults to non-deterministic on invalid output", () => {
    expect(parseExactnessClassifierOutput("not json")).toMatchObject({
      requiresDeterministic: false,
      kinds: [],
    });
  });
});

describe("classifyExactnessNeed", () => {
  it("uses the LLM classifier rather than a local regex route", async () => {
    const result = await classifyExactnessNeed({
      llm: mockLlm(
        JSON.stringify({
          requiresDeterministic: true,
          kinds: ["calculation"],
          reason: "Needs exact arithmetic.",
          suggestedTools: ["Calculation"],
        }),
      ),
      model: "gpt-5.5",
      userMessage: "최근 30일 평균 매출 알려줘",
    });

    expect(result.requiresDeterministic).toBe(true);
    expect(result.kinds).toEqual(["calculation"]);
  });
});

describe("deterministic exactness hook", () => {
  it("records LLM-classified deterministic requirements as runtime contract", async () => {
    const store = new ExecutionContractStore({ now: () => 1 });
    store.startTurn({ userMessage: "최근 30일 평균 매출 알려줘" });
    const hook = makeDeterministicExactnessHook();

    const result = await hook.handler(
      {
        messages: [
          {
            role: "user",
            content: [{ type: "text", text: "최근 30일 평균 매출 알려줘" }],
          },
        ],
        tools: [],
        system: "base",
        iteration: 0,
      },
      ctx(
        store,
        JSON.stringify({
          turnMode: { label: "other", confidence: 0.9 },
          skipTdd: false,
          implementationIntent: false,
          documentOrFileOperation: false,
          deterministic: {
            requiresDeterministic: true,
            kinds: ["clock", "date_range", "calculation"],
            reason: "The user asks for an exact recent average.",
            suggestedTools: ["Clock", "DateRange", "Calculation"],
            acceptanceCriteria: [
              "Determine today's date through the Clock tool.",
              "Compute the average through Calculation.",
            ],
          },
          fileDelivery: {
            intent: "none",
            path: null,
            wantsChatDelivery: false,
            wantsKbDelivery: false,
            wantsFileOutput: false,
          },
        }),
      ),
    );

    expect(result).toEqual({ action: "continue" });
    const requirements = store.snapshot().taskState.deterministicRequirements;
    expect(requirements).toHaveLength(1);
    expect(requirements[0]).toMatchObject({
      source: "llm_classifier",
      status: "active",
      kinds: ["clock", "date_range", "calculation"],
      suggestedTools: ["Clock", "DateRange", "Calculation"],
    });
  });

  it("does not create a contract when the LLM says deterministic evidence is unnecessary", async () => {
    const store = new ExecutionContractStore({ now: () => 1 });
    store.startTurn({ userMessage: "매출이 뭔지 설명해줘" });
    const hook = makeDeterministicExactnessHook();

    await hook.handler(
      {
        messages: [{ role: "user", content: "매출이 뭔지 설명해줘" }],
        tools: [],
        system: "base",
        iteration: 0,
      },
      ctx(
        store,
        JSON.stringify({
          turnMode: { label: "other", confidence: 0.9 },
          skipTdd: false,
          implementationIntent: false,
          documentOrFileOperation: false,
          deterministic: {
            requiresDeterministic: false,
            kinds: [],
            reason: "Conceptual explanation.",
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
      ),
    );

    expect(store.snapshot().taskState.deterministicRequirements).toHaveLength(0);
  });
});
