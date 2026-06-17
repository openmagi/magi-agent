import { describe, expect, it } from "vitest";

import { buildPlaintextPersistRows } from "./build-plaintext-persist-rows";

describe("buildPlaintextPersistRows", () => {
  it("builds plaintext user + assistant rows with client_msg_ids", () => {
    const rows = buildPlaintextPersistRows({
      userText: "hi there",
      assistant: { content: "hello back" },
      userClientMsgId: "user-1700000000000-aaa",
      assistantClientMsgId: "assistant-1700000000001-bbb",
    });

    expect(rows).toEqual([
      {
        role: "user",
        content: "hi there",
        client_msg_id: "user-1700000000000-aaa",
      },
      {
        role: "assistant",
        content: "hello back",
        client_msg_id: "assistant-1700000000001-bbb",
      },
    ]);
  });

  it("stores assistant content as plain visible text (no sentinel, no envelope) by default", () => {
    const rows = buildPlaintextPersistRows({
      userText: "q",
      assistant: {
        content: "visible answer",
        thinkingContent: "secret reasoning",
        usage: { inputTokens: 1, outputTokens: 2, costUsd: 0.01 },
      },
      userClientMsgId: "u",
      assistantClientMsgId: "a",
    });

    const assistant = rows.find((r) => r.role === "assistant");
    expect(assistant?.content).toBe("visible answer");
    // No plaintext sentinel — the API route adds it server-side.
    expect(assistant?.content.startsWith("plaintext:")).toBe(false);
    // No history envelope — thinking is omitted in the default (simplest) mode.
    expect(assistant?.content.startsWith('{"_v":')).toBe(false);
  });

  it("encodes assistant metadata via the history envelope when opted in", () => {
    const rows = buildPlaintextPersistRows({
      userText: "q",
      assistant: {
        content: "visible answer",
        thinkingContent: "secret reasoning",
        usage: { inputTokens: 1, outputTokens: 2, costUsd: 0.01 },
      },
      userClientMsgId: "u",
      assistantClientMsgId: "a",
      includeAssistantMetadata: true,
    });

    const assistant = rows.find((r) => r.role === "assistant");
    expect(assistant?.content.startsWith('{"_v":')).toBe(true);
    expect(assistant?.content).toContain("visible answer");
    expect(assistant?.content).toContain("secret reasoning");
  });

  it("trims user text and omits an empty user row", () => {
    const rows = buildPlaintextPersistRows({
      userText: "   ",
      assistant: { content: "only assistant" },
      userClientMsgId: "u",
      assistantClientMsgId: "a",
    });
    expect(rows.map((r) => r.role)).toEqual(["assistant"]);
  });

  it("omits the assistant row when there is no visible assistant text", () => {
    const rows = buildPlaintextPersistRows({
      userText: "just a user msg",
      assistant: { content: "" },
      userClientMsgId: "u",
      assistantClientMsgId: "a",
    });
    expect(rows.map((r) => r.role)).toEqual(["user"]);
  });

  it("returns [] when both texts are empty", () => {
    const rows = buildPlaintextPersistRows({
      userText: "",
      assistant: { content: "" },
      userClientMsgId: "u",
      assistantClientMsgId: "a",
    });
    expect(rows).toEqual([]);
  });

  it("returns only the user row when user text is present but assistant is empty", () => {
    const rows = buildPlaintextPersistRows({
      userText: "user message only",
      assistant: { content: "" },
      userClientMsgId: "u-only",
      assistantClientMsgId: "a-unused",
    });
    expect(rows).toHaveLength(1);
    expect(rows[0]).toEqual({
      role: "user",
      content: "user message only",
      client_msg_id: "u-only",
    });
  });
});
