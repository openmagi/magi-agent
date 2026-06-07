import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  "apps/web/app/dashboard/[botId]/memory/memory-client.tsx",
  "utf8",
);

describe("local OSS memory dashboard", () => {
  it("uses local runtime memory APIs instead of hosted bot APIs", () => {
    expect(source).toContain("@/lib/local-api");
    expect(source).toContain("/v1/app/memory");
    expect(source).toContain("/v1/app/memory/file");
    expect(source).toContain("/v1/app/memory/search");
    expect(source).toContain("/v1/app/memory/files");
    expect(source).not.toContain("/api/bots/");
  });
});
