import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  "apps/web/app/dashboard/[botId]/settings/tools/page.tsx",
  "utf8",
);

describe("local OSS tools settings", () => {
  it("uses local tool APIs instead of hosted bot tool APIs", () => {
    expect(source).toContain("/api/tools");
    expect(source).toContain("@/lib/local-api");
    expect(source).not.toContain("/api/bots/");
  });
});
