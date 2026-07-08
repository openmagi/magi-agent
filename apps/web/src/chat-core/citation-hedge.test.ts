import { describe, expect, it } from "vitest";
import { CITATION_HEDGE_SENTINEL, matchCitationHedge } from "./citation-hedge";

describe("matchCitationHedge", () => {
  it("detects and strips the sentinel when it leads the text", () => {
    const notice = "Contains unverified figures; no source was available for: X";
    const result = matchCitationHedge(`${CITATION_HEDGE_SENTINEL}\n${notice}`);
    expect(result.isHedge).toBe(true);
    expect(result.body).toBe(notice);
  });

  it("tolerates leading whitespace before the sentinel", () => {
    const result = matchCitationHedge(`  \n${CITATION_HEDGE_SENTINEL}\nhedge`);
    expect(result.isHedge).toBe(true);
    expect(result.body).toBe("hedge");
  });

  it("does NOT match when the sentinel is not the leading token", () => {
    const result = matchCitationHedge(
      `Some normal answer mentioning ${CITATION_HEDGE_SENTINEL} later.`,
    );
    expect(result.isHedge).toBe(false);
    expect(result.body).toContain("Some normal answer");
  });

  it("does NOT match normal blockquote text", () => {
    const result = matchCitationHedge("A quoted sentence from a source.");
    expect(result.isHedge).toBe(false);
  });

  it("stays byte-identical to the backend sentinel", () => {
    expect(CITATION_HEDGE_SENTINEL).toBe("[!citation-hedge]");
  });
});
