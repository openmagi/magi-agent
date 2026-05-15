import { describe, expect, it } from "vitest";
import type { ChatMessage } from "./types";
import { mergeChatHistoryPage } from "./history-merge";

function msg(id: string, timestamp: number, role: "user" | "assistant" = "user"): ChatMessage {
  return { id, role, content: id, timestamp };
}

describe("mergeChatHistoryPage", () => {
  it("keeps the newest preview messages when an older history page arrives", () => {
    const latestPreview = [msg("m-198", 198), msg("m-199", 199), msg("m-200", 200)];
    const olderPage = Array.from({ length: 100 }, (_, i) => msg(`m-${i + 1}`, i + 1));

    const merged = mergeChatHistoryPage(latestPreview, olderPage);

    expect(merged.map((m) => m.id)).toEqual([
      ...olderPage.map((m) => m.id),
      "m-198",
      "m-199",
      "m-200",
    ]);
  });

  it("deduplicates overlapping pages and preserves system dividers by timestamp", () => {
    const existing: ChatMessage[] = [
      msg("m-10", 10),
      { id: "divider-15", role: "system", content: "Reset", timestamp: 15 },
      msg("m-20", 20, "assistant"),
    ];
    const incoming = [msg("m-5", 5), msg("m-10", 10), msg("m-30", 30)];

    const merged = mergeChatHistoryPage(existing, incoming);

    expect(merged.map((m) => m.id)).toEqual([
      "m-5",
      "m-10",
      "divider-15",
      "m-20",
      "m-30",
    ]);
  });

  it("orders same-timestamp user messages before assistant replies", () => {
    const sameTs = 1_713_925_000_000;
    const existing = [msg("assistant-1", sameTs, "assistant")];
    const incoming = [msg("user-1", sameTs, "user")];

    const merged = mergeChatHistoryPage(existing, incoming);

    expect(merged.map((m) => `${m.role}:${m.id}`)).toEqual([
      "user:user-1",
      "assistant:assistant-1",
    ]);
  });

  it("uses client message timestamps to keep replies after their prompt when server times skew", () => {
    const existing = [
      {
        id: "assistant-1700000002000",
        role: "assistant" as const,
        content: "answer",
        timestamp: 1_000,
      },
    ];
    const incoming = [
      {
        id: "user-1700000001000",
        role: "user" as const,
        content: "question",
        timestamp: 1_001,
      },
    ];

    const merged = mergeChatHistoryPage(existing, incoming);

    expect(merged.map((m) => `${m.role}:${m.id}`)).toEqual([
      "user:user-1700000001000",
      "assistant:assistant-1700000002000",
    ]);
  });
});
