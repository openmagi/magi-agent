import { describe, expect, it } from "vitest";
import {
  decodeHistoryPlaintext,
  encodeHistoryPlaintext,
} from "./history-envelope";
import type { ToolActivity, TranscriptSegment } from "./types";

const activities: ToolActivity[] = [
  { id: "call-1", label: "Grep", status: "done", startedAt: 0, durationMs: 12 },
];

const segments: TranscriptSegment[] = [
  { kind: "thinking", text: "let me look", openedAt: 111, closedAt: 222 },
  { kind: "tool", toolId: "call-1" },
  { kind: "text", text: "Found it." },
];

describe("history envelope segments (OPTION A: optional key, no version bump)", () => {
  it("round-trips segments inside a v4 envelope", () => {
    const raw = encodeHistoryPlaintext({
      role: "assistant",
      content: "Found it.",
      thinkingContent: "let me look",
      activities,
      segments,
    });
    // v4 because activities are present; segments ride as an optional key.
    expect(raw).toContain('"_v":4');
    expect(raw).toContain('"segments"');

    const decoded = decodeHistoryPlaintext("assistant", raw);
    expect(decoded.content).toBe("Found it.");
    expect(decoded.segments).toEqual([
      // openedAt is not persisted; re-synthesized to 0 on decode.
      { kind: "thinking", text: "let me look", openedAt: 0 },
      { kind: "tool", toolId: "call-1" },
      { kind: "text", text: "Found it." },
    ]);
    // Tool segment id still resolves against the persisted activities.
    expect(decoded.activities?.[0].id).toBe("call-1");
  });

  it("does NOT bump the version: segments can ride in a v2 (thinking-only) envelope", () => {
    const raw = encodeHistoryPlaintext({
      role: "assistant",
      content: "hi",
      thinkingContent: "reason",
      segments: [
        { kind: "thinking", text: "reason", openedAt: 5 },
        { kind: "text", text: "hi" },
      ],
    });
    expect(raw).toContain('"_v":2');
    expect(raw).toContain('"segments"');
    const decoded = decodeHistoryPlaintext("assistant", raw);
    expect(decoded.segments?.map((s) => s.kind)).toEqual(["thinking", "text"]);
  });

  it("drops decoded segments whose derived text does not match content (stale guard)", () => {
    // Hand-craft an envelope where the segment text disagrees with content.
    const raw = JSON.stringify({
      _v: 2,
      content: "the real body",
      thinking: "x",
      segments: [{ k: "text", t: "a different body" }],
    });
    const decoded = decodeHistoryPlaintext("assistant", raw);
    expect(decoded.content).toBe("the real body");
    expect(decoded.segments).toBeUndefined();
  });

  it("old-style envelope without segments decodes fine (back-compat)", () => {
    // A pre-upgrade v4 envelope has no segments key.
    const raw = JSON.stringify({
      _v: 4,
      content: "answer",
      thinking: "t",
      activities: [{ id: "call-1", label: "Grep", status: "done" }],
    });
    const decoded = decodeHistoryPlaintext("assistant", raw);
    expect(decoded.content).toBe("answer");
    expect(decoded.segments).toBeUndefined();
    expect(decoded.activities?.[0].id).toBe("call-1");
  });

  it("an upgraded reader ignores segments in an unknown-shape gracefully", () => {
    const raw = JSON.stringify({
      _v: 4,
      content: "answer",
      segments: "not-an-array",
    });
    const decoded = decodeHistoryPlaintext("assistant", raw);
    expect(decoded.content).toBe("answer");
    expect(decoded.segments).toBeUndefined();
  });

  it("does not emit a segments key when none are provided", () => {
    const raw = encodeHistoryPlaintext({
      role: "assistant",
      content: "answer",
      thinkingContent: "t",
    });
    expect(raw).not.toContain('"segments"');
  });
});
