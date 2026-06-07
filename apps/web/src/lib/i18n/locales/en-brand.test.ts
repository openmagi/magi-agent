import { describe, expect, it } from "vitest";
import en from "./en";

describe("English Open Magi public copy", () => {
  it("uses Open Magi in high-impact landing and FAQ copy", () => {
    expect(en.landing.heroDescription).toContain("Open Magi");
    expect(en.landing.heroDescription).toContain("real business operations");
    expect(en.landing.heroDescription).toContain("verifiable outcomes");
    expect(en.landing.heroDescription).toContain("long-running missions");
    expect(en.landing.useCasesSubtitle).toContain("Open Magi");
    expect(en.landing.useCasesSubtitle).toContain("Open Magi Cloud");
    expect(en.landing.assistantFooter).toContain("Open Magi");
    expect(en.landing.footerCopy).toBe("Open Magi. All rights reserved.");
    expect(en.landing.selfHostColOpenMagi).toBe("Open Magi");
    expect(en.faq.items[0]?.q).toBe("What is Open Magi?");
    expect(en.faq.items[0]?.a).toContain("programmable AI agent");
    expect(JSON.stringify(en.landing)).not.toContain("AI Transformation");
    expect(en.onboarding.purposeTitle).toBe("What will you use Open Magi for?");
  });
});
