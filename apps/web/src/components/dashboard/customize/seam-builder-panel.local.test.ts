import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./seam-builder-panel.tsx", import.meta.url),
  "utf8",
);

describe("SeamBuilderPanel — PR-C3 rule builder surface", () => {
  it("calls compileSeamSpec / putSeamSpec / deleteSeamSpec from the api client", () => {
    expect(src).toContain("compileSeamSpec");
    expect(src).toContain("putSeamSpec");
    expect(src).toContain("deleteSeamSpec");
  });

  it("flags the feature is gated behind MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED", () => {
    expect(src).toContain("MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED");
  });

  it("requires the policy textarea to be non-empty before enabling Compile", () => {
    expect(src).toContain("disabled={compileBusy || !nlText.trim()}");
  });

  it("disables Activate when schema issues are present", () => {
    // canActivate must AND-in (schemaIssues?.length ?? 0) === 0 so the
    // deterministic gate is the last line of defence.
    expect(src).toContain("schemaIssues?.length ?? 0) === 0");
    expect(src).toContain("disabled={!canActivate || activateBusy}");
  });

  it("renders all three signals from the compile response: spec summary, review, schemaIssues", () => {
    // Phase-2 swapped the raw "Compiled SeamSpec" JSON header for a plain-
    // English summary list ("This spec will: ...") rendered via
    // describeSpecActions; the raw JSON is now in a details disclosure.
    expect(src).toContain("This spec will");
    expect(src).toContain("describeSpecActions");
    expect(src).toContain("View raw SeamSpec JSON");
    expect(src).toContain("Reviewer verdict");
    expect(src).toContain("Schema check (deterministic)");
  });

  it("surfaces clarifying questions when the compiler asks for them", () => {
    expect(src).toContain("Compiler needs clarification");
    expect(src).toContain("clarifyingQuestions");
  });

  it("lists active specs and exposes Delete per row", () => {
    expect(src).toContain("Active specs");
    expect(src).toContain("Delete");
    expect(src).toContain("deleteBusyId");
  });
});
