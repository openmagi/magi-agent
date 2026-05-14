import { describe, expect, it } from "vitest";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";
import type { HookContext } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import {
  classifyRequestMeta,
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

function capturingRequestLlm(
  text: string,
  calls: { system: string | null; userText: string | null; count: number },
): LLMClient {
  return {
    stream: (input: {
      system: string;
      messages: Array<{ content: Array<{ text: string }> }>;
    }) =>
      (async function* () {
        calls.count += 1;
        calls.system = input.system;
        calls.userText = input.messages[0]?.content[0]?.text ?? null;
        yield { kind: "text_delta" as const, delta: text };
        yield { kind: "message_end" as const };
      })(),
  } as unknown as LLMClient;
}

function ctx(
  store: ExecutionContractStore,
  llm: LLMClient,
  overrides: Partial<Pick<HookContext, "deadlineMs">> = {},
): HookContext {
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
    deadlineMs: overrides.deadlineMs ?? 10_000,
    executionContract: store,
  };
}

function requestMetaPayload(
  overrides: Partial<{
    turnMode: { label: "coding" | "exploratory" | "other"; confidence: number };
    goalProgress: {
      requiresAction: boolean;
      actionKinds: string[];
      reason: string;
    };
  }> = {},
): string {
  return JSON.stringify({
    turnMode: overrides.turnMode ?? { label: "other", confidence: 0.95 },
    skipTdd: false,
    implementationIntent: false,
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
    planning: {
      need: "inline",
      reason: "Short staged work.",
      suggestedStrategy: "Use runtime tools and report evidence.",
    },
    goalProgress: overrides.goalProgress ?? {
      requiresAction: true,
      actionKinds: ["browser_interaction"],
      reason: "The user asked for concrete browser work.",
    },
    sourceAuthority: {
      longTermMemoryPolicy: "normal",
      currentSourcesAuthoritative: false,
      reason: "No source authority override required.",
    },
    clarification: {
      needed: false,
      reason: "No clarification required.",
      question: null,
      choices: [],
      allowFreeText: false,
      riskIfAssumed: "",
    },
  });
}

function finalAnswerMetaPayload(
  overrides: Partial<{
    assistantEndsWithUnexecutedPlan: boolean;
    assistantClaimsActionWithoutEvidence: boolean;
    reason: string;
  }> = {},
): string {
  return JSON.stringify({
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
    assistantClaimsActionWithoutEvidence:
      overrides.assistantClaimsActionWithoutEvidence ?? false,
    assistantEndsWithUnexecutedPlan:
      overrides.assistantEndsWithUnexecutedPlan ?? true,
    assistantNeedsMoreRuntimeWork: true,
    assistantNeedsInteractiveRuntimeWork: true,
    assistantClaimsMemoryMutation: false,
    assistantReportsMemoryMutationFailure: false,
    sourceAuthorityViolation: false,
    reason: overrides.reason ?? "The draft announces future work instead of using tools.",
  });
}

