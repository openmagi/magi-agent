import { describe, expect, it } from "vitest";
import {
  normalizeResearchEvidenceSnapshot,
  stripResearchEvidenceMarker,
} from "./research-evidence";

describe("stripResearchEvidenceMarker", () => {
  it("removes trailing research evidence HTML comment", () => {
    const content = "Hello world\n\n<!-- clawy:research-evidence:v1:eyJpbnNwZWN0ZWRTb3VyY2VzIjpbXX0 -->";
    expect(stripResearchEvidenceMarker(content)).toBe("Hello world");
  });

  it("returns content unchanged when no marker present", () => {
    expect(stripResearchEvidenceMarker("plain text")).toBe("plain text");
  });

  it("handles empty string", () => {
    expect(stripResearchEvidenceMarker("")).toBe("");
  });

  it("does not strip markers in the middle of content", () => {
    const content = "before <!-- clawy:research-evidence:v1:abc --> after";
    expect(stripResearchEvidenceMarker(content)).toBe(content);
  });

  it("strips marker with surrounding whitespace", () => {
    const content = "Hello\n\n  <!-- clawy:research-evidence:v1:abc-def_123 -->  ";
    expect(stripResearchEvidenceMarker(content)).toBe("Hello");
  });

  it("normalizes governed public claim summaries without raw ledger objects", () => {
    expect(normalizeResearchEvidenceSnapshot({
      inspectedSources: [],
      capturedAt: 123,
      projectionMode: "structured_claims_only",
      claims: [{
        claimId: "claim-1",
        claimType: "numeric",
        supportStatus: "supported",
        claimText: "Revenue increased by a private amount.",
        citationRefs: ["source_1_span_1"],
        evidenceRefs: [
          "evidence:sha256:1111111111111111111111111111111111111111111111111111111111111111",
          "session:private-ref",
        ],
        rawEvidenceLedger: { private: true },
      }],
    })).toEqual({
      inspectedSources: [],
      capturedAt: 123,
      projectionMode: "structured_claims_only",
      claims: [{
        claimId: "claim-1",
        claimType: "numeric",
        supportStatus: "supported",
        citationRefs: ["source_1_span_1"],
        evidenceRefs: ["evidence:sha256:1111111111111111111111111111111111111111111111111111111111111111"],
      }],
    });
  });

  it("ignores projection modes without public evidence content", () => {
    expect(normalizeResearchEvidenceSnapshot({
      inspectedSources: [],
      capturedAt: 123,
      projectionMode: "raw_text_allowed",
    })).toBeUndefined();
  });
});
