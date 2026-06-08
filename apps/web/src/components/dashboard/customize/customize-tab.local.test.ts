import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./customize-tab.tsx", import.meta.url),
  "utf8",
);

describe("CustomizeRuntimeConsole local runtime contract", () => {
  it("uses local runtime APIs instead of hosted bot customization endpoints", () => {
    expect(source).toContain("useAgentFetch");
    expect(source).toContain("/api/tools");
    expect(source).toContain("/v1/app/skills");
    expect(source).toContain("/v1/app/config");
    expect(source).not.toContain("/api/bots/");
    expect(source).not.toContain("useAuthFetch");
    expect(source).not.toContain("Privy");
  });

  it("surfaces ADK runtime customization concepts instead of TS-era bot settings", () => {
    expect(source).toContain("Python ADK runtime");
    expect(source).toContain("FIRST_PARTY_RECIPES");
    expect(source).toContain("HARNESS_PRESETS");
    expect(source).toContain("PHASE_ROUTES");
    expect(source).toContain("REPAIR_CONTROLS");
    expect(source).toContain("Evidence & repair");
    expect(source).toContain("openmagi.research");
    expect(source).toContain("coding.evidence_gate");
  });
});
