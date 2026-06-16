import { describe, expect, it } from "vitest";
import {
  assistantContentsSubstantiallyOverlap,
  normalizedAssistantDedupeContent,
  shouldPreferIncomingAssistantMessageCopy,
} from "./assistant-dedupe";
import type { ChatMessage } from "@/chat-core";

const baseMessage: ChatMessage = {
  id: "assistant-1",
  role: "assistant",
  content: "",
  timestamp: 1_000,
};

function assistantMessage(content: string, overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    ...baseMessage,
    ...overrides,
    content,
  };
}

describe("assistant dedupe helpers", () => {
  it("normalizes assistant content by removing runtime-only preambles and inline progress", () => {
    const message = assistantMessage(
      "[META: intent=chat route=default]\nThinking through next step\nCalling claude-sonnet-4-20250514\n" +
        "This is the durable assistant answer that should be considered for duplicate detection. ".repeat(2),
    );

    const normalized = normalizedAssistantDedupeContent(message);

    expect(normalized).toBe(
      "This is the durable assistant answer that should be considered for duplicate detection. " +
        "This is the durable assistant answer that should be considered for duplicate detection.",
    );
  });

  it("treats overlapping streamed and server assistant copies as duplicates", () => {
    const prefix =
      "The same assistant response can arrive first from the local stream and later from server persistence. ".repeat(2);
    const local = assistantMessage(`${prefix}Local tail`);
    const server = assistantMessage(`${prefix}Server tail`, {
      id: "assistant-2",
      serverId: "server-2",
      timestamp: 1_200,
    });

    expect(assistantContentsSubstantiallyOverlap(local, server)).toBe(true);
    expect(shouldPreferIncomingAssistantMessageCopy(local, server)).toBe(true);
  });
});
