import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./gates-panel.tsx", import.meta.url),
  "utf8",
);

describe("GatesPanel — UX restructure B unified gates surface", () => {
  it("reuses the existing CustomRulesSection and CustomChecksSection (no fork)", () => {
    expect(src).toContain("CustomRulesSection");
    expect(src).toContain("CustomChecksSection");
  });

  it("frames the two backends by WHEN the gate fires, not by code path", () => {
    expect(src).toContain("pre-final");
    expect(src).toContain("before-tool");
    expect(src).toContain("After-tool");
  });

  it("explicitly disclaims SeamSpec so users do not look for the gate builder here", () => {
    expect(src).toContain("SeamSpec");
    expect(src).toContain("Advanced");
    expect(src).toContain("does not add a new gate");
  });

  it("calls out the after-tool feature flag honestly", () => {
    expect(src).toContain("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED");
  });
});
