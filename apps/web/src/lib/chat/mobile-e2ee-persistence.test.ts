import { describe, expect, it } from "vitest";
import { readFileSync } from "fs";
import path from "path";

const repoRoot = process.cwd();

function readRepoFile(filePath: string): string {
  return readFileSync(path.join(repoRoot, filePath), "utf8");
}

describe("mobile e2ee remote persistence wiring", () => {
  it("does not depend on a view-level pending E2EE save ref", () => {
    const channelView = readRepoFile("apps/mobile/app/(tabs)/chat/[channel].tsx");

    expect(channelView).not.toContain("pendingE2EESave");
  });

  it("persists completed mobile turns from the store finalization path", () => {
    const store = readRepoFile("apps/mobile/src/stores/chat-store.ts");

    expect(store).toContain("setRemoteHistorySaver");
    expect(store).toContain("saveRemoteHistoryTurn");
  });

  it("persists web-authored user messages immediately, including mid-turn injected messages", () => {
    const defaultChatView = readRepoFile("src/app/dashboard/chat/chat-view-client.tsx");
    const botScopedChatView = readRepoFile("src/app/dashboard/[botId]/chat/chat-view-client.tsx");

    for (const source of [defaultChatView, botScopedChatView]) {
      expect(source).toContain("persistUserHistoryMessage({");
      expect(source).toContain("message: userMsg");
      expect(source).toContain("message: injectedMsg");
    }
  });

  it("does not trust the partial-rollout v2 signature cache as the active key", () => {
    const webHook = readRepoFile("src/lib/chat/use-e2ee.ts");
    const mobileHook = readRepoFile("apps/mobile/src/hooks/use-e2ee.ts");

    expect(webHook).toContain("v2-short");
    expect(webHook).toContain("suspectV2CacheKey");
    expect(mobileHook).toContain("v2-short");
    expect(mobileHook).toContain("suspectV2CacheKey");
  });
});
