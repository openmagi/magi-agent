/**
 * Extract tool names from tool_reference blocks in message history.
 *
 * When deferred tool loading is enabled, tools marked with shouldDefer
 * are sent to the API with defer_loading: true. The model discovers them
 * via ToolSearch, which returns tool_reference content blocks. This
 * function scans message history to find all tool names that have been
 * referenced, so subsequent API requests include those tools with full
 * schema (no defer_loading).
 */

import type { LLMMessage } from "../transport/LLMClient.js";

export function extractDiscoveredToolNames(
  messages: readonly LLMMessage[],
): Set<string> {
  const discovered = new Set<string>();

  for (const msg of messages) {
    if (msg.role !== "user" || !Array.isArray(msg.content)) continue;
    for (const block of msg.content) {
      if (
        typeof block === "object" &&
        block !== null &&
        "type" in block &&
        (block as { type: string }).type === "tool_result" &&
        "content" in block &&
        Array.isArray((block as { content: unknown }).content)
      ) {
        for (const item of (block as { content: unknown[] }).content) {
          if (
            typeof item === "object" &&
            item !== null &&
            "type" in item &&
            (item as { type: string }).type === "tool_reference" &&
            "tool_name" in item &&
            typeof (item as { tool_name: unknown }).tool_name === "string"
          ) {
            discovered.add((item as { tool_name: string }).tool_name);
          }
        }
      }
    }
  }

  return discovered;
}
