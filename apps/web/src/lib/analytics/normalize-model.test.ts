import { describe, expect, it } from "vitest";

import { normalizeModelName } from "./normalize-model";

describe("normalizeModelName", () => {
  it("preserves Gemini 3.1 Pro usage as the Gemini 3.1 Pro display key", () => {
    expect(normalizeModelName("gemini-3.1-pro-preview")).toBe("gemini_3_1_pro");
    expect(normalizeModelName("google/gemini-3.1-pro-preview")).toBe("gemini_3_1_pro");
  });

  it("preserves Gemini 3.1 Flash Lite usage as the Gemini 3.1 Flash display key", () => {
    expect(normalizeModelName("gemini-3.1-flash-lite-preview")).toBe("gemini_3_1_flash");
    expect(normalizeModelName("google/gemini-3.1-flash-lite-preview")).toBe("gemini_3_1_flash");
  });
});
