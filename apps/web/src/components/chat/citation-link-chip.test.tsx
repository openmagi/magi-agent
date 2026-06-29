/**
 * Tests for ``CitationLinkChip``.
 *
 * The chip renders ReactMarkdown's ``<a>`` element.  When the href resolves
 * to an entry in ``inspectedSources`` (via the strict ``matchCitationSource``
 * matcher), the chip styles itself as a citation with hover popover.  When
 * the href does not match, it falls back to a plain ``<a>`` so external
 * links and non-tool URLs render normally.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";

import { CitationLinkChip } from "./citation-link-chip";
import type { InspectedSource } from "../../chat-core/types";


function source(
  uri: string,
  partial: Partial<InspectedSource> = {},
): InspectedSource {
  return {
    sourceId: partial.sourceId ?? `src-${Math.random().toString(36).slice(2, 8)}`,
    kind: partial.kind ?? "web_fetch",
    uri,
    inspectedAt: partial.inspectedAt ?? 1_000,
    ...partial,
  };
}


describe("CitationLinkChip", () => {
  it("renders a plain anchor when href does not match any source", () => {
    const html = renderToStaticMarkup(
      createElement(CitationLinkChip, {
        href: "https://other.example.com/page",
        sources: [source("https://different.example.com/article")],
        children: "external link",
      }),
    );
    expect(html).not.toContain('data-citation-link-chip="matched"');
    expect(html).toContain("external link");
    expect(html).toContain("https://other.example.com/page");
  });

  it("renders as a citation chip when href matches a source", () => {
    const html = renderToStaticMarkup(
      createElement(CitationLinkChip, {
        href: "https://example.com/article",
        sources: [
          source("https://example.com/article", { title: "Example Title" }),
        ],
        children: "Example",
      }),
    );
    expect(html).toContain('data-citation-link-chip="matched"');
    expect(html).toContain("Example");
  });

  it("threads the matched sourceId onto the chip anchor as a data attribute", () => {
    const html = renderToStaticMarkup(
      createElement(CitationLinkChip, {
        href: "https://example.com/article",
        sources: [
          source("https://example.com/article", { sourceId: "src-abc123" }),
        ],
        children: "Example",
      }),
    );
    expect(html).toContain('data-citation-source-id="src-abc123"');
  });

  it("matches across the www. prefix difference (matcher contract)", () => {
    const html = renderToStaticMarkup(
      createElement(CitationLinkChip, {
        href: "https://example.com/article",
        sources: [source("https://www.example.com/article")],
        children: "Example",
      }),
    );
    expect(html).toContain('data-citation-link-chip="matched"');
  });

  it("falls back to plain anchor for non-http href", () => {
    const html = renderToStaticMarkup(
      createElement(CitationLinkChip, {
        href: "mailto:a@example.com",
        sources: [source("https://example.com/article")],
        children: "email",
      }),
    );
    expect(html).not.toContain('data-citation-link-chip="matched"');
    expect(html).toContain("email");
  });

  it("opens link in a new tab via target/rel attrs (matched + unmatched)", () => {
    const matchedHtml = renderToStaticMarkup(
      createElement(CitationLinkChip, {
        href: "https://example.com/article",
        sources: [source("https://example.com/article")],
        children: "Example",
      }),
    );
    const unmatchedHtml = renderToStaticMarkup(
      createElement(CitationLinkChip, {
        href: "https://other.com/page",
        sources: [source("https://example.com/article")],
        children: "Other",
      }),
    );
    expect(matchedHtml).toContain('target="_blank"');
    expect(matchedHtml).toContain('rel="noreferrer"');
    expect(unmatchedHtml).toContain('target="_blank"');
    expect(unmatchedHtml).toContain('rel="noreferrer"');
  });

  it("renders no chip when sources is empty", () => {
    const html = renderToStaticMarkup(
      createElement(CitationLinkChip, {
        href: "https://example.com/article",
        sources: [],
        children: "Example",
      }),
    );
    expect(html).not.toContain('data-citation-link-chip="matched"');
  });

  it("preserves the children content verbatim in both render branches", () => {
    const matched = renderToStaticMarkup(
      createElement(CitationLinkChip, {
        href: "https://example.com/article",
        sources: [source("https://example.com/article")],
        children: "the original anchor text",
      }),
    );
    const unmatched = renderToStaticMarkup(
      createElement(CitationLinkChip, {
        href: "https://other.com/x",
        sources: [],
        children: "the original anchor text",
      }),
    );
    expect(matched).toContain("the original anchor text");
    expect(unmatched).toContain("the original anchor text");
  });
});
