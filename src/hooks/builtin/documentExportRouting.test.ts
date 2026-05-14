import { describe, expect, it } from "vitest";
import {
  ExecutionContractStore,
  type RequestMetaClassificationResult,
} from "../../execution/ExecutionContract.js";
import type { HookArgs, HookContext } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import {
  makeDocumentExportRoutingHook,
  resolveDocumentExportRoute,
} from "./documentExportRouting.js";
import { hashMetaInput } from "./turnMetaClassifier.js";

function neverLlm(): LLMClient {
  return {
    stream: () => {
      throw new Error("request meta should be read from cache");
    },
  } as unknown as LLMClient;
}

function meta(
  documentExport: RequestMetaClassificationResult["documentExport"],
): RequestMetaClassificationResult {
  return {
    turnMode: { label: "other", confidence: 0.9 },
    skipTdd: false,
    implementationIntent: false,
    documentOrFileOperation: true,
    documentExport,
    deterministic: {
      requiresDeterministic: false,
      kinds: [],
      reason: "No exact deterministic work.",
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
      reason: "Document creation should be tracked.",
      suggestedStrategy: "Create and verify the document.",
    },
    goalProgress: {
      requiresAction: true,
      actionKinds: ["file_delivery"],
      reason: "The user wants a document artifact.",
    },
    sourceAuthority: {
      longTermMemoryPolicy: "normal",
      currentSourcesAuthoritative: false,
      reason: "No source override.",
    },
    research: {
      sourceSensitive: false,
      reason: "No research sensitivity.",
    },
    clarification: {
      needed: false,
      reason: "No clarification required.",
      question: null,
      choices: [],
      allowFreeText: false,
      riskIfAssumed: "",
    },
    memoryMutation: {
      intent: "none",
      target: null,
      rawFileRedactionRequested: false,
      reason: "No memory mutation requested.",
    },
  };
}

function canonicalMeta(
  overrides: Partial<RequestMetaClassificationResult["documentExport"]> = {},
): RequestMetaClassificationResult {
  return meta({
    strategy: "canonical_markdown",
    confidence: 0.92,
    renderParityRequired: true,
    nativeTemplateRequired: false,
    docxMode: null,
    reason: "The user asked for Markdown render parity.",
    ...overrides,
  });
}

function ctx(
  store: ExecutionContractStore,
  userMessage: string,
): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey: "session",
    turnId: "turn-1",
    llm: neverLlm(),
    transcript: [
      {
        kind: "user_message",
        ts: 100,
        turnId: "turn-1",
        text: userMessage,
      },
    ],
    emit: () => {},
    log: () => {},
    agentModel: "gpt-5.5",
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
    executionContract: store,
  };
}

function documentWriteArgs(input: Record<string, unknown>): HookArgs["beforeToolUse"] {
  return {
    toolName: "DocumentWrite",
    toolUseId: "tool-1",
    input,
  };
}

describe("document export routing", () => {
  it("routes omitted DocumentWrite renderer to canonical markdown from cached request meta", async () => {
    const userMessage = "md로 먼저 작성하고 웹 렌더와 동일한 PDF로 변환해줘";
    const store = new ExecutionContractStore({ now: () => 123 });
    store.recordRequestMetaClassification({
      turnId: "turn-1",
      inputHash: hashMetaInput(userMessage),
      source: "llm_classifier",
      result: canonicalMeta(),
    });
    const hook = makeDocumentExportRoutingHook();

    const result = await hook.handler(
      documentWriteArgs({
        mode: "create",
        format: "pdf",
        title: "IC Memo",
        filename: "ic-memo.pdf",
        source: "# IC Memo",
      }),
      ctx(store, userMessage),
    );

    expect(result).toEqual({
      action: "replace",
      value: {
        toolName: "DocumentWrite",
        toolUseId: "tool-1",
        input: {
          mode: "create",
          format: "pdf",
          title: "IC Memo",
          filename: "ic-memo.pdf",
          source: "# IC Memo",
          renderer: "canonical_markdown",
        },
      },
    });
  });

  it("does not override an explicit default renderer", () => {
    const route = resolveDocumentExportRoute(
      {
        mode: "create",
        format: "pdf",
        renderer: "default",
        title: "Report",
        filename: "report.pdf",
        source: "# Report",
      },
      canonicalMeta(),
    );

    expect(route).toEqual({ strategy: "default", changedInput: null });
  });

  it("keeps native template and edit flows on the default renderer", () => {
    const templateRoute = resolveDocumentExportRoute(
      {
        mode: "create",
        format: "docx",
        template: "report",
        title: "Report",
        filename: "report.docx",
        source: "# Report",
      },
      canonicalMeta(),
    );
    const editRoute = resolveDocumentExportRoute(
      {
        mode: "edit",
        format: "docx",
        title: "Report",
        filename: "report.docx",
        source: "# Report",
      },
      canonicalMeta(),
    );

    expect(templateRoute).toEqual({ strategy: "default", changedInput: null });
    expect(editRoute).toEqual({ strategy: "default", changedInput: null });
  });

  it("adds fixed-layout docx mode when render parity requires it", () => {
    const route = resolveDocumentExportRoute(
      {
        mode: "create",
        format: "docx",
        title: "IC Memo",
        filename: "ic-memo.docx",
        source: "# IC Memo",
      },
      canonicalMeta({ docxMode: "fixed_layout" }),
    );

    expect(route).toEqual({
      strategy: "canonical_markdown",
      changedInput: {
        mode: "create",
        format: "docx",
        title: "IC Memo",
        filename: "ic-memo.docx",
        source: "# IC Memo",
        renderer: "canonical_markdown",
        docxMode: "fixed_layout",
      },
    });
  });

  it("does not route low-confidence classifier results to canonical markdown", () => {
    const route = resolveDocumentExportRoute(
      {
        mode: "create",
        format: "pdf",
        title: "Report",
        filename: "report.pdf",
        source: "# Report",
      },
      canonicalMeta({ confidence: 0.51 }),
    );

    expect(route).toEqual({ strategy: "default", changedInput: null });
  });
});
