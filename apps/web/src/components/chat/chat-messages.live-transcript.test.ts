import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync("apps/web/src/components/chat/chat-messages.tsx", "utf8");

describe("ChatMessages active run transcript rendering", () => {
  it("keeps live work progress out of inline assistant bubbles", () => {
    expect(source).toContain("@/chat-core");
    expect(source).toContain("hasActiveRunState");
    expect(source).toContain("renderLiveTranscriptWithInjected");
    expect(source).toContain('item.kind === "text"');
    expect(source).toContain("liveAssistantTurn");
    expect(source).not.toContain("InlineRunStatus");
    expect(source).not.toContain("deriveWorkConsoleRows");
    expect(source).not.toContain("liveWorkRows");
    expect(source).not.toContain("data-chat-queued-card");
  });
});
