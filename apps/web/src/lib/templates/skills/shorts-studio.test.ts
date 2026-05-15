import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { SKILLS } from "@/lib/skills-catalog";
import en from "@/lib/i18n/locales/en";
import es from "@/lib/i18n/locales/es";
import ja from "@/lib/i18n/locales/ja";
import ko from "@/lib/i18n/locales/ko";
import zh from "@/lib/i18n/locales/zh";

const skillPath = new URL("./shorts-studio/SKILL.md", import.meta.url);

describe("shorts-studio skill", () => {
  it("is registered as a marketing skill with user-facing commands", () => {
    const skill = SKILLS.find((entry) => entry.id === "shorts-studio");

    expect(skill).toBeDefined();
    expect(skill?.category).toBe("marketing");
    expect(skill?.commands).toEqual(expect.arrayContaining(["shorts", "쇼츠", "reels", "릴스"]));
    expect(skill?.related).toEqual(expect.arrayContaining(["ad-creative-generator", "meta-social", "twitter"]));
  });

  it("documents an Open Magi-native shorts workflow without Kling or ffmpeg requirements", () => {
    expect(existsSync(skillPath)).toBe(true);

    const body = readFileSync(skillPath, "utf8");

    expect(body).toContain("integration.sh gemini-image/generate");
    expect(body).toContain("integration.sh gemini-video/generate");
    expect(body).toContain("FileDeliver");
    expect(body).toContain("/workspace/shorts-studio/");
    expect(body).not.toMatch(/\bKling\b/i);
    expect(body).not.toMatch(/\bffmpeg\b/i);
  });

  it("has dashboard copy for every supported locale", () => {
    for (const messages of [en, ko, ja, zh, es]) {
      const entry = messages.skillsCatalog.skills["shorts-studio"];

      expect(entry?.name).toBeTruthy();
      expect(entry?.description).toBeTruthy();
      expect(entry?.examples?.length).toBeGreaterThanOrEqual(2);
      expect(entry?.details).toContain("Gemini");
    }
  });
});
