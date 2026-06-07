import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("UsageChart", () => {
  it("uses a light readable tooltip surface in dashboard usage cards", () => {
    const source = readFileSync(new URL("./usage-chart.tsx", import.meta.url), "utf8");

    expect(source).not.toContain("bg-black/90");
    expect(source).toContain("bg-white/95");
    expect(source).toContain("shadow-lg");
  });
});
