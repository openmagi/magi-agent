import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync("apps/web/src/components/chat/message-bubble.tsx", "utf8");

describe("MessageBubble live transcript rendering", () => {
  it("renders live assistant transcript text without inline work rows", () => {
    expect(source).toContain("liveTranscriptItems");
    expect(source).toContain("liveAssistantTurn");
    expect(source).toContain("data-chat-live-transcript");
    expect(source).toContain("displayLiveTranscriptItems");
    expect(source).toContain("stripStreamingAssistantMetadataPreamble");
    expect(source).not.toContain("liveWorkRows");
    expect(source).not.toContain("data-chat-inline-work-log");
    expect(source).not.toContain("data-chat-inline-work-row");
  });
});
