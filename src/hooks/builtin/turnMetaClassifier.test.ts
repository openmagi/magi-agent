import { describe, expect, it } from "vitest";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";
import type { HookContext } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import {
  classifyFinalAnswerMeta,
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

function slowFirstTokenLlm(
  text: string,
  calls: { count: number },
  delayMs = 500,
): LLMClient {
  return {
    stream: () =>
      (async function* () {
        calls.count += 1;
        await new Promise((resolve) => setTimeout(resolve, delayMs));
        yield { kind: "text_delta" as const, delta: text };
        yield { kind: "message_end" as const };
      })(),
  } as unknown as LLMClient;
}

function capturingLlm(text: string, calls: { system: string | null }): LLMClient {
  return {
    stream: (input: { system: string }) =>
      (async function* () {
        calls.system = input.system;
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
        planning: {
          need: "task_board",
          reason: "The work has multiple coordinated deliverables.",
          suggestedStrategy: "Create a TaskBoard and verify each item.",
        },
        goalProgress: {
          requiresAction: true,
          actionKinds: ["browser_interaction", "file_delivery"],
          reason: "The user wants the agent to operate on runtime resources.",
        },
        sourceAuthority: {
          longTermMemoryPolicy: "background_only",
          currentSourcesAuthoritative: true,
          reason: "The user is asking to use the current attachment as the basis.",
        },
        clarification: {
          needed: true,
          reason: "The requested report format changes the implementation.",
          question: "Which report format should I produce?",
          choices: ["DOCX", "PDF", "Both"],
          allowFreeText: true,
          riskIfAssumed: "The agent may create the wrong deliverable.",
        },
        memoryMutation: {
          intent: "redact",
          target: "private launch name",
          rawFileRedactionRequested: true,
          reason: "The user asked to remove it from memory files.",
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
    expect(result.planning).toEqual({
      need: "task_board",
      reason: "The work has multiple coordinated deliverables.",
      suggestedStrategy: "Create a TaskBoard and verify each item.",
    });
    expect(result.goalProgress).toMatchObject({
      requiresAction: true,
      actionKinds: ["browser_interaction", "file_delivery"],
    });
    expect(result.sourceAuthority).toEqual({
      longTermMemoryPolicy: "background_only",
      currentSourcesAuthoritative: true,
      reason: "The user is asking to use the current attachment as the basis.",
    });
    expect(result.clarification).toEqual({
      needed: true,
      reason: "The requested report format changes the implementation.",
      question: "Which report format should I produce?",
      choices: ["DOCX", "PDF", "Both"],
      allowFreeText: true,
      riskIfAssumed: "The agent may create the wrong deliverable.",
    });
    expect(result.memoryMutation).toEqual({
      intent: "redact",
      target: "private launch name",
      rawFileRedactionRequested: true,
      reason: "The user asked to remove it from memory files.",
    });
  });

  it("defaults request planning to none when the classifier omits it", () => {
    const result = parseRequestMetaOutput(
      JSON.stringify({
        turnMode: { label: "other", confidence: 0.7 },
        skipTdd: false,
        implementationIntent: false,
        documentOrFileOperation: false,
        deterministic: {
          requiresDeterministic: false,
          kinds: [],
          reason: "Simple answer.",
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
    );

    expect(result.planning).toEqual({
      need: "none",
      reason: "No runtime planning required.",
      suggestedStrategy: "Answer directly.",
    });
    expect(result.goalProgress).toMatchObject({
      requiresAction: false,
      actionKinds: [],
    });
    expect(result.sourceAuthority).toEqual({
      longTermMemoryPolicy: "normal",
      currentSourcesAuthoritative: false,
      reason: "No source authority override required.",
    });
    expect(result.clarification).toEqual({
      needed: false,
      reason: "No clarification required.",
      question: null,
      choices: [],
      allowFreeText: false,
      riskIfAssumed: "",
    });
    expect(result.memoryMutation).toEqual({
      intent: "none",
      target: null,
      rawFileRedactionRequested: false,
      reason: "No memory mutation requested.",
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
        assistantReportsDeliveryUnverified: false,
        assistantGivesUpEarly: true,
        assistantClaimsActionWithoutEvidence: true,
        assistantClaimsMemoryMutation: true,
        assistantReportsMemoryMutationFailure: false,
        sourceAuthorityViolation: true,
        reason: "The answer claims a file was sent.",
      }),
    );

    expect(result).toMatchObject({
      internalReasoningLeak: true,
      selfClaim: true,
      assistantClaimsFileCreated: true,
      assistantClaimsChatDelivery: true,
      assistantReportsDeliveryFailure: false,
      assistantReportsDeliveryUnverified: false,
      assistantGivesUpEarly: true,
      assistantClaimsActionWithoutEvidence: true,
      assistantClaimsMemoryMutation: true,
      assistantReportsMemoryMutationFailure: false,
      sourceAuthorityViolation: true,
    });
  });
});

describe("final-answer meta classifier prompt", () => {
  it("owns deferred-work classification examples instead of relying on hook regexes", async () => {
    const calls = { system: null as string | null };
    await classifyFinalAnswerMeta({
      llm: capturingLlm(
        JSON.stringify({
          internalReasoningLeak: false,
          lazyRefusal: false,
          selfClaim: false,
          deferralPromise: true,
          assistantClaimsFileCreated: false,
          assistantClaimsChatDelivery: false,
          assistantClaimsKbDelivery: false,
          assistantReportsDeliveryFailure: false,
          assistantReportsDeliveryUnverified: false,
          assistantGivesUpEarly: false,
          assistantClaimsActionWithoutEvidence: false,
          reason: "The draft promises future work.",
        }),
        calls,
      ),
      model: "gpt-5.5",
      userMessage: "어캐돼가누",
      assistantText: "지금 1-3 마무리 시작할 게. 10분 내 완성할 거야.",
    });

    expect(calls.system).toContain("지금 1-3 마무리 시작할 게");
    expect(calls.system).toContain("10분 내 완성할 거야");
    expect(calls.system).toContain("완료되면 결과 보내드리겠습니다");
    expect(calls.system).toContain("I will start now and finish later");
  });

  it("owns plan-only dispatch classification examples instead of relying on hook regexes", async () => {
    const calls = { system: null as string | null };
    await classifyFinalAnswerMeta({
      llm: capturingLlm(
        JSON.stringify({
          internalReasoningLeak: false,
          lazyRefusal: false,
          selfClaim: false,
          deferralPromise: false,
          assistantClaimsFileCreated: false,
          assistantClaimsChatDelivery: false,
          assistantClaimsKbDelivery: false,
          assistantReportsDeliveryFailure: false,
          assistantReportsDeliveryUnverified: false,
          assistantGivesUpEarly: false,
          assistantClaimsActionWithoutEvidence: false,
          assistantEndsWithUnexecutedPlan: true,
          reason: "The draft only announces future subagent dispatch.",
        }),
        calls,
      ),
      model: "gpt-5.5",
      userMessage:
        "Spawn 4 subagents with different SOTA LLMs, compute 1+1, cross-validate, and return the final answer as a .md file.",
      assistantText:
        "I'll spawn 4 subagents with different SOTA LLMs to compute 1+1, then cross-validate and deliver the result as a markdown file.",
    });

    expect(calls.system).toContain("I'll spawn 4 subagents");
    expect(calls.system).toContain("이제 서브에이전트를 띄우겠습니다");
    expect(calls.system).toContain("not complete the turn");
  });
  it("includes source authority context in the shared final-answer classifier call", async () => {
    const calls = { system: null as string | null, userText: null as string | null };
    const llm = {
      stream: (input: { system: string; messages: Array<{ content: Array<{ text: string }> }> }) =>
        (async function* () {
          calls.system = input.system;
          calls.userText = input.messages[0]?.content[0]?.text ?? null;
          yield {
            kind: "text_delta" as const,
            delta: JSON.stringify({
              internalReasoningLeak: false,
              lazyRefusal: false,
              selfClaim: false,
              deferralPromise: false,
              assistantClaimsFileCreated: false,
              assistantClaimsChatDelivery: false,
              assistantClaimsKbDelivery: false,
              assistantReportsDeliveryFailure: false,
              assistantReportsDeliveryUnverified: false,
              assistantGivesUpEarly: false,
              assistantClaimsActionWithoutEvidence: false,
              sourceAuthorityViolation: true,
              reason: "The draft used disabled memory.",
            }),
          };
          yield { kind: "message_end" as const };
        })(),
    } as unknown as LLMClient;

    const result = await classifyFinalAnswerMeta({
      llm,
      model: "gpt-5.5",
      userMessage: "과거 메모리 참조하지 말고 지금 파일만 봐",
      assistantText: "예전 논의에 따르면 일본식 이름이 맞습니다.",
      sourceAuthorityContext:
        "long_term_memory_policy=disabled\nmemory_phrase=일본식 이름",
    });

    expect(result.sourceAuthorityViolation).toBe(true);
    expect(calls.system).toContain("sourceAuthorityViolation");
    expect(calls.userText).toContain("Source authority context:");
    expect(calls.userText).toContain("long_term_memory_policy=disabled");
  });
});

describe("request meta classifier prompt", () => {
  it("owns action-request classification examples for tool and subagent work", async () => {
    const calls = { system: null as string | null };
    await getOrClassifyRequestMeta(
      ctx(
        new ExecutionContractStore({ now: () => 111 }),
        capturingLlm(
          JSON.stringify({
            turnMode: { label: "other", confidence: 0.95 },
            skipTdd: false,
            implementationIntent: false,
            documentOrFileOperation: true,
            deterministic: {
              requiresDeterministic: true,
              kinds: ["calculation"],
              reason: "The task asks for exact arithmetic.",
              suggestedTools: ["Calculation"],
              acceptanceCriteria: ["Use deterministic evidence for 1+1."],
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
              reason: "The user asked for coordinated subagent work and a file deliverable.",
              suggestedStrategy: "Track dispatch, validation, report creation, and delivery.",
            },
            goalProgress: {
              requiresAction: true,
              actionKinds: ["subagent_dispatch", "calculation", "file_delivery"],
              reason: "The request explicitly asks the agent to run work before answering.",
            },
          }),
          calls,
        ),
      ),
      {
        userMessage:
          "Spawn 4 subagents with different SOTA LLMs, compute 1+1, cross-validate, and return the final answer as a .md file.",
      },
    );

    expect(calls.system).toContain("Spawn 4 subagents");
    expect(calls.system).toContain("서브에이전트");
    expect(calls.system).toContain("사람처럼 브라우저");
    expect(calls.system).toContain("include browser_interaction");
    expect(calls.system).toContain("set goalProgress.requiresAction=true");
    expect(calls.system).toContain("memoryMutation");
    expect(calls.system).toContain("persistent memory");
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
          goalProgress: {
            requiresAction: true,
            actionKinds: ["code_change"],
            reason: "Implementation requires tool work.",
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
          assistantReportsDeliveryUnverified: false,
          assistantGivesUpEarly: false,
          assistantClaimsActionWithoutEvidence: false,
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
        assistantReportsDeliveryUnverified: false,
        assistantGivesUpEarly: false,
        assistantClaimsActionWithoutEvidence: false,
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
          assistantReportsDeliveryUnverified: false,
          assistantGivesUpEarly: false,
          assistantClaimsActionWithoutEvidence: false,
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

  it("fails open before the hook guard when final-answer classification stalls", async () => {
    const store = new ExecutionContractStore({ now: () => 1_000 });
    const calls = { count: 0 };
    const context = {
      ...ctx(
        store,
        slowFirstTokenLlm(
          JSON.stringify({
            internalReasoningLeak: false,
            lazyRefusal: false,
            selfClaim: true,
            deferralPromise: false,
            assistantClaimsFileCreated: false,
            assistantClaimsChatDelivery: false,
            assistantClaimsKbDelivery: false,
            assistantReportsDeliveryFailure: false,
            assistantReportsDeliveryUnverified: false,
            assistantGivesUpEarly: false,
            assistantClaimsActionWithoutEvidence: false,
            reason: "late classifier result",
          }),
          calls,
        ),
      ),
      deadlineMs: 100,
    };

    const promise = getOrClassifyFinalAnswerMeta(context, {
      userMessage: "status?",
      assistantText: "The draft is still in progress.",
    });

    const result = await Promise.race([
      promise,
      new Promise<"pending">((resolve) => setTimeout(resolve, 150, "pending")),
    ]);

    expect(result).not.toBe("pending");
    expect(result).toMatchObject({
      selfClaim: false,
      reason: "classifier output was not valid JSON",
    });
    expect(calls.count).toBe(1);
  });
});
