import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MessageBubble } from "./message-bubble";
import { SegmentedTranscript } from "./segmented-transcript";
import type { ToolActivity, TranscriptSegment } from "@/chat-core";

const activities: ToolActivity[] = [
  { id: "call-1", label: "Grep", status: "done", startedAt: 0, durationMs: 40 },
  { id: "call-2", label: "Edit", status: "done", startedAt: 0, durationMs: 80 },
];

describe("SegmentedTranscript", () => {
  it("renders segments in chronological order with text via renderText", () => {
    const segments: TranscriptSegment[] = [
      { kind: "thinking", text: "planning", openedAt: 0, closedAt: 1 },
      { kind: "tool", toolId: "call-1" },
      { kind: "thinking", text: "reconsider", openedAt: 2, closedAt: 3 },
      { kind: "tool", toolId: "call-2" },
      { kind: "text", text: "All done." },
    ];
    const html = renderToStaticMarkup(
      <SegmentedTranscript
        segments={segments}
        activities={activities}
        renderText={(t) => <p data-text>{t}</p>}
      />,
    );
    // Text body present.
    expect(html).toContain("All done.");
    // Two thinking phrases present (independently collapsible blocks).
    expect(html).toContain("planning");
    expect(html).toContain("reconsider");
    // Interleave order: first thinking appears before the final text.
    expect(html.indexOf("planning")).toBeLessThan(html.indexOf("All done."));
    expect(html.indexOf("reconsider")).toBeLessThan(html.indexOf("All done."));
  });

  it("groups CONSECUTIVE tool segments into one timeline but splits across a text segment", () => {
    const grouped: TranscriptSegment[] = [
      { kind: "tool", toolId: "call-1" },
      { kind: "tool", toolId: "call-2" },
      { kind: "text", text: "x" },
    ];
    const split: TranscriptSegment[] = [
      { kind: "tool", toolId: "call-1" },
      { kind: "text", text: "between" },
      { kind: "tool", toolId: "call-2" },
    ];
    const groupedHtml = renderToStaticMarkup(
      <SegmentedTranscript segments={grouped} activities={activities} renderText={(t) => <>{t}</>} />,
    );
    const splitHtml = renderToStaticMarkup(
      <SegmentedTranscript segments={split} activities={activities} renderText={(t) => <>{t}</>} />,
    );
    // Grouped: one tools timeline group. Split: two tools timeline groups.
    const groupedTimelines = (groupedHtml.match(/data-chat-segment="tools"/g) ?? []).length;
    const splitTimelines = (splitHtml.match(/data-chat-segment="tools"/g) ?? []).length;
    expect(groupedTimelines).toBe(1);
    expect(splitTimelines).toBe(2);
    // "between" text splits the two tool runs.
    expect(splitHtml).toContain("between");
  });
});

describe("MessageBubble interleaved vs flat layout", () => {
  it("uses the interleaved layout when segments are content-authoritative", () => {
    const segments: TranscriptSegment[] = [
      { kind: "thinking", text: "thinking-marker", openedAt: 0, closedAt: 1 },
      { kind: "tool", toolId: "call-1" },
      { kind: "text", text: "Final body." },
    ];
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content="Final body."
        timestamp={1_800_000_000_000}
        thinkingContent="thinking-marker"
        activities={[activities[0]]}
        segments={segments}
      />,
    );
    expect(html).toContain('data-chat-segmented-transcript="true"');
    expect(html).toContain("Final body.");
    expect(html).toContain("thinking-marker");
  });

  it("falls back to the FLAT layout when segments do not match content (back-compat)", () => {
    const staleSegments: TranscriptSegment[] = [
      { kind: "text", text: "old partial" },
    ];
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content="the real finalized body"
        timestamp={1_800_000_000_000}
        thinkingContent="t"
        segments={staleSegments}
      />,
    );
    // No interleaved container; the real body renders via the flat markdown path.
    expect(html).not.toContain('data-chat-segmented-transcript="true"');
    expect(html).toContain("the real finalized body");
  });

  it("legacy message without segments renders the flat layout", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content="legacy answer"
        timestamp={1_800_000_000_000}
        thinkingContent="legacy thinking"
        activities={[activities[0]]}
      />,
    );
    expect(html).not.toContain('data-chat-segmented-transcript="true"');
    expect(html).toContain("legacy answer");
  });
});
