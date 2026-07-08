import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { PolicyCardList } from "./policy-card-list";
import type { PolicyCardListProps } from "./policy-card-list";
import type { PolicyCatalogEntry } from "@/lib/customize-api";
import type { RuleRow } from "@/lib/policy-model";

const src = readFileSync(new URL("./policy-card-list.tsx", import.meta.url), "utf8");

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function customRuleRow(id: string, enabled: boolean): RuleRow {
  return {
    id: `custom_rule:${id}`,
    name: id,
    description: `custom rule ${id}`,
    origin: "user",
    source: "custom_rule",
    state: enabled ? "enabled" : "disabled",
    when: { scope: "always", firesAt: "pre_final" },
    condition: { kind: "evidence_ref", summary: "requires evidence" },
    action: "block",
    togglable: true,
    editable: true,
    deletable: true,
    rawSource: {
      kind: "custom_rule",
      rule: {
        id,
        scope: "always",
        enabled,
        what: { kind: "evidence_ref", payload: {} },
        firesAt: "pre_final",
        action: "block",
      },
    },
  };
}

function presetRow(id: string, domain: string): RuleRow {
  return {
    id: `preset_seam:${id}`,
    name: `Preset ${id}`,
    description: `preset ${id}`,
    origin: "builtin",
    source: "preset_seam",
    state: "enabled",
    when: { scope: domain, firesAt: "pre_final" },
    condition: { kind: "none", summary: "" },
    action: "block",
    togglable: true,
    editable: true,
    deletable: false,
    rawSource: {
      kind: "preset_seam",
      preset: {
        id,
        title: `Preset ${id}`,
        category: "quality",
        domain,
        hookPoints: ["pre_final"],
        description: "",
        tier: "deterministic",
        optMethod: "opt-out",
        defaultEnabled: true,
        enforcement: "enforcing",
        supportedModes: [],
      },
    },
  };
}

function nativePolicy(over: Partial<PolicyCatalogEntry>): PolicyCatalogEntry {
  return {
    id: "pol1",
    displayName: "My Policy",
    intent: "keep answers grounded",
    ruleIds: ["r1"],
    origin: "user",
    userDisableable: true,
    reviewVerdict: "unreviewed",
    hasBinding: false,
    enabledState: "on",
    ...over,
  };
}

const NOOP = () => {};

function baseProps(over: Partial<PolicyCardListProps>): PolicyCardListProps {
  return {
    catalogPolicies: [],
    ruleRows: [],
    pendingPresets: new Set(),
    busy: false,
    scopedInModes: {},
    onTogglePolicy: NOOP,
    onDeletePolicy: NOOP,
    onTogglePreset: NOOP,
    onToggleCustomRule: NOOP,
    onDeleteCustomRule: NOOP,
    onToggleDashboardCheck: NOOP,
    onDeleteDashboardCheck: NOOP,
    onDeleteSeamSpec: NOOP,
    ...over,
  };
}

function render(over: Partial<PolicyCardListProps>): string {
  return renderToStaticMarkup(<PolicyCardList {...baseProps(over)} />);
}

// ---------------------------------------------------------------------------
// Precedence: policy-referenced rules never render as their own top-level card
// ---------------------------------------------------------------------------

