/**
 * PR-F-UX8 behavioural test for the lifecycle picker search matcher.
 *
 * The sibling ``author-wizard.local.test.ts`` already pins the matcher's
 * *shape* (regex over the function body asserts the three
 * ``.toLowerCase().includes(q)`` calls + the empty-query short-circuit).
 * Shape pinning catches a rename like ``opt.description → opt.desc`` —
 * but it does NOT catch a regression where the operator swaps ``&&`` for
 * ``||``, inverts a return, or breaks the case-insensitivity guarantee.
 *
 * This file drives a tiny MIRROR of the real matcher with representative
 * inputs and asserts the true/false outcome directly. We mirror (rather
 * than import) for the same reason ``author-wizard.payload-roundtrip
 * .local.test.ts`` does: ``author-wizard.tsx`` is a "use client" module
 * that transitively imports `@/lib/customize-api` + lucide-react, and
 * those resolutions are friction in a non-Next vitest run. The mirror is
 * anchored to the real source via substring assertions below so a
 * divergence between the mirror and the real implementation surfaces
 * here at red-light time rather than only on a manual UI test.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const wizardSrc = readFileSync(
  new URL("./author-wizard.tsx", import.meta.url),
  "utf8",
);

// ---------------------------------------------------------------------------
// Mirror — copied verbatim from the ``lifecycleOptionMatchesQuery`` body
// in author-wizard.tsx. Keep the bodies byte-identical so the source-
// anchor assertions below pin drift.
// ---------------------------------------------------------------------------
type MatcherOption = {
  id: string;
  label: string;
  description: string;
};

function lifecycleOptionMatchesQueryMirror(
  opt: MatcherOption,
  query: string,
): boolean {
  const q = query.trim().toLowerCase();
  if (q.length === 0) {
    return true;
  }
  if (opt.id.toLowerCase().includes(q)) {
    return true;
  }
  if (opt.label.toLowerCase().includes(q)) {
    return true;
  }
  if (opt.description.toLowerCase().includes(q)) {
    return true;
  }
  return false;
}

// Representative slot mirrors the real LIFECYCLE_OPTIONS[0]
// (``before_tool_use``) verbatim so a future copy edit on the canonical
// option that drops a keyword like "PreToolUse" or "tool" surfaces here,
// not only in the runtime. The sibling source-anchor block guards the
// label/description strings inside author-wizard.tsx itself.
const BEFORE_TOOL_USE: MatcherOption = {
  id: "before_tool_use",
  label: "Before a tool runs",
  description: "Fires at PreToolUse — before the agent invokes a tool.",
};

// Pulled verbatim from LIFECYCLE_OPTIONS[10] (``before_compaction``).
// Used to assert the description-substring match path independently of
// the id/label match paths, so a regression that drops opt.description
// from the matcher fails this case while leaving the id/label cases
// green.
const BEFORE_COMPACTION: MatcherOption = {
  id: "before_compaction",
  label: "Before compaction",
  description:
    "Fires immediately before the context-compaction plugin trims the model request — covers both the automatic threshold/real-token decision path and the manual /compact force path. PR-F-LIFE4a: block tells the plugin to SKIP the tail-drop.",
};

describe("lifecycleOptionMatchesQuery — source anchor (mirror ↔ real)", () => {
  // These five assertions pin the mirror to the real implementation. If
  // someone edits author-wizard.tsx (rename a field, flip an operator,
  // drop a branch) the mirror must be updated to match — or one of these
  // anchors fails at red-light time.
  it("real source declares the matcher with the documented signature", () => {
    expect(wizardSrc).toMatch(
      /function lifecycleOptionMatchesQuery\(\s*opt: LifecycleOption,\s*query: string,?\s*\): boolean \{/,
    );
  });
  it("real source normalises the query with trim().toLowerCase()", () => {
    expect(wizardSrc).toContain("const q = query.trim().toLowerCase();");
  });
  it("real source short-circuits the empty query to true", () => {
    expect(wizardSrc).toMatch(/if \(q\.length === 0\) \{\s*return true;\s*\}/);
  });
  it("real source checks all three searchable surfaces", () => {
    expect(wizardSrc).toContain("opt.id.toLowerCase().includes(q)");
    expect(wizardSrc).toContain("opt.label.toLowerCase().includes(q)");
    expect(wizardSrc).toContain("opt.description.toLowerCase().includes(q)");
  });
  it("real source falls through to return false on a full miss", () => {
    // The matcher MUST end with `return false;` after the three positive
    // branches. A regression that swaps it for `return true;` (default-
    // match) would break the "type something to filter" affordance and
    // the no-matches empty state would never render.
    expect(wizardSrc).toMatch(
      /opt\.description\.toLowerCase\(\)\.includes\(q\)[\s\S]*?return true;\s*\}\s*return false;\s*\}/,
    );
  });

  it("real source pins the BEFORE_TOOL_USE option this test exercises", () => {
    // Anchors the chosen fixture against the canonical LIFECYCLE_OPTIONS
    // entry so a label/description edit in the wizard cannot silently
    // diverge from the mirror.
    expect(wizardSrc).toContain('id: "before_tool_use"');
    expect(wizardSrc).toContain('label: "Before a tool runs"');
    expect(wizardSrc).toContain(
      'description: "Fires at PreToolUse — before the agent invokes a tool."',
    );
  });
  it("real source pins the BEFORE_COMPACTION option this test exercises", () => {
    expect(wizardSrc).toContain('id: "before_compaction"');
    expect(wizardSrc).toContain('label: "Before compaction"');
    // Anchor a token that lives only in the description so the description
    // -match assertions below stay honest.
    expect(wizardSrc).toContain("SKIP the tail-drop");
  });
});

describe("lifecycleOptionMatchesQuery — behavioural (true/false on real inputs)", () => {
  it("matches by id substring", () => {
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_TOOL_USE, "before_tool_use")).toBe(true);
    // partial id is enough — the matcher is substring, not equality
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_TOOL_USE, "tool_use")).toBe(true);
  });

  it("matches by label substring", () => {
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_TOOL_USE, "Before a tool runs")).toBe(true);
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_TOOL_USE, "tool runs")).toBe(true);
  });

  it("matches by description substring (independent of id/label)", () => {
    // "tail-drop" appears only in the description of BEFORE_COMPACTION
    // (not in the id ``before_compaction`` nor in the label
    // "Before compaction"). A regression that drops opt.description from
    // the matcher fails this assertion while leaving the id/label
    // assertions above green.
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_COMPACTION, "tail-drop")).toBe(true);
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_COMPACTION, "model request")).toBe(true);
  });

  it("is case-insensitive across id, label, and description", () => {
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_TOOL_USE, "TOOL")).toBe(true);
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_TOOL_USE, "BEFORE A TOOL RUNS")).toBe(true);
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_COMPACTION, "TAIL-DROP")).toBe(true);
  });

  it("treats the empty query as a tautology (returns true unconditionally)", () => {
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_TOOL_USE, "")).toBe(true);
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_COMPACTION, "")).toBe(true);
    // whitespace-only also collapses to empty after trim()
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_TOOL_USE, "   ")).toBe(true);
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_TOOL_USE, "\t\n")).toBe(true);
  });

  it("returns false for a query that misses all three fields", () => {
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_TOOL_USE, "zzzzz")).toBe(false);
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_COMPACTION, "zzzzz")).toBe(false);
    // a near-miss — "tail-drop" lives in BEFORE_COMPACTION's description
    // but not in BEFORE_TOOL_USE's three fields — must still return false
    expect(lifecycleOptionMatchesQueryMirror(BEFORE_TOOL_USE, "tail-drop")).toBe(false);
  });
});
