import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./page.tsx", import.meta.url),
  "utf8",
);

describe("local OSS customize dashboard", () => {
  it("renders the local Python ADK runtime customize console", () => {
    expect(source).toContain("CustomizeRuntimeConsole");
    expect(source).not.toContain("/v1/app/workspace/file");
    expect(source).not.toContain("useAuthFetch");
    expect(source).not.toContain("Instruction Files");
  });
});
