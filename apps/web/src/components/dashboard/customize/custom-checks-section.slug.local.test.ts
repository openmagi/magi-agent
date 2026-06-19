import { describe, expect, it } from "vitest";
// Imports the pure helper module (no React / `@/` deps) so the unit test runs
// in the node env. The component re-exports `slugifyCheckId` from here and uses
// it at submit time.
import { slugifyCheckId } from "./custom-checks-section.slug";

/** Backend id contract: `magi_agent/packs/dashboard_authored.py` `_ID_RE`. */
const ID_RE = /^[a-z0-9][a-z0-9_-]{0,62}$/;

describe("slugifyCheckId", () => {
  it("slugifies a plain Latin label", () => {
    expect(slugifyCheckId("Block SSN leak")).toBe("block-ssn-leak");
  });

  it("falls back to a valid id for a non-Latin label", () => {
    const id = slugifyCheckId("한글 라벨");
    expect(id).toBe("check");
    expect(id).toMatch(ID_RE);
  });

  it("falls back to 'check' for punctuation-only labels", () => {
    expect(slugifyCheckId("!!!")).toBe("check");
  });

  it("clamps a long label to <= 63 chars and stays valid", () => {
    const label = "a".repeat(70);
    const id = slugifyCheckId(label);
    expect(id.length).toBeLessThanOrEqual(63);
    expect(id).toMatch(ID_RE);
  });

  it("appends -N on collision", () => {
    expect(slugifyCheckId("My check", new Set(["my-check"]))).toBe("my-check-2");
  });

  it("walks past multiple collisions", () => {
    const taken = new Set(["my-check", "my-check-2", "my-check-3"]);
    expect(slugifyCheckId("My check", taken)).toBe("my-check-4");
  });

  it("always yields a backend-valid id across varied inputs", () => {
    const labels = [
      "Block SSN leak",
      "한글 라벨",
      "!!!",
      "",
      "   ",
      "a".repeat(70),
      "Mixed 한글 + 123 !!!",
      "-leading-and-trailing-",
      "UPPER CASE Label",
      "emoji 🎉 label",
      "z".repeat(63) + "-",
    ];
    for (const label of labels) {
      expect(slugifyCheckId(label)).toMatch(ID_RE);
    }
  });
});
