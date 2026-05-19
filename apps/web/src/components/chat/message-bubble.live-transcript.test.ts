import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync("apps/web/src/components/chat/message-bubble.tsx", "utf8");

describe("MessageBubble live transcript rendering", () => {
  it("accepts live transcript items and inline work rows for active assistant streams", () => {
    expect(source).toContain("liveTranscriptItems");
    expect(source).toContain("data-chat-live-transcript");
    expect(source).toContain("liveWorkRows");
    expect(source).toContain("data-chat-inline-work-log");
  });
});
