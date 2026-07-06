/**
 * Tests for source-citation rendering in ``MessageBubble`` (Wave 3b, Piece A).
 *
 * Covers: chip transform after terminal finalize, optimistic transform during
 * streaming (no payload), dangling de-chip, and the flag-OFF invariant (no
 * `citations` and no `[src_N]` tokens renders byte-identically to today).
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MessageBubble } from "./message-bubble";
import type { CitationsPayload } from "@/chat-core";

const payload: CitationsPayload = {
  markers: [["src_3", 1], ["src_7", 2]],
  sources: [
    { n: 1, sourceId: "src_3", uri: "https://sec.gov/x", title: "10-Q", kind: "web_fetch", trustTier: "official", inspected: true },
    { n: 2, sourceId: "src_7", uri: "https://reuters.com/y", title: null, kind: "web_search", trustTier: "secondary", inspected: false },
  ],
  danglingRefs: ["src_9"],
  verdict: "partial",
};

describe("MessageBubble source citations", () => {
  it("transforms [src_N] into [n] chips from the terminal payload", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content={"Revenue was solid [src_3] and cash rose [src_7]."}
        citations={payload}
        timestamp={1_800_000_000_000}
      />,
    );
    expect(html).toContain('data-source-id="src_3"');
    expect(html).toContain('data-source-id="src_7"');
    // Display indices, not the raw canonical ids.
    expect(html).not.toContain("[src_3]");
    expect(html).not.toContain("[src_7]");
  });

  it("de-chips a dangling ref to plain literal text", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content={"A fabricated cite [src_9] here."}
        citations={payload}
        timestamp={1_800_000_000_000}
      />,
    );
    expect(html).not.toContain('data-source-id="src_9"');
    expect(html).toContain("[src_9]");
  });

  it("renders optimistic chips while streaming with no payload yet", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content={"Streaming first [src_5] then [src_2]"}
        isStreaming
        timestamp={1_800_000_000_000}
      />,
    );
    expect(html).toContain('data-source-id="src_5"');
    expect(html).toContain('data-source-id="src_2"');
  });

  it("is byte-identical with no citations and no tokens (flag-OFF)", () => {
    const props = {
      role: "assistant" as const,
      content: "Plain answer with no citations.",
      timestamp: 1_800_000_000_000,
    };
    const withUndefined = renderToStaticMarkup(<MessageBubble {...props} />);
    const withNull = renderToStaticMarkup(<MessageBubble {...props} citations={null} />);
    expect(withUndefined).toBe(withNull);
    expect(withUndefined).not.toContain("data-citation-marker");
  });
});
