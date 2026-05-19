import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("SkillsCatalog responsive layout", () => {
  it("keeps tab scrolling and card content within the dashboard viewport", () => {
    const source = readFileSync(new URL("./skills-catalog.tsx", import.meta.url), "utf8");

    expect(source).toContain('className="max-w-6xl space-y-6 pb-20"');
    expect(source).toContain("lg:grid-cols-[minmax(0,1fr)_auto]");
    expect(source).toContain("grid gap-3 md:grid-cols-2 xl:grid-cols-3");
    expect(source).toContain("truncate");
    expect(source).toContain("Search skills...");
  });

  it("exposes local runtime skill directory controls", () => {
    const source = readFileSync(new URL("./skills-catalog.tsx", import.meta.url), "utf8");

    expect(source).toContain("/v1/app/skills");
    expect(source).toContain("/v1/app/skills/reload");
    expect(source).toContain("Prompt skills");
    expect(source).toContain("Script skills");
    expect(source).toContain("Runtime Hooks");
    expect(source).toContain("Issue detail");
    expect(source).not.toContain("/api/bots/${botId}");
  });
});
