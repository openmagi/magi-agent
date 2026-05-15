import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("social-browser skill template", () => {
  it("documents the native read-only tool and password prohibition", () => {
    const skill = readFileSync(
      path.join(process.cwd(), "src/lib/templates/skills/social-browser/SKILL.md"),
      "utf8",
    );

    expect(skill).toContain("SocialBrowser");
    expect(skill).toContain("scrape_visible");
    expect(skill).toContain("Do not ask for, collect, store, replay");
    expect(skill).toContain("Do not do bulk crawling");
  });
});
