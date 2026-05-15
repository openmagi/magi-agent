import { describe, expect, it } from "vitest";
import {
  buildCustomSkillContent,
  customSkillPathKey,
  normalizeCustomSkillName,
  parseCustomSkillRow,
  validateCustomSkillInput,
} from "./custom-skills";

describe("custom skills", () => {
  it("normalizes owner skill names into custom-prefixed tool names", () => {
    expect(normalizeCustomSkillName("Invoice Review")).toBe("custom-invoice-review");
    expect(normalizeCustomSkillName("custom-tax-check")).toBe("custom-tax-check");
    expect(normalizeCustomSkillName("  매출 분석  ")).toMatch(/^custom-skill-[a-z0-9]{6}$/);
    expect(normalizeCustomSkillName("매출 분석")).not.toBe(normalizeCustomSkillName("세금 분석"));
  });

  it("generates prompt-only SKILL.md with safe frontmatter", () => {
    const input = validateCustomSkillInput({
      title: "Invoice Review",
      description: "Check vendor invoices before payment",
      body: "1. Compare vendor name.\n2. Check payment terms.",
      tags: ["finance", "ap"],
    });

    const content = buildCustomSkillContent(input);

    expect(content).toContain("name: custom-invoice-review");
    expect(content).toContain("kind: prompt");
    expect(content).toContain("description: Use this skill to check vendor invoices before payment.");
    expect(content).toContain("tags:");
    expect(content).toContain("- finance");
    expect(content).toContain("## Instructions");
    expect(content).toContain("Compare vendor name");
  });

  it("rejects collisions with bundled skill ids", () => {
    expect(() =>
      validateCustomSkillInput({
        title: "web-search",
        description: "Search the web better",
        body: "Use reliable sources.",
        tags: [],
      }),
    ).toThrow(/reserved/);
  });

  it("rejects overlong custom skill bodies", () => {
    expect(() =>
      validateCustomSkillInput({
        title: "Long Skill",
        description: "Use a long skill",
        body: "x".repeat(12_001),
        tags: [],
      }),
    ).toThrow(/too long/);
  });

  it("maps database rows to first-class custom skill list items", () => {
    const item = parseCustomSkillRow({
      id: "id-1",
      skill_name: "custom-invoice-review",
      content: buildCustomSkillContent(
        validateCustomSkillInput({
          title: "Invoice Review",
          description: "Check invoices",
          body: "Check totals.",
          tags: ["finance"],
        }),
      ),
      status: "promoted",
      created_at: "2026-05-02T00:00:00Z",
      reviewed_at: null,
    });

    expect(item).toEqual(
      expect.objectContaining({
        id: "id-1",
        name: "custom-invoice-review",
        title: "Invoice Review",
        description: "Use this skill to check invoices.",
        tags: ["finance"],
        status: "installed",
      }),
    );
  });

  it("uses skill directory safe keys for provisioning payloads", () => {
    expect(customSkillPathKey("custom-invoice-review")).toBe(
      "custom-invoice-review__SKILL.md",
    );
  });
});
