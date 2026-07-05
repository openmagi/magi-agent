import { describe, it, expect } from "vitest";
import {
  appendSegmentText,
  appendSegmentThinking,
  appendSegmentTool,
  closeOpenThinking,
  deriveContentFromSegments,
  deriveThinkingFromSegments,
  deriveToolIdsFromSegments,
  segmentsMatchContent,
} from "./transcript-segments";
import type { TranscriptSegment } from "./types";

describe("transcript segment builder", () => {
  it("coalesces consecutive text into one text segment", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentText(segs, "Hello");
    segs = appendSegmentText(segs, " world");
    expect(segs).toEqual([{ kind: "text", text: "Hello world" }]);
  });

  it("coalesces reasoning into an open thinking segment", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentThinking(segs, "Let me ", 100);
    segs = appendSegmentThinking(segs, "think.", 200);
    expect(segs).toEqual([
      { kind: "thinking", text: "Let me think.", openedAt: 100 },
    ]);
  });

  it("builds true chronological think -> tool -> think -> tool -> text order", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentThinking(segs, "plan", 10);
    segs = appendSegmentTool(segs, "call-1", 20);
    segs = appendSegmentThinking(segs, "reconsider", 30);
    segs = appendSegmentTool(segs, "call-2", 40);
    segs = appendSegmentText(segs, "Done.", 50);

    expect(segs!.map((s) => s.kind)).toEqual([
      "thinking",
      "tool",
      "thinking",
      "tool",
      "text",
    ]);
    // First thinking phase closed at the tool boundary; second closed at text.
    const first = segs![0];
    const third = segs![2];
    expect(first.kind === "thinking" && first.closedAt).toBe(20);
    expect(third.kind === "thinking" && third.closedAt).toBe(40);
  });

  it("opens a NEW thinking segment after a tool (does not reopen the closed one)", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentThinking(segs, "first", 10);
    segs = appendSegmentTool(segs, "call-1", 20);
    segs = appendSegmentThinking(segs, "second", 30);
    const thinking = segs!.filter((s) => s.kind === "thinking");
    expect(thinking).toHaveLength(2);
    expect(deriveThinkingFromSegments(segs)).toBe("firstsecond");
  });

  it("dedupes repeated references to the same trailing tool id", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentTool(segs, "call-1", 10); // tool_start
    segs = appendSegmentTool(segs, "call-1", 20); // tool_progress
    segs = appendSegmentTool(segs, "call-1", 30); // tool_end
    expect(deriveToolIdsFromSegments(segs)).toEqual(["call-1"]);
  });

  it("keeps distinct consecutive tools as separate segments", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentTool(segs, "call-1", 10);
    segs = appendSegmentTool(segs, "call-2", 20);
    expect(deriveToolIdsFromSegments(segs)).toEqual(["call-1", "call-2"]);
  });

  it("a bare text delta after thinking implicitly closes the thinking phase", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentThinking(segs, "hmm", 10);
    segs = appendSegmentText(segs, "answer", 20);
    const first = segs![0];
    expect(first.kind === "thinking" && first.closedAt).toBe(20);
    expect(segs![1]).toEqual({ kind: "text", text: "answer" });
  });

  it("closeOpenThinking is a no-op when the tail is not open thinking", () => {
    const segs: TranscriptSegment[] = [{ kind: "text", text: "x" }];
    expect(closeOpenThinking(segs, 99)).toBe(segs);
  });

  it("derives content, thinking, and tool ids equal to the flat fields", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentThinking(segs, "reason", 10);
    segs = appendSegmentTool(segs, "t1", 20);
    segs = appendSegmentText(segs, "Hello ", 30);
    segs = appendSegmentText(segs, "there", 40);
    expect(deriveContentFromSegments(segs)).toBe("Hello there");
    expect(deriveThinkingFromSegments(segs)).toBe("reason");
    expect(deriveToolIdsFromSegments(segs)).toEqual(["t1"]);
  });

  it("ignores empty appends", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentText(segs, "");
    segs = appendSegmentThinking(segs, "");
    segs = appendSegmentTool(segs, "");
    expect(segs).toEqual([]);
  });
});

describe("segmentsMatchContent (content-authority check)", () => {
  it("returns true when derived text equals content", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentText(segs, "The answer");
    expect(segmentsMatchContent(segs, "The answer")).toBe(true);
  });

  it("returns false when content was mutated after capture", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentText(segs, "partial");
    // e.g. a snapshot-repair replaced the visible text or appended a suffix.
    expect(segmentsMatchContent(segs, "partial [interrupted]")).toBe(false);
  });

  it("returns false for empty/absent segments", () => {
    expect(segmentsMatchContent(undefined, "")).toBe(false);
    expect(segmentsMatchContent([], "")).toBe(false);
  });

  it("thinking and tool segments do not affect text authority", () => {
    let segs: TranscriptSegment[] | undefined;
    segs = appendSegmentThinking(segs, "noise", 1);
    segs = appendSegmentTool(segs, "call-1", 2);
    segs = appendSegmentText(segs, "body", 3);
    expect(segmentsMatchContent(segs, "body")).toBe(true);
  });
});
