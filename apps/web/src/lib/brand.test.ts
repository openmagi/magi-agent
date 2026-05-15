import { describe, expect, it } from "vitest";
import { PUBLIC_BRAND } from "./brand";

describe("public brand", () => {
  it("uses Open Magi as the public brand on openmagi.ai", () => {
    expect(PUBLIC_BRAND.name).toBe("Open Magi");
    expect(PUBLIC_BRAND.domain).toBe("openmagi.ai");
    expect(PUBLIC_BRAND.siteUrl).toBe("https://openmagi.ai");
    expect(PUBLIC_BRAND.sourceUrl).toBe("https://github.com/openmagi/magi-agent");
    expect(PUBLIC_BRAND.tagline).toBe("The programmable agent that complies with your rules");
    expect(PUBLIC_BRAND.supportEmail).toBe("support@openmagi.ai");
  });
});
