import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  "apps/web/app/dashboard/[botId]/chat/chat-view-client.tsx",
  "utf8",
);
const chatClientSource = readFileSync(
  "apps/web/src/lib/chat/chat-client.ts",
  "utf8",
);

describe("local OSS chat dashboard", () => {
  it("does not show hosted Telegram connection banners for the local agent", () => {
    expect(source).toContain('botId !== "local"');
    expect(source).toContain("showTelegramBanner");
  });

  it("keeps local chat transport while adopting cloud live transcript streaming", () => {
    expect(source).toContain("@/chat-core");
    expect(source).toContain("appendLiveTranscriptText");
    expect(source).toContain("appendLiveWorkSnapshot");
    expect(chatClientSource).toContain("/v1/chat/stream");
    expect(chatClientSource).toContain("/v1/chat/control-response");
    expect(chatClientSource).toContain("/v1/chat/cancel");
    expect(chatClientSource).not.toContain('localRuntimeFetch("/v1/chat/completions"');
  });
});
