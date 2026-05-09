import { describe, expect, it, vi } from "vitest";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { HookContext } from "../types.js";
import { hashMetaInput } from "./turnMetaClassifier.js";
import { makeMemoryMutationGateHooks } from "./memoryMutationGate.js";

function makeCtx(
  store: ExecutionContractStore,
  transcript: ReadonlyArray<TranscriptEntry> = [],
): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-1",
    llm: {} as never,
    transcript,
    emit: vi.fn(),
    log: vi.fn(),
    agentModel: "gpt-test",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
    executionContract: store,
  };
}

function recordMemoryRequest(store: ExecutionContractStore, userMessage: string): void {
  store.recordRequestMetaClassification({
    turnId: "turn-1",
    inputHash: hashMetaInput(userMessage),
    source: "llm_classifier",
    result: {
      turnMode: { label: "other", confidence: 0.91 },
      skipTdd: false,
      implementationIntent: false,
      documentOrFileOperation: false,
      deterministic: {
        requiresDeterministic: false,
        kinds: [],
        reason: "No deterministic math.",
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
        reason: "Direct memory mutation.",
        suggestedStrategy: "Use MemoryRedact.",
      },
      goalProgress: {
        requiresAction: true,
        actionKinds: ["memory_mutation"],
        reason: "The request asks to modify stored memory.",
      },
      sourceAuthority: {
        longTermMemoryPolicy: "normal",
        currentSourcesAuthoritative: false,
        reason: "Memory mutation request.",
      },
      clarification: {
        needed: false,
        reason: "Target is supplied.",
        question: null,
        choices: [],
        allowFreeText: false,
        riskIfAssumed: "",
      },
      memoryMutation: {
        intent: "redact",
        target: "secret project name",
        rawFileRedactionRequested: true,
        reason: "The user asked to remove stored memory content.",
      },
    },
  });
}

function beforeCommitArgs(userMessage: string, assistantText: string, retryCount = 0) {
  return {
    assistantText,
    toolCallCount: 0,
    toolReadHappened: false,
    userMessage,
    retryCount,
  };
}

describe("memoryMutationGate", () => {
  it("injects a runtime contract telling the model to use MemoryRedact", async () => {
    const userMessage = "메모리에서 secret project name 지워줘";
    const store = new ExecutionContractStore({ now: () => 1 });
    recordMemoryRequest(store, userMessage);
    const hooks = makeMemoryMutationGateHooks();

    const result = await hooks.beforeLLMCall.handler(
      {
        messages: [{ role: "user", content: [{ type: "text", text: userMessage }] }],
        tools: [],
        system: "base system",
        iteration: 0,
      },
      makeCtx(store),
    );

    expect(result?.action).toBe("replace");
    if (result?.action === "replace") {
      expect(result.value.system).toContain("MemoryRedact");
      expect(result.value.system).toContain("secret project name");
      expect(result.value.system).toContain("base system");
    }
  });

  it("blocks memory deletion requests until MemoryRedact evidence exists", async () => {
    const userMessage = "메모리에서 secret project name 지워줘";
    const store = new ExecutionContractStore({ now: () => 1 });
    recordMemoryRequest(store, userMessage);
    const hooks = makeMemoryMutationGateHooks();

    const result = await hooks.beforeCommit.handler(
      beforeCommitArgs(userMessage, "메모리에서 삭제했습니다."),
      makeCtx(store),
    );

    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("MemoryRedact");
    }
  });

  it("allows memory deletion completion when current turn has successful MemoryRedact evidence", async () => {
    const userMessage = "메모리에서 secret project name 지워줘";
    const store = new ExecutionContractStore({ now: () => 1 });
    recordMemoryRequest(store, userMessage);
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-1",
        toolUseId: "tool-1",
        name: "MemoryRedact",
        input: { target_text: "secret project name" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-1",
        toolUseId: "tool-1",
        status: "ok",
        output: JSON.stringify({ matchedCount: 1, verification: { targetStillPresent: false } }),
      },
    ];
    const hooks = makeMemoryMutationGateHooks({
      agent: { readSessionTranscript: async () => transcript },
    });

    const result = await hooks.beforeCommit.handler(
      beforeCommitArgs(userMessage, "메모리에서 삭제했습니다."),
      makeCtx(store, transcript),
    );

    expect(result).toEqual({ action: "continue" });
  });

  it("blocks direct memory edits through generic write tools for memory mutation turns", async () => {
    const userMessage = "메모리에서 secret project name 지워줘";
    const store = new ExecutionContractStore({ now: () => 1 });
    recordMemoryRequest(store, userMessage);
    const hooks = makeMemoryMutationGateHooks();

    const result = await hooks.beforeToolUse.handler(
      {
        toolName: "FileEdit",
        toolUseId: "tool-1",
        input: {
          path: "memory/daily/2026-05-08.md",
          old_string: "secret project name",
          new_string: "",
        },
      },
      makeCtx(store),
    );

    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("MemoryRedact");
    }
  });

  it("blocks Bash during active memory mutation turns so raw file edits cannot bypass MemoryRedact", async () => {
    const userMessage = "메모리에서 secret project name 지워줘";
    const store = new ExecutionContractStore({ now: () => 1 });
    recordMemoryRequest(store, userMessage);
    const hooks = makeMemoryMutationGateHooks();

    const result = await hooks.beforeToolUse.handler(
      {
        toolName: "Bash",
        toolUseId: "tool-1",
        input: {
          command: "date",
        },
      },
      makeCtx(store),
    );

    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("MemoryRedact");
    }
  });
});
