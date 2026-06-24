import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./customize-hub.tsx", import.meta.url),
  "utf8",
);

describe("CustomizeHub — Policy unification (PR-E1)", () => {
  it("keeps the rules / guidance / tools / recipes / hooks sub-nav (deep-link compat)", () => {
    expect(src).toContain('"rules"');
    expect(src).toContain('"guidance"');
    expect(src).toContain('"tools"');
    expect(src).toContain('"recipes"');
    expect(src).toContain('"hooks"');
  });

  it("renames the Rules section label to 'Policies' (unified terminology)", () => {
    expect(src).toContain('label: "Policies"');
  });

  it("mounts the unified PoliciesTable, Reusable Evidence + Conditions sub-tabs", () => {
    expect(src).toContain("PoliciesTable");
    expect(src).toContain("ReusableEvidenceTab");
    expect(src).toContain("ReusableConditionsTab");
  });

  it("uses the policy-model unifier + extractors", () => {
    expect(src).toContain("unifyPolicies");
    expect(src).toContain("extractEvidenceTypes");
    expect(src).toContain("extractNamedConditions");
  });

  it("Add policy entry shows the 3-mode AddPolicyModePicker (NL / Guided / Raw)", () => {
    expect(src).toContain("AddPolicyModePicker");
    expect(src).toContain('phase: "picking_mode"');
    expect(src).toContain('phase: "nl"');
    expect(src).toContain('phase: "guided"');
    expect(src).toContain('phase: "raw_picking"');
    expect(src).toContain('phase: "raw_authoring"');
  });

  it("routes the Guided choice to the GuidedWizard (PR-E2)", () => {
    expect(src).toContain("GuidedWizard");
    expect(src).toContain('mode === "guided"');
  });

  it("loads DashboardChecks at hub level so they appear in the unified table", () => {
    expect(src).toContain("getDashboardChecks");
    expect(src).toContain("setDashboardChecks");
  });

  it("provides a SeamSpec delete handler so unified table can remove built-in overrides", () => {
    expect(src).toContain("handleDeleteSeamSpec");
    expect(src).toContain("deleteSeamSpecApi");
  });

  it("renames the Add button to 'Add policy' (matches the unified concept)", () => {
    expect(src).toContain("Add policy");
  });

  it("keeps the legacy CustomRulesSection / CustomChecksSection / SeamBuilderPanel reachable under raw_authoring", () => {
    expect(src).toContain("CustomRulesSection");
    expect(src).toContain("CustomChecksSection");
    expect(src).toContain("SeamBuilderPanel");
  });

  it("hides the unified list while authoring so the page is focused", () => {
    expect(src).toContain("List hidden while adding a policy");
  });

  it("registers the PR-F7 Budgets sub-tab (id, label, icon, panel mount)", () => {
    // Section vocabulary
    expect(src).toContain('"budgets"');
    expect(src).toContain('label: "Budgets"');
    // Hub imports + handlers
    expect(src).toContain("BudgetsTab");
    expect(src).toContain("getBudgets");
    expect(src).toContain("putBudgets");
    expect(src).toContain("handleSaveBudgets");
    // Render branch
    expect(src).toContain('section === "budgets"');
    // The hub lazy-loads budgets only when the operator opens the tab
    expect(src).toContain("loadBudgets");
  });
});
