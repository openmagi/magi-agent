import { describe, it, expect } from "vitest";
import { isDiscordEnabled } from "./plan-gate";

describe("isDiscordEnabled", () => {
  it("returns true for max plan", () => {
    expect(isDiscordEnabled("max")).toBe(true);
  });

  it("returns true for flex plan", () => {
    expect(isDiscordEnabled("flex")).toBe(true);
  });

  it("returns false for pro plan", () => {
    expect(isDiscordEnabled("pro")).toBe(false);
  });

  it("returns false for pro_plus plan", () => {
    expect(isDiscordEnabled("pro_plus")).toBe(false);
  });

  it("returns false for byok plan", () => {
    expect(isDiscordEnabled("byok")).toBe(false);
  });

  it("returns false for unknown plan", () => {
    expect(isDiscordEnabled("unknown")).toBe(false);
  });
});