describe("PolicyCardList — precedence (no shattering)", () => {
  it("a rule referenced by a catalog policy renders ONLY inside the drill-down, not as its own card", () => {
    const html = render({
      catalogPolicies: [nativePolicy({ id: "pol1", ruleIds: ["r1"] })],
      ruleRows: [customRuleRow("r1", true)],
    });
    // Exactly one top-level card (the native policy), plus its drill-down.
    // (Match the card testid specifically — not the "policy-card-list" root.)
    const cards = html.match(/data-testid="policy-card-(native|adapter):/g) ?? [];
    expect(cards.length).toBe(1);
    expect(html).toContain('data-testid="policy-card-native:pol1"');
    // The member rule appears in the drill-down, not as an adapter card.
    expect(html).not.toContain("policy-card-adapter:custom_rule:r1");
    expect(html).toContain('data-testid="policy-drilldown-native:pol1"');
  });

  it("an unreferenced user rule renders as its own 1-rule adapter card under Your policies", () => {
    const html = render({ ruleRows: [customRuleRow("loose", true)] });
    expect(html).toContain("policy-card-adapter:custom_rule:loose");
    expect(html).toContain("Your policies");
  });
});

// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------

describe("PolicyCardList — sections", () => {
  it("renders Your policies / First-party / Built-in sections", () => {
    const html = render({
      catalogPolicies: [
        nativePolicy({ id: "u1", origin: "user", ruleIds: ["a"] }),
        nativePolicy({ id: "b1", origin: "builtin", ruleIds: [], enabledState: "managed" }),
      ],
      ruleRows: [customRuleRow("a", true), presetRow("p1", "coding")],
    });
    expect(html).toContain("Your policies");
    expect(html).toContain("First-party");
    expect(html).toContain("Built-in");
  });

  it("groups built-in presets into a COLLAPSED domain section (Coding, count 1)", () => {
    const html = render({ ruleRows: [presetRow("p1", "coding")] });
    // The Built-in domain sections default to collapsed (design D1), so the
    // static markup shows the section header + count but not the inner card.
    expect(html).toContain("Built-in");
    expect(html).toContain("Coding");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("(1)");
  });
});

// ---------------------------------------------------------------------------
// Toggle semantics: managed / mixed / floor / native
// ---------------------------------------------------------------------------

describe("PolicyCardList — toggle semantics", () => {
  it("managed => renders a static pill, NOT a Switch", () => {
    const html = render({
      catalogPolicies: [nativePolicy({ id: "m1", enabledState: "managed", ruleIds: [] })],
    });
    expect(html).toContain("managed");
    // A native managed card carries no switch role.
    const cardOnly = html.slice(html.indexOf("policy-card-native:m1"));
    expect(cardOnly).not.toContain('role="switch"');
  });

  it("mixed => renders the 'N of M rules on' note", () => {
    const html = render({
      catalogPolicies: [nativePolicy({ id: "x1", enabledState: "mixed", ruleIds: ["a", "b"] })],
      ruleRows: [customRuleRow("a", true), customRuleRow("b", false)],
    });
    expect(html).toContain("1 of 2 rules on");
  });

  it("floor (userDisableable=false) => always-on pill, no toggle", () => {
    const html = render({
      catalogPolicies: [
        nativePolicy({ id: "floor1", origin: "builtin", userDisableable: false, ruleIds: [] }),
      ],
    });
    const card = html.slice(html.indexOf("policy-card-native:floor1"));
    expect(card).toContain("always-on");
    expect(card).not.toContain('role="switch"');
  });

  it("a togglable native policy renders a Switch", () => {
    const html = render({
      catalogPolicies: [nativePolicy({ id: "on1", enabledState: "on", ruleIds: ["a"] })],
      ruleRows: [customRuleRow("a", true)],
    });
    expect(html).toContain('role="switch"');
  });
});

// ---------------------------------------------------------------------------
// Forced-on honesty label
// ---------------------------------------------------------------------------

describe("PolicyCardList — forced-on-in-mode honesty", () => {
  it("off globally but scoped in a mode => 'forced on in {mode} mode'", () => {
    const html = render({
      catalogPolicies: [nativePolicy({ id: "off1", enabledState: "off", ruleIds: ["a"] })],
      ruleRows: [customRuleRow("a", false)],
      scopedInModes: { "policy:off1": ["Research"] },
    });
    expect(html).toContain("Off globally");
    expect(html).toContain("forced on in Research mode");
  });

  it("maps a legacy member-rule ref (not just policy:) to the owning card's scope badge", () => {
    const html = render({
      catalogPolicies: [nativePolicy({ id: "p2", enabledState: "on", ruleIds: ["a"] })],
      ruleRows: [customRuleRow("a", true)],
      scopedInModes: { "custom_rule:a": ["Coding"] },
    });
    expect(html).toContain("scoped in Coding");
  });
});

// ---------------------------------------------------------------------------
// Drill-down + binding
// ---------------------------------------------------------------------------

describe("PolicyCardList — drill-down", () => {
  it("renders member rows inside the drill-down", () => {
    const html = render({
      catalogPolicies: [nativePolicy({ id: "d1", ruleIds: ["a", "b"] })],
      ruleRows: [customRuleRow("a", true), customRuleRow("b", true)],
    });
    const dd = html.slice(html.indexOf("policy-drilldown-native:d1"));
    expect(dd).toContain(">a<");
    expect(dd).toContain(">b<");
  });

  it("renders producer -> gate chips when the policy has a binding", () => {
    const html = render({
      catalogPolicies: [nativePolicy({ id: "bnd", hasBinding: true, ruleIds: ["a", "b"] })],
      ruleRows: [customRuleRow("a", true), customRuleRow("b", true)],
    });
    expect(html).toContain("producer");
    expect(html).toContain("gate");
  });
});

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

describe("PolicyCardList — empty", () => {
  it("renders an empty state when there are no policies", () => {
    const html = render({});
    expect(html).toContain("No policies yet");
  });
});

// ---------------------------------------------------------------------------
// Source pins (structure / imports)
// ---------------------------------------------------------------------------

describe("PolicyCardList — structure", () => {
  it("uses the shared _ds/Switch (follows #1407)", () => {
    expect(src).toContain('import { Switch } from "@/components/ui/_ds"');
  });

  it("derives the strongest action (BLOCK > ASK > AUDIT > NUDGE)", () => {
    expect(src).toContain("strongestAction");
    expect(src).toContain("block: 4");
    expect(src).toContain("nudge: 1");
  });

  it("routes native policy toggles/deletes separately from adapter rows", () => {
    expect(src).toContain("onTogglePolicy");
    expect(src).toContain("onDeletePolicy");
    expect(src).toContain("onTogglePreset");
  });
});

describe("delete confirmation (review fold)", () => {
  it("asks for confirmation with an honest member-cascade note", () => {
    expect(src).toContain("window.confirm");
    expect(src).toContain("member rule");
  });
});
