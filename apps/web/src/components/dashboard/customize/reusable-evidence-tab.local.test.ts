import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./reusable-evidence-tab.tsx", import.meta.url),
  "utf8",
);

const apiSrc = readFileSync(
  new URL("../../../lib/customize-api.ts", import.meta.url),
  "utf8",
);

describe("ReusableEvidenceTab — live input space browser (PR-F2)", () => {
  it("calls the live catalog API helper getEvidenceLiveCatalog", () => {
    expect(src).toContain("getEvidenceLiveCatalog");
  });

  it("uses the local agent fetch (useAgentFetch)", () => {
    expect(src).toContain("useAgentFetch");
  });

  it("renders a loading state while the catalog is being fetched", () => {
    expect(src).toMatch(/Loading|loading/);
    // loading is tracked as state, default true so first render is the loading branch
    expect(src).toContain("useState");
  });

  it("renders an empty state when the live catalog has zero entries", () => {
    expect(src).toContain("No evidence types");
  });

  it("renders a browsable table with type name, registered/populated field counts, refs and rules", () => {
    expect(src).toContain("registeredFields");
    expect(src).toContain("fieldsPopulatedRecently");
    expect(src).toContain("refsUsing");
    expect(src).toContain("rulesReferencing");
  });

  it("shows an 'Authorable now' badge for types with populated fields AND a rule-ready ref", () => {
    // Badge copy + the conditional that gates it.
    expect(src).toContain("Authorable now");
    // populated >= 1 AND refsUsing >= 1 are the two binary conditions per spec section 5 / PR-F2.
    expect(src).toMatch(/fieldsPopulatedRecently[^;]*length\s*>=?\s*1|fieldsPopulatedRecently[^;]*length\s*>\s*0/);
    expect(src).toMatch(/refsUsing[^;]*length\s*>=?\s*1|refsUsing[^;]*length\s*>\s*0/);
  });

  it("honors the inert-producer hide invariant: types with [] registered fields are NOT silently hidden but flagged 'producer extension needed'", () => {
    expect(src).toContain("producer extension needed");
    // The copy must mention that no field constraints are authorable for these types.
    expect(src).toMatch(/no field constraints/i);
  });

  it("provides a per-type drilldown that lists registered + populated field names", () => {
    // The drilldown surface must surface BOTH field-name lists, not just counts.
    // Implementation can use a details disclosure or expanded card; either is fine
    // as long as both name-lists appear in the render path.
    expect(src).toMatch(/Registered fields|registered fields/);
    expect(src).toMatch(/Populated|populated/);
    // toggle / open state for drilldown
    expect(src).toMatch(/expanded|open|drilldown|drillDown|expandedTypes/);
  });

  it("surfaces the sampling window from the endpoint payload", () => {
    expect(src).toContain("samplingWindow");
  });

  it("keeps the original EvidenceTypeEntry refs panel for back-compat (consumedBy / producedBy summary)", () => {
    // The old "auto-derived from policies" inventory remains valuable; F2 expands the tab
    // with the live catalog as the primary view, not by deleting policy-derived refs context.
    // entries prop must remain so the customize-hub call-site does not break.
    expect(src).toContain("entries");
  });
});

describe("customize-api.ts — getEvidenceLiveCatalog (PR-F2)", () => {
  it("exposes a getEvidenceLiveCatalog API helper", () => {
    expect(apiSrc).toContain("getEvidenceLiveCatalog");
  });

  it("targets the GET /v1/app/customize/evidence/live-catalog endpoint", () => {
    expect(apiSrc).toContain("/v1/app/customize/evidence/live-catalog");
  });

  it("exports an EvidenceLiveCatalog response type with the documented fields", () => {
    expect(apiSrc).toContain("EvidenceLiveCatalog");
    expect(apiSrc).toContain("evidenceTypes");
    expect(apiSrc).toContain("registeredFields");
    expect(apiSrc).toContain("fieldsPopulatedRecently");
    expect(apiSrc).toContain("samplePopulationCount");
    expect(apiSrc).toContain("refsUsing");
    expect(apiSrc).toContain("rulesReferencing");
    expect(apiSrc).toContain("samplingWindow");
    expect(apiSrc).toContain("asOf");
  });

  it("fails open (returns an empty catalog) on non-OK response, mirroring the fail-open spec contract", () => {
    // Spec §5 PR-F2: endpoint is "fail-open (returns empty list on ledger read error)".
    // The frontend helper should also degrade gracefully so the UI never crashes.
    expect(apiSrc).toMatch(/getEvidenceLiveCatalog[\s\S]{0,1200}evidenceTypes:\s*\[\s*\]/);
  });
});
