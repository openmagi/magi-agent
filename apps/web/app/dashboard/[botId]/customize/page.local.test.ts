import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./page.tsx", import.meta.url),
  "utf8",
);

describe("local OSS customize dashboard", () => {
  it("mounts the Phase-4 hub (sub-nav full-page surface)", () => {
    // Phase 4 replaced the legacy CustomizeRuntimeConsole modal duo with the
    // CustomizeHub component (left sub-nav + page-resident panels).
    expect(source).toContain("CustomizeHub");
  });

  it("syncs the active sub-nav section to the URL query string", () => {
    expect(source).toContain("section");
    expect(source).toContain("useSearchParams");
    expect(source).toContain("onSectionChange");
  });

  it("uses the new unified Rules sub-nav as the default landing section", () => {
    // Phase-1 unified redesign collapsed Verification + Advanced; rules is
    // the new first sub-nav. The legacy ids must not survive in the
    // VALID_SECTIONS list, or deep-links like ?section=verification would
    // silently land on the wrong page.
    expect(source).toContain('"rules"');
    expect(source).not.toMatch(/['"]verification['"]/);
    expect(source).not.toMatch(/['"]advanced['"]/);
  });

  it("does not embed hosted-only auth/file surfaces", () => {
    expect(source).not.toContain("/v1/app/workspace/file");
    expect(source).not.toContain("useAuthFetch");
    expect(source).not.toContain("Instruction Files");
  });
});
