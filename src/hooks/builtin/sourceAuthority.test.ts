import { describe, expect, it } from "vitest";
import type { HookContext } from "../types.js";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";
import { makeSourceAuthorityPromptHook } from "./sourceAuthority.js";
import { makeSourceAuthorityGateHook } from "./sourceAuthorityGate.js";
import type { LLMClient } from "../../transport/LLMClient.js";

function llmReturning(json: unknown, calls: { count: number }): LLMClient {
  return {
    stream: () =>
      (async function* () {
        calls.count += 1;
        yield { kind: "text_delta" as const, delta: JSON.stringify(json) };
        yield { kind: "message_end" as const };
      })(),
  } as unknown as LLMClient;
}

function ctx(contract: ExecutionContractStore, llm: LLMClient): HookContext {
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
    executionContract: contract,
  };
}

describe("sourceAuthority prompt hook", () => {
  it("uses the shared request classifier and records one effective source-authority contract", async () => {
    const contract = new ExecutionContractStore({ now: () => 123 });
    const calls = { count: 0 };
    const hook = makeSourceAuthorityPromptHook();

    const result = await hook.handler(
      {
        messages: [{ role: "user", content: "이 파일 기준으로 다시 답해" }],
        tools: [],
        system:
          "<current-turn-source kind=\"selected_kb\" authority=\"L1\"><kb-context>new source</kb-context></current-turn-source>",
        iteration: 0,
      },
      ctx(
        contract,
        llmReturning(
          {
            turnMode: { label: "other", confidence: 0.9 },
            skipTdd: false,
            implementationIntent: false,
            documentOrFileOperation: false,
            documentExport: { strategy: "none", confidence: 0, renderParityRequired: false, nativeTemplateRequired: false, docxMode: null, reason: "No document export routing requested." },
            deterministic: {
              requiresDeterministic: false,
              kinds: [],
              reason: "No exact calculation.",
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
              need: "none",
              reason: "Answer directly.",
              suggestedStrategy: "Answer directly.",
            },
            goalProgress: {
              requiresAction: false,
              actionKinds: [],
              reason: "Current source is enough.",
            },
            sourceAuthority: {
              longTermMemoryPolicy: "background_only",
              currentSourcesAuthoritative: true,
              reason: "The user asks to use the current file as the basis.",
            },
          },
          calls,
        ),
      ),
    );

    expect(result?.action).toBe("replace");
    expect(calls.count).toBe(1);
    expect(contract.sourceAuthorityForTurn("turn-1")).toEqual([
      expect.objectContaining({
        currentSourceKinds: ["selected_kb"],
        longTermMemoryPolicy: "background_only",
      }),
    ]);
    if (result?.action === "replace") {
      expect(result.value.system).toContain("<source_authority_contract");
      expect(result.value.system).toContain("long_term_memory_policy: background_only");
    }
  });
});

describe("sourceAuthority gate", () => {
  it("blocks a draft that the shared final-answer classifier marks as a source authority violation", async () => {
    const contract = new ExecutionContractStore({ now: () => 456 });
    contract.replaceSourceAuthorityForTurn("turn-1", [
      {
        turnId: "turn-1",
        currentSourceKinds: ["attachment"],
        longTermMemoryPolicy: "disabled",
        classifierReason: "The latest user message says not to use memory.",
      },
    ]);
    contract.recordMemoryRecall({
      turnId: "turn-1",
      source: "qmd",
      path: "memory/old.md",
      continuity: "background",
      distinctivePhrases: ["일본식 이름"],
    });

    const hook = makeSourceAuthorityGateHook();
    const result = await hook.handler(
      {
        assistantText: "예전 메모리의 일본식 이름 기준으로 답하면 됩니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "과거 메모리 참조하지 말고 새 파일만 기준으로 답해",
        retryCount: 0,
      },
      ctx(
        contract,
        llmReturning(
          {
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
            reason: "The draft used disabled long-term memory over the latest file.",
          },
          { count: 0 },
        ),
      ),
    );

    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("[RETRY:SOURCE_AUTHORITY]");
    }
  });
});
