import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const source = () =>
  readFileSync(path.join(process.cwd(), "src/components/dashboard/referral-panel.tsx"), "utf8");

describe("ReferralPanel visual tokens", () => {
  it("uses dashboard light-theme tokens instead of dark-panel text colors", () => {
    const component = source();

    expect(component).toContain("text-foreground");
    expect(component).toContain("text-secondary");
    expect(component).toContain("text-muted");
    expect(component).not.toContain("text-gray-300");
    expect(component).not.toContain("text-gray-400");
    expect(component).not.toContain("font-bold text-white");
    expect(component).not.toContain("disabled:bg-gray-700");
  });
});
