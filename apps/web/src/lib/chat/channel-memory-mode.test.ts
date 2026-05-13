import { describe, expect, it } from "vitest";

import {
  buildMemoryModeChannelIdentity,
  formatChannelMemoryLabel,
  getChannelMemoryMode,
  withChannelMemoryModeSuffix,
  type ChannelMemoryModeOption,
} from "./channel-memory-mode";
import type { Channel } from "./types";

function channel(overrides: Partial<Channel>): Channel {
  return {
    id: overrides.id ?? "channel-1",
    name: overrides.name ?? "general",
    display_name: overrides.display_name ?? null,
    position: overrides.position ?? 0,
    category: overrides.category ?? "General",
    memory_mode: overrides.memory_mode,
    created_at: overrides.created_at ?? "2026-05-11T00:00:00.000Z",
  };
}

describe("channel memory mode helpers", () => {
  it.each([
    ["research-read-only-memory", "read_only"],
    ["research-readonly-memory", "read_only"],
    ["research-no-memory", "incognito"],
    ["memory-off-research", "incognito"],
  ] satisfies Array<[string, ChannelMemoryModeOption]>)(
    "detects %s from the channel slug",
    (name, expected) => {
      expect(getChannelMemoryMode(channel({ name }))).toBe(expected);
    },
  );

  it("prefers explicit memory_mode when a local channel has one", () => {
    expect(getChannelMemoryMode(channel({ name: "research", memory_mode: "incognito" }))).toBe("incognito");
    expect(getChannelMemoryMode(channel({ name: "memo", memory_mode: "read_only" }))).toBe("read_only");
  });

  it("formats visible labels without duplicating an existing suffix", () => {
    const noMemory = channel({ name: "research-no-memory", display_name: "Research" });
    const alreadyLabeled = channel({
      name: "memo-read-only-memory",
      display_name: "Memo · Read-only memory",
    });

    expect(withChannelMemoryModeSuffix(noMemory)).toBe("Research · No memory");
    expect(withChannelMemoryModeSuffix(alreadyLabeled)).toBe("Memo · Read-only memory");
    expect(formatChannelMemoryLabel("incognito")).toBe("No memory");
    expect(formatChannelMemoryLabel("read_only")).toBe("Read-only memory");
  });

  it("builds durable slug and display name for new memory-mode channels", () => {
    expect(buildMemoryModeChannelIdentity("Research", "incognito")).toEqual({
      name: "research-no-memory",
      displayName: "Research · No memory",
      memoryMode: "incognito",
    });
    expect(buildMemoryModeChannelIdentity("Memo", "read_only")).toEqual({
      name: "memo-read-only-memory",
      displayName: "Memo · Read-only memory",
      memoryMode: "read_only",
    });
    expect(buildMemoryModeChannelIdentity("No Memory", "incognito")).toEqual({
      name: "no-memory",
      displayName: "No Memory",
      memoryMode: "incognito",
    });
  });
});
