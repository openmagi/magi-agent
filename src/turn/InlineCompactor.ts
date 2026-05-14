/**
 * Layer 2 — Inline Compactor.
 * Pre-LLM and emergency compaction strategies that operate on the
 * in-memory messages array within a single turn.
 *
 * Three strategies in escalating aggressiveness:
 *   1. snipCompact  — Drop oldest tool_use/tool_result pairs
 *   2. microCompact — Re-truncate remaining large tool_results
 *   3. compactMessagesInline — Orchestrates 1+2 to hit a token target
 */

import type { LLMMessage, LLMContentBlock } from "../transport/LLMClient.js";

export function estimateMessageTokens(messages: readonly LLMMessage[]): number {
  let chars = 0;
  for (const msg of messages) {
    if (typeof msg.content === "string") {
      chars += msg.content.length;
    } else if (Array.isArray(msg.content)) {
      for (const block of msg.content) {
        if (typeof block === "string") {
          chars += (block as string).length;
        } else if (typeof block === "object" && block !== null) {
          const b = block as Record<string, unknown>;
          if (b.type === "text" && typeof b.text === "string") {
            chars += b.text.length;
          } else if (b.type === "tool_result" && typeof b.content === "string") {
            chars += b.content.length;
          } else if (b.type === "tool_use") {
            chars += JSON.stringify(b.input ?? {}).length;
          } else {
            chars += JSON.stringify(b).length;
          }
        }
      }
    }
  }
  return Math.ceil(chars / 4);
}

interface ToolPairIndex {
  assistantIdx: number;
  userIdx: number;
  toolUseId: string;
}

function findToolPairs(messages: readonly LLMMessage[]): ToolPairIndex[] {
  const pairs: ToolPairIndex[] = [];
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i]!;
    if (msg.role !== "assistant" || !Array.isArray(msg.content)) continue;
    for (const block of msg.content) {
      const b = block as Record<string, unknown>;
      if (b.type !== "tool_use") continue;
      const toolUseId = b.id as string;
      const resultIdx = messages.findIndex((m, j) => {
        if (j <= i || m.role !== "user" || !Array.isArray(m.content)) return false;
        return (m.content as LLMContentBlock[]).some(
          (rb) =>
            typeof rb === "object" &&
            rb !== null &&
            (rb as Record<string, unknown>).type === "tool_result" &&
            (rb as Record<string, unknown>).tool_use_id === toolUseId,
        );
      });
      if (resultIdx >= 0) {
        pairs.push({ assistantIdx: i, userIdx: resultIdx, toolUseId });
      }
    }
  }
  return pairs;
}

export function snipCompact(messages: readonly LLMMessage[], keepLast: number): LLMMessage[] {
  const pairs = findToolPairs(messages);
  if (pairs.length <= keepLast) return [...messages];

  const dropCount = pairs.length - keepLast;
  const dropPairs = pairs.slice(0, dropCount);
  const dropToolUseIds = new Set(dropPairs.map((p) => p.toolUseId));

  const result: LLMMessage[] = [];
  for (const msg of messages) {
    if (!Array.isArray(msg.content)) {
      result.push(msg);
      continue;
    }

    const filtered = (msg.content as LLMContentBlock[]).filter((block) => {
      const b = block as Record<string, unknown>;
      if (b.type === "tool_use" && dropToolUseIds.has(b.id as string)) return false;
      if (b.type === "tool_result" && dropToolUseIds.has(b.tool_use_id as string)) return false;
      return true;
    });

    if (filtered.length > 0) {
      result.push({ role: msg.role, content: filtered });
    } else if (msg.role === "user" && typeof msg.content === "string") {
      result.push(msg);
    }
  }

  return result;
}

export function microCompact(messages: readonly LLMMessage[], maxResultChars: number): LLMMessage[] {
  return messages.map((msg): LLMMessage => {
    if (msg.role !== "user" || !Array.isArray(msg.content)) return msg;
    const newContent = (msg.content as LLMContentBlock[]).map((block): LLMContentBlock => {
      const b = block as Record<string, unknown>;
      if (b.type !== "tool_result" || typeof b.content !== "string") return block;
      const content = b.content as string;
      if (content.length <= maxResultChars) return block;
      const head = content.slice(0, Math.floor(maxResultChars * 0.6));
      const tail = content.slice(-Math.floor(maxResultChars * 0.3));
      const truncated = head + `\n...[${content.length - head.length - tail.length} chars omitted]...\n` + tail;
      return { type: "tool_result", tool_use_id: b.tool_use_id as string, content: truncated, ...(b.is_error ? { is_error: true } : {}) };
    });
    return { role: msg.role, content: newContent };
  });
}

export function compactMessagesInline(
  messages: LLMMessage[],
  targetTokenBudget: number,
): LLMMessage[] {
  let current = estimateMessageTokens(messages);
  if (current <= targetTokenBudget) return messages;

  let result = snipCompact(messages, Math.max(3, Math.floor(messages.length / 4)));
  current = estimateMessageTokens(result);
  if (current <= targetTokenBudget) return result;

  result = snipCompact(result, 2);
  current = estimateMessageTokens(result);
  if (current <= targetTokenBudget) return result;

  result = microCompact(result, 2048);
  current = estimateMessageTokens(result);
  if (current <= targetTokenBudget) return result;

  result = microCompact(result, 512);
  return result;
}
