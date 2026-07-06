/**
 * Tests for ``SourcesPanel`` (Wave 3b, Piece B).
 *
 * Renders the session's cited sources from terminal ``citations`` payloads:
 * display index, title (host fallback), kind, trust tier, host, and a pointer
 * affordance for uninspected search hits. Empty state when nothing is cited.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SourcesPanel, type SessionCitationGroup } from "./sources-panel";
import type { CitationsPayload } from "@/chat-core";

function payload(sources: CitationsPayload["sources"]): CitationsPayload {
  return { markers: [], sources, danglingRefs: [], verdict: "cited" };
}

describe("SourcesPanel", () => {
  it("renders cited sources grouped from the payload", () => {
    const groups: SessionCitationGroup[] = [
      {
        messageId: "m1",
        citations: payload([
          {
            n: 1,
            sourceId: "src_3",
            uri: "https://www.sec.gov/tesla",
            title: "Tesla 10-Q",
            kind: "web_fetch",
            trustTier: "official",
            inspected: true,
          },
          {
            n: 2,
            sourceId: "src_7",
            uri: "https://reuters.com/story",
            title: null,
            kind: "web_search",
            trustTier: "secondary",
            inspected: false,
          },
        ]),
      },
    ];
    const html = renderToStaticMarkup(<SourcesPanel sessionCitations={groups} />);
    expect(html).toContain("Tesla 10-Q");
    // Title falls back to host when null.
    expect(html).toContain("reuters.com");
    expect(html).toContain('data-source-id="src_3"');
    expect(html).toContain('data-source-id="src_7"');
    // Uninspected search hit gets a pointer affordance.
    expect(html).toContain("pointer");
    expect(html).toContain('data-source-inspected="false"');
    expect(html).toContain("official");
  });

  it("shows the empty state when nothing is cited", () => {
    const html = renderToStaticMarkup(<SourcesPanel sessionCitations={[]} />);
    expect(html).toContain("No sources cited yet.");
  });

  it("ignores messages whose payload has no cited sources", () => {
    const groups: SessionCitationGroup[] = [
      { messageId: "m1", citations: payload([]) },
    ];
    const html = renderToStaticMarkup(<SourcesPanel sessionCitations={groups} />);
    expect(html).toContain("No sources cited yet.");
  });

  it("renders an https source uri as a clickable anchor", () => {
    const groups: SessionCitationGroup[] = [
      {
        messageId: "m1",
        citations: payload([
          {
            n: 1,
            sourceId: "src_1",
            uri: "https://sec.gov/tesla",
            title: "Tesla 10-Q",
            kind: "web_fetch",
            trustTier: "official",
            inspected: true,
          },
        ]),
      },
    ];
    const html = renderToStaticMarkup(<SourcesPanel sessionCitations={groups} />);
    expect(html).toContain('href="https://sec.gov/tesla"');
    expect(html).toContain("Open source");
  });

  it("renders a javascript: source uri as inert text, never an anchor (XSS guard)", () => {
    const groups: SessionCitationGroup[] = [
      {
        messageId: "m1",
        citations: payload([
          {
            n: 1,
            sourceId: "src_1",
            // eslint-disable-next-line no-script-url
            uri: "javascript:alert(1)",
            title: "poisoned source",
            kind: "web_fetch",
            trustTier: "secondary",
            inspected: true,
          },
        ]),
      },
    ];
    const html = renderToStaticMarkup(<SourcesPanel sessionCitations={groups} />);
    // No anchor and no javascript: href is emitted for the unsafe uri.
    expect(html).not.toContain('href="javascript:alert(1)"');
    expect(html).not.toContain("Open source");
    // The uri is shown as plain text so it stays visible but inert.
    expect(html).toContain("javascript:alert(1)");
  });
});
