import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  "apps/web/app/dashboard/[botId]/observability/page.tsx",
  "utf8",
);

describe("local OSS observability dashboard", () => {
  it("renders observability from local runtime APIs", () => {
    expect(source).toContain("/api/observability/v1/meta");
    expect(source).toContain("/api/observability/v1/activity");
    expect(source).toContain("/api/observability/v1/sessions");
    expect(source).toContain("/api/observability/v1/health/live");
    expect(source).toContain("/api/observability/v1/board");
    expect(source).toContain("Runtime Observability");
    expect(source).not.toContain("/api/bots/");
    expect(source).not.toContain("Supabase");
    expect(source).not.toContain("Privy");
  });
});
