import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { SKILLS } from "@/lib/skills-catalog";

const reportedMissingSkillIds = [
  "legal-document-drafter",
  "notion-kb",
  "slack-integration",
  "spotify-integration",
  "twitter",
  "zapier",
  "yahoo-finance-data",
  "imf-economic-data",
  "world-bank-data",
  "sec-edgar-research",
  "trading",
  "yaml-finance-data",
  "financial-statement-forensics",
  "capital-allocation-quality",
];

describe("bundled skill inventory", () => {
  it("keeps reported integration/data skills present in source and the web catalog", () => {
    const catalogIds = new Set(SKILLS.map((skill) => skill.id));
    const mobileCatalogSource = readFileSync(
      join(process.cwd(), "apps/mobile/src/lib/skills-catalog.ts"),
      "utf8",
    );

    for (const skillId of reportedMissingSkillIds) {
      expect(
        existsSync(join(process.cwd(), "src/lib/templates/skills", skillId, "SKILL.md")),
        `${skillId} should have a bundled SKILL.md`,
      ).toBe(true);
      expect(catalogIds.has(skillId), `${skillId} should be in SKILLS`).toBe(true);
      expect(mobileCatalogSource, `${skillId} should be in the mobile catalog`).toContain(
        `id: "${skillId}"`,
      );
    }
  });
});
