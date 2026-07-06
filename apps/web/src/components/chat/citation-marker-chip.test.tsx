/**
 * Tests for ``CitationMarkerChip`` (Wave 3b, Piece A).
 *
 * The chip renders a clickable superscript ``[n]`` carrying the canonical
 * ``src_N`` id as ``data-source-id`` so the Sources panel can cross-link.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CitationMarkerChip } from "./citation-marker-chip";

describe("CitationMarkerChip", () => {
  it("renders the display index and carries the source id", () => {
    const html = renderToStaticMarkup(
      <CitationMarkerChip sourceId="src_3" index={1} />,
    );
    expect(html).toContain('data-source-id="src_3"');
    expect(html).toContain('data-citation-marker="true"');
    expect(html).toContain(">1<");
    expect(html).toContain("<sup");
  });
});
