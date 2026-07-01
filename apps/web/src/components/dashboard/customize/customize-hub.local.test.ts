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

  it("labels the enforcement section 'Rules' and the pack section 'Packs' (region terminology)", () => {
    expect(src).toContain('label: "Rules"');
    expect(src).toContain('label: "Packs"');
    // the section ids stay stable so deep-links / routes are unaffected
    expect(src).toContain('id: "rules"');
    expect(src).toContain('id: "recipes"');
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


// ---------------------------------------------------------------------------
// PR-F-UX5 — evidence vs verifier/condition split + counter formulas
// ---------------------------------------------------------------------------


describe("CustomizeHub — PR-F-UX5 counters + built-in judgment merge", () => {
  it("imports extractBuiltinJudgmentRefs for the Conditions tab merge", () => {
    expect(src).toContain("extractBuiltinJudgmentRefs");
  });

  it("derives builtinJudgments from the catalog (sourced from judgmentMenu)", () => {
    // The source of truth for built-in verifier primitives is
    // ``catalog.verification.judgmentMenu``; the hub must memoise the
    // extraction on the catalog (not the policies) so a customize PATCH
    // does not re-run it unnecessarily.
    expect(src).toContain("extractBuiltinJudgmentRefs(data.catalog)");
    expect(src).toContain("builtinJudgments");
  });

  it("Evidence sub-tab counter sums catalog.evidenceMenu + user-consumed evidenceTypes", () => {
    // F-UX5 spec: the counter must reflect the actual count of evidence
    // types (not just the F2.5 user-only count). The body of the tab
    // renders both halves so the counter mirrors the visible row count.
    expect(src).toContain(
      "data.catalog.verification.evidenceMenu.length",
    );
    expect(src).toContain("+ evidenceTypes.length");
  });

  it("Conditions sub-tab counter sums builtinJudgments + user-authored conditions", () => {
    // F-UX5 spec: counter = judgmentMenu.length +
    // extractNamedConditions(policies).length. The Conditions tab body
    // merges both halves under an origin badge; the counter equals the row
    // count there.
    expect(src).toContain("builtinJudgments.length + conditions.length");
  });

  it("ReusableConditionsTab receives builtinEntries={builtinJudgments}", () => {
    // The Conditions tab body needs the built-in list as a separate prop
    // (origin-badged differently from user-authored conditions); without
    // this wire the merge happens at hub-level but the tab never sees the
    // built-in half.
    expect(src).toMatch(
      /<ReusableConditionsTab[\s\S]*?builtinEntries=\{builtinJudgments\}/,
    );
  });
});
