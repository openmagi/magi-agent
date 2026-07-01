import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(new URL("./modes-panel.tsx", import.meta.url), "utf8");

describe("ModesPanel: PR-U3.3 intent-first, plain-language mode composer", () => {
  it("opens the new-mode editor with a 'describe the stance' intent lead-in", () => {
    // The lead-in only renders when creating (editor.modeId is null) so the
    // edit path stays compact.
    expect(src).toContain("Describe the stance you want the agent to take");
    expect(src).toMatch(/editor\.modeId \? null : \(/);
  });

  it("uses plain-language field labels instead of the internal field names", () => {
    expect(src).toContain("Name this stance");
    expect(src).toContain("How the agent should behave");
    expect(src).toContain("How strict are approvals?");
    expect(src).toContain("Turn tools off");
    expect(src).toContain("Turn extra tools on");
    expect(src).toContain("Rules active in this mode");
  });

  it("retires the raw System-prompt / Exclude / Scoped-policies visible labels", () => {
    // These were the pre-U3.3 labels that leaked the wire-shape field names.
    // (The element ids like `mode-exclude`/`mode-policies` stay; only the
    // visible label text changed.)
    expect(src).not.toContain("System prompt (soft)");
    expect(src).not.toContain("Exclude tools");
    expect(src).not.toContain("Scoped policies");
  });

  it("points the empty-rules hint at the renamed 'Rules' tab (PR-U1), not 'Policies'", () => {
    expect(src).toContain("Create one under <strong>Rules</strong>");
  });
});

describe("ModesPanel: PR-U3.4 conversational NL → mode composer", () => {
  it("offers a 'Describe a mode' entry alongside 'New mode'", () => {
    expect(src).toContain("Describe a mode");
    expect(src).toContain("New mode");
    expect(src).toContain("setComposing(true)");
  });

  it("compiles via compileMode and drops the draft into the editor for review", () => {
    expect(src).toContain("compileMode");
    // The NL surface never activates a mode: it hands the draft to the editor.
    expect(src).toContain("editorFromDraft(draft)");
    expect(src).toMatch(/onDrafted=\{\(draft, warnings\) =>/);
  });

  it("grounds the compile on the operator's scopable rule ids", () => {
    expect(src).toContain("scopablePolicyIds={policyOptions.map((o) => o.id)}");
  });

  it("surfaces compiler warnings (dropped tools/ids, capped permission) in the editor", () => {
    expect(src).toContain("We adjusted the draft:");
    expect(src).toContain("warnings.length > 0");
  });

  it("fails soft when the compiler is disabled (points at the manual form)", () => {
    expect(src).toContain('res.error === "nl-mode compiler disabled"');
    expect(src).toContain("author by hand");
  });
});

describe("ModesPanel: PR-P1 tool picker replaces freeform typing", () => {
  it("renders a ModeToolPicker sourced from the live tool catalog", () => {
    expect(src).toContain("function ModeToolPicker");
    expect(src).toContain("<ModeToolPicker");
    expect(src).toContain("customizeData?.catalog.tools");
    expect(src).toContain("tools={toolItems}");
  });

  it("splits into default-on (exclude) and safe default-off (include), never dangerous", () => {
    expect(src).toContain("t.enabled && match(t)");
    // Only safe, currently-off tools are offerable to include.
    expect(src).toContain("!t.enabled && !t.dangerous && match(t)");
  });

  it("toggles selection through the newline exclude/include strings", () => {
    expect(src).toContain("toggleListItem(editor.exclude, name)");
    expect(src).toContain("toggleListItem(editor.include, name)");
  });

  it("keeps the raw name textareas as an Advanced escape hatch", () => {
    expect(src).toContain("Advanced: tools by name");
    expect(src).toContain('id="mode-exclude"');
    expect(src).toContain('id="mode-include"');
  });
});
