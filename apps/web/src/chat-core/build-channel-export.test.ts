/**
 * Unit tests for buildChannelExport — pure helper mapping ChatMessage[] → { filename, markdown }.
 * These run without jsdom: no browser APIs, no React.
 */
import { describe, it, expect } from "vitest";
import type { ChatMessage } from "./types";

// We use the NOT-YET-CREATED module so the first run is RED.
import { buildChannelExport } from "./build-channel-export";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const USER_MSG: ChatMessage = {
  id: "u1",
  role: "user",
  content: "Hello, what can you do?",
  timestamp: new Date("2026-06-10T10:00:00Z").getTime(),
};

const ASSISTANT_MSG: ChatMessage = {
  id: "a1",
  role: "assistant",
  content: "I can help with many things!",
  timestamp: new Date("2026-06-10T10:01:00Z").getTime(),
};

const SYSTEM_MSG: ChatMessage = {
  id: "s1",
  role: "system",
  content: "System prompt context",
  timestamp: new Date("2026-06-10T09:59:00Z").getTime(),
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("buildChannelExport", () => {
  it("returns a filename matching the buildChatExportFilename convention", () => {
    const result = buildChannelExport({
      botName: "My Bot",
      channelName: "general",
      messages: [USER_MSG, ASSISTANT_MSG],
    });
    // Convention: open-magi-<botSlug>-<channelSlug>-<YYYY-MM-DD>.md
    expect(result.filename).toMatch(/^open-magi-my-bot-general-\d{4}-\d{2}-\d{2}\.md$/);
  });

  it("returns markdown containing the user message content", () => {
    const result = buildChannelExport({
      botName: "TestBot",
      channelName: "main",
      messages: [USER_MSG, ASSISTANT_MSG],
    });
    expect(result.markdown).toContain("Hello, what can you do?");
  });

  it("returns markdown containing the assistant message content", () => {
    const result = buildChannelExport({
      botName: "TestBot",
      channelName: "main",
      messages: [USER_MSG, ASSISTANT_MSG],
    });
    expect(result.markdown).toContain("I can help with many things!");
  });

  it("maps ChatMessage role to export role labels (User / Assistant)", () => {
    const result = buildChannelExport({
      botName: "TestBot",
      channelName: "main",
      messages: [USER_MSG, ASSISTANT_MSG],
    });
    expect(result.markdown).toContain("## User");
    expect(result.markdown).toContain("## Assistant");
  });

  it("preserves message timestamps in the markdown", () => {
    const result = buildChannelExport({
      botName: "TestBot",
      channelName: "main",
      messages: [USER_MSG, ASSISTANT_MSG],
    });
    // Timestamps formatted as YYYY-MM-DD HH:MM in UTC
    expect(result.markdown).toContain("2026-06-10 10:00");
    expect(result.markdown).toContain("2026-06-10 10:01");
  });

  it("includes the bot name and channel in the markdown header", () => {
    const result = buildChannelExport({
      botName: "My Bot",
      channelName: "support",
      messages: [USER_MSG],
    });
    expect(result.markdown).toContain("My Bot");
    expect(result.markdown).toContain("support");
  });

  it("excludes system messages (only user + assistant are exportable)", () => {
    const result = buildChannelExport({
      botName: "TestBot",
      channelName: "main",
      messages: [SYSTEM_MSG, USER_MSG, ASSISTANT_MSG],
    });
    // System content must NOT appear in the export
    expect(result.markdown).not.toContain("System prompt context");
    // User and assistant content must still appear
    expect(result.markdown).toContain("Hello, what can you do?");
    expect(result.markdown).toContain("I can help with many things!");
  });

  it("returns a valid (minimal) result for empty messages array", () => {
    const result = buildChannelExport({
      botName: "TestBot",
      channelName: "general",
      messages: [],
    });
    // Should not throw; filename and markdown should be strings
    expect(typeof result.filename).toBe("string");
    expect(result.filename.length).toBeGreaterThan(0);
    expect(typeof result.markdown).toBe("string");
    // Header should still be present even with no messages
    expect(result.markdown).toContain("Open Magi Chat Export");
  });

  it("uses serverId as the message id for deduplication when present", () => {
    const msgWithServerId: ChatMessage = {
      ...USER_MSG,
      id: "local-u1",
      serverId: "server-u1",
    };
    // Should not throw — serverId is just passed through
    const result = buildChannelExport({
      botName: "TestBot",
      channelName: "main",
      messages: [msgWithServerId, ASSISTANT_MSG],
    });
    expect(result.markdown).toContain("Hello, what can you do?");
  });

  it("does not duplicate messages when both local id and serverId are present", () => {
    const msgs: ChatMessage[] = [USER_MSG, ASSISTANT_MSG];
    const result = buildChannelExport({
      botName: "TestBot",
      channelName: "main",
      messages: msgs,
    });
    // Each message content should appear exactly once
    const userCount = (result.markdown.match(/Hello, what can you do\?/g) ?? []).length;
    const assistantCount = (result.markdown.match(/I can help with many things!/g) ?? []).length;
    expect(userCount).toBe(1);
    expect(assistantCount).toBe(1);
  });

  it("strips attachment markers from message content in the export", () => {
    const attachmentId = "550e8400-e29b-41d4-a716-446655440000";
    const msgWithAttachment: ChatMessage = {
      id: "u2",
      role: "user",
      content: `[attachment:${attachmentId}:report.pdf] Please review this file.`,
      timestamp: new Date("2026-06-10T10:02:00Z").getTime(),
    };
    const result = buildChannelExport({
      botName: "TestBot",
      channelName: "main",
      messages: [msgWithAttachment],
    });
    // Raw attachment marker must NOT appear in the exported markdown
    expect(result.markdown).not.toContain(`[attachment:${attachmentId}:report.pdf]`);
    // The visible text content must still appear
    expect(result.markdown).toContain("Please review this file.");
    // Public-share payload must keep attachment metadata for the share page.
    expect(result.messages[0]?.attachments).toEqual([
      { id: attachmentId, filename: "report.pdf" },
    ]);
    expect(result.markdown).toContain("Attachments:");
    expect(result.markdown).toContain(`report.pdf (attachment:${attachmentId})`);
  });

  it("keeps attachment-only messages in the public export payload", () => {
    const attachmentId = "550e8400-e29b-41d4-a716-446655440001";
    const attachmentOnly: ChatMessage = {
      id: "u4",
      role: "user",
      content: `[attachment:${attachmentId}:image.png]`,
      timestamp: new Date("2026-06-10T10:04:00Z").getTime(),
    };
    const result = buildChannelExport({
      botName: "TestBot",
      channelName: "main",
      messages: [attachmentOnly],
    });
    expect(result.messages).toHaveLength(1);
    expect(result.messages[0]).toMatchObject({
      id: "u4",
      role: "user",
      content: "",
      attachments: [{ id: attachmentId, filename: "image.png" }],
    });
    expect(result.markdown).toContain(`image.png (attachment:${attachmentId})`);
  });

  it("strips KB_CONTEXT markers from message content in the export", () => {
    const msgWithKb: ChatMessage = {
      id: "u3",
      role: "user",
      // buildKbContextMarker format: [KB_CONTEXT: id=filename, ...]
      content: "[KB_CONTEXT: kb-abc=context.txt]\nWhat is the answer?",
      timestamp: new Date("2026-06-10T10:03:00Z").getTime(),
    };
    const result = buildChannelExport({
      botName: "TestBot",
      channelName: "main",
      messages: [msgWithKb],
    });
    // Raw KB marker must NOT appear in the exported markdown
    expect(result.markdown).not.toContain("[KB_CONTEXT:");
    // The visible question must still appear
    expect(result.markdown).toContain("What is the answer?");
  });
});
