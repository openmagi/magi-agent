import { describe, expect, it } from "vitest";
import {
  formatChannelBaseLabel,
  formatChannelMemoryBadgeLabel,
  formatChannelMemoryLabel,
  getChannelMemoryMode,
  stripChannelMemoryModeSuffix,
  withChannelMemoryModeSuffix,
} from "./channel-memory-mode";

describe("channel memory mode labels", () => {
  it("formats user-visible labels for non-normal modes", () => {
    expect(formatChannelMemoryLabel("normal")).toBeNull();
    expect(formatChannelMemoryLabel("read_only")).toBe("Read-only memory");
    expect(formatChannelMemoryLabel("incognito")).toBe("No memory");
  });

  it("formats compact badge labels for sidebar rows", () => {
    expect(formatChannelMemoryBadgeLabel("normal")).toBeNull();
    expect(formatChannelMemoryBadgeLabel("read_only")).toBe("Read-only");
    expect(formatChannelMemoryBadgeLabel("incognito")).toBe("No mem");
  });

  it("defaults missing channel memory modes to normal", () => {
    expect(getChannelMemoryMode(null)).toBe("normal");
    expect(getChannelMemoryMode({ memory_mode: undefined })).toBe("normal");
    expect(getChannelMemoryMode({ memory_mode: "read_only" })).toBe("read_only");
  });

  it("adds a durable text suffix when a channel has a memory mode", () => {
    expect(
      withChannelMemoryModeSuffix({
        name: "research",
        display_name: "Research",
        memory_mode: "read_only",
      }),
    ).toBe("Research · Read-only memory");

    expect(
      withChannelMemoryModeSuffix({
        name: "private",
        display_name: null,
        memory_mode: "incognito",
      }),
    ).toBe("private · No memory");
  });

  it("does not duplicate an existing suffix", () => {
    expect(stripChannelMemoryModeSuffix("Research · No memory")).toBe("Research");
    expect(formatChannelBaseLabel({ name: "research", display_name: "Research · No memory" })).toBe("Research");
    expect(
      withChannelMemoryModeSuffix({
        name: "research",
        display_name: "Research · No memory",
        memory_mode: "incognito",
      }),
    ).toBe("Research · No memory");
  });
});
