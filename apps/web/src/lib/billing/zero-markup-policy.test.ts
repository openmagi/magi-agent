import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

function read(path: string): string {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

describe("zero-markup billing policy", () => {
  it("does not apply student discounts in subscription checkout paths", () => {
    const checkoutSources = [
      read("../services/billing-service.ts"),
      read("../services/bot-service.ts"),
    ];

    for (const source of checkoutSources) {
      expect(source).not.toContain("applyStudentDiscount");
      expect(source).not.toContain("getPrivyUserEmail");
      expect(source).not.toContain("user_email");
      expect(source).not.toMatch(/student discount/i);
    }
  });

  it("does not grant fallback welcome bonus credits after checkout", () => {
    const fallbackSources = [
      read("../../app/dashboard/overview/page.tsx"),
      read("../../app/dashboard/new/page.tsx"),
    ];

    for (const source of fallbackSources) {
      expect(source).not.toContain("WELCOME_BONUS_CENTS");
      expect(source).not.toContain("Welcome bonus");
      expect(source).not.toMatch(/increment_credits[\s\S]*bonus/i);
    }
  });

  it("replaces public trial-credit and student-discount copy with zero-markup copy", () => {
    const homeSource = read("../../app/home-client.tsx");
    const marketingSource = read("../../../docs/content/marketing/reddit-autonomous-agent-os.md");
    expect(homeSource).toContain("provider cost plus VAT only");
    expect(homeSource).toContain("0% LLM markup");
    expect(homeSource).toContain("$5/mo LLM credits");
    expect(homeSource).toContain("$80/mo LLM credits");
    expect(homeSource).toContain("$350/mo LLM credits");
    expect(homeSource).toContain("$1,900/mo LLM credits");
    expect(homeSource).not.toContain("dollar-for-dollar model credits");
    expect(homeSource).not.toContain("$15/mo credits");
    expect(homeSource).not.toContain("$100/mo credits");
    expect(homeSource).not.toContain("$450/mo credits");
    expect(homeSource).not.toContain("$2,300/mo credits");
    expect(homeSource).not.toContain("welcomeCredits");
    expect(homeSource).not.toContain("studentDiscount");
    expect(marketingSource).toContain("provider cost plus VAT only");
    expect(marketingSource).toContain("0% LLM markup");
    expect(marketingSource).not.toContain("$10 welcome credits");

    for (const locale of ["en", "ko", "ja", "zh", "es"]) {
      const source = read(`../i18n/locales/${locale}.ts`);
      const legacyPlanCreditPatterns = [
        /\$15(?:\/mo|\/mes|\s+platform|\s+플랫폼|\s+크레딧|クレジット|のプラットフォーム|\s+平台|\s+额度)/,
        /\$100(?:\/mo|\/mes|\s+platform|\s+플랫폼|\s+크레딧|クレジット|のプラットフォーム|\s+平台|\s+额度)/,
        /\$450(?:\/mo|\/mes|\s+platform|\s+플랫폼|\s+크레딧|クレジット|のプラットフォーム|\s+平台|\s+额度)/,
        /\$2,300(?:\/mo|\/mes|\s+platform|\s+플랫폼|\s+크레딧|クレジット|のプラットフォーム|\s+平台|\s+额度)/,
      ];

      expect(source).toContain("zeroMarkupBadge");
      expect(source).not.toContain("welcomeCredits");
      expect(source).not.toContain("studentDiscount");
      for (const pattern of legacyPlanCreditPatterns) {
        expect(source).not.toMatch(pattern);
      }
      expect(source).toContain("$14.99");
      expect(source).toContain("$89.99");
      expect(source).toContain("$399");
      expect(source).toContain("$1,999");
    }
  });

  it("keeps structured app metadata and mobile plan copy aligned to hosted LLM credits", () => {
    const layoutSource = read("../../app/layout.tsx");
    const pageSource = read("../../app/page.tsx");
    const mobileSource = read("../../../apps/mobile/src/lib/constants.ts");
    const metadataSources = [layoutSource, pageSource, mobileSource];

    for (const source of metadataSources) {
      expect(source).not.toContain("student discounts");
      expect(source).not.toContain("Free trial credits");
      expect(source).not.toContain("$15/mo platform credits");
      expect(source).not.toContain("$100/mo platform credits");
      expect(source).not.toContain("$450/mo platform credits");
      expect(source).not.toContain("$2,300/mo platform credits");
    }
    expect(layoutSource).toContain('price: "14.99"');
    expect(pageSource).toContain("$14.99");
    expect(mobileSource).toContain("$14.99");
    expect(layoutSource).toContain("$5/mo LLM credits");
    expect(layoutSource).toContain("$80/mo LLM credits");
    expect(layoutSource).toContain("$350/mo LLM credits");
    expect(layoutSource).toContain("$1,900/mo LLM credits");
    expect(mobileSource).toContain("$5/mo LLM credits");
    expect(mobileSource).toContain("$80/mo LLM credits");
  });
});
