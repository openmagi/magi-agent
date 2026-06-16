import { describe, expect, it } from "vitest";
import {
  appendLiveTranscriptText,
  replaceLiveTranscriptText,
  upsertLiveTranscriptWorkRows,
} from "./live-transcript";
import type { LiveTranscriptItem } from "@/chat-core";
import type { WorkConsoleRow } from "./work-console";

describe("live transcript helpers", () => {
  it("coalesces adjacent streamed text chunks in receive order", () => {
    const first = appendLiveTranscriptText(undefined, "Hel", 100);
    const second = appendLiveTranscriptText(first, "lo", 110);

    expect(second).toEqual([
      {
        id: first[0]?.id,
        kind: "text",
        content: "Hello",
        receivedAt: 110,
      },
    ]);
  });

  it("upserts work rows without moving their original transcript position", () => {
    const items: LiveTranscriptItem[] = replaceLiveTranscriptText("Thinking", 100);
    const row: WorkConsoleRow = {
      id: "tool:grep",
      group: "tool",
      label: "Grep",
      detail: "searching",
      status: "running",
    };

    const inserted = upsertLiveTranscriptWorkRows(items, [row], 120);
    const updated = upsertLiveTranscriptWorkRows(inserted, [{ ...row, detail: "done", status: "done" }], 140);

    expect(updated).toHaveLength(2);
    expect(updated[0]?.kind).toBe("text");
    expect(updated[1]).toMatchObject({
      kind: "work",
      rowId: "tool:grep",
      label: "Grep",
      detail: "done",
      status: "done",
      receivedAt: 120,
    });
  });
});
