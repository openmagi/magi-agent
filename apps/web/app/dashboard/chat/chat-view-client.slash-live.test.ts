/**
 * Source-wiring checks for the live-skills slash-autocomplete integration.
 *
 * Verifies that chat-view-client.tsx (local dashboard) imports the hook,
 * calls it, and passes `liveSkills` down to ChatInput — without requiring
 * DOM/React rendering (node vitest environment).
 */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const chatViewSource = readFileSync(
  new URL("./chat-view-client.tsx", import.meta.url),
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

describe("live-skills slash-autocomplete wiring", () => {
  it("chat-view-client imports useLiveSkills", () => {
    expect(chatViewSource).toContain("useLiveSkills");
    expect(chatViewSource).toContain("use-live-skills");
  });

  it("chat-view-client calls useLiveSkills and destructures skills as liveSkills", () => {
    expect(chatViewSource).toContain("useLiveSkills(");
    expect(chatViewSource).toContain("liveSkills");
  });

  it("chat-view-client passes liveSkills prop to ChatInput", () => {
    expect(chatViewSource).toContain("liveSkills={liveSkills}");
  });

  it("chat-input.tsx accepts a liveSkills prop", () => {
    expect(chatInputSource).toContain("liveSkills");
    expect(chatInputSource).toContain("liveSkills?: ChatInputCustomSkill[]");
  });

  it("buildSlashEntries accepts liveSkills parameter", () => {
    // The canonical implementation lives in slash-entries.ts
    expect(slashEntriesSource).toContain("liveSkills: SlashSkill[] = []");
  });

  it("buildSlashEntries iterates over liveSkills", () => {
    // The canonical implementation lives in slash-entries.ts
    expect(slashEntriesSource).toContain("for (const skill of liveSkills)");
  });
});
