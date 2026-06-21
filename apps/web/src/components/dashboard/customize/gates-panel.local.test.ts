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

  it("points users at Advanced for SeamSpec rewires so they do not look for it here", () => {
    expect(src).toContain("Advanced");
    expect(src).toContain("Rewire an existing built-in preset");
  });

  it("calls out the after-tool feature flag honestly", () => {
    expect(src).toContain("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED");
  });
});
