import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  "apps/web/app/dashboard/[botId]/learning/page.tsx",
  "utf8",
);

describe("local OSS learning dashboard", () => {
  it("renders learning governance from local runtime APIs", () => {
    expect(source).toContain("/v1/learning/learnings");
    expect(source).toContain("/v1/learning/reflection/run");
    expect(source).toContain("x-approver");
    expect(source).toContain("Learning Governance");
    expect(source).not.toContain("/api/bots/");
    expect(source).not.toContain("Supabase");
    expect(source).not.toContain("Privy");
  });
});
