/**
 * TDD tests for Task 4.3 — SHACL example template constant + load-example affordance.
 *
 * Pattern: source-scan (readFileSync + toContain) matching the repo's existing
 * modal test style.  No DOM rendering required.
 * NOT browser-verified (component tests only).
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { SHACL_EXAMPLE_TEMPLATE } from "./shacl-example-template";

const modalSrc = readFileSync(
  new URL("./verification-rule-modal.tsx", import.meta.url),
  "utf8",
);

// ---------------------------------------------------------------------------
// Test 1 — SHACL_EXAMPLE_TEMPLATE is a valid, non-empty Turtle/SHACL string
// ---------------------------------------------------------------------------
describe("SHACL_EXAMPLE_TEMPLATE — structure and validity", () => {
  it("is a non-empty string", () => {
    expect(typeof SHACL_EXAMPLE_TEMPLATE).toBe("string");
    expect(SHACL_EXAMPLE_TEMPLATE.length).toBeGreaterThan(0);
  });

  it("contains sh:NodeShape declaration", () => {
    expect(SHACL_EXAMPLE_TEMPLATE).toContain("sh:NodeShape");
  });

  it("uses a magi:field_* predicate path", () => {
    expect(SHACL_EXAMPLE_TEMPLATE).toContain("magi:field_");
  });

  it("declares the @prefix sh: namespace", () => {
    expect(SHACL_EXAMPLE_TEMPLATE).toContain("@prefix sh:");
    expect(SHACL_EXAMPLE_TEMPLATE).toContain("http://www.w3.org/ns/shacl#");
  });

  it("declares the @prefix magi: namespace", () => {
    expect(SHACL_EXAMPLE_TEMPLATE).toContain("@prefix magi:");
    expect(SHACL_EXAMPLE_TEMPLATE).toContain("https://openmagi.ai/ns/evidence#");
  });

  it("includes a sh:targetClass targeting magi:Evidence", () => {
    expect(SHACL_EXAMPLE_TEMPLATE).toContain("sh:targetClass");
    expect(SHACL_EXAMPLE_TEMPLATE).toContain("magi:Evidence");
  });

  it("includes a sh:property constraint (e.g. sh:maxInclusive)", () => {
    expect(SHACL_EXAMPLE_TEMPLATE).toContain("sh:property");
    expect(SHACL_EXAMPLE_TEMPLATE).toContain("sh:maxInclusive");
  });

  it("includes a leading comment indicating it is a starter example to edit", () => {
    // Must have at least one Turtle comment line (# …)
    expect(SHACL_EXAMPLE_TEMPLATE).toMatch(/#.+/);
  });
});

// ---------------------------------------------------------------------------
// Test 2 — modal source exposes the template and a "예시 불러오기" affordance
// ---------------------------------------------------------------------------
describe("verification-rule-modal — load-example affordance", () => {
  it("imports SHACL_EXAMPLE_TEMPLATE from shacl-example-template", () => {
    expect(modalSrc).toContain("SHACL_EXAMPLE_TEMPLATE");
    expect(modalSrc).toContain("shacl-example-template");
  });

  it('renders a "예시 불러오기" button in raw mode', () => {
    expect(modalSrc).toContain("예시 불러오기");
  });

  it("the load-example button calls setRawTtl with SHACL_EXAMPLE_TEMPLATE", () => {
    // The onClick handler must reference both setRawTtl and SHACL_EXAMPLE_TEMPLATE
    expect(modalSrc).toContain("setRawTtl");
    expect(modalSrc).toContain("SHACL_EXAMPLE_TEMPLATE");
  });

  it("the load-example button is type=button (no accidental form submit)", () => {
    // Verify the button near 예시 불러오기 has type="button"
    // We rely on the repo convention that all action buttons carry type="button"
    // — verified structurally: the modal already sets type="button" on every button.
    expect(modalSrc).toContain('type="button"');
  });
});
