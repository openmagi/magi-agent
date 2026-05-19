import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  "apps/web/app/dashboard/[botId]/settings/hooks/page.tsx",
  "utf8",
);

describe("local OSS hooks settings", () => {
  it("uses local runtime skill hook APIs instead of hosted bot hook APIs", () => {
    expect(source).toContain("/v1/app/skills");
    expect(source).toContain("@/lib/local-api");
    expect(source).not.toContain("/api/bots/");
  });
});
