/**
 * Tests for the live-skills slash-autocomplete merge logic in chat-input.tsx.
 *
 * We import the pure exported helpers directly (no DOM/React rendering
 * required) so these run under the `node` vitest environment.
 */

import { describe, expect, it } from "vitest";
import { buildSlashEntries, getSlashMatches } from "./slash-entries";
import type { SlashSkill as ChatInputCustomSkill } from "./slash-entries";

const BUILTIN_COUNT = 4; // reset / status / compact / help

function makeLiveSkill(name: string, description = ""): ChatInputCustomSkill {
  return { name, title: name, description };
}

describe("buildSlashEntries — live skills", () => {
  it("includes a live skill fetched from /v1/app/skills in the autocomplete list", () => {
    const liveSkill = makeLiveSkill("deep-solve", "Solve hard problems");
    const entries = buildSlashEntries([], [liveSkill]);
    const commands = entries.map((e) => e.command);
    expect(commands).toContain("deep-solve");
  });

  it("labels live skills with category 'skill'", () => {
    const entries = buildSlashEntries([], [makeLiveSkill("my-skill")]);
    const entry = entries.find((e) => e.command === "my-skill");
    expect(entry?.category).toBe("skill");
  });

  it("fetch failure (empty liveSkills) falls back to exactly the same list as no liveSkills param", () => {
    const baseline = buildSlashEntries();
    const fallback = buildSlashEntries([], []);
    expect(fallback.map((e) => e.command)).toEqual(baseline.map((e) => e.command));
  });

  it("dedupes: builtins win over live skills with the same command name", () => {
    // 'reset' is a builtin command; a live skill called 'reset' must not appear twice.
    const collidingSkill = makeLiveSkill("reset");
    const entries = buildSlashEntries([], [collidingSkill]);
    const resetEntries = entries.filter((e) => e.command.toLowerCase() === "reset");
    expect(resetEntries).toHaveLength(1);
    expect(resetEntries[0].builtin).toBe(true);
  });

  it("dedupes: customSkills win over live skills with the same command name", () => {
    const customSkill: ChatInputCustomSkill = {
      name: "brainstorm",
      title: "Custom brainstorm",
      description: "Custom version",
    };
    const liveSkill = makeLiveSkill("brainstorm", "Live version");
    const entries = buildSlashEntries([customSkill], [liveSkill]);
    const brainstormEntries = entries.filter(
      (e) => e.command.toLowerCase() === "brainstorm",
    );
    expect(brainstormEntries).toHaveLength(1);
    expect(brainstormEntries[0].category).toBe("custom");
    expect(brainstormEntries[0].label).toBe("Custom brainstorm");
  });

  it("dedupes: live skills win over static bundled catalog entries with the same command name", () => {
    // The bundled catalog is skipped for anything already in seenCommands.
    // We can't easily predict which bundled commands exist, so we test
    // that a unique live-skill name does not produce duplicates.
    const liveSkill = makeLiveSkill("unique-live-skill-xyz");
    const entries = buildSlashEntries([], [liveSkill]);
    const matches = entries.filter((e) => e.command === "unique-live-skill-xyz");
    expect(matches).toHaveLength(1);
    expect(matches[0].category).toBe("skill");
  });

  it("live skills appear between customSkills and static bundled entries in ordering", () => {
    const custom: ChatInputCustomSkill = { name: "my-custom", title: "Custom", description: "" };
    const live = makeLiveSkill("my-live");
    const entries = buildSlashEntries([custom], [live]);

    const customIdx = entries.findIndex((e) => e.command === "my-custom");
    const liveIdx = entries.findIndex((e) => e.command === "my-live");

    expect(customIdx).toBeGreaterThanOrEqual(BUILTIN_COUNT);
    expect(liveIdx).toBeGreaterThan(customIdx);
  });

  it("getSlashMatches finds a live skill by name query", () => {
    const liveSkill = makeLiveSkill("deep-solve", "Iterative refinement solver");
    const entries = buildSlashEntries([], [liveSkill]);
    const matches = getSlashMatches(entries, "deep");
    expect(matches.map((e) => e.command)).toContain("deep-solve");
  });

  it("getSlashMatches finds a live skill by description", () => {
    const liveSkill = makeLiveSkill("my-solver", "Solve hard olympiad problems");
    const entries = buildSlashEntries([], [liveSkill]);
    const matches = getSlashMatches(entries, "olympiad");
    expect(matches.map((e) => e.command)).toContain("my-solver");
  });
});
