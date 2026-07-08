import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CitationHedgeCallout } from "./citation-hedge-callout";

describe("CitationHedgeCallout", () => {
  it("renders the hedge body inside a distinguished (muted amber) callout", () => {
    const html = renderToStaticMarkup(
      <CitationHedgeCallout>
        Contains unverified figures; no source was available for: X
      </CitationHedgeCallout>,
    );
    // Distinguished from plain answer text: muted amber design token.
    expect(html).toContain("bg-amber-500");
    // Accessible: an aside/note, not an alert (this is an honesty hedge).
    expect(html).toContain('role="note"');
    expect(html).toContain("Contains unverified figures");
  });
});
