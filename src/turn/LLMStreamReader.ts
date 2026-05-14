/**
 * LLMStreamReader — consumes a single LLM round-trip.
 *
 * Extracted from `Turn.streamOneIteration` (R3 refactor, 2026-04-19).
 * Owns stream event assembly: text_delta / thinking_delta +
 * thinking_signature (T4-18) / tool_use_start + tool_use_input_delta /
 * block_stop / message_end / error. Returns the ordered content blocks
 * ready to be re-emitted to the model on the next round.
 */

import type {
  LLMClient,
  LLMContentBlock,
  LLMEvent,
  LLMMessage,
  LLMStreamRequest,
  LLMToolDef,
  LLMUsage,
} from "../transport/LLMClient.js";
import type { SseWriter } from "../transport/SseWriter.js";
import { RepetitionDetector } from "./RepetitionDetector.js";
import type { SystemBlock } from "../prompt/CacheOptimizedPrompt.js";

export type StopReasonRaw =
  | "end_turn"
  | "tool_use"
  | "max_tokens"
  | "stop_sequence"
  | "refusal"
  | "pause_turn"
  | null;

export interface LLMStreamReaderDeps {
  readonly llm: LLMClient;
  readonly model: string;
  readonly sse: SseWriter;
  readonly abortSignal?: AbortSignal;
  /**
   * Called when the stream emits an `error` event or the upstream
   * connect throws. Receives `(code, err)`; the reader then re-throws
   * the error.
   */
  readonly onError: (code: string, err: unknown) => void;
}

export interface LLMStreamReaderResult {
  blocks: LLMContentBlock[];
  stopReason: StopReasonRaw;
  usage: LLMUsage;
}

/**
 * Run one `/v1/messages` round-trip and materialise the assistant
 * blocks. The returned blocks preserve streamed order; thinking blocks
 * carry their signatures so Anthropic accepts a replay on the next
 * turn (T4-18).
 */
export interface ReadOneOptions {
  /** Override thinking mode for this call (e.g. disable for empty-response recovery). */
  thinkingOverride?: { type: "adaptive" } | { type: "disabled" };
  /** Cross-service diagnostic trace ID propagated to api-proxy. */
  traceId?: string;
  /** Non-authoritative routing metadata passed to LLM transport/proxy logs. */
  routing?: LLMStreamRequest["routing"];
}

const MAX_DOCUMENT_DRAFT_PREVIEW_CHARS = 4_000;

interface ToolInputAccumulator {
  id: string;
  name: string;
  inputJson: string;
}

interface DocumentDraftState {
  contentPreview: string;
  contentLength: number;
  truncated: boolean;
  filename?: string;
}

interface DocumentDraftCandidate {
  filename?: string;
  format: "md" | "txt";
  content: string;
}

