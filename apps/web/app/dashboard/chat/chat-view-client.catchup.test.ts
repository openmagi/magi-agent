import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const CHAT_VIEW_FILES = [
  "src/app/dashboard/chat/chat-view-client.tsx",
  "src/app/dashboard/[botId]/chat/chat-view-client.tsx",
];

describe("chat view assistant catch-up", () => {
  it("uses chat-proxy history instead of the nonexistent dashboard channel route", () => {
    for (const file of CHAT_VIEW_FILES) {
      const source = readFileSync(join(process.cwd(), file), "utf8");

      expect(source).not.toContain("/api/chat/${botId}/channels/${encodeURIComponent(channel)}/messages?limit=1&role=assistant");
      expect(source).toContain("chatApi.fetchChannelMessages(");
      expect(source).toContain("ASSISTANT_CATCHUP_LIMIT");
      expect(source).toContain("shouldPatchAssistantTextFromServer");
    }
  });
});
