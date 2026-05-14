import { describe, expect, it } from "vitest";
import {
  ExecutionContractStore,
  type RequestMetaClassificationResult,
} from "../../execution/ExecutionContract.js";
import type { LLMClient, LLMMessage, LLMToolDef } from "../../transport/LLMClient.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { HookContext } from "../types.js";
import { hashMetaInput } from "./turnMetaClassifier.js";
import { makeInteractiveWorkControllerHooks } from "./interactiveWorkController.js";

const USER_MESSAGE =
  "Open the investor profiler site, interact with it like a human, and report what happens.";

const BROWSER_REQUEST_META: RequestMetaClassificationResult = {
  turnMode: { label: "other", confidence: 0.94 },
  skipTdd: false,
  implementationIntent: false,
  documentOrFileOperation: false,
  documentExport: { strategy: "none", confidence: 0, renderParityRequired: false, nativeTemplateRequired: false, docxMode: null, reason: "No document export routing requested." },
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
    reason: "The request needs short staged interaction.",
    suggestedStrategy: "Use browser tools and checkpoint progress.",
  },
  goalProgress: {
    requiresAction: true,
    actionKinds: ["browser_interaction"],
    reason: "The user asked the agent to operate a website.",
  },
};

const NON_INTERACTIVE_REQUEST_META: RequestMetaClassificationResult = {
  ...BROWSER_REQUEST_META,
  goalProgress: {
    requiresAction: false,
    actionKinds: [],
    reason: "Pure explanation.",
  },
};

function tool(name: string): LLMToolDef {
  return {
    name,
    description: `${name} tool`,
    input_schema: { type: "object", properties: {} },
  };
}

function storeWithMeta(
  userMessage: string,
  result: RequestMetaClassificationResult,
): ExecutionContractStore {
  const store = new ExecutionContractStore({ now: () => 1_000 });
  store.recordRequestMetaClassification({
    turnId: "turn-1",
    inputHash: hashMetaInput(userMessage),
    source: "llm_classifier",
    result,
  });
  return store;
}

function ctx(input: {
  transcript: TranscriptEntry[];
  store: ExecutionContractStore;
  now?: () => number;
}): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey: "session",
    turnId: "turn-1",
    llm: {} as LLMClient,
    transcript: input.transcript,
    emit: () => {},
    log: () => {},
    agentModel: "gpt-5.5",
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
    executionContract: input.store,
  };
}

function startedTurn(ts = 1_000): TranscriptEntry[] {
  return [
    { kind: "turn_started", ts, turnId: "turn-1", declaredRoute: "direct" },
    { kind: "user_message", ts: ts + 1, turnId: "turn-1", text: USER_MESSAGE },
  ];
}

