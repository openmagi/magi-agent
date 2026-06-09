import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./customize-tab.tsx", import.meta.url),
  "utf8",
);

describe("CustomizeRuntimeConsole local runtime contract", () => {
  it("loads customization from the local /v1/app/customize hook, not hosted bot endpoints", () => {
    expect(source).toContain("useCustomize");
    expect(source).not.toContain("/api/bots/");
    expect(source).not.toContain("useAuthFetch");
    expect(source).not.toContain("Privy");
  });

  it("keeps the exported component name + props stable so the page keeps compiling", () => {
    expect(source).toContain("export function CustomizeRuntimeConsole");
    expect(source).toContain("botId");
  });

  it("renders the two-card shell wired to the verification and tools modals", () => {
    expect(source).toContain("Verification Rules");
    expect(source).toContain("Custom Tools");
    expect(source).toContain("VerificationRuleModal");
    expect(source).toContain("CustomToolModal");
  });

  it("drops the old runtime-console catalog constants and direct local fetches", () => {
    expect(source).not.toContain("FIRST_PARTY_RECIPES");
    expect(source).not.toContain("HARNESS_PRESETS");
    expect(source).not.toContain("PHASE_ROUTES");
    expect(source).not.toContain("REPAIR_CONTROLS");
    expect(source).not.toContain("/api/tools");
    expect(source).not.toContain("/v1/app/skills");
    expect(source).not.toContain("/v1/app/config");
  });
});
