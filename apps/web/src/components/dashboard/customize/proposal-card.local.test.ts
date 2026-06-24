import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./proposal-card.tsx", import.meta.url),
  "utf8",
);

describe("ProposalCard — F-UX6 architect proposal renderer", () => {
  it("renders single vs hybrid composition header copy", () => {
    expect(src).toContain('proposal.mode === "hybrid"');
    expect(src).toContain("hybrid composition");
    expect(src).toContain("single primitive");
  });

  it("renders one PrimitiveCard per primitive with a per-primitive TrustBadge", () => {
    expect(src).toContain("PrimitiveCard");
    expect(src).toContain("TrustBadge");
    expect(src).toContain("primitive.trustClass");
  });

  it("surfaces the architect's per-primitive rationale", () => {
    expect(src).toContain("primitive.rationale");
  });

  it("collapses the raw primitive payload behind a details disclosure", () => {
    expect(src).toContain("View payload");
    expect(src).toContain("primitive.payload");
  });

  it("offers Activate / Refine / Author manually instead affordances", () => {
    expect(src).toContain("Activate");
    expect(src).toContain("Refine");
    expect(src).toContain("Author manually instead");
  });

  it("maps the architect trust-class vocabulary to the dashboard TrustClass enum", () => {
    expect(src).toContain("mapArchitectTrust");
    expect(src).toContain("ArchitectTrustClass");
  });

  it("explains hybrid composition (deterministic-narrows-advisory)", () => {
    expect(src).toContain("Composed as one logical policy");
  });
});
