import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  "apps/web/app/dashboard/[botId]/customize/page.tsx",
  "utf8",
);

describe("local OSS customize dashboard", () => {
  it("edits local workspace prompt files instead of hosted bot customization", () => {
    expect(source).toContain("/v1/app/workspace/file");
    expect(source).toContain("Local Customization");
    expect(source).not.toContain("CustomizeTab");
    expect(source).not.toContain("/api/bots/");
  });
});
