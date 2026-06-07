import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("ChatViewClient export wiring", () => {
  it("wires selected-message export to markdown download and public link creation", () => {
    const source = readFileSync(new URL("./chat-view-client.tsx", import.meta.url), "utf8");
    const enLocale = readFileSync(new URL("../../../lib/i18n/locales/en.ts", import.meta.url), "utf8");

    expect(source).toContain("normalizeSelectedChatExportMessages");
    expect(source).toContain("buildChatExportMarkdown");
    expect(source).toContain("buildChatExportFilename");
    expect(source).toContain('fetch("/api/chat/exports"');
    expect(source).toContain("createPublicLinkDescription");
    expect(enLocale).toContain("Anyone with this link can view the selected messages.");
    expect(source).toContain("onExportSelected={handleExportSelected}");
  });

  it("does not render the legacy current-run dock alongside inline progress", () => {
    const source = readFileSync(new URL("./chat-view-client.tsx", import.meta.url), "utf8");

    expect(source).not.toContain("<RunInspectorDock");
  });

  it("does not move response_clear drafts into exportable thinking content", () => {
    const source = readFileSync(new URL("./chat-view-client.tsx", import.meta.url), "utf8");

    expect(source).not.toContain("discardedText");
    expect(source).not.toContain("--- Retrying ---");
    expect(source).not.toContain("--- 다시 시도 중 ---");
  });

  it("keeps the transcript loading until the latest channel history preview resolves", () => {
    const source = readFileSync(new URL("./chat-view-client.tsx", import.meta.url), "utf8");

    expect(source).toContain("const [initialHistoryLoading, setInitialHistoryLoading] = useState(false);");
    expect(source).toMatch(/setInitialHistoryLoading\(true\);[\s\S]*loadMessages\(initialChannel, undefined, HISTORY_PREVIEW_LIMIT, \{ latest: true \}\)/);
    expect(source).toMatch(/finally \{[\s\S]*setInitialHistoryLoading\(false\);[\s\S]*\}/);
    expect(source).toContain("loading={!e2eeReady || initialHistoryLoading}");
  });

  it("loads installed custom skills and passes them to the slash composer", () => {
    const source = readFileSync(new URL("./chat-view-client.tsx", import.meta.url), "utf8");

    expect(source).toContain('fetch(`/api/bots/${botId}/custom-skills`');
    expect(source).toContain("setCustomSkills");
    expect(source).toContain("customSkills={customSkills}");
  });
});
