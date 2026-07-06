/**
 * Tests for the source-citation display projection (Wave 3b).
 *
 * These cover the pure contract the renderer relies on: first-appearance
 * numbering during streaming, terminal-payload finalization, dangling
 * de-chipping, tolerant near-miss normalization (display only), and defensive
 * parsing of the wire payload.
 */
import { describe, expect, it } from "vitest";

import {
  buildCitationIndex,
  computeOptimisticMarkers,
  hasCanonicalCitationRef,
  parseCitationsPayload,
  splitCitationTokens,
} from "./citation-markers";
import type { CitationsPayload } from "./types";

const payload: CitationsPayload = {
  markers: [
    ["src_3", 1],
    ["src_7", 2],
  ],
  sources: [
    {
      n: 1,
      sourceId: "src_3",
      uri: "https://sec.gov/tesla",
      title: "Tesla 10-Q",
      kind: "web_fetch",
      trustTier: "official",
      inspected: true,
    },
    {
      n: 2,
      sourceId: "src_7",
      uri: "https://reuters.com/x",
      title: null,
      kind: "web_search",
      trustTier: "secondary",
      inspected: false,
    },
  ],
  danglingRefs: ["src_9"],
  verdict: "partial",
};

describe("computeOptimisticMarkers", () => {
  it("numbers canonical refs in first-appearance order", () => {
    const map = computeOptimisticMarkers("A [src_7] then [src_3] then [src_7] again");
    expect(map.get("src_7")).toBe(1);
    expect(map.get("src_3")).toBe(2);
    expect(map.size).toBe(2);
  });

  it("returns an empty map when there are no refs", () => {
    expect(computeOptimisticMarkers("no citations here").size).toBe(0);
  });
});

describe("buildCitationIndex", () => {
  it("uses the terminal payload markers and dangling set when present", () => {
    const idx = buildCitationIndex(payload, "ignored [src_3]");
    expect(idx.displayIndexFor("src_3")).toBe(1);
    expect(idx.displayIndexFor("src_7")).toBe(2);
    expect(idx.displayIndexFor("src_9")).toBeNull();
    expect(idx.isDangling("src_9")).toBe(true);
    expect(idx.size).toBe(2);
  });

  it("falls back to optimistic numbering from text while streaming", () => {
    const idx = buildCitationIndex(null, "first [src_5] second [src_2]");
    expect(idx.displayIndexFor("src_5")).toBe(1);
    expect(idx.displayIndexFor("src_2")).toBe(2);
    expect(idx.isDangling("src_5")).toBe(false);
  });

  it("agrees on numbering between optimistic and finalized (no dangling)", () => {
    const text = "x [src_3] y [src_7]";
    const optimistic = buildCitationIndex(null, text);
    const finalized = buildCitationIndex(payload, text);
    expect(optimistic.displayIndexFor("src_3")).toBe(finalized.displayIndexFor("src_3"));
    expect(optimistic.displayIndexFor("src_7")).toBe(finalized.displayIndexFor("src_7"));
  });
});

describe("splitCitationTokens", () => {
  it("turns a canonical marker into a marker part", () => {
    const idx = buildCitationIndex(payload, "");
    const parts = splitCitationTokens("Revenue rose [src_3].", idx);
    expect(parts).toEqual([
      { kind: "text", value: "Revenue rose " },
      { kind: "marker", sourceId: "src_3", index: 1 },
      { kind: "text", value: "." },
    ]);
  });

  it("de-chips a dangling ref to plain literal text", () => {
    const idx = buildCitationIndex(payload, "");
    const parts = splitCitationTokens("bogus [src_9] cite", idx);
    expect(parts).toEqual([
      { kind: "text", value: "bogus " },
      { kind: "dangling", sourceId: "src_9", raw: "[src_9]" },
      { kind: "text", value: " cite" },
    ]);
  });

  it("normalizes tolerant near-misses to an existing canonical id (display only)", () => {
    const idx = buildCitationIndex(payload, "");
    for (const token of ["[src3]", "(src_3)", "[SRC_3]"]) {
      const parts = splitCitationTokens(`see ${token}`, idx);
      expect(parts.at(-1)).toEqual({ kind: "marker", sourceId: "src_3", index: 1 });
    }
  });

  it("leaves a tolerant near-miss with no cited id as plain text", () => {
    const idx = buildCitationIndex(payload, "");
    const parts = splitCitationTokens("[src99] unknown", idx);
    expect(parts[0]).toEqual({ kind: "dangling", sourceId: "src_99", raw: "[src99]" });
  });
});

describe("parseCitationsPayload", () => {
  it("parses a well-formed wire payload", () => {
    const parsed = parseCitationsPayload({
      markers: [["src_3", 1]],
      sources: [
        {
          n: 1,
          sourceId: "src_3",
          uri: "https://sec.gov",
          title: null,
          kind: "web_fetch",
          trustTier: null,
          inspected: true,
        },
      ],
      danglingRefs: [],
      verdict: "cited",
    });
    expect(parsed?.markers).toEqual([["src_3", 1]]);
    expect(parsed?.sources[0].trustTier).toBeNull();
    expect(parsed?.verdict).toBe("cited");
  });

  it("returns null for a payload with no display information", () => {
    expect(
      parseCitationsPayload({ markers: [], sources: [], danglingRefs: [], verdict: "not_applicable" }),
    ).toBeNull();
  });

  it("returns null for malformed input", () => {
    expect(parseCitationsPayload(null)).toBeNull();
    expect(parseCitationsPayload("nope")).toBeNull();
    expect(parseCitationsPayload(42)).toBeNull();
  });

  it("coerces an unknown verdict to not_applicable and drops junk markers", () => {
    const parsed = parseCitationsPayload({
      markers: [["src_3", 1], ["bad"], [1, 2]],
      sources: [],
      danglingRefs: ["src_9"],
      verdict: "weird",
    });
    expect(parsed?.markers).toEqual([["src_3", 1]]);
    expect(parsed?.verdict).toBe("not_applicable");
    expect(parsed?.danglingRefs).toEqual(["src_9"]);
  });
});

describe("hasCanonicalCitationRef", () => {
  it("detects canonical refs and ignores prose", () => {
    expect(hasCanonicalCitationRef("cite [src_4] now")).toBe(true);
    expect(hasCanonicalCitationRef("no source ids")).toBe(false);
  });
});
