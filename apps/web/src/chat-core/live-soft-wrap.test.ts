import { describe, it, expect } from "vitest";
import { collapseLiveSoftWraps } from "./live-soft-wrap";

describe("collapseLiveSoftWraps", () => {
  it("collapses per-token lone newlines (Korean) into flowing text", () => {
    // Real hosted live-stream payload: one Hangul syllable per line.
    const live = "계\n산\n 결과\n는\n2\n입\n니다\n.\n 서\n브\n 에\n이\n전\n트\n 스";
    const out = collapseLiveSoftWraps(live);
    expect(out).not.toMatch(/\n/);
    expect(out).toBe("계 산 결과 는 2 입 니다 . 서 브 에 이 전 트 스");
  });

  it("collapses per-token lone newlines (English)", () => {
    const live = "got\nthe\n10\n-K\nfiling\nbut\nit's\nin\nX\nB\nRL";
    expect(collapseLiveSoftWraps(live)).toBe("got the 10 -K filing but it's in X B RL");
  });

  it("preserves blank lines between paragraphs", () => {
    const live = "First answer line.\n\nSecond paragraph here.";
    expect(collapseLiveSoftWraps(live)).toBe("First answer line.\n\nSecond paragraph here.");
  });

  it("collapses a single inter-token newline but keeps a following blank line", () => {
    const live = "는\n\n2";
    expect(collapseLiveSoftWraps(live)).toBe("는\n\n2");
  });

  it("is a no-op without newlines", () => {
    expect(collapseLiveSoftWraps("plain text answer")).toBe("plain text answer");
  });

  it("preserves a whitespace-polluted blank line as a paragraph break", () => {
    expect(collapseLiveSoftWraps("a\n \nb")).toBe("a\n\nb");
  });

  it("collapses runs of 3+ newlines to a single paragraph break", () => {
    expect(collapseLiveSoftWraps("a\n\n\n\nb")).toBe("a\n\nb");
  });

  it("normalizes CRLF paragraph breaks and lone CRLF newlines", () => {
    expect(collapseLiveSoftWraps("a\r\nb")).toBe("a b");
    expect(collapseLiveSoftWraps("a\r\n\r\nb")).toBe("a\n\nb");
  });
});
