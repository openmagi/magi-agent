import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  "apps/web/app/dashboard/[botId]/customize/page.tsx",
  "utf8",
);

describe("local OSS customize dashboard", () => {
  it("uses the shared cloud customization surface instead of the old OSS prompt file editor", () => {
    expect(source).toContain("CustomizeTab");
    expect(source).not.toContain("/v1/app/workspace/file");
    expect(source).not.toContain("Local Customization");
    expect(source).not.toContain("Instruction Files");
  });
});
