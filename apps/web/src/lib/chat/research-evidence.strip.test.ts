import { stripResearchEvidenceMarker } from "./research-evidence";

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
});
