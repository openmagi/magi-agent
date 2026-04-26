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
  LLMToolDef,
  LLMUsage,
} from "../transport/LLMClient.js";
import type { SseWriter } from "../transport/SseWriter.js";
import { RepetitionDetector } from "./RepetitionDetector.js";

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
}

export async function readOne(
  deps: LLMStreamReaderDeps,
  systemPrompt: string,
  messages: LLMMessage[],
  toolDefs: LLMToolDef[],
  options?: ReadOneOptions,
): Promise<LLMStreamReaderResult> {
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
  const toolByIndex = new Map<
    number,
    { id: string; name: string; inputJson: string }
  >();
  const blockOrder: number[] = [];

  let stream: AsyncGenerator<LLMEvent, void, void>;
  try {
    stream = deps.llm.stream({
      model: deps.model,
      system: systemPrompt || undefined,
      messages,
      tools: toolDefs.length ? toolDefs : undefined,
      ...(options?.thinkingOverride ? { thinking: options.thinkingOverride } : {}),
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
        if (cur) cur.inputJson += evt.partial;
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
