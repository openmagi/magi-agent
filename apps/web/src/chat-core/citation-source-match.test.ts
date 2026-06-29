/**
 * Tests for the citation URL matcher.
 *
 * When the model emits a markdown link from a tool-sourced fact
 * (per the citation-convention block, PR1), the UI needs to resolve the
 * link's href back to a matching ``InspectedSource`` so the renderer can
 * draw a citation chip with title / trust tier / snippet preview.
 *
 * URL matching is intentionally STRICT — same origin + canonical path — to
 * avoid false positives (e.g. matching a generic homepage link to a
 * specific inspected article).  This keeps the chip's promise meaningful:
 * "this exact link came from this exact source".
 */
import { describe, expect, it } from "vitest";

import {
  matchCitationSource,
  normalizeCitationHref,
} from "./citation-source-match";
import type { InspectedSource } from "./types";


function source(uri: string, partial: Partial<InspectedSource> = {}): InspectedSource {
  return {
    sourceId: `src-${Math.random().toString(36).slice(2, 8)}`,
    kind: "web_fetch",
    uri,
    inspectedAt: 1_000,
    ...partial,
  };
}


describe("normalizeCitationHref", () => {
  it("returns null for non-http(s) hrefs", () => {
    expect(normalizeCitationHref("mailto:k@example.com")).toBeNull();
    expect(normalizeCitationHref("javascript:alert(1)")).toBeNull();
    expect(normalizeCitationHref("#section")).toBeNull();
  });

  it("returns null for malformed URLs", () => {
    expect(normalizeCitationHref("not-a-url")).toBeNull();
  });

  it("strips the URL fragment but keeps the query", () => {
    expect(normalizeCitationHref("https://example.com/a?q=1#x")).toBe(
      "https://example.com/a?q=1",
    );
  });

  it("normalizes trailing slash on path", () => {
    expect(normalizeCitationHref("https://example.com/a/")).toBe(
      "https://example.com/a",
    );
  });

  it("preserves root path as /", () => {
    expect(normalizeCitationHref("https://example.com/")).toBe(
      "https://example.com/",
    );
  });

  it("lowercases scheme and host", () => {
    expect(normalizeCitationHref("HTTPS://Example.COM/Path")).toBe(
      "https://example.com/Path",
    );
  });

  it("collapses www. prefix on host", () => {
    expect(normalizeCitationHref("https://www.example.com/a")).toBe(
      "https://example.com/a",
    );
  });
});


describe("matchCitationSource", () => {
  it("matches by exact URI", () => {
    const sources = [source("https://example.com/article")];
    const match = matchCitationSource(
      "https://example.com/article",
      sources,
    );
    expect(match).toEqual(sources[0]);
  });

  it("matches across the www. prefix difference", () => {
    const sources = [source("https://www.example.com/article")];
    const match = matchCitationSource(
      "https://example.com/article",
      sources,
    );
    expect(match).toEqual(sources[0]);
  });

  it("matches across trailing slash difference", () => {
    const sources = [source("https://example.com/article")];
    expect(
      matchCitationSource("https://example.com/article/", sources),
    ).toEqual(sources[0]);
  });

  it("matches across fragment-only divergence", () => {
    const sources = [source("https://example.com/article")];
    expect(
      matchCitationSource("https://example.com/article#section-2", sources),
    ).toEqual(sources[0]);
  });

  it("does NOT match across different origins", () => {
    const sources = [source("https://example.com/article")];
    expect(
      matchCitationSource("https://other.com/article", sources),
    ).toBeNull();
  });

  it("does NOT match across different paths on the same origin", () => {
    const sources = [source("https://example.com/article")];
    expect(
      matchCitationSource("https://example.com/about", sources),
    ).toBeNull();
  });

  it("does NOT match across different query strings", () => {
    const sources = [source("https://example.com/article?id=1")];
    expect(
      matchCitationSource("https://example.com/article?id=2", sources),
    ).toBeNull();
  });

  it("returns null for non-http(s) hrefs", () => {
    const sources = [source("https://example.com/article")];
    expect(matchCitationSource("mailto:a@b.com", sources)).toBeNull();
  });

  it("returns null for empty source list", () => {
    expect(matchCitationSource("https://example.com/a", [])).toBeNull();
  });

  it("returns null for empty / null href", () => {
    const sources = [source("https://example.com/article")];
    expect(matchCitationSource("", sources)).toBeNull();
    expect(matchCitationSource(undefined as unknown as string, sources)).toBeNull();
  });

  it("picks the earliest-inspected source when two entries share a URL", () => {
    // Cache-style coalescing: same URL inspected twice → prefer the older
    // one so chip references are stable across the turn.
    const older = source("https://example.com/article", { inspectedAt: 1_000 });
    const newer = source("https://example.com/article", { inspectedAt: 5_000 });
    expect(
      matchCitationSource("https://example.com/article", [newer, older]),
    ).toBe(older);
  });
});
