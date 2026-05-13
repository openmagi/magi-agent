import { describe, it, expect } from "vitest";
import { sanitizeMessagesForLLM } from "./MessageSanitizer.js";
import type { LLMMessage } from "../transport/LLMClient.js";

describe("MessageSanitizer", () => {
  it("returns empty array for empty input", () => {
    expect(sanitizeMessagesForLLM([])).toEqual([]);
  });

  it("passes through well-formed user→assistant→user sequence", () => {
    const msgs: LLMMessage[] = [
      { role: "user", content: "hello" },
      {
        role: "assistant",
        content: [
          { type: "tool_use", id: "t1", name: "Read", input: {} },
        ],
      },
      {
        role: "user",
        content: [
          { type: "tool_result", tool_use_id: "t1", content: "file contents" },
        ],
      },
    ];
    const result = sanitizeMessagesForLLM(msgs);
    expect(result).toHaveLength(3);
  });

  it("strips trailing assistant message (no prefill)", () => {
    const msgs: LLMMessage[] = [
      { role: "user", content: "hello" },
      { role: "assistant", content: "partial response" },
    ];
    const result = sanitizeMessagesForLLM(msgs);
    expect(result).toHaveLength(1);
    expect(result[0]!.role).toBe("user");
  });

  it("strips orphaned tool_use when no matching tool_result follows", () => {
    const msgs: LLMMessage[] = [
      { role: "user", content: "do something" },
      {
        role: "assistant",
        content: [
          { type: "text", text: "Let me help" },
          { type: "tool_use", id: "orphan1", name: "Bash", input: {} },
        ],
      },
      { role: "user", content: "next message" },
    ];
    const result = sanitizeMessagesForLLM(msgs);
    const assistantContent = result[1]!.content as Array<{ type: string }>;
    expect(assistantContent).toHaveLength(1);
    expect(assistantContent[0]!.type).toBe("text");
  });

  it("strips orphaned tool_result when no matching tool_use precedes", () => {
    const msgs: LLMMessage[] = [
      { role: "user", content: "hello" },
      { role: "assistant", content: [{ type: "text", text: "ok" }] },
      {
        role: "user",
        content: [
          { type: "tool_result", tool_use_id: "noexist", content: "data" },
        ],
      },
    ];
    const result = sanitizeMessagesForLLM(msgs);
    // The orphaned tool_result user msg is removed, then trailing assistant is stripped
    expect(result).toHaveLength(1);
    expect(result[0]!.role).toBe("user");
  });

  it("does not mutate the input array", () => {
    const msgs: LLMMessage[] = [
      { role: "user", content: "hello" },
      { role: "assistant", content: "trailing" },
    ];
    const original = [...msgs];
    sanitizeMessagesForLLM(msgs);
    expect(msgs).toEqual(original);
  });

  it("keeps matched tool_use/tool_result pairs intact", () => {
    const msgs: LLMMessage[] = [
      { role: "user", content: "go" },
      {
        role: "assistant",
        content: [
          { type: "tool_use", id: "matched1", name: "Read", input: {} },
          { type: "tool_use", id: "orphan1", name: "Bash", input: {} },
        ],
      },
      {
        role: "user",
        content: [
          { type: "tool_result", tool_use_id: "matched1", content: "ok" },
        ],
      },
    ];
    const result = sanitizeMessagesForLLM(msgs);
    const assistantContent = result[1]!.content as Array<{ type: string; id?: string }>;
    expect(assistantContent).toHaveLength(1);
    expect(assistantContent[0]!.id).toBe("matched1");
  });

  it("removes assistant message entirely when all tool_use blocks are orphaned", () => {
    const msgs: LLMMessage[] = [
      { role: "user", content: "go" },
      {
        role: "assistant",
        content: [
          { type: "tool_use", id: "orphan1", name: "Read", input: {} },
        ],
      },
      { role: "user", content: "next" },
    ];
    const result = sanitizeMessagesForLLM(msgs);
    // The assistant message with only orphaned tool_use should be removed,
    // and then the two consecutive user messages remain
    expect(result.every((m) => m.role === "user")).toBe(true);
  });

  it("strips multiple trailing assistant messages", () => {
    const msgs: LLMMessage[] = [
      { role: "user", content: "hello" },
      { role: "assistant", content: "first" },
      { role: "assistant", content: "second" },
    ];
    // Note: this already violates alternation but sanitizer should handle gracefully
    const result = sanitizeMessagesForLLM(msgs);
    expect(result).toHaveLength(1);
    expect(result[0]!.role).toBe("user");
  });
});
