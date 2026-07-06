/**
 * Tests for the citation-verdict projection in the Audit tab (Wave 3b, Piece C).
 *
 * The terminal render `verdict` maps to a governance label + badge severity and
 * projects alongside existing rule verdicts. `not_applicable` turns (no external
 * reads) are dropped. This is render-provisional pending the Wave 4 gate record.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AuditPanel } from "./audit-panel";
import type { SessionCitationGroup } from "./sources-panel";
import type { CitationVerdict } from "@/chat-core";

function group(messageId: string, verdict: CitationVerdict, dangling: string[] = []): SessionCitationGroup {
  return {
    messageId,
    citations: {
      markers: [],
      sources: [
        { n: 1, sourceId: "src_1", uri: "https://x.test", title: "X", kind: "web_fetch", trustTier: null, inspected: true },
      ],
      danglingRefs: dangling,
      verdict,
    },
  };
}

describe("AuditPanel citation verdicts", () => {
  it("maps each verdict to its governance label", () => {
    const html = renderToStaticMarkup(
      <AuditPanel
        botId="bot-1"
        citationGroups={[group("m1", "cited"), group("m2", "partial", ["src_9"]), group("m3", "uncited")]}
      />,
    );
    expect(html).toContain("Sources cited");
    expect(html).toContain("Partially cited");
    expect(html).toContain("Uncited claims");
    expect(html).toContain("1 dangling reference");
  });

  it("drops not_applicable turns", () => {
    const html = renderToStaticMarkup(
      <AuditPanel botId="bot-1" citationGroups={[group("m1", "not_applicable")]} />,
    );
    expect(html).not.toContain("No sources");
    expect(html).not.toContain("Source citation");
  });
});