describe("interactive work controller", () => {
  it("injects a browser work contract before the first LLM call", async () => {
    const hooks = makeInteractiveWorkControllerHooks({ now: () => 2_000 });
    const args = {
      messages: [{ role: "user", content: USER_MESSAGE }] as LLMMessage[],
      system: "base system",
      tools: [tool("Browser"), tool("FileRead")],
      iteration: 0,
    };

    const result = await hooks.beforeLLMCall.handler(
      args,
      ctx({
        transcript: startedTurn(),
        store: storeWithMeta(USER_MESSAGE, BROWSER_REQUEST_META),
      }),
    );

    expect(result?.action).toBe("replace");
    if (result?.action !== "replace") return;
    expect(result.value.system).toContain("<interactive_work_contract>");
    expect(result.value.system).toContain("Browser");
    expect(result.value.tools.map((t) => t.name)).toEqual(["Browser", "FileRead"]);
  });

  it("removes browser tools and asks for a checkpoint after the interactive budget", async () => {
    const hooks = makeInteractiveWorkControllerHooks({
      now: () => 130_000,
      checkpointMs: 120_000,
      maxToolResults: 2,
    });
    const transcript: TranscriptEntry[] = [
      ...startedTurn(1_000),
      {
        kind: "tool_call",
        ts: 2_000,
        turnId: "turn-1",
        toolUseId: "toolu_1",
        name: "Browser",
        input: { action: "open", url: "https://example.com" },
      },
      {
        kind: "tool_result",
        ts: 3_000,
        turnId: "turn-1",
        toolUseId: "toolu_1",
        status: "ok",
        output: "opened",
      },
      {
        kind: "tool_call",
        ts: 4_000,
        turnId: "turn-1",
        toolUseId: "toolu_2",
        name: "Browser",
        input: { action: "snapshot" },
      },
      {
        kind: "tool_result",
        ts: 5_000,
        turnId: "turn-1",
        toolUseId: "toolu_2",
        status: "ok",
        output: "snapshot",
      },
    ];

    const result = await hooks.beforeLLMCall.handler(
      {
        messages: [{ role: "user", content: USER_MESSAGE }] as LLMMessage[],
        system: "base system",
        tools: [tool("Browser"), tool("SocialBrowser"), tool("FileRead")],
        iteration: 3,
      },
      ctx({
        transcript,
        store: storeWithMeta(USER_MESSAGE, BROWSER_REQUEST_META),
      }),
    );

    expect(result?.action).toBe("replace");
    if (result?.action !== "replace") return;
    expect(result.value.tools.map((t) => t.name)).toEqual(["FileRead"]);
    expect(result.value.messages.at(-1)).toMatchObject({
      role: "user",
      content: expect.stringContaining("Runtime interactive checkpoint"),
    });
  });

  it("blocks interactive final answers with no current-turn browser tool evidence", async () => {
    const hooks = makeInteractiveWorkControllerHooks({ now: () => 2_000 });

    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "I will open the browser session now and check the page.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: USER_MESSAGE,
        retryCount: 0,
      },
      ctx({
        transcript: startedTurn(),
        store: storeWithMeta(USER_MESSAGE, BROWSER_REQUEST_META),
      }),
    );

    expect(result?.action).toBe("block");
    if (result?.action !== "block") return;
    expect(result.reason).toContain("[RETRY:INTERACTIVE_TOOL_REQUIRED]");
  });

  it("keeps blocking as a hard rule after the interactive evidence retry is spent", async () => {
    const hooks = makeInteractiveWorkControllerHooks({ now: () => 2_000 });

    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "I will open the browser session now and check the page.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: USER_MESSAGE,
        retryCount: 1,
      },
      ctx({
        transcript: startedTurn(),
        store: storeWithMeta(USER_MESSAGE, BROWSER_REQUEST_META),
      }),
    );

    expect(result?.action).toBe("block");
    if (result?.action !== "block") return;
    expect(result.reason).toContain("[RULE:INTERACTIVE_TOOL_REQUIRED]");
  });

  it("treats current-turn browser tool evidence as sufficient even when final text is brief", async () => {
    const hooks = makeInteractiveWorkControllerHooks({ now: () => 2_000 });
    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "Opened the page; it rendered the login screen.",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: USER_MESSAGE,
        retryCount: 0,
      },
      ctx({
        transcript: [
          ...startedTurn(),
          {
            kind: "tool_call",
            ts: 1_100,
            turnId: "turn-1",
            toolUseId: "toolu_1",
            name: "Browser",
            input: { action: "open", url: "https://example.com" },
          },
        ],
        store: storeWithMeta(USER_MESSAGE, BROWSER_REQUEST_META),
      }),
    );

    expect(result?.action).toBe("continue");
  });

  it("reads persisted transcript through the delegate when hook context transcript is empty", async () => {
    const persistedTranscript: TranscriptEntry[] = [
      ...startedTurn(),
      {
        kind: "tool_call",
        ts: 1_100,
        turnId: "turn-1",
        toolUseId: "toolu_1",
        name: "Browser",
        input: { action: "open", url: "https://example.com" },
      },
    ];
    const hooks = makeInteractiveWorkControllerHooks({
      now: () => 2_000,
      agent: {
        readSessionTranscript: async () => persistedTranscript,
      },
    });

    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "Opened the page; it rendered the login screen.",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: USER_MESSAGE,
        retryCount: 0,
      },
      ctx({
        transcript: [],
        store: storeWithMeta(USER_MESSAGE, BROWSER_REQUEST_META),
      }),
    );

    expect(result?.action).toBe("continue");
  });

  it("does not affect non-interactive requests", async () => {
    const hooks = makeInteractiveWorkControllerHooks({ now: () => 2_000 });

    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "This is a conceptual explanation.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: USER_MESSAGE,
        retryCount: 0,
      },
      ctx({
        transcript: startedTurn(),
        store: storeWithMeta(USER_MESSAGE, NON_INTERACTIVE_REQUEST_META),
      }),
    );

    expect(result?.action).toBe("continue");
  });
});
