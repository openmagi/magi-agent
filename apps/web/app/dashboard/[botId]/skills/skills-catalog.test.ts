import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("SkillsCatalog responsive layout", () => {
  it("keeps tab scrolling and card content within the dashboard viewport", () => {
    const source = readFileSync(new URL("./skills-catalog.tsx", import.meta.url), "utf8");

    expect(source).toContain('className="min-w-0 space-y-6 sm:space-y-8 pb-24"');
    expect(source).toContain("min-w-0 overflow-x-auto");
    expect(source).toContain("grid-cols-1 xl:grid-cols-2 2xl:grid-cols-3");
    expect(source).toContain("min-w-0 flex-1");
    expect(source).toContain("break-words");
  });

  it("exposes first-class custom skill installation controls", () => {
    const source = readFileSync(new URL("./skills-catalog.tsx", import.meta.url), "utf8");

    expect(source).toContain("sc.customSkills");
    expect(source).toContain("/api/bots/${botId}/custom-skills");
    expect(source).toContain("/api/bots/${botId}/skills/refresh");
    expect(source).toContain("handleInstallCustomSkill");
    expect(source).toContain("handleDeleteCustomSkill");
  });
});
