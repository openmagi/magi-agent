import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("bot usage page model keys", () => {
  it("supports the normalized Gemini 3.1 Flash usage key returned by the usage API", () => {
    const source = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");

    expect(source).toContain("\"gemini_3_1_flash\"");
    expect(source).toContain("gemini_3_1_flash: \"Gemini 3.1 Flash\"");
  });
});
