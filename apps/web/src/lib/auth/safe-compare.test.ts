import { describe, it, expect } from "vitest";
import { safeCompare } from "./safe-compare";

describe("safeCompare", () => {
  it("returns true for identical strings", () => {
    expect(safeCompare("secret123", "secret123")).toBe(true);
  });

  it("returns false for different strings", () => {
    expect(safeCompare("secret123", "wrong")).toBe(false);
  });

  it("returns false for different lengths", () => {
    expect(safeCompare("short", "a-much-longer-string")).toBe(false);
  });

  it("returns false for empty vs non-empty", () => {
    expect(safeCompare("", "something")).toBe(false);
  });

  it("returns true for two empty strings", () => {
    expect(safeCompare("", "")).toBe(true);
  });

  it("handles unicode strings", () => {
    expect(safeCompare("한국어", "한국어")).toBe(true);
    expect(safeCompare("한국어", "日本語")).toBe(false);
  });
});
