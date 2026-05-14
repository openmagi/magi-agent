import { describe, expect, it } from "vitest";
import {
  buildChatExportFilename,
  buildChatExportMarkdown,
  normalizeSelectedChatExportMessages,
} from "./export";
import type { ChatMessage } from "./types";

const messages: ChatMessage[] = [
  { id: "sys-1", role: "system", content: "Reset", timestamp: 1 },
  {
    id: "user-1",
    role: "user",
    content: "[KB_CONTEXT: id=doc-1 name=Brief.pdf collection=Downloads]\nHello",
    timestamp: Date.parse("2026-05-05T01:00:00.000Z"),
  },
  {
    id: "assistant-1",
    role: "assistant",
    content: "Result\n[Attachment: report.pdf](attachment:att-1)",
    timestamp: Date.parse("2026-05-05T01:01:00.000Z"),
  },
];

describe("chat export formatting", () => {
  it("normalizes only selected user and assistant messages in chronological order", () => {
    const selected = new Set(["assistant-1", "user-1", "sys-1"]);

    expect(normalizeSelectedChatExportMessages(messages, selected)).toEqual([
      {
        id: "user-1",
        role: "user",
        content: "Hello",
        timestamp: Date.parse("2026-05-05T01:00:00.000Z"),
      },
      {
        id: "assistant-1",
        role: "assistant",
        content: "Result",
        timestamp: Date.parse("2026-05-05T01:01:00.000Z"),
      },
    ]);
  });

  it("builds a readable markdown transcript", () => {
    const normalized = normalizeSelectedChatExportMessages(messages, new Set(["user-1", "assistant-1"]));

    const markdown = buildChatExportMarkdown({
      botName: "Research Bot",
      channelName: "general",
      exportedAt: new Date("2026-05-05T02:00:00.000Z"),
      messages: normalized,
    });

    expect(markdown).toContain("# Open Magi Chat Export");
    expect(markdown).toContain("- Bot: Research Bot");
    expect(markdown).toContain("- Channel: general");
    expect(markdown).toContain("- Messages: 2");
    expect(markdown).toContain("## User - 2026-05-05 01:00");
    expect(markdown).toContain("Hello");
    expect(markdown).toContain("## Assistant - 2026-05-05 01:01");
    expect(markdown).toContain("Result");
    expect(markdown).not.toContain("KB_CONTEXT");
    expect(markdown).not.toContain("attachment:att-1");
  });

  it("builds a safe markdown filename", () => {
    expect(
      buildChatExportFilename({
        botName: "Research/Bot",
        channelName: "general chat",
        exportedAt: new Date("2026-05-05T02:00:00.000Z"),
      }),
    ).toBe("open-magi-research-bot-general-chat-2026-05-05.md");
  });
});
