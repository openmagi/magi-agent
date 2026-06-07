import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import en from "@/lib/i18n/locales/en";
import es from "@/lib/i18n/locales/es";
import ja from "@/lib/i18n/locales/ja";
import ko from "@/lib/i18n/locales/ko";
import zh from "@/lib/i18n/locales/zh";

const requiredChatKeys = [
  "channelsTitle",
  "dropFilesToAttach",
  "openChannels",
  "resetSession",
  "reset",
  "dismiss",
  "escAgainToStop",
  "selectedKnowledgeSendsAfterRun",
  "noChannelsTitle",
  "noChannelsDescription",
  "openTelegram",
  "exportSelectedMessagesTitle",
  "exportSelectedMessagesCount",
  "closeExportDialog",
  "downloadMarkdown",
  "createPublicLink",
  "createPublicLinkDescription",
  "publicLinkCreated",
  "copy",
  "open",
  "deleteMessagesTitle",
  "deleteMessagesCount",
  "deleteMessagesWarning",
  "cancel",
  "delete",
  "messagesDeleted",
  "undo",
] as const;

const locales = { en, es, ja, ko, zh };

describe("chat view i18n strings", () => {
  it("defines the dashboard chat chrome strings for every supported locale", () => {
    for (const [locale, messages] of Object.entries(locales)) {
      for (const key of requiredChatKeys) {
        expect(messages.chat[key], `${locale}.chat.${key}`).toEqual(expect.any(String));
        expect(messages.chat[key].trim(), `${locale}.chat.${key}`).not.toBe("");
      }
    }

    expect(ko.chat.noChannelsTitle).not.toBe(en.chat.noChannelsTitle);
    expect(ja.chat.deleteMessagesTitle).not.toBe(en.chat.deleteMessagesTitle);
    expect(zh.chat.createPublicLink).not.toBe(en.chat.createPublicLink);
    expect(es.chat.messagesDeleted).not.toBe(en.chat.messagesDeleted);
  });

  it("keeps common dashboard chat chrome text out of the route components", () => {
    const files = [
      "src/app/dashboard/chat/chat-view-client.tsx",
      "src/app/dashboard/[botId]/chat/chat-view-client.tsx",
    ];
    const hardcodedPhrases = [
      "Drop files to attach",
      "Open channels",
      "Reset session",
      "ESC again to stop",
      "Selected knowledge will send after the current run.",
      "No channels yet",
      "Create a channel from the sidebar to start chatting.",
      "Open @{telegramBotUsername}",
      "Export selected messages",
      "Close export dialog",
      "Download Markdown",
      "Create public link",
      "Anyone with this link can view the selected messages.",
      "Public link created",
      "Delete messages",
      "messages will be permanently deleted",
      "This action cannot be undone.",
      "Messages deleted",
    ];

    for (const file of files) {
      const source = readFileSync(resolve(process.cwd(), file), "utf8");
      for (const phrase of hardcodedPhrases) {
        expect(source, `${file} should not hardcode ${phrase}`).not.toContain(phrase);
      }
    }
  });
});
