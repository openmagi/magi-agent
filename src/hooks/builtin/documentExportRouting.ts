import type {
  HookArgs,
  HookContext,
  HookResult,
  RegisteredHook,
} from "../types.js";
import type { RequestMetaClassificationResult } from "../../execution/ExecutionContract.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import { getOrClassifyRequestMeta } from "./turnMetaClassifier.js";

type DocumentExportStrategy = "default" | "canonical_markdown";
type DocumentWriteInputLike = Record<string, unknown>;

export interface DocumentExportRoute {
  strategy: DocumentExportStrategy;
  changedInput: DocumentWriteInputLike | null;
}

function isEnabled(): boolean {
  const raw = process.env.MAGI_DOCUMENT_EXPORT_ROUTING;
  if (raw === undefined || raw === null) return true;
  const value = raw.trim().toLowerCase();
  return value === "" || value === "on" || value === "true" || value === "1";
}

function isRecord(input: unknown): input is DocumentWriteInputLike {
  return !!input && typeof input === "object" && !Array.isArray(input);
}

function supportsCanonicalFormat(format: unknown): boolean {
  return format === "html" || format === "pdf" || format === "docx";
}

function isStructuredSource(source: unknown): boolean {
  if (!isRecord(source)) return false;
  const kind = typeof source.kind === "string"
    ? source.kind
    : typeof source.type === "string"
      ? source.type
      : null;
  return kind === "structured" || Array.isArray(source.blocks) || typeof source.blocksFile === "string";
}

function hasNativeDocumentGuard(input: DocumentWriteInputLike): boolean {
  return (
    input.mode === "edit" ||
    input.format === "hwpx" ||
    typeof input.template === "string" ||
    isStructuredSource(input.source)
  );
}

function canAutoRoute(input: DocumentWriteInputLike): boolean {
  const renderer = input.renderer;
  return renderer === undefined || renderer === null || renderer === "auto";
}

export function resolveDocumentExportRoute(
  input: unknown,
  meta: RequestMetaClassificationResult,
): DocumentExportRoute {
  if (!isRecord(input)) return { strategy: "default", changedInput: null };
  if (!canAutoRoute(input)) return { strategy: "default", changedInput: null };
  if (!supportsCanonicalFormat(input.format)) return { strategy: "default", changedInput: null };
  if (hasNativeDocumentGuard(input)) return { strategy: "default", changedInput: null };

  const intent = meta.documentExport;
  if (!meta.documentOrFileOperation || !intent) {
    return { strategy: "default", changedInput: null };
  }
  if (
    intent.strategy !== "canonical_markdown" ||
    intent.confidence < 0.7 ||
    intent.nativeTemplateRequired
  ) {
    return { strategy: "default", changedInput: null };
  }

  const changedInput: DocumentWriteInputLike = {
    ...input,
    renderer: "canonical_markdown",
  };
  if (
    input.format === "docx" &&
    typeof input.docxMode !== "string" &&
    (intent.docxMode === "editable" || intent.docxMode === "fixed_layout")
  ) {
    changedInput.docxMode = intent.docxMode;
  }
  return { strategy: "canonical_markdown", changedInput };
}

function latestUserTextFromTranscript(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): string | null {
  for (let i = transcript.length - 1; i >= 0; i--) {
    const entry = transcript[i];
    if (entry?.kind === "user_message" && entry.turnId === turnId && entry.text.trim()) {
      return entry.text;
    }
  }
  for (let i = transcript.length - 1; i >= 0; i--) {
    const entry = transcript[i];
    if (entry?.kind === "user_message" && entry.text.trim()) return entry.text;
  }
  return null;
}

export function makeDocumentExportRoutingHook(): RegisteredHook<"beforeToolUse"> {
  return {
    name: "builtin:document-export-routing",
    point: "beforeToolUse",
    priority: 18,
    blocking: true,
    failOpen: true,
    timeoutMs: 4_000,
    handler: async (
      args: HookArgs["beforeToolUse"],
      ctx: HookContext,
    ): Promise<HookResult<HookArgs["beforeToolUse"]> | void> => {
      if (!isEnabled() || args.toolName !== "DocumentWrite") {
        return { action: "continue" };
      }
      const userMessage = latestUserTextFromTranscript(ctx.transcript, ctx.turnId);
      if (!userMessage) return { action: "continue" };

      const meta = await getOrClassifyRequestMeta(ctx, { userMessage });
      const route = resolveDocumentExportRoute(args.input, meta);
      if (!route.changedInput) return { action: "continue" };
      return {
        action: "replace",
        value: {
          ...args,
          input: route.changedInput,
        },
      };
    },
  };
}
