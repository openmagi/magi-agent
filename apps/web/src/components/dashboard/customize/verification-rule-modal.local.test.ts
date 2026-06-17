import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const modalSrc = readFileSync(
  new URL("./verification-rule-modal.tsx", import.meta.url),
  "utf8",
);
const tabSrc = readFileSync(
  new URL("./customize-tab.tsx", import.meta.url),
  "utf8",
);
const apiSrc = readFileSync(
  new URL("../../../lib/customize-api.ts", import.meta.url),
  "utf8",
);

describe("verification modal — honest enforcement surface", () => {
  it("groups presets by WHEN-domain, not semantic category", () => {
    expect(modalSrc).toContain("DOMAIN_ORDER");
    expect(modalSrc).toContain("byDomain");
    expect(modalSrc).toContain("preset.domain");
    expect(modalSrc).not.toContain("byCategory");
  });

  it("separates preview presets into their own section", () => {
    expect(modalSrc).toContain("previewPresets");
    expect(modalSrc).toContain('p.enforcement === "preview"');
  });

  it("renders tier / opt-method / wiring badges + descriptions", () => {
    expect(modalSrc).toContain("Badges");
    expect(modalSrc).toContain("preset.tier");
    expect(modalSrc).toContain("preset.optMethod");
    expect(modalSrc).toContain("preset.description");
    expect(modalSrc).toContain("Always on");
    expect(modalSrc).toContain("Preview");
  });

  it("only enforcing presets get a live toggle", () => {
    expect(modalSrc).toContain('preset.enforcement === "enforcing"');
  });

  it("resolves effective state as override ?? defaultEnabled", () => {
    expect(modalSrc).toContain("presetOverrides[preset.id] ?? preset.defaultEnabled");
  });

  it("has a USER-RULES editor wired to a save handler", () => {
    expect(modalSrc).toContain("textarea");
    expect(modalSrc).toContain("onSaveRules");
    expect(modalSrc).toContain("rulesDraft");
  });
});

describe("customize tab — persists verification changes", () => {
  it("persists preset toggles via patchVerificationOverride (not session-only)", () => {
    expect(tabSrc).toContain("patchVerificationOverride");
    expect(tabSrc).toContain('"harness_presets"');
    expect(tabSrc).not.toContain("apply to this session");
  });

  it("seeds preset state from preset_overrides and saves rules via putRules", () => {
    expect(tabSrc).toContain("preset_overrides");
    expect(tabSrc).toContain("putRules");
  });
});

describe("customize-api — new contract surface", () => {
  it("exposes enforcement + defaultEnabled + supportedModes on presets", () => {
    expect(apiSrc).toContain("enforcement");
    expect(apiSrc).toContain("defaultEnabled");
    expect(apiSrc).toContain("supportedModes");
  });

  it("exposes domain/hookPoints/tier/optMethod/description on presets", () => {
    expect(apiSrc).toContain("domain");
    expect(apiSrc).toContain("hookPoints");
    expect(apiSrc).toContain("tier");
    expect(apiSrc).toContain("optMethod");
    expect(apiSrc).toContain("description");
  });

  it("exposes preset_overrides + user_rules in overrides", () => {
    expect(apiSrc).toContain("preset_overrides");
    expect(apiSrc).toContain("user_rules");
  });

  it("exports patchVerificationOverride + putRules", () => {
    expect(apiSrc).toContain("export async function patchVerificationOverride");
    expect(apiSrc).toContain("export async function putRules");
  });
});
