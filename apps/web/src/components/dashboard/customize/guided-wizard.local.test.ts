import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./guided-wizard.tsx", import.meta.url),
  "utf8",
);

describe("GuidedWizard — thin shell over AuthorWizard (PR-E5)", () => {
  it("mounts AuthorWizard directly — no KindPicker, no kind router", () => {
    expect(src).toContain("AuthorWizard");
    expect(src).not.toContain("KindPicker");
    expect(src).not.toContain("BlockAnswerWizard");
    expect(src).not.toContain("RestrictToolWizard");
    expect(src).not.toContain("FilterResultWizard");
    expect(src).not.toContain("RewireBuiltinWizard");
  });

  it("treats 'Pick different mode' as the cancel path (no intermediate sub-step)", () => {
    expect(src).toContain("onCancel={onPickDifferent}");
  });
});


const chrome = readFileSync(
  new URL("./guided/wizard-chrome.tsx", import.meta.url),
  "utf8",
);


describe("WizardChrome — shared chrome (still in use)", () => {
  it("renders aria progressbar with valuenow + valuemax", () => {
    expect(chrome).toContain('role="progressbar"');
    expect(chrome).toContain("aria-valuenow={step + 1}");
    expect(chrome).toContain("aria-valuemax={total}");
  });

  it("exposes the reusable RadioCard primitive", () => {
    expect(chrome).toContain("export function RadioCard");
  });
});
