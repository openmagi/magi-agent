/**
 * Source-wiring checks for the live-skills slash-autocomplete integration.
 *
 * Two shells serve the chat UI:
 *   - app/dashboard/chat/chat-view-client.tsx  (secondary, /dashboard/chat/*)
 *   - app/dashboard/[botId]/chat/chat-view-client.tsx (PRIMARY, /dashboard/local/chat/*)
 *
 * Both must import useLiveSkills and pass liveSkills to ChatInput. The [botId]
 * shell gates on `botId === "local"` so hosted bots are unaffected.
 */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// Secondary shell (app/dashboard/chat/)
const chatViewSource = readFileSync(
  new URL("./chat-view-client.tsx", import.meta.url),
  "utf8",
);

// PRIMARY shell (app/dashboard/[botId]/chat/) — the route that renders
// localhost:8080/dashboard/local/chat/*
const botIdChatViewSource = readFileSync(
  new URL("../[botId]/chat/chat-view-client.tsx", import.meta.url),
  "utf8",
);

const chatInputSource = readFileSync(
  new URL("../../../src/components/chat/chat-input.tsx", import.meta.url),
  "utf8",
);

// The merge logic lives in slash-entries.ts (no JSX dependency = unit-testable)
const slashEntriesSource = readFileSync(
  new URL("../../../src/components/chat/slash-entries.ts", import.meta.url),
  "utf8",
);

describe("live-skills slash-autocomplete wiring — secondary shell (dashboard/chat/)", () => {
  it("imports useLiveSkills", () => {
    expect(chatViewSource).toContain("useLiveSkills");
    expect(chatViewSource).toContain("use-live-skills");
  });

  it("calls useLiveSkills and passes liveSkills to ChatInput", () => {
    expect(chatViewSource).toContain("useLiveSkills(");
    expect(chatViewSource).toContain("liveSkills={liveSkills}");
  });
});

describe("live-skills slash-autocomplete wiring — PRIMARY shell (dashboard/[botId]/chat/)", () => {
  it("imports useLiveSkills", () => {
    expect(botIdChatViewSource).toContain("useLiveSkills");
    expect(botIdChatViewSource).toContain("use-live-skills");
  });

  it("calls useLiveSkills gated on botId === 'local'", () => {
    // Must pass enabled=false for hosted bots; only fetch for local agent
    expect(botIdChatViewSource).toContain('useLiveSkills(botId === "local")');
  });

  it("passes liveSkills prop to ChatInput", () => {
    expect(botIdChatViewSource).toContain("liveSkills={liveSkills}");
  });

  it("still contains the existing hosted custom-skills fetch (not removed)", () => {
    // The hosted path must remain intact for non-local bots
    expect(botIdChatViewSource).toContain("/api/bots/${botId}/custom-skills");
  });
});

describe("live-skills slash-autocomplete — shared plumbing", () => {
  it("chat-input.tsx accepts a liveSkills prop", () => {
    expect(chatInputSource).toContain("liveSkills?: ChatInputCustomSkill[]");
  });

  it("buildSlashEntries accepts liveSkills parameter", () => {
    expect(slashEntriesSource).toContain("liveSkills: SlashSkill[] = []");
  });

  it("buildSlashEntries iterates over liveSkills", () => {
    expect(slashEntriesSource).toContain("for (const skill of liveSkills)");
  });
});
