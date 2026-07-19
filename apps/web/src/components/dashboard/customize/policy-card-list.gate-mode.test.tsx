import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PolicyCardList } from "./policy-card-list";
import type { PolicyCardListProps } from "./policy-card-list";
import type { PolicyCatalogEntry } from "@/lib/customize-api";

// Source-string check for the change->API wiring: the node vitest environment
// renders via react-dom/server (no DOM events), so the "calls the API on change"
// contract is asserted structurally against the component source (the same idiom
// the sibling readFileSync suites use).
const src = readFileSync(
  new URL("./policy-card-list.tsx", import.meta.url),
  "utf8",
);

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function citationFloor(
  over: Partial<PolicyCatalogEntry> = {},
): PolicyCatalogEntry {
  // A floored first-party policy (userDisableable=false) carrying the 3-way
  // gate-mode descriptor: renders an always-on card with the strictness selector.
  return {
    id: "source_citation",
    displayName: "Source citation",
    intent: "cite the sources you used",
    ruleIds: [],
    origin: "builtin",
    userDisableable: false,
    reviewVerdict: "unreviewed",
    hasBinding: false,
    enabledState: "managed",
    source: "builtinPolicy",
    gateMode: { value: "repair", options: ["repair", "audit", "off"] },
    ...over,
  };
}

function plainFloor(): PolicyCatalogEntry {
  // A floored builtin policy WITHOUT a gate-mode descriptor: always-on, no
  // selector. Guards that the selector is source_citation-specific.
  return {
    id: "other_floor",
    displayName: "Other floor",
    intent: "an enforced floor with no strictness step-down",
    ruleIds: [],
    origin: "builtin",
    userDisableable: false,
    reviewVerdict: "unreviewed",
    hasBinding: false,
    enabledState: "managed",
    source: "builtinPolicy",
  };
}

function userPolicy(): PolicyCatalogEntry {
  return {
    id: "pol1",
    displayName: "My Policy",
    intent: "keep answers grounded",
    ruleIds: [],
    origin: "user",
    userDisableable: true,
    reviewVerdict: "unreviewed",
    hasBinding: false,
    enabledState: "managed",
    source: "policy",
  };
}

function executionIntegrity(): PolicyCatalogEntry {
  return citationFloor({
    id: "execution_integrity",
    displayName: "Execution Integrity",
    gateMode: { value: "audit", options: ["enforce", "audit", "off"] },
    components: [
      { id: "read-before-write", label: "Read before write", status: "live" },
      { id: "sandbox-execution", label: "Sandbox execution", status: "available" },
    ],
  });
}

const NOOP = () => {};

function baseProps(
  over: Partial<PolicyCardListProps>,
): PolicyCardListProps {
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
    onToggleBuiltinPolicy: NOOP,
    onToggleControlPlane: NOOP,
    pendingBuiltinPolicies: new Set(),
    pendingControlPlane: new Set(),
    citationGateMode: null,
    onCitationGateModeChange: NOOP,
    citationGateModePending: false,
    citationGateModeError: null,
    gateModes: {},
    onGateModeChange: NOOP,
    pendingGateModes: new Set(),
    gateModeError: null,
    ...over,
  };
}

function render(over: Partial<PolicyCardListProps>): string {
  return renderToStaticMarkup(<PolicyCardList {...baseProps(over)} />);
}

// ---------------------------------------------------------------------------
// Selector renders on the source_citation floor card
// ---------------------------------------------------------------------------

describe("PolicyCardList - source_citation gate-mode selector", () => {
  it("renders a 3-option strictness selector on the floored source_citation card", () => {
    const html = render({
      catalogPolicies: [citationFloor()],
    });
    // Always-on floor card (no toggle) is present.
    expect(html).toContain("always-on");
    // A native <select> with exactly the three gate modes.
    const options = html.match(/<option[^>]*value="(repair|audit|off)"/g) ?? [];
    expect(options.length).toBe(3);
    expect(html).toContain('value="repair"');
    expect(html).toContain('value="audit"');
    expect(html).toContain('value="off"');
    expect(html).toContain("Enforcement strictness");
  });

  it("reflects the current mode: an explicit override wins over the catalog value", () => {
    const html = render({
      catalogPolicies: [citationFloor({ gateMode: { value: "repair", options: ["repair", "audit", "off"] } })],
      citationGateMode: "off",
    });
    // The native <select> marks the selected option server-side.
    expect(html).toMatch(/<option[^>]*value="off"[^>]*selected/);
    expect(html).not.toMatch(/<option[^>]*value="repair"[^>]*selected/);
  });

  it("falls back to the catalog gateMode.value when there is no explicit override", () => {
    const html = render({
      catalogPolicies: [citationFloor({ gateMode: { value: "audit", options: ["repair", "audit", "off"] } })],
      citationGateMode: null,
    });
    expect(html).toMatch(/<option[^>]*value="audit"[^>]*selected/);
  });

  it("is absent on a floored policy that carries no gate-mode descriptor", () => {
    const html = render({ catalogPolicies: [plainFloor()] });
    // Still an always-on floor card, but no strictness selector.
    expect(html).toContain("always-on");
    expect(html).not.toContain("Enforcement strictness");
    expect(html).not.toMatch(/<option[^>]*value="repair"/);
  });

  it("is absent on a non-floor (user) policy card", () => {
    const html = render({ catalogPolicies: [userPolicy()] });
    expect(html).not.toContain("Enforcement strictness");
  });

  it("is absent when no gate-mode change handler is supplied", () => {
    const html = render({
      catalogPolicies: [citationFloor()],
      onCitationGateModeChange: undefined,
    });
    expect(html).not.toContain("Enforcement strictness");
  });

  it("wires the selector onChange to the gate-mode change handler (API call path)", () => {
    // The onChange forwards the selected value to onCitationGateModeChange, which
    // the hub binds to patchCitationGateMode (PATCH /citation-gate-mode).
    expect(src).toContain("onChange={(e) => onChange(e.target.value)}");
    expect(src).toContain("onCitationGateModeChange");
  });


  it("renders and resolves a generalized execution-integrity mode", () => {
    const html = render({
      catalogPolicies: [executionIntegrity()],
      gateModes: { execution_integrity: "enforce" },
    });
    expect(html).toContain("Execution Integrity");
    expect(html).toMatch(/<option[^>]*value="enforce"[^>]*selected/);
    expect(html).toContain("Read before write");
    expect(html).toContain("live");
    expect(html).toContain("Sandbox execution");
    expect(html).toContain("available");
  });

  it("routes generalized selectors with their policy id", () => {
    expect(src).toContain("onGateModeChange?.(vm.policyId, mode)");
  });
});
