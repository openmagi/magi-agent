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

  it("opens a SKILL.md detail modal from a clickable skill card", () => {
    const source = readFileSync(new URL("./skills-catalog.tsx", import.meta.url), "utf8");

    // Detail endpoint is fetched with an encoded dir query param.
    expect(source).toContain("/v1/app/skills/file?dir=");
    expect(source).toContain("encodeURIComponent(skill.dir)");
    // Cards are keyboard-operable and open the detail modal.
    expect(source).toContain('role="button"');
    expect(source).toContain("openSkillDetail");
    expect(source).toContain("SkillDetailModal");

    const modalSource = readFileSync(
      new URL("./skill-detail-modal.tsx", import.meta.url),
      "utf8",
    );
    expect(modalSource).toContain("ReactMarkdown");
    expect(modalSource).toContain("overflow-y-auto");
  });
});
