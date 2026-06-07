import { describe, expect, it } from "vitest";

import { getFirstChannelName, getNextChannelAfterDeletion } from "./channel-navigation";

describe("channel-navigation", () => {
  const channels = [
    { name: "general", position: 0 },
    { name: "random", position: 1 },
    { name: "quick-memo", position: 2 },
  ];

  it("returns the first channel name when channels exist", () => {
    expect(getFirstChannelName(channels)).toBe("general");
  });

  it("returns null when there are no channels", () => {
    expect(getFirstChannelName([])).toBeNull();
  });

  it("falls back to the first remaining channel after deleting general", () => {
    expect(getNextChannelAfterDeletion(channels, "general")).toBe("random");
  });

  it("returns null when the deleted channel was the last remaining channel", () => {
    expect(getNextChannelAfterDeletion([{ name: "general", position: 0 }], "general")).toBeNull();
  });
});
