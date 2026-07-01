import { describe, expect, it } from "vitest";

import { parseList, slugifyModeId } from "./modes-panel.helpers";

/** Backend id contract: `magi_agent/customize/modes.py` `_MODE_ID_RE`. */
const MODE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

describe("slugifyModeId", () => {
  it("slugifies a plain Latin label", () => {
    expect(slugifyModeId("Careful coding")).toBe("careful-coding");
  });

  it("falls back to 'mode' for a non-Latin label", () => {
    const id = slugifyModeId("한글 모드");
    expect(id).toBe("mode");
    expect(id).toMatch(MODE_ID_RE);
  });

  it("falls back to 'mode' for punctuation-only labels", () => {
    expect(slugifyModeId("!!!")).toBe("mode");
  });

  it("clamps long labels and stays backend-valid", () => {
    const id = slugifyModeId("a".repeat(120));
    expect(id.length).toBeLessThanOrEqual(64);
    expect(id).toMatch(MODE_ID_RE);
  });

  it("disambiguates a colliding base id with a -N suffix", () => {
    const taken = new Set(["careful-coding"]);
    expect(slugifyModeId("Careful coding", taken)).toBe("careful-coding-2");
  });

  it("walks past multiple collisions", () => {
    const taken = new Set(["mode", "mode-2", "mode-3"]);
    expect(slugifyModeId("!!!", taken)).toBe("mode-4");
  });

  it("keeps a disambiguated id within the 64-char cap and valid", () => {
    const taken = new Set(["a".repeat(64)]);
    const id = slugifyModeId("a".repeat(120), taken);
    expect(id.length).toBeLessThanOrEqual(64);
    expect(id).toMatch(MODE_ID_RE);
    expect(id.endsWith("-2")).toBe(true);
  });

  it("always yields a backend-valid id across varied inputs", () => {
    const labels = [
      "Careful coding",
      "한글 모드",
      "!!!",
      "",
      "   ",
      "a".repeat(120),
      "Mixed 한글 + 123 !!!",
      "-leading-and-trailing-",
      "UPPER CASE",
      "emoji 🎉 mode",
    ];
    for (const label of labels) {
      expect(slugifyModeId(label)).toMatch(MODE_ID_RE);
    }
  });
});

describe("parseList", () => {
  it("splits on newlines", () => {
    expect(parseList("WebSearch\nBash\n")).toEqual(["WebSearch", "Bash"]);
  });

  it("splits on commas", () => {
    expect(parseList("WebSearch, Bash")).toEqual(["WebSearch", "Bash"]);
  });

  it("trims and drops empty entries", () => {
    expect(parseList("  WebSearch  \n\n , Bash ")).toEqual(["WebSearch", "Bash"]);
  });

  it("de-duplicates in first-seen order", () => {
    expect(parseList("Bash\nWebSearch\nBash")).toEqual(["Bash", "WebSearch"]);
  });

  it("returns [] for an all-whitespace input", () => {
    expect(parseList("   \n , ")).toEqual([]);
  });
});
