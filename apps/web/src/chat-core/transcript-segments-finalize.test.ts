import { describe, it, expect } from "vitest";
import {
  appendNewToolSegments,
  appendSegmentText,
  appendSegmentThinking,
  finalizedSegmentsForMessage,
  deriveToolIdsFromSegments,
} from "./transcript-segments";
import type { ToolActivity, TranscriptSegment } from "./types";

function tool(id: string): ToolActivity {
  return { id, label: id, status: "done", startedAt: 0 };
}

describe("appendNewToolSegments (live full-list callback)", () => {
  it("appends tool segments for new real ids in list order", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendNewToolSegments(segs, [tool("call-1"), tool("call-2")]);
    expect(deriveToolIdsFromSegments(segs)).toEqual(["call-1", "call-2"]);
  });

  it("does not duplicate a tool id already present, and preserves position", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendNewToolSegments(segs, [tool("call-1")]);
    segs = appendSegmentText(segs, "interim");
    // Same list arrives again plus a new tool: call-1 stays put, call-2 appends.
    segs = appendNewToolSegments(segs, [tool("call-1"), tool("call-2")]);
    expect(segs!.map((s) => s.kind)).toEqual(["tool", "text", "tool"]);
    expect(deriveToolIdsFromSegments(segs)).toEqual(["call-1", "call-2"]);
  });

  it("ignores synthetic llm: progress activity ids", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendNewToolSegments(segs, [tool("llm:turn:0"), tool("call-1")]);
    expect(deriveToolIdsFromSegments(segs)).toEqual(["call-1"]);
  });

  it("closes an open thinking segment before the first tool", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentThinking(segs, "reason", 10);
    segs = appendNewToolSegments(segs, [tool("call-1")], 20);
    const first = segs![0];
    expect(first.kind === "thinking" && first.closedAt).toBe(20);
  });
});

describe("finalizedSegmentsForMessage (content-authority guard)", () => {
  it("returns the closed segments when derived text matches final content", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentThinking(segs, "think", 10);
    segs = appendNewToolSegments(segs, [tool("call-1")], 20);
    segs = appendSegmentText(segs, "The answer.", 30);
    const finalized = finalizedSegmentsForMessage(segs, "The answer.", 99);
    expect(finalized).toBeDefined();
    expect(finalized!.map((s) => s.kind)).toEqual(["thinking", "tool", "text"]);
  });

  it("returns undefined when content was mutated after capture (flat fallback)", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentText(segs, "partial");
    // e.g. interrupted suffix appended, or snapshot repair replaced text.
    expect(finalizedSegmentsForMessage(segs, "partial [interrupted]")).toBeUndefined();
  });

  it("returns undefined for empty/absent segments", () => {
    expect(finalizedSegmentsForMessage(undefined, "")).toBeUndefined();
    expect(finalizedSegmentsForMessage([], "anything")).toBeUndefined();
  });

  it("closes a trailing open thinking segment on finalize", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentText(segs, "body");
    segs = appendSegmentThinking(segs, "trailing thought", 10);
    // Trailing thinking is still open. finalize must close it, and since the
    // derived TEXT still equals "body" the segments remain authoritative.
    const finalized = finalizedSegmentsForMessage(segs, "body", 55);
    expect(finalized).toBeDefined();
    const lastThinking = finalized!.find((s) => s.kind === "thinking");
    expect(lastThinking && lastThinking.kind === "thinking" && lastThinking.closedAt).toBe(55);
  });
});