describe("turn meta classifier parsing", () => {
  it("parses request meta as one JSON classifier payload", () => {
    const result = parseRequestMetaOutput(
      JSON.stringify({
        turnMode: { label: "coding", confidence: 0.92 },
        skipTdd: true,
        implementationIntent: true,
        documentOrFileOperation: false,
        documentExport: {
          strategy: "canonical_markdown",
          confidence: 0.91,
          renderParityRequired: true,
          nativeTemplateRequired: false,
          docxMode: "fixed_layout",
          reason: "The user asked for Markdown preview parity across PDF and DOCX.",
        },
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
        research: {
          sourceSensitive: true,
          reason: "The user asks for current facts that need external source verification.",
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
    expect(result.documentExport).toEqual({
      strategy: "canonical_markdown",
      confidence: 0.91,
      renderParityRequired: true,
      nativeTemplateRequired: false,
      docxMode: "fixed_layout",
      reason: "The user asked for Markdown preview parity across PDF and DOCX.",
    });
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
    expect(result.research).toEqual({
      sourceSensitive: true,
      reason: "The user asks for current facts that need external source verification.",
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
    expect(result.research).toEqual({
      sourceSensitive: false,
      reason: "No external source verification required.",
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
    expect(result.documentExport).toEqual({
      strategy: "none",
      confidence: 0,
      renderParityRequired: false,
      nativeTemplateRequired: false,
      docxMode: null,
      reason: "No document export routing requested.",
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
        assistantNeedsMoreRuntimeWork: true,
        assistantNeedsInteractiveRuntimeWork: true,
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
      assistantNeedsMoreRuntimeWork: true,
      assistantNeedsInteractiveRuntimeWork: true,
      assistantClaimsMemoryMutation: true,
      assistantReportsMemoryMutationFailure: false,
      sourceAuthorityViolation: true,
    });
  });
});

describe("request meta classifier prompt", () => {
  it("owns research source-sensitivity examples instead of relying on hook regexes", async () => {
    const calls = { system: null as string | null };
    await classifyRequestMeta({
      llm: capturingLlm(
        JSON.stringify({
          turnMode: { label: "other", confidence: 0.8 },
          skipTdd: false,
          implementationIntent: false,
          documentOrFileOperation: false,
          deterministic: {
            requiresDeterministic: false,
            kinds: [],
            reason: "No deterministic calculation.",
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
          research: {
            sourceSensitive: false,
            reason: "The user asks for UI operation, not a sourced factual answer.",
          },
        }),
        calls,
      ),
      model: "gpt-5.5",
      userMessage:
        "API \uC785\uB825 \uBC29\uC2DD\uC73C\uB85C \uC6B0\uD68C\uD558\uC9C0 \uB9D0\uACE0 \uC2E4\uC81C \uC0AC\uB78C\uCC98\uB7FC \uBE0C\uB77C\uC6B0\uC800\uC5D0\uC11C \uC6F9\uC11C\uBE44\uC2A4\uB97C \uD14C\uC2A4\uD2B8\uD574\uC918.",
    });

    expect(calls.system).toContain("research");
    expect(calls.system).toContain("sourceSensitive");
    expect(calls.system).toContain("Don't bypass via API input");
    expect(calls.system).toContain("browser/UI operation");
    expect(calls.system).toContain("market sentiment");
    expect(calls.system).toContain("Do not rely on keyword matching");
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
          assistantNeedsMoreRuntimeWork: true,
          reason: "The draft promises future work.",
        }),
        calls,
      ),
      model: "gpt-5.5",
      userMessage: "\uC5B4\uCE90\uB3FC\uAC00\uB204",
      assistantText: "\uC9C0\uAE08 1-3 \uB9C8\uBB34\uB9AC \uC2DC\uC791\uD560 \uAC8C. 10\uBD84 \uB0B4 \uC644\uC131\uD560 \uAC70\uC57C.",
    });

    expect(calls.system).toContain("I will start wrapping up 1-3 now");
    expect(calls.system).toContain("I will finish within 10 minutes");
    expect(calls.system).toContain("I will send the results when done");
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
          assistantNeedsMoreRuntimeWork: true,
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
    expect(calls.system).toContain("I will now spawn the subagents");
    expect(calls.system).toContain("KnowledgeSearch and Browser");
    expect(calls.system).toContain("not complete the turn");
    expect(calls.system).toContain("assistantNeedsMoreRuntimeWork");
    expect(calls.system).toContain("assistantNeedsInteractiveRuntimeWork");
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
              assistantNeedsMoreRuntimeWork: false,
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
      userMessage: "\uACFC\uAC70 \uBA54\uBAA8\uB9AC \uCC38\uC870\uD558\uC9C0 \uB9D0\uACE0 \uC9C0\uAE08 \uD30C\uC77C\uB9CC \uBD10",
      assistantText: "\uC608\uC804 \uB17C\uC758\uC5D0 \uB530\uB974\uBA74 \uC77C\uBCF8\uC2DD \uC774\uB984\uC774 \uB9DE\uC2B5\uB2C8\uB2E4.",
      sourceAuthorityContext:
        "long_term_memory_policy=disabled\nmemory_phrase=\uC77C\uBCF8\uC2DD \uC774\uB984",
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
    expect(calls.system).toContain("have each calculate and verify");
    expect(calls.system).toContain("clicking and typing directly in the browser");
    expect(calls.system).toContain("include browser_interaction");
    expect(calls.system).toContain("set goalProgress.requiresAction=true");
    expect(calls.system).toContain("memoryMutation");
    expect(calls.system).toContain("persistent memory");
    expect(calls.system).toContain("documentExport");
    expect(calls.system).toContain("canonical_markdown");
    expect(calls.system).toContain("native_template");
  });
});

describe("turn meta classifier caching", () => {
  it("includes recent conversation context when classifying terse follow-up work requests", async () => {
    const store = new ExecutionContractStore({ now: () => 123 });
    const calls = { system: null as string | null, userText: null as string | null, count: 0 };
    const context = {
      ...ctx(
        store,
        capturingRequestLlm(
          JSON.stringify({
            turnMode: { label: "other", confidence: 0.9 },
            skipTdd: false,
            implementationIntent: false,
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
              actionKinds: ["browser_interaction"],
              reason: "The prior task asks the agent to operate a browser UI.",
            },
          }),
          calls,
        ),
      ),
      transcript: [
        {
          kind: "turn_started" as const,
          ts: 1,
          turnId: "turn-prev",
          declaredRoute: "direct",
        },
        {
          kind: "user_message" as const,
          ts: 2,
          turnId: "turn-prev",
          text:
            "Open https://investor-profiler.example and test it like a human with the browser, mouse, and keyboard.",
        },
        {
          kind: "assistant_text" as const,
          ts: 3,
          turnId: "turn-prev",
          text: "I will open the browser now and start testing the login flow.",
        },
        {
          kind: "turn_committed" as const,
          ts: 4,
          turnId: "turn-prev",
          inputTokens: 10,
          outputTokens: 10,
        },
        {
          kind: "turn_started" as const,
          ts: 5,
          turnId: "turn-1",
          declaredRoute: "direct",
        },
        {
          kind: "user_message" as const,
          ts: 6,
          turnId: "turn-1",
          text: "\uD558\uACE0 \uC788\uC5B4?",
        },
      ],
    };

    const result = await getOrClassifyRequestMeta(context, {
      userMessage: "\uD558\uACE0 \uC788\uC5B4?",
    });

    expect(result.goalProgress.actionKinds).toContain("browser_interaction");
    expect(calls.count).toBe(1);
    expect(calls.userText).toContain("Recent conversation context");
    expect(calls.userText).toContain("test it like a human with the browser");
    expect(calls.userText).toContain("Current user request:");
    expect(calls.userText).toContain("\uD558\uACE0 \uC788\uC5B4?");
  });

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

  it("does not cache fail-open request metadata after classifier timeout", async () => {
    const store = new ExecutionContractStore({ now: () => 123 });
    const userMessage = "Open the site in a browser and report what the page shows.";
    const payload = requestMetaPayload();
    const slowCalls = { count: 0 };

    const first = await getOrClassifyRequestMeta(
      ctx(store, slowFirstTokenLlm(payload, slowCalls, 25), { deadlineMs: 5 }),
      { userMessage },
    );

    expect(first.goalProgress.requiresAction).toBe(false);
    expect(first.goalProgress.reason).toBe("classifier output was not valid JSON");
    expect(slowCalls.count).toBe(1);

    const fastCalls = { count: 0 };
    const second = await getOrClassifyRequestMeta(
      ctx(store, mockLlm(payload, fastCalls), { deadlineMs: 10_000 }),
      { userMessage },
    );

    expect(second.goalProgress.requiresAction).toBe(true);
    expect(second.goalProgress.actionKinds).toContain("browser_interaction");
    expect(fastCalls.count).toBe(1);
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
          assistantNeedsMoreRuntimeWork: false,
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

  it("does not cache fail-open final-answer metadata after classifier timeout", async () => {
    const store = new ExecutionContractStore({ now: () => 456 });
    const userMessage = "Open the site in a browser and report what the page shows.";
    const assistantText = "I will open the browser now and report back.";
    const payload = finalAnswerMetaPayload();
    const slowCalls = { count: 0 };

    const first = await getOrClassifyFinalAnswerMeta(
      ctx(store, slowFirstTokenLlm(payload, slowCalls, 25), { deadlineMs: 5 }),
      { userMessage, assistantText },
    );

    expect(first.assistantEndsWithUnexecutedPlan).toBe(false);
    expect(first.reason).toBe("classifier output was not valid JSON");
    expect(slowCalls.count).toBe(1);

    const fastCalls = { count: 0 };
    const second = await getOrClassifyFinalAnswerMeta(
      ctx(store, mockLlm(payload, fastCalls), { deadlineMs: 10_000 }),
      { userMessage, assistantText },
    );

    expect(second.assistantEndsWithUnexecutedPlan).toBe(true);
    expect(fastCalls.count).toBe(1);
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
          assistantNeedsMoreRuntimeWork: true,
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
            assistantNeedsMoreRuntimeWork: false,
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
