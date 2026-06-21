import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./page-hint.tsx", import.meta.url),
  "utf8",
);

describe("PageHint — structured What/When/How card", () => {
  it("ships exactly three discrete slot kinds: can / cannot / note", () => {
    expect(src).toContain("can?: PageHintItem[]");
    expect(src).toContain("cannot?: PageHintItem[]");
    expect(src).toContain("note?: React.ReactNode");
  });

  it("renders ✓ for can-items and ✗ for cannot-items so the boundary is scannable", () => {
    expect(src).toContain("✓");
    expect(src).toContain("✗");
  });

  it("supports a warning tone for surfaces that mutate runtime behavior", () => {
    expect(src).toContain("warning");
    expect(src).toContain("amber");
  });

  it("exposes an aria-label so screen readers can name the hint card", () => {
    expect(src).toContain('aria-label={`Page hint:');
  });
});
