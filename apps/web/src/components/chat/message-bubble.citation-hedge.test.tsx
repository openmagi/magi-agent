import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MessageBubble } from "./message-bubble";

// Standalone citation coverage (GAP #5). These integration cases render the
// real MessageBubble and exercise the blockquote override that turns the
// source-citation fail-open hedge sentinel into a distinguished callout. They
// live here (not in the colocated message-bubble.test.tsx) so they are listed
// in the vitest include and run in the standard suite. message-bubble.test.tsx
// carries pre-existing dormant failures (markdown-literal streaming cases) that
// keep it out of the include, so these citation assertions would otherwise only
// run via a by-name CLI invocation nobody performs.
describe("MessageBubble source-citation hedge", () => {
  it("renders the source-citation fail-open hedge as a distinguished callout", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        botId="bot-1"
        content={[
          "Revenue grew 40% year over year.",
          "",
          "> [!citation-hedge]",
          "> Contains unverified figures; no source was available for: Revenue grew 40%",
        ].join("\n")}
        timestamp={1_800_000_000_000}
      />,
    );

    // The hedge is styled as the muted callout, not plain answer prose.
    expect(html).toContain("citation-hedge-callout");
    expect(html).toContain("bg-amber-500");
    expect(html).toContain("Contains unverified figures");
    // The sentinel itself is stripped from the visible text.
    expect(html).not.toContain("[!citation-hedge]");
  });

  it("does NOT restyle a normal blockquote as a citation callout", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        botId="bot-1"
        content={"> A normal quote from a source document."}
        timestamp={1_800_000_000_000}
      />,
    );

    expect(html).not.toContain("citation-hedge-callout");
    expect(html).toContain("A normal quote from a source document.");
    expect(html).toContain("<blockquote>");
  });
});
