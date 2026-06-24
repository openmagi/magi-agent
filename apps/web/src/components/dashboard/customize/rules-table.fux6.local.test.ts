import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./rules-table.tsx", import.meta.url),
  "utf8",
);

describe("RulesTable — F-UX6 hybrid (groupId) composition rendering", () => {
  it("buckets custom rules by groupId before building rows", () => {
    expect(src).toContain("groupedBuckets");
    expect(src).toContain("rule.groupId");
  });

  it("renders one summary row per groupId via customRuleGroupToRow", () => {
    expect(src).toContain("customRuleGroupToRow");
    expect(src).toContain("custom-group:");
  });

  it("composes the summary title from groupId + composing kinds", () => {
    expect(src).toContain("hybrid:");
  });

  it("uses the hybrid TrustClass bucket for the group summary row", () => {
    expect(src).toContain('trustClass: "hybrid"');
  });

  it("nests the per-primitive sub-rows under the parent via children", () => {
    expect(src).toContain("children:");
    expect(src).toContain("hasChildren");
  });

  it("the row view chevron toggles expanded state", () => {
    expect(src).toContain('aria-expanded={expanded}');
    expect(src).toContain("setExpanded");
  });
});
