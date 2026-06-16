import { describe, expect, it } from "vitest";
import type { ChatMessage } from "./types";
import { buildOutboundChatContext, mergeChatHistoryPage } from "./history-merge";

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

describe("buildOutboundChatContext", () => {
  it("includes decrypted server history before the new local user turn", () => {
    const serverMessages = [
      msg("server-user-1", 100, "user"),
      msg("server-assistant-1", 101, "assistant"),
    ];
    const localMessages = [
      msg("user-1700000002000", 200, "user"),
    ];

    const context = buildOutboundChatContext(localMessages, serverMessages);

    expect(context).toEqual([
      { role: "user", content: "server-user-1" },
      { role: "assistant", content: "server-assistant-1" },
      { role: "user", content: "user-1700000002000" },
    ]);
  });

  it("deduplicates overlapping local and server messages by server id", () => {
    const serverMessages: ChatMessage[] = [
      { id: "row-1", serverId: "srv-1", role: "assistant", content: "answer", timestamp: 100 },
    ];
    const localMessages: ChatMessage[] = [
      { id: "assistant-1700000000000", serverId: "srv-1", role: "assistant", content: "answer", timestamp: 101 },
      msg("user-1700000001000", 102, "user"),
    ];

    const context = buildOutboundChatContext(localMessages, serverMessages);

    expect(context).toEqual([
      { role: "assistant", content: "answer" },
      { role: "user", content: "user-1700000001000" },
    ]);
  });

  it("starts the sent context after the latest reset divider", () => {
    const serverMessages = Array.from({ length: 30 }, (_, index) => (
      msg(`m-${index}`, index, index % 2 === 0 ? "user" : "assistant")
    ));
    const localMessages: ChatMessage[] = [
      { id: "divider", role: "system", content: "Session ended", timestamp: 31 },
      msg("latest", 32, "user"),
    ];

    const context = buildOutboundChatContext(localMessages, serverMessages, 4);

    expect(context).toEqual([
      { role: "user", content: "latest" },
    ]);
  });

  it("uses the latest reset divider when a channel has multiple reset markers", () => {
    const serverMessages: ChatMessage[] = [
      msg("old-user", 10, "user"),
      { id: "system-reset-old", role: "system", content: "Session ended — new conversation started", timestamp: 20 },
      msg("middle-user", 30, "user"),
      msg("middle-assistant", 31, "assistant"),
    ];
    const localMessages: ChatMessage[] = [
      { id: "system-reset-new", role: "system", content: "Session ended — new conversation started", timestamp: 40 },
      msg("fresh-user", 41, "user"),
    ];

    const context = buildOutboundChatContext(localMessages, serverMessages);

    expect(context).toEqual([
      { role: "user", content: "fresh-user" },
    ]);
  });

  it("does not send operational canary smoke exchanges back to the model", () => {
    const serverMessages: ChatMessage[] = [
      {
        id: "canary-smoke",
        role: "user",
        content: "SAFE CANARY ONLY. Do not use external web and do not write files.",
        timestamp: 10,
      },
      msg("assistant-after-canary", 11, "assistant"),
    ];
    const localMessages = [msg("real-user", 12, "user")];

    const context = buildOutboundChatContext(localMessages, serverMessages);

    expect(context).toEqual([
      { role: "user", content: "real-user" },
    ]);
  });

  it("drops every assistant reply after an internal diagnostic until the next user turn", () => {
    const serverMessages: ChatMessage[] = [
      {
        id: "internal-diagnostic",
        role: "user",
        content: "internal diagnostic smoke: confirm the hosted chat route",
        timestamp: 10,
      },
      msg("diagnostic-assistant-1", 11, "assistant"),
      msg("diagnostic-assistant-2", 12, "assistant"),
      msg("real-user", 13, "user"),
      msg("real-assistant", 14, "assistant"),
    ];

    const context = buildOutboundChatContext([], serverMessages);

    expect(context).toEqual([
      { role: "user", content: "real-user" },
      { role: "assistant", content: "real-assistant" },
    ]);
  });
});
