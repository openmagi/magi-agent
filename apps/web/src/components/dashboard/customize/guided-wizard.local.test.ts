import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./guided-wizard.tsx", import.meta.url),
  "utf8",
);

describe("GuidedWizard — kind router (PR-E3)", () => {
  it("offers four kind choices for guided authoring", () => {
    expect(src).toContain('"block-answer"');
    expect(src).toContain('"restrict-tool"');
    expect(src).toContain('"filter-result"');
    expect(src).toContain('"rewire-builtin"');
  });

  it("routes each kind to its dedicated sub-wizard component", () => {
    expect(src).toContain("BlockAnswerWizard");
    expect(src).toContain("RestrictToolWizard");
    expect(src).toContain("FilterResultWizard");
    expect(src).toContain("RewireBuiltinWizard");
  });

  it("KindPicker forwards a ← Pick different to the parent (mode picker)", () => {
    expect(src).toContain("Pick different mode");
  });

  it("Sub-wizards' ← Pick different goes back to the kind picker (one level up)", () => {
    expect(src).toContain("backToKindPicker");
  });
});


const chrome = readFileSync(
  new URL("./guided/wizard-chrome.tsx", import.meta.url),
  "utf8",
);


describe("WizardChrome — shared chrome (PR-E3)", () => {
  it("renders aria progressbar with valuenow + valuemax", () => {
    expect(chrome).toContain('role="progressbar"');
    expect(chrome).toContain("aria-valuenow={step + 1}");
    expect(chrome).toContain("aria-valuemax={total}");
  });

  it("first step shows Cancel; subsequent steps show ← Back", () => {
    expect(chrome).toContain("isFirst ? (");
    expect(chrome).toContain("isLast ? (");
  });

  it("exposes a reusable RadioCard primitive", () => {
    expect(chrome).toContain("export function RadioCard");
  });
});


const blockAnswer = readFileSync(
  new URL("./guided/block-answer-wizard.tsx", import.meta.url),
  "utf8",
);


describe("BlockAnswerWizard (PR-E3 extraction)", () => {
  it("activates via putCustomRule with deterministic_ref kind", () => {
    expect(blockAnswer).toContain("putCustomRule");
    expect(blockAnswer).toContain('kind: "deterministic_ref"');
    expect(blockAnswer).toContain('payload: { ref: draft.evidenceRef }');
  });

  it("ships 5 steps", () => {
    expect(blockAnswer).toContain("const TOTAL = 5");
  });
});


const restrictTool = readFileSync(
  new URL("./guided/restrict-tool-wizard.tsx", import.meta.url),
  "utf8",
);


describe("RestrictToolWizard (PR-E3 new)", () => {
  it("activates via putCustomRule with tool_perm kind + before_tool_use firesAt", () => {
    expect(restrictTool).toContain("putCustomRule");
    expect(restrictTool).toContain('kind: "tool_perm"');
    expect(restrictTool).toContain('firesAt: "before_tool_use"');
  });

  it("maps the deny/ask decision to the block/ask_approval action", () => {
    expect(restrictTool).toContain('draft.decision === "ask" ? "ask_approval" : "block"');
  });

  it("supports three match types: tool / domain / domainAllowlist", () => {
    expect(restrictTool).toContain('"tool"');
    expect(restrictTool).toContain('"domain"');
    expect(restrictTool).toContain('"domainAllowlist"');
  });

  it("ships 5 steps", () => {
    expect(restrictTool).toContain("const TOTAL = 5");
  });
});


const filterResult = readFileSync(
  new URL("./guided/filter-result-wizard.tsx", import.meta.url),
  "utf8",
);


describe("FilterResultWizard (PR-E3 new)", () => {
  it("activates via putDashboardCheck (after-tool, self-host only)", () => {
    expect(filterResult).toContain("putDashboardCheck");
  });

  it("loads the tool catalog menu so users can pick a chip", () => {
    expect(filterResult).toContain("getDashboardPacksMenu");
  });

  it("exposes the isRegex checkbox", () => {
    expect(filterResult).toContain("Treat pattern as a regular expression");
  });

  it("ships 6 steps", () => {
    expect(filterResult).toContain("const TOTAL = 6");
  });
});


const rewireBuiltin = readFileSync(
  new URL("./guided/rewire-builtin-wizard.tsx", import.meta.url),
  "utf8",
);


describe("RewireBuiltinWizard (PR-E3 new)", () => {
  it("activates via putSeamSpec with op=modify_seam", () => {
    expect(rewireBuiltin).toContain("putSeamSpec");
    expect(rewireBuiltin).toContain('op: "modify_seam"');
  });

  it("only lists togglable built-in presets (enforcement = enforcing)", () => {
    expect(rewireBuiltin).toContain('p.enforcement === "enforcing"');
  });

  it("auto-derives the doc id from preset id", () => {
    expect(rewireBuiltin).toContain("docId: `rewire-${preset.id}`");
  });

  it("ships 4 steps", () => {
    expect(rewireBuiltin).toContain("const TOTAL = 4");
  });
});
