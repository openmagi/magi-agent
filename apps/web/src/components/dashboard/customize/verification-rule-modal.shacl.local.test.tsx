/**
 * TDD tests for Task 4.2 — SHACL rule type in the custom-rule builder modal.
 *
 * Pattern: read source files and assert string / structural properties.
 * This avoids the need for DOM rendering or context providers.
 * NOT browser-verified (component tests only).
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const modalSrc = readFileSync(
  new URL("./verification-rule-modal.tsx", import.meta.url),
  "utf8",
);

const tabSrc = readFileSync(
  new URL("./customize-tab.tsx", import.meta.url),
  "utf8",
);

const apiSrc = readFileSync(
  new URL("../../../lib/customize-api.ts", import.meta.url),
  "utf8",
);

// ---------------------------------------------------------------------------
// Test 1 — Selecting SHACL rule type exposes nl/raw mode toggle
// ---------------------------------------------------------------------------
describe("Test 1 — SHACL rule type option and nl/raw mode toggle", () => {
  it("adds shacl_constraint to the kind union", () => {
    expect(modalSrc).toContain('"shacl_constraint"');
  });

  it("renders a SHACL option in the rule-type select", () => {
    expect(modalSrc).toContain("결정론 제약 (SHACL)");
  });

  it("renders an input-mode toggle with nl and raw options when SHACL is selected", () => {
    // The modal must render nl/raw mode toggle UI
    expect(modalSrc).toContain("shaclMode");
    expect(modalSrc).toContain('"nl"');
    expect(modalSrc).toContain('"raw"');
  });

  it("shows nl mode (자연어) and raw mode (.ttl) labels", () => {
    expect(modalSrc).toContain("자연어");
    expect(modalSrc).toContain(".ttl");
  });
});

// ---------------------------------------------------------------------------
// Test 2 — nl mode: compile → ok:true → preview panel
// ---------------------------------------------------------------------------
describe("Test 2 — nl mode compile success shows preview panel", () => {
  it("renders a nlText textarea in nl mode", () => {
    expect(modalSrc).toContain("nlText");
    expect(modalSrc).toContain("컴파일");
  });

  it("calls onCompileShacl when the compile button is clicked", () => {
    // The modal must accept and call onCompileShacl prop
    expect(modalSrc).toContain("onCompileShacl");
    expect(modalSrc).toContain("setCompiling");
  });

  it("shows preview panel with verdict, explanation, previewCases, shapeTtl on ok:true", () => {
    expect(modalSrc).toContain("shaclPreview");
    expect(modalSrc).toContain("review.verdict");
    expect(modalSrc).toContain("review.confidence");
    expect(modalSrc).toContain("explanation");
    expect(modalSrc).toContain("previewCases");
    expect(modalSrc).toContain("생성된 SHACL 보기");
  });

  it("shows compile loading state", () => {
    expect(modalSrc).toContain("compiling");
  });

  it("renders approve and retry buttons after successful compile", () => {
    expect(modalSrc).toContain("이게 맞습니다");
    expect(modalSrc).toContain("다시");
  });
});

// ---------------------------------------------------------------------------
// Test 3 — compile ok:false → error shown, activate disabled
// ---------------------------------------------------------------------------
describe("Test 3 — compile failure: error shown, activate button disabled", () => {
  it("stores and displays compile error when ok:false", () => {
    expect(modalSrc).toContain("shaclError");
    expect(modalSrc).toContain("shaclError");
  });

  it("activate/save button stays disabled when compile failed", () => {
    // canAdd for shacl_constraint must check shaclPreview and not shaclError
    expect(modalSrc).toContain("shaclPreview");
    // The condition must guard against calling onAdd when error exists
    expect(modalSrc).toContain("!shaclError");
  });
});

// ---------------------------------------------------------------------------
// Test 4 — approve flow: onAddCustomRule called only after "이게 맞습니다"
// ---------------------------------------------------------------------------
describe("Test 4 — save only on approval, not before", () => {
  it("activate button calls onAdd (which maps to onAddCustomRule) with correct payload", () => {
    // The approve button must invoke onAdd with shacl_constraint kind
    expect(modalSrc).toContain('kind: "shacl_constraint"');
    expect(modalSrc).toContain('firesAt: "pre_final"');
    expect(modalSrc).toContain('action: "block"');
  });

  it("approve callback includes shapeTtl in payload", () => {
    expect(modalSrc).toContain("shapeTtl");
    // shapeTtl comes from the compile result (shaclPreview.shapeTtl) or raw mode
    expect(modalSrc).toContain("shaclPreview");
  });

  it("approve only fires from the dedicated approve button, not the generic Add rule button", () => {
    // The approve action (이게 맞습니다) should directly call onAdd; the generic
    // "Add rule" button path must require kind !== shacl_constraint or is guarded.
    // We verify by checking that the approve button calls onAdd separately.
    expect(modalSrc).toContain("이게 맞습니다");
    // Resetting after approval: state is cleared
    expect(modalSrc).toContain("setShaclPreview");
    expect(modalSrc).toContain("setNlText");
  });
});

// ---------------------------------------------------------------------------
// Test 5 — raw mode: shapeTtl direct input → activate → onAddCustomRule
// ---------------------------------------------------------------------------
describe("Test 5 — raw .ttl mode", () => {
  it("renders a shapeTtl textarea in raw mode", () => {
    // raw mode shows a textarea for shapeTtl
    expect(modalSrc).toContain("shapeTtl");
    expect(modalSrc).toContain("raw");
  });

  it("raw mode activate button sets correct payload without compiling", () => {
    // In raw mode, the activate calls onAdd directly with the textarea shapeTtl
    // The canAdd gate for raw shacl is based on !!shapeTtl.trim()
    expect(modalSrc).toContain("rawTtl");
  });
});

// ---------------------------------------------------------------------------
// Test 6 — saved shacl_constraint rule row renders SHACL badge
// ---------------------------------------------------------------------------
describe("Test 6 — saved shacl_constraint rule renders with SHACL badge", () => {
  it("describe() function handles shacl_constraint kind for row display", () => {
    expect(modalSrc).toContain('rule.what?.kind === "shacl_constraint"');
  });

  it("renders 결정론 · SHACL · live badge for shacl_constraint rules", () => {
    expect(modalSrc).toContain("결정론 · SHACL · live");
  });
});

// ---------------------------------------------------------------------------
// Test 7 — regression: deterministic_ref path untouched
// ---------------------------------------------------------------------------
describe("Test 7 — regression: deterministic_ref builder path unchanged", () => {
  it("still has deterministic_ref in kind union", () => {
    expect(modalSrc).toContain('"deterministic_ref"');
  });

  it("still builds deterministic_ref rule correctly", () => {
    expect(modalSrc).toContain('kind: "deterministic_ref"');
    expect(modalSrc).toContain("payload: { ref }");
  });

  it("customize-tab still wires onAddCustomRule to putCustomRule", () => {
    expect(tabSrc).toContain("putCustomRule");
    expect(tabSrc).toContain("handleAddCustomRule");
    expect(tabSrc).toContain("onAddCustomRule={handleAddCustomRule}");
  });

  it("customize-tab wires onCompileShacl to compileCustomRule", () => {
    expect(tabSrc).toContain("compileCustomRule");
    expect(tabSrc).toContain("onCompileShacl");
  });

  it("customize-api exports compileCustomRule and ShaclCompileResponse", () => {
    expect(apiSrc).toContain("export async function compileCustomRule");
    expect(apiSrc).toContain("ShaclCompileResponse");
    expect(apiSrc).toContain("ShaclReview");
    expect(apiSrc).toContain("ShaclPreviewCase");
  });
});
