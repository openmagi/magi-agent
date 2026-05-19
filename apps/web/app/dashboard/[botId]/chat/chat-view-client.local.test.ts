import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  "apps/web/app/dashboard/[botId]/chat/chat-view-client.tsx",
  "utf8",
);

describe("local OSS chat dashboard", () => {
  it("does not show hosted Telegram connection banners for the local agent", () => {
    expect(source).toContain('botId !== "local"');
    expect(source).toContain("showTelegramBanner");
  });
});