function documentFormatFromPath(filePath: string | undefined): "md" | "txt" | null {
  const normalized = (filePath ?? "").split(/[?#]/, 1)[0]?.toLowerCase() ?? "";
  if (normalized.endsWith(".md") || normalized.endsWith(".markdown")) return "md";
  if (normalized.endsWith(".txt")) return "txt";
  return null;
}

function decodeJsonStringPrefix(raw: string, start: number): string {
  let value = "";
  let escaped = false;
  for (let index = start; index < raw.length; index += 1) {
    const char = raw[index];
    if (escaped) {
      switch (char) {
        case "n":
          value += "\n";
          break;
        case "r":
          value += "\r";
          break;
        case "t":
          value += "\t";
          break;
        case "b":
          value += "\b";
          break;
        case "f":
          value += "\f";
          break;
        case "u": {
          const hex = raw.slice(index + 1, index + 5);
          if (/^[0-9a-f]{4}$/i.test(hex)) {
            value += String.fromCharCode(Number.parseInt(hex, 16));
            index += 4;
          }
          break;
        }
        default:
          value += char ?? "";
          break;
      }
      escaped = false;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (char === "\"") break;
    value += char ?? "";
  }
  return value;
}

function jsonStringFieldPrefix(raw: string, key: string): string | undefined {
  const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`"${escapedKey}"\\s*:\\s*"`, "g");
  const match = pattern.exec(raw);
  if (!match) return undefined;
  return decodeJsonStringPrefix(raw, match.index + match[0].length);
}

function firstJsonStringFieldPrefix(raw: string, keys: readonly string[]): string | undefined {
  for (const key of keys) {
    const value = jsonStringFieldPrefix(raw, key);
    if (value !== undefined) return value;
  }
  return undefined;
}

function parsedObject(raw: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function stringField(object: Record<string, unknown> | null, keys: readonly string[]): string | undefined {
  if (!object) return undefined;
  for (const key of keys) {
    const value = object[key];
    if (typeof value === "string") return value;
  }
  return undefined;
}

function documentWriteSourceContent(source: unknown): string | undefined {
  if (typeof source === "string") return source;
  if (!source || typeof source !== "object" || Array.isArray(source)) return undefined;
  return stringField(source as Record<string, unknown>, ["content", "markdown", "text"]);
}

function draftCandidateFromTool(tool: ToolInputAccumulator): DocumentDraftCandidate | null {
  if (tool.name !== "FileWrite" && tool.name !== "DocumentWrite") return null;
  const parsed = parsedObject(tool.inputJson);

  if (tool.name === "FileWrite") {
    const filename =
      stringField(parsed, ["path", "file_path", "filepath", "filename"]) ??
      firstJsonStringFieldPrefix(tool.inputJson, ["path", "file_path", "filepath", "filename"]);
    const format = documentFormatFromPath(filename);
    if (!format) return null;
    const content =
      stringField(parsed, ["content"]) ??
      jsonStringFieldPrefix(tool.inputJson, "content") ??
      "";
    return content ? { filename, format, content } : null;
  }

  const filename =
    stringField(parsed, ["filename", "path"]) ??
    firstJsonStringFieldPrefix(tool.inputJson, ["filename", "path"]);
  const formatValue =
    stringField(parsed, ["format"]) ??
    jsonStringFieldPrefix(tool.inputJson, "format");
  const format =
    formatValue === "md" || formatValue === "txt"
      ? formatValue
      : documentFormatFromPath(filename);
  if (format !== "md" && format !== "txt") return null;
  const content =
    documentWriteSourceContent(parsed?.source) ??
    firstJsonStringFieldPrefix(tool.inputJson, ["content", "markdown", "text", "source"]) ??
    "";
  return content ? { filename, format, content } : null;
}

function previewTail(content: string): { contentPreview: string; truncated: boolean } {
  if (content.length <= MAX_DOCUMENT_DRAFT_PREVIEW_CHARS) {
    return { contentPreview: content, truncated: false };
  }
  return {
    contentPreview: content.slice(-MAX_DOCUMENT_DRAFT_PREVIEW_CHARS),
    truncated: true,
  };
}

export async function readOne(
  deps: LLMStreamReaderDeps,
  systemPrompt: string | SystemBlock[],
  messages: LLMMessage[],
  toolDefs: LLMToolDef[],
  options?: ReadOneOptions,
): Promise<LLMStreamReaderResult> {
  throwIfAborted(deps.abortSignal);
  // Per-block accumulators — indexed by blockIndex from the SSE.
  const textByIndex = new Map<number, string>();
  // T4-18: thinking blocks must be preserved across iterations with
  // their signatures so Anthropic accepts the replayed assistant
  // trajectory on subsequent turns. Accumulate text + signature per
  // block, assembled alongside text/tool_use blocks below.
  const thinkingByIndex = new Map<
    number,
    { thinking: string; signature: string }
  >();
  const toolByIndex = new Map<number, ToolInputAccumulator>();
  const documentDraftByIndex = new Map<number, DocumentDraftState>();
  const blockOrder: number[] = [];

  let stream: AsyncGenerator<LLMEvent, void, void>;
  try {
    const systemValue: LLMStreamRequest["system"] = Array.isArray(systemPrompt)
      ? systemPrompt
      : systemPrompt || undefined;
    stream = deps.llm.stream({
      model: deps.model,
      system: systemValue,
      messages,
      tools: toolDefs.length ? toolDefs : undefined,
      ...(deps.abortSignal ? { signal: deps.abortSignal } : {}),
      ...(options?.thinkingOverride ? { thinking: options.thinkingOverride } : {}),
      ...(options?.routing ? { routing: options.routing } : {}),
    });
  } catch (err) {
    deps.onError("llm_connect_failed", err);
    throw err;
  }

  let stopReason: StopReasonRaw = null;
  let usage: LLMUsage = { inputTokens: 0, outputTokens: 0 };
  let repetitionAborted = false;

  // Streaming repetition detector — catches LLM text degeneration
  // (same sentence repeating indefinitely within a single response).
  const repetitionDetector = new RepetitionDetector();

  for await (const evt of stream) {
    throwIfAborted(deps.abortSignal);
    switch (evt.kind) {
      case "text_delta": {
        const prev = textByIndex.get(evt.blockIndex) ?? "";
        if (prev.length === 0) blockOrder.push(evt.blockIndex);
        textByIndex.set(evt.blockIndex, prev + evt.delta);

        // Check for degenerate repetition before emitting to client.
        const repResult = repetitionDetector.feed(evt.delta);
        if (repResult.detected) {
          console.warn(
            `[core-agent] REPETITION DETECTED — aborting stream.` +
            ` pattern="${repResult.pattern}" count=${repResult.count}`,
          );
          deps.sse.agent({
            type: "text_delta",
            delta: "\n\n⚠️ [반복 감지됨 — 응답이 중단되었습니다]",
          });
          repetitionAborted = true;
          break;
        }

        // Emit on the structured `event: agent` channel only. The legacy
        // OpenAI-compat `choices[0].delta.content` path was previously
        // dual-emitted here as a migration aid for pre-§7.9 clients; all
        // live clients (web/mobile chat-client, Telegram/Discord capture
        // writer) now consume the agent channel, and dual-emit caused
        // every token to render twice once the client wired text_delta
        // (commit eda9047c, 2026-04-20). `legacyDelta` stays on the ABI
        // for `legacyFinish` (`data: [DONE]`) and any future adapter.
        deps.sse.agent({ type: "text_delta", delta: evt.delta });
        break;
      }
      case "thinking_delta": {
        const prev = thinkingByIndex.get(evt.blockIndex);
        if (!prev) {
          blockOrder.push(evt.blockIndex);
          thinkingByIndex.set(evt.blockIndex, {
            thinking: evt.delta,
            signature: "",
          });
        } else {
          prev.thinking += evt.delta;
        }
        deps.sse.agent({ type: "thinking_delta", delta: evt.delta });
        break;
      }
      case "thinking_signature": {
        // Signature arrives at the end of the thinking block. If no
        // delta preceded it (edge case), still register the block.
        const prev = thinkingByIndex.get(evt.blockIndex);
        if (prev) {
          prev.signature = evt.signature;
        } else {
          blockOrder.push(evt.blockIndex);
          thinkingByIndex.set(evt.blockIndex, {
            thinking: "",
            signature: evt.signature,
          });
        }
        break;
      }
      case "tool_use_start": {
        toolByIndex.set(evt.blockIndex, {
          id: evt.id,
          name: evt.name,
          inputJson: "",
        });
        blockOrder.push(evt.blockIndex);
        // Note: tool_start is NOT emitted here — deferred to runTools
        // so we can include the full input_preview (input is streamed
        // via tool_use_input_delta and not complete until block_stop).
        break;
      }
      case "tool_use_input_delta": {
        const cur = toolByIndex.get(evt.blockIndex);
        if (cur) {
          cur.inputJson += evt.partial;
          const draft = draftCandidateFromTool(cur);
          if (draft) {
            const preview = previewTail(draft.content);
            const previous = documentDraftByIndex.get(evt.blockIndex);
            if (
              !previous ||
              previous.contentPreview !== preview.contentPreview ||
              previous.contentLength !== draft.content.length ||
              previous.filename !== draft.filename
            ) {
              deps.sse.agent({
                type: "document_draft",
                id: cur.id,
                ...(draft.filename ? { filename: draft.filename } : {}),
                format: draft.format,
                contentPreview: preview.contentPreview,
                contentLength: draft.content.length,
                truncated: preview.truncated,
              });
              documentDraftByIndex.set(evt.blockIndex, {
                ...preview,
                contentLength: draft.content.length,
                ...(draft.filename ? { filename: draft.filename } : {}),
              });
            }
          }
        }
        break;
      }
      case "block_stop":
        // Nothing to do; finalization happens when we assemble blocks.
        break;
      case "message_end":
        stopReason = evt.stopReason;
        usage = evt.usage;
        break;
      case "error":
        deps.onError(evt.code, new Error(evt.message));
        throw new Error(`${evt.code}: ${evt.message}`);
    }
    // Break the stream consumption loop if repetition was detected.
    if (repetitionAborted) break;
    throwIfAborted(deps.abortSignal);
  }

  // If repetition was detected, force end_turn so the commit pipeline
  // finalizes cleanly rather than attempting tool dispatch or recovery.
  if (repetitionAborted) {
    stopReason = "end_turn";
  }

  // Assemble blocks in streamed order, de-duped.
  const seen = new Set<number>();
  const blocks: LLMContentBlock[] = [];
  for (const idx of blockOrder) {
    if (seen.has(idx)) continue;
    seen.add(idx);
    const txt = textByIndex.get(idx);
    if (txt !== undefined) {
      blocks.push({ type: "text", text: txt });
      continue;
    }
    // T4-18: assemble thinking block with signature for replay.
    const thinking = thinkingByIndex.get(idx);
    if (thinking !== undefined) {
      blocks.push({
        type: "thinking",
        thinking: thinking.thinking,
        signature: thinking.signature,
      });
      continue;
    }
    const tu = toolByIndex.get(idx);
    if (tu) {
      let input: unknown = {};
      if (tu.inputJson.length > 0) {
        try {
          input = JSON.parse(tu.inputJson);
        } catch {
          input = { _malformed: true, _raw: tu.inputJson };
        }
      }
      blocks.push({ type: "tool_use", id: tu.id, name: tu.name, input });
    }
  }

  return { blocks, stopReason, usage };
}

function throwIfAborted(signal?: AbortSignal): void {
  if (!signal?.aborted) return;
  const reason = (signal as AbortSignal & { reason?: unknown }).reason;
  throw reason instanceof Error ? reason : new Error("llm_stream_aborted");
}
