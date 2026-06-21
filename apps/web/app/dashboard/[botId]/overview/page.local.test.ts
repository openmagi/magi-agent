import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("local overview page", () => {
  it("renders local runtime status instead of hosted bot provisioning empty state", () => {
    const source = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");

    expect(source).toContain("/v1/app/runtime");
    expect(source).toContain("Open Magi Agent");
    expect(source).toContain("magi-agent serve");
    expect(source).not.toContain("DashboardOverview");
    expect(source).not.toContain("noBotTitle");
    expect(source).not.toContain("deployFirstBot");
  });
});
