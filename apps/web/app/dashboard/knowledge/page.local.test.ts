import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync("apps/web/app/dashboard/knowledge/page.tsx", "utf8");

describe("local OSS knowledge dashboard", () => {
  it("uses workspace knowledge APIs instead of hosted knowledge console APIs", () => {
    expect(source).toContain("/v1/app/knowledge");
    expect(source).toContain("/v1/app/knowledge/file");
    expect(source).toContain("Local Knowledge");
    expect(source).not.toContain("/api/knowledge");
    expect(source).not.toContain("KnowledgeConsole");
  });
});
