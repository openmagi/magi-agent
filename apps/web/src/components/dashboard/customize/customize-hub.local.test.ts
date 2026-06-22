import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./customize-hub.tsx", import.meta.url),
  "utf8",
);

describe("CustomizeHub — unified Rules redesign (Phase 1)", () => {
  it("declares the five top-level sub-nav sections after the redesign", () => {
    expect(src).toContain('"rules"');
    expect(src).toContain('"guidance"');
    expect(src).toContain('"tools"');
    expect(src).toContain('"recipes"');
    expect(src).toContain('"hooks"');
  });

  it("drops the legacy verification + advanced top-level sections", () => {
    // The verification umbrella + the standalone Advanced sub-nav are gone;
    // their bodies are now reachable via the Rules section's Add-rule modal.
    expect(src).not.toMatch(/['"]verification['"]/);
    expect(src).not.toMatch(/['"]advanced['"]/);
  });

  it("renders the Rules section via RulesSectionMount + RulesTable + inline AddRulePicker", () => {
    expect(src).toContain("RulesSectionMount");
    expect(src).toContain("RulesTable");
    expect(src).toContain("AddRulePicker");
  });

  it("uses a phase state machine (idle / picking / authoring) so the picker and form share scroll position with the Add button", () => {
    expect(src).toContain('phase: "idle"');
    expect(src).toContain('phase: "picking"');
    expect(src).toContain('phase: "authoring"');
  });

  it("keeps SeamBuilderPanel / CustomRulesSection / CustomChecksSection reachable via the Add-rule routing", () => {
    expect(src).toContain("SeamBuilderPanel");
    expect(src).toContain("CustomRulesSection");
    expect(src).toContain("CustomChecksSection");
  });

  it("primaries the NL rule compose (PR-D2) and demotes the 4-card picker to a fold-out", () => {
    // NL compose mounts at the top of the picking phase; the legacy 4-card
    // picker still mounts but inside a <details> labelled "Or build manually"
    // so the operator can fall back when the unified compiler is unavailable.
    expect(src).toContain("NlRuleCompose");
    expect(src).toContain("Or build manually");
  });

  it("hides the rules table while the Add-rule flow is active so the picker/form has the page to itself", () => {
    // RulesTable renders inside an addState.phase === "idle" guard; while
    // picking/authoring the 38-row catalog collapses to a one-line note so
    // the user is not buried under the existing rules they are editing
    // around.
    // Use a regex so indentation drift in the source file does not falsely
    // fail this assertion — the guard order (idle → RulesTable, else note)
    // is what matters.
    expect(src).toMatch(/addState\.phase === "idle"\s*\?\s*\(\s*<RulesTable/);
    expect(src).toContain("Catalog hidden while adding a rule");
    expect(src).toContain("hiddenRuleCount");
  });

  it("pre-fills CustomRulesSection.initialKind from the AddRuleModal choice (Phase 2)", () => {
    // restrict-tool routes to tool_perm; block-answer routes to deterministic_ref.
    expect(src).toContain("autoOpen");
    expect(src).toContain('"restrict-tool" ? "tool_perm" : "deterministic_ref"');
  });

  it("mounts Guidance as its own top-level section (not nested inside Rules)", () => {
    expect(src).toContain('section === "guidance"');
    expect(src).toContain("GuidancePanel");
  });

  it("forwards the active section through onSectionChange so the page can sync the URL", () => {
    expect(src).toContain("onSectionChange");
  });

  it("ships a HookBus panel still honest about file-only authoring", () => {
    expect(src).toContain("HooksPanel");
    expect(src).toContain("settings.json");
    expect(src).toContain("self-host");
  });
});
