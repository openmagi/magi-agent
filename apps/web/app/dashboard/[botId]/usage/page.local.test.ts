import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  "apps/web/app/dashboard/[botId]/usage/page.tsx",
  "utf8",
);

describe("local OSS usage dashboard", () => {
  it("renders runtime usage from local app APIs without hosted billing APIs", () => {
    expect(source).toContain("/v1/app/runtime");
    expect(source).toContain("Runtime Usage");
    expect(source).not.toContain("/api/bots/");
    expect(source).not.toContain("/api/search-quota");
    expect(source).not.toContain("/api/email-quota");
    expect(source).not.toContain("/api/credits");
  });
});
