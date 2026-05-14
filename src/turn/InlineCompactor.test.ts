import { describe, it, expect } from "vitest";
import type { LLMMessage } from "../transport/LLMClient.js";
import {
  estimateMessageTokens,
  snipCompact,
  microCompact,
  compactMessagesInline,
} from "./InlineCompactor.js";

function makeToolPair(id: string, content: string): LLMMessage[] {
  return [
    {
      role: "assistant",
      content: [{ type: "tool_use", id, name: "Bash", input: { command: "echo hi" } }],
    },
    {
      role: "user",
      content: [{ type: "tool_result", tool_use_id: id, content }],
    },
  ];
}

describe("estimateMessageTokens", () => {
  it("estimates string content", () => {
    const msgs: LLMMessage[] = [{ role: "user", content: "hello world" }];
    const tokens = estimateMessageTokens(msgs);
    expect(tokens).toBe(Math.ceil(11 / 4));
  });

  it("estimates array content with text blocks", () => {
    const msgs: LLMMessage[] = [
      { role: "user", content: [{ type: "text", text: "A".repeat(100) }] },
    ];
    expect(estimateMessageTokens(msgs)).toBe(25);
  });

  it("counts tool_result content", () => {
    const msgs: LLMMessage[] = [
      {
        role: "user",
        content: [{ type: "tool_result", tool_use_id: "x", content: "B".repeat(400) }],
      },
    ];
    expect(estimateMessageTokens(msgs)).toBe(100);
  });
});

describe("snipCompact", () => {
  it("drops oldest tool pairs when exceeding keepLast", () => {
    const msgs: LLMMessage[] = [
      { role: "user", content: "initial" },
      ...makeToolPair("t1", "result1"),
      ...makeToolPair("t2", "result2"),
      ...makeToolPair("t3", "result3"),
    ];
    const result = snipCompact(msgs, 1);
    // Should keep only the last pair (t3) and the initial user message
    const toolResults = result.filter(
      (m) =>
        Array.isArray(m.content) &&
        (m.content as Array<Record<string, unknown>>).some((b) => b.type === "tool_result"),
    );
    expect(toolResults.length).toBeLessThanOrEqual(1);
  });

  it("returns unchanged when pairs <= keepLast", () => {
    const msgs: LLMMessage[] = [
      { role: "user", content: "hi" },
      ...makeToolPair("t1", "result1"),
    ];
    const result = snipCompact(msgs, 5);
    expect(result).toEqual(msgs);
  });
});

describe("microCompact", () => {
  it("truncates large tool_result content blocks", () => {
    const msgs: LLMMessage[] = [
      {
        role: "user",
        content: [
          { type: "tool_result", tool_use_id: "x", content: "C".repeat(5000) },
        ],
      },
    ];
    const result = microCompact(msgs, 500);
    const block = (result[0]!.content as Array<Record<string, unknown>>)[0]!;
    expect(typeof block.content).toBe("string");
    expect((block.content as string).length).toBeLessThan(5000);
    expect((block.content as string)).toContain("chars omitted");
  });

  it("leaves small tool_result content unchanged", () => {
    const msgs: LLMMessage[] = [
      {
        role: "user",
        content: [
          { type: "tool_result", tool_use_id: "x", content: "small" },
        ],
      },
    ];
    const result = microCompact(msgs, 500);
    const block = (result[0]!.content as Array<Record<string, unknown>>)[0]!;
    expect(block.content).toBe("small");
  });
});

describe("compactMessagesInline", () => {
  it("returns messages unchanged when under budget", () => {
    const msgs: LLMMessage[] = [{ role: "user", content: "hi" }];
    const result = compactMessagesInline(msgs, 100000);
    expect(result).toBe(msgs);
  });

  it("compacts messages to fit within budget", () => {
    const msgs: LLMMessage[] = [
      { role: "user", content: "start" },
      ...makeToolPair("t1", "D".repeat(50000)),
      ...makeToolPair("t2", "E".repeat(50000)),
      ...makeToolPair("t3", "F".repeat(50000)),
      ...makeToolPair("t4", "G".repeat(50000)),
    ];
    const budget = 5000; // ~20K chars
    const result = compactMessagesInline(msgs, budget);
    const est = estimateMessageTokens(result);
    // Should have reduced significantly
    expect(est).toBeLessThan(estimateMessageTokens(msgs));
  });
});
