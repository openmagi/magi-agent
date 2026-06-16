import { describe, it, expect } from "vitest";
import { appendLiveTranscriptText, upsertLiveTranscriptWorkRows } from "./live-transcript";
import type { LiveTranscriptItem } from "./types";

describe("appendLiveTranscriptText", () => {
  it("coalesces consecutive text deltas into one item", () => {
    let items: LiveTranscriptItem[] = [];
    items = appendLiveTranscriptText(items, "Hello", 1);
    items = appendLiveTranscriptText(items, " world", 2);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: "text", content: "Hello world" });
  });

  it("keeps a turn's text in a single item even when work rows interleave between tokens", () => {
    // Reproduces the hosted-web bug: during a tool/trace-heavy phase a fresh
    // runtime-trace work row (unique rowId) is pushed between nearly every token.
    // Before the fix this shattered the sentence into one text item per word,
    // and InlineLiveTranscript rendered each word on its own line.
    const words = "Web Search tool failed. Let me try using WebFetch".split(" ");
    let items: LiveTranscriptItem[] = [];
    let t = 1;
    words.forEach((w, i) => {
      items = appendLiveTranscriptText(items, (i === 0 ? "" : " ") + w, t++);
      items = upsertLiveTranscriptWorkRows(
        items,
        [
          {
            id: `trace:turn:${t}:retry_scheduled`,
            group: "trace",
            label: "Searching the web",
            status: "running",
          },
        ],
        t++,
      );
    });

    const textItems = items.filter((it) => it.kind === "text");
    expect(textItems).toHaveLength(1);
    expect(textItems[0]).toMatchObject({
      kind: "text",
      content: "Web Search tool failed. Let me try using WebFetch",
    });
  });

  it("floats the text block to the tail after each delta so the cursor stays at the bottom", () => {
    let items: LiveTranscriptItem[] = appendLiveTranscriptText([], "First", 1);
    items = upsertLiveTranscriptWorkRows(
      items,
      [{ id: "trace:1", group: "trace", label: "working", status: "running" }],
      2,
    );
    // A work row now sits after the text; the next delta should pull text back to the tail.
    items = appendLiveTranscriptText(items, " second", 3);
    expect(items[items.length - 1]).toMatchObject({ kind: "text", content: "First second" });
  });

  it("preserves the text item when many work rows would overflow the trim cap", () => {
    let items: LiveTranscriptItem[] = appendLiveTranscriptText([], "Answer body", 1);
    for (let i = 0; i < 300; i += 1) {
      items = upsertLiveTranscriptWorkRows(
        items,
        [{ id: `trace:${i}`, group: "trace", label: `step ${i}`, status: "running" }],
        i + 2,
      );
    }
    const textItems = items.filter((it) => it.kind === "text");
    expect(textItems).toHaveLength(1);
    expect(textItems[0]).toMatchObject({ content: "Answer body" });
    expect(items).toHaveLength(120);
    // Oldest work rows are trimmed; the newest survive.
    const workRowIds = items.filter((it) => it.kind === "work").map((it) => (it as { rowId: string }).rowId);
    expect(workRowIds).toContain("trace:299");
    expect(workRowIds).not.toContain("trace:0");
  });

  it("starts a text item after leading work rows and merges later deltas into it", () => {
    let items: LiveTranscriptItem[] = [];
    items = upsertLiveTranscriptWorkRows(
      items,
      [{ id: "tool:read", group: "tool", label: "Reading", status: "running" }],
      1,
    );
    items = appendLiveTranscriptText(items, "First", 2);
    items = upsertLiveTranscriptWorkRows(
      items,
      [{ id: "tool:cpu", group: "tool", label: "CPU", status: "running" }],
      3,
    );
    items = appendLiveTranscriptText(items, " second", 4);
    const textItems = items.filter((it) => it.kind === "text");
    expect(textItems).toHaveLength(1);
    expect(textItems[0]).toMatchObject({ content: "First second" });
  });
});
