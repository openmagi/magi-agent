import type { LLMContentBlock, LLMMessage } from "../transport/LLMClient.js";

export function sanitizeMessagesForLLM(messages: LLMMessage[]): LLMMessage[] {
  if (messages.length === 0) return [];

  const result: LLMMessage[] = messages.map((m) => ({ ...m }));

  stripOrphanedToolUse(result);
  stripOrphanedToolResult(result);

  const filtered = result.filter((msg) => {
    if (typeof msg.content === "string") return msg.content.length > 0;
    return msg.content.length > 0;
  });

  while (
    filtered.length > 0 &&
    filtered[filtered.length - 1]!.role === "assistant"
  ) {
    filtered.pop();
  }

  return filtered;
}

function stripOrphanedToolUse(messages: LLMMessage[]): void {
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i]!;
    if (msg.role !== "assistant" || !Array.isArray(msg.content)) continue;

    const toolUseIds = new Set<string>();
    for (const block of msg.content) {
      if (block.type === "tool_use" && "id" in block) {
        toolUseIds.add((block as { id: string }).id);
      }
    }
    if (toolUseIds.size === 0) continue;

    const next = messages[i + 1];
    const matchedIds = new Set<string>();
    if (next && next.role === "user" && Array.isArray(next.content)) {
      for (const block of next.content) {
        if (block.type === "tool_result" && "tool_use_id" in block) {
          const id = (block as { tool_use_id: string }).tool_use_id;
          if (toolUseIds.has(id)) matchedIds.add(id);
        }
      }
    }

    if (matchedIds.size < toolUseIds.size) {
      msg.content = [...msg.content].filter((block) => {
        if (block.type !== "tool_use" || !("id" in block)) return true;
        return matchedIds.has((block as { id: string }).id);
      });
    }

    if (msg.content.length === 0) {
      messages.splice(i, 1);
      i -= 1;
    }
  }
}

function stripOrphanedToolResult(messages: LLMMessage[]): void {
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i]!;
    if (msg.role !== "user" || !Array.isArray(msg.content)) continue;

    const hasToolResult = msg.content.some((b) => b.type === "tool_result");
    if (!hasToolResult) continue;

    const prev = messages[i - 1];
    const prevToolIds = new Set<string>();
    if (prev && prev.role === "assistant" && Array.isArray(prev.content)) {
      for (const block of prev.content) {
        if (block.type === "tool_use" && "id" in block) {
          prevToolIds.add((block as { id: string }).id);
        }
      }
    }

    const seenResultIds = new Set<string>();
    msg.content = [...msg.content].filter((block) => {
      if (block.type !== "tool_result") return true;
      const id = (block as { tool_use_id: string }).tool_use_id;
      if (!prevToolIds.has(id)) return false;
      if (seenResultIds.has(id)) return false;
      seenResultIds.add(id);
      return true;
    });

    if (msg.content.length === 0) {
      messages.splice(i, 1);
      i -= 1;
    }
  }
}
