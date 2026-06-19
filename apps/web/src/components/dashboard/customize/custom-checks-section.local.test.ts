import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./custom-checks-section.tsx", import.meta.url),
  "utf8",
);
const modalSrc = readFileSync(
  new URL("./verification-rule-modal.tsx", import.meta.url),
  "utf8",
);

describe("custom-checks-section", () => {
  it("renders the after-tool builder form fields", () => {
    expect(src).toContain("trigger.match");
    expect(src).toContain("isRegex");
    expect(src).toContain("Tool name");
    expect(src).toContain("Action");
  });
  it("uses block/audit actions only (v1)", () => {
    expect(src).toContain('value="block"');
    expect(src).toContain('value="audit"');
  });
  it("uses putDashboardCheck / deleteDashboardCheck / getDashboardChecks", () => {
    expect(src).toContain("putDashboardCheck");
    expect(src).toContain("deleteDashboardCheck");
    expect(src).toContain("getDashboardChecks");
  });
  it("honest 'self-host only' label", () => {
    expect(src).toContain("Self-host only");
    expect(src).toContain("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED");
  });
});

describe("verification-rule-modal mounts CustomChecksSection", () => {
  it("imports + renders CustomChecksSection", () => {
    expect(modalSrc).toContain("CustomChecksSection");
  });
});
