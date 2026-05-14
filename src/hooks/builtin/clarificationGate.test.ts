import { describe, expect, it, vi } from "vitest";
import type { ControlRequestRecord } from "../../control/ControlEvents.js";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";
import type { LLMClient, LLMMessage } from "../../transport/LLMClient.js";
import type { HookContext } from "../types.js";
import {
  makeClarificationGateHook,
  shouldRequestClarification,
  type ClarificationGateAgent,
} from "./clarificationGate.js";

function classifierLlm(payload: unknown, calls: { count: number }): LLMClient {
  return {
    stream: () =>
      (async function* () {
        calls.count += 1;
        yield { kind: "text_delta" as const, delta: JSON.stringify(payload) };
        yield { kind: "message_end" as const };
      })(),
  } as unknown as LLMClient;
}

function requestMeta(overrides: Record<string, unknown> = {}) {
  return {
    turnMode: { label: "other", confidence: 0.9 },
    skipTdd: false,
    implementationIntent: false,
    documentOrFileOperation: true,
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
      wantsFileOutput: true,
    },
    planning: {
      need: "task_board",
      reason: "The work has multiple deliverables.",
      suggestedStrategy: "Clarify the missing format before execution.",
    },
    goalProgress: {
      requiresAction: true,
      actionKinds: ["document_generation"],
      reason: "The user wants concrete document work.",
    },
    sourceAuthority: {
      longTermMemoryPolicy: "normal",
      currentSourcesAuthoritative: false,
      reason: "No source authority override required.",
    },
    clarification: {
      needed: true,
      reason: "The deliverable format is ambiguous.",
      question: "Which output format should I create?",
      choices: ["DOCX", "PDF", "Both"],
      allowFreeText: true,
      riskIfAssumed: "The agent may create the wrong file type.",
    },
    ...overrides,
  };
}

function makeCtx(llm: LLMClient): HookContext {
  return {
    botId: "bot-1",
    userId: "user-1",
    sessionKey: "agent:main:app:general",
    turnId: "turn-1",
    llm,
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    agentModel: "gpt-5.5",
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
    executionContract: new ExecutionContractStore({ now: () => 1 }),
  };
}

function args(text: string, iteration = 0): {
  messages: LLMMessage[];
  tools: [];
  system: string;
  iteration: number;
} {
  return {
    messages: [{ role: "user", content: text }],
    tools: [],
    system: "base system",
    iteration,
  };
}

describe("clarificationGate", () => {
  it("requires clarification only for non-trivial work where ambiguity changes the outcome", () => {
    expect(shouldRequestClarification(requestMeta())).toBe(true);
    expect(
      shouldRequestClarification(
        requestMeta({
          goalProgress: {
            requiresAction: false,
            actionKinds: [],
            reason: "Simple question.",
          },
          documentOrFileOperation: false,
          fileDelivery: {
            intent: "none",
            path: null,
            wantsChatDelivery: false,
            wantsKbDelivery: false,
            wantsFileOutput: false,
          },
          planning: {
            need: "none",
            reason: "No planning.",
            suggestedStrategy: "Answer directly.",
          },
        }),
      ),
    ).toBe(false);
    expect(
      shouldRequestClarification(
        requestMeta({
          clarification: {
            needed: false,
            reason: "No clarification required.",
            question: null,
            choices: [],
            allowFreeText: false,
            riskIfAssumed: "",
          },
        }),
      ),
    ).toBe(false);
    expect(
      shouldRequestClarification(
        requestMeta({
          clarification: undefined,
        }),
      ),
    ).toBe(false);
  });

  it("creates a durable user_question, waits for the answer, and injects the clarification into the LLM messages", async () => {
    const calls = { count: 0 };
    const ctx = makeCtx(classifierLlm(requestMeta(), calls));
    const request: ControlRequestRecord = {
      requestId: "cr_clarify",
      kind: "user_question",
      state: "pending",
      sessionKey: ctx.sessionKey,
      turnId: ctx.turnId,
      channelName: "general",
      source: "system",
      prompt: "Which output format should I create?",
      proposedInput: {
        reason: "The deliverable format is ambiguous.",
        riskIfAssumed: "The agent may create the wrong file type.",
        choices: [
          { id: "choice_1", label: "DOCX" },
          { id: "choice_2", label: "PDF" },
          { id: "choice_3", label: "Both" },
        ],
        allowFreeText: true,
      },
      createdAt: 1,
      expiresAt: 2,
    };
    const agent: ClarificationGateAgent = {
      askClarification: vi.fn(async (input) => {
        input.onRequest?.(request);
        return {
          request,
          resolved: {
            ...request,
            state: "answered",
            decision: "answered",
            answer: "choice_3",
            resolvedAt: 2,
          },
        };
      }),
    };

    const hook = makeClarificationGateHook({ agent });
    const result = await hook.handler(
      args("투자자 업데이트 예쁘게 문서로 만들어줘"),
      ctx,
    );

    expect(calls.count).toBe(1);
    expect(agent.askClarification).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionKey: ctx.sessionKey,
        turnId: ctx.turnId,
        question: "Which output format should I create?",
        choices: ["DOCX", "PDF", "Both"],
        allowFreeText: true,
        reason: "The deliverable format is ambiguous.",
        riskIfAssumed: "The agent may create the wrong file type.",
        signal: ctx.abortSignal,
        onRequest: expect.any(Function),
      }),
    );
    expect(ctx.emit).toHaveBeenCalledWith({
      type: "control_event",
      seq: 0,
      event: {
        type: "control_request_created",
        request,
      },
    });
    expect(result?.action).toBe("replace");
    if (result?.action !== "replace") throw new Error("expected replace");
    expect(result.value.messages).toHaveLength(2);
    expect(result.value.messages[1]?.role).toBe("user");
    expect(String(result.value.messages[1]?.content)).toContain(
      "<clarification_response>",
    );
    expect(String(result.value.messages[1]?.content)).toContain("Both");
  });

  it("continues without creating a request on later iterations", async () => {
    const ctx = makeCtx(classifierLlm(requestMeta(), { count: 0 }));
    const agent: ClarificationGateAgent = {
      askClarification: vi.fn(),
    };
    const hook = makeClarificationGateHook({ agent });

    const result = await hook.handler(args("make the report", 1), ctx);

    expect(result).toEqual({ action: "continue" });
    expect(agent.askClarification).not.toHaveBeenCalled();
  });
});
