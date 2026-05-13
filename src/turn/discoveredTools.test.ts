import { describe, it, expect } from "vitest";
import { extractDiscoveredToolNames } from "./discoveredTools.js";
import type { LLMMessage } from "../transport/LLMClient.js";

describe("extractDiscoveredToolNames", () => {
  it("extracts tool names from tool_reference blocks in tool_result content", () => {
    const messages: LLMMessage[] = [
      { role: "user", content: "hello" },
      {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "tu_1",
            content: [
              { type: "tool_reference", tool_name: "Browser" },
              { type: "tool_reference", tool_name: "CronCreate" },
            ],
          } as any,
        ],
      },
    ];
    const discovered = extractDiscoveredToolNames(messages);
    expect(discovered).toEqual(new Set(["Browser", "CronCreate"]));
  });

  it("returns empty set when no tool_reference blocks exist", () => {
    const messages: LLMMessage[] = [
      { role: "user", content: "hello" },
      { role: "assistant", content: [{ type: "text", text: "hi" }] },
    ];
    expect(extractDiscoveredToolNames(messages)).toEqual(new Set());
  });

  it("ignores assistant messages", () => {
    const messages: LLMMessage[] = [
      {
        role: "assistant",
        content: [
          {
            type: "tool_result",
            tool_use_id: "tu_1",
            content: [{ type: "tool_reference", tool_name: "Browser" }],
          } as any,
        ],
      },
    ];
    expect(extractDiscoveredToolNames(messages)).toEqual(new Set());
  });

  it("deduplicates repeated references", () => {
    const messages: LLMMessage[] = [
      {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "tu_1",
            content: [{ type: "tool_reference", tool_name: "Browser" }],
          } as any,
        ],
      },
      {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "tu_2",
            content: [{ type: "tool_reference", tool_name: "Browser" }],
          } as any,
        ],
      },
    ];
    const discovered = extractDiscoveredToolNames(messages);
    expect(discovered.size).toBe(1);
    expect(discovered.has("Browser")).toBe(true);
  });

  it("handles tool_result with string content (not array)", () => {
    const messages: LLMMessage[] = [
      {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "tu_1",
            content: "some text result",
          },
        ],
      },
    ];
    expect(extractDiscoveredToolNames(messages)).toEqual(new Set());
  });

  it("handles empty messages array", () => {
    expect(extractDiscoveredToolNames([])).toEqual(new Set());
  });
});
