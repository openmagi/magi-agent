import { describe, expect, it } from "vitest";

import {
  foldRuntimeEvent,
  foldRuntimeEvents,
  initialStreamChatState,
} from "./stream-chat-reducer";
import {
  deriveContentFromSegments,
  deriveThinkingFromSegments,
  deriveToolIdsFromSegments,
} from "./transcript-segments";
import { streamStateToChannelState } from "./stream-state-to-channel-state";

describe("stream-chat-reducer interleaved segments", () => {
  it("builds ordered think -> tool -> think -> tool -> text segments", () => {
    const state = foldRuntimeEvents([
      { type: "thinking_delta", delta: "let me plan" },
      { type: "tool_start", id: "call-1", name: "Grep" },
      { type: "tool_end", id: "call-1", status: "ok" },
      { type: "thinking_delta", delta: "now edit" },
      { type: "tool_start", id: "call-2", name: "Edit" },
      { type: "tool_end", id: "call-2", status: "ok" },
      { type: "text_delta", delta: "All done." },
    ]);

    expect(state.segments.map((s) => s.kind)).toEqual([
      "thinking",
      "tool",
      "thinking",
      "tool",
      "text",
    ]);
    expect(deriveToolIdsFromSegments(state.segments)).toEqual(["call-1", "call-2"]);
    expect(deriveThinkingFromSegments(state.segments)).toBe("let me plannow edit");
    expect(deriveContentFromSegments(state.segments)).toBe("All done.");
  });

  it("keeps segments derived-equal to the flat assistantText/thinkingText", () => {
    const state = foldRuntimeEvents([
      { type: "text_delta", delta: "Hello " },
      { type: "thinking_delta", delta: "wait" },
      { type: "text_delta", delta: "world" },
    ]);
    expect(deriveContentFromSegments(state.segments)).toBe(state.assistantText);
    expect(deriveThinkingFromSegments(state.segments)).toBe(state.thinkingText);
  });

  it("does not create a segment for a synthetic model-progress card", () => {
    const state = foldRuntimeEvent(initialStreamChatState(), {
      type: "llm_progress",
      turnId: "t1",
      iter: 0,
      stage: "waiting",
    });
    // A model-progress tool card exists but produces no ordered tool segment.
    expect(state.tools.size).toBe(1);
    expect(state.segments).toEqual([]);
  });

  it("coalesces text_delta bursts and dedupes tool lifecycle events", () => {
    const state = foldRuntimeEvents([
      { type: "tool_start", id: "call-1", name: "Bash" },
      { type: "tool_progress", id: "call-1", status: "running" },
      { type: "tool_end", id: "call-1", status: "ok" },
      { type: "text_delta", delta: "a" },
      { type: "text_delta", delta: "b" },
      { type: "text_delta", delta: "c" },
    ]);
    expect(deriveToolIdsFromSegments(state.segments)).toEqual(["call-1"]);
    const textSegments = state.segments.filter((s) => s.kind === "text");
    expect(textSegments).toHaveLength(1);
    expect(deriveContentFromSegments(state.segments)).toBe("abc");
  });

  it("orders text before a following tool (flush-before-tool)", () => {
    const state = foldRuntimeEvents([
      { type: "text_delta", delta: "Looking..." },
      { type: "tool_start", id: "call-1", name: "Grep" },
    ]);
    expect(state.segments.map((s) => s.kind)).toEqual(["text", "tool"]);
  });

  it("bridges segments onto ChannelState with matching tool ids", () => {
    const state = foldRuntimeEvents([
      { type: "thinking_delta", delta: "hmm" },
      { type: "tool_start", id: "call-1", name: "Grep" },
      { type: "tool_end", id: "call-1", status: "ok" },
      { type: "text_delta", delta: "done" },
    ]);
    const channel = streamStateToChannelState(state);
    expect(channel.segments?.map((s) => s.kind)).toEqual([
      "thinking",
      "tool",
      "text",
    ]);
    // The tool segment id resolves against activeTools.
    const toolIds = deriveToolIdsFromSegments(channel.segments);
    expect(channel.activeTools?.some((a) => a.id === toolIds[0])).toBe(true);
  });
});
