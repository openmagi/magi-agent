import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("local OSS skills page", () => {
  it("uses the runtime skill directory instead of hosted bot skill APIs", () => {
    const source = readFileSync(new URL("./skills-catalog.tsx", import.meta.url), "utf8");

    expect(source).toContain("/v1/app/skills");
    expect(source).toContain("/v1/app/skills/reload");
    expect(source).toContain("Prompt skills");
    expect(source).toContain("Script skills");
    expect(source).toContain("Runtime hooks");
    expect(source).not.toContain("/api/bots/${botId}/custom-skills");
    expect(source).not.toContain("/api/bots/${botId}/skills/refresh");
    expect(source).not.toContain("@/lib/skills-catalog");
  });
});
