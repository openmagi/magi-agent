import { describe, it, expect } from "vitest";
import { streamStateToChannelState } from "./stream-state-to-channel-state";
import {
  foldRuntimeEvents,
  initialStreamChatState,
  type StreamChatState,
  type ToolCardState,
} from "./stream-chat-reducer";

function makeToolCard(overrides: Partial<ToolCardState> = {}): ToolCardState {
  return {
    id: "t1",
    name: "BashTool",
    inputPreview: "echo hi",
    status: "running",
    outputPreview: null,
    durationMs: null,
    kind: "tool",
    rejected: false,
    ...overrides,
  };
}

function withTools(cards: ToolCardState[]): StreamChatState {
  const state = initialStreamChatState();
  for (const card of cards) state.tools.set(card.id, card);
  return state;
}

describe("streamStateToChannelState", () => {
  it("maps streaming flag + assistant/thinking text", () => {
    const state: StreamChatState = {
      ...initialStreamChatState(),
      streaming: true,
      assistantText: "hello world",
      thinkingText: "thinking...",
    };
    const cs = streamStateToChannelState(state);
    expect(cs.streaming).toBe(true);
    expect(cs.streamingText).toBe("hello world");
    expect(cs.thinkingText).toBe("thinking...");
    expect(cs.hasTextContent).toBe(true);
    expect(cs.error).toBeNull();
  });

  it("hasTextContent is false when there is no assistant text", () => {
    const cs = streamStateToChannelState(initialStreamChatState());
    expect(cs.hasTextContent).toBe(false);
    expect(cs.streamingText).toBe("");
    expect(cs.streaming).toBe(false);
  });

  it("projects tools Map → activeTools[] preserving id/label/preview", () => {
    const cs = streamStateToChannelState(
      withTools([
        makeToolCard({ id: "a", name: "BashTool", inputPreview: "ls" }),
        makeToolCard({ id: "b", name: "ReadFile", inputPreview: "x.ts", status: "running" }),
      ]),
    );
    expect(cs.activeTools).toHaveLength(2);
    expect(cs.activeTools?.[0]).toMatchObject({
      id: "a",
      label: "BashTool",
      status: "running",
      inputPreview: "ls",
    });
    expect(cs.activeTools?.[1]).toMatchObject({ id: "b", label: "ReadFile" });
  });

  it("maps tool status: done / error / denied", () => {
    const cs = streamStateToChannelState(
      withTools([
        makeToolCard({ id: "done", status: "success", rejected: false }),
        makeToolCard({ id: "err", status: "error", rejected: true }),
        makeToolCard({ id: "deny", status: "denied", rejected: true }),
      ]),
    );
    const byId = Object.fromEntries(
      (cs.activeTools ?? []).map((t) => [t.id, t.status]),
    );
    expect(byId.done).toBe("done");
    expect(byId.err).toBe("error");
    expect(byId.deny).toBe("denied");
  });

  it("includes outputPreview + durationMs when present", () => {
    const cs = streamStateToChannelState(
      withTools([
        makeToolCard({ id: "a", outputPreview: "ok", durationMs: 123, status: "success" }),
      ]),
    );
    expect(cs.activeTools?.[0]).toMatchObject({
      outputPreview: "ok",
      durationMs: 123,
    });
  });

  it("maps known phase strings to the turnPhase enum", () => {
    const state = { ...initialStreamChatState(), phase: { phase: "planning", label: null, detail: null } };
    expect(streamStateToChannelState(state).turnPhase).toBe("planning");
  });

  it("normalizes synonym phases and falls back to null", () => {
    const exec = { ...initialStreamChatState(), phase: { phase: "execute", label: null, detail: null } };
    expect(streamStateToChannelState(exec).turnPhase).toBe("executing");
    const preparing = { ...initialStreamChatState(), phase: { phase: "preparing", label: null, detail: null } };
    expect(streamStateToChannelState(preparing).turnPhase).toBe("pending");
    const unknown = { ...initialStreamChatState(), phase: { phase: "weird", label: null, detail: null } };
    expect(streamStateToChannelState(unknown).turnPhase).toBeNull();
    expect(streamStateToChannelState(initialStreamChatState()).turnPhase).toBeNull();
  });

  it("provides inert defaults for untracked panel fields", () => {
    const cs = streamStateToChannelState(initialStreamChatState());
    expect(cs.subagents).toEqual([]);
    expect(cs.missions).toEqual([]);
    expect(cs.taskBoard).toBeNull();
    expect(cs.activeTools).toEqual([]);
    expect(cs.liveTranscriptItems).toEqual([]);
  });

  it("projects runtime work events into the legacy Work panel state", () => {
    const state = foldRuntimeEvents([
      {
        type: "task_board",
        tasks: [
          {
            id: "task-1",
            title: "Verify sources",
            description: "Check public reports",
            status: "in_progress",
          },
        ],
      },
      {
        type: "source_inspected",
        source: {
          sourceId: "src-1",
          kind: "web_fetch",
          uri: "https://example.com/report",
          inspectedAt: 1_779_206_400_000,
        },
      },
      {
        type: "runtime_trace",
        turnId: "turn-1",
        phase: "retry_scheduled",
        severity: "warning",
        title: "Verifier retry",
      },
      { type: "child_started", taskId: "child-1", role: "research", detail: "Checking sources" },
      { type: "child_tool_request", taskId: "child-1", toolName: "WebFetch" },
    ]);

    const cs = streamStateToChannelState(state);

    expect(cs.taskBoard?.tasks[0]).toMatchObject({
      id: "task-1",
      title: "Verify sources",
      description: "Check public reports",
      status: "in_progress",
    });
    expect(cs.inspectedSources?.[0]).toMatchObject({
      sourceId: "src-1",
      uri: "https://example.com/report",
    });
    expect(cs.runtimeTraces?.[0]).toMatchObject({
      turnId: "turn-1",
      phase: "retry_scheduled",
      title: "Verifier retry",
    });
    expect(cs.subagents?.[0]).toMatchObject({
      taskId: "child-1",
      role: "research",
      status: "waiting",
      detail: "WebFetch",
    });
    expect(cs.streaming).toBe(true);
  });

  it("projects model progress and heartbeat events into active work rows", () => {
    const state = foldRuntimeEvents([
      {
        type: "llm_progress",
        turnId: "turn-1",
        iter: 0,
        stage: "waiting",
        label: "Collecting sources",
        detail: "Checking public web results",
        elapsedMs: 12_000,
      },
    ]);

    const cs = streamStateToChannelState(state);

    expect(cs.streaming).toBe(true);
    expect(cs.heartbeatElapsedMs).toBe(12_000);
    expect(cs.activeTools?.[0]).toMatchObject({
      id: "llm:turn-1:0",
      label: "ModelProgress",
      status: "running",
      inputPreview: JSON.stringify({
        stage: "waiting",
        label: "Collecting sources",
        detail: "Checking public web results",
        elapsedMs: 12_000,
      }),
    });
  });

  it("integrates with a real folded SSE stream (text + tool)", () => {
    const state = foldRuntimeEvents([
      { type: "text_delta", delta: "Answer: " },
      { type: "tool_start", id: "x", name: "BashTool", input_preview: "echo" },
      { type: "text_delta", delta: "done." },
    ]);
    const cs = streamStateToChannelState(state);
    expect(cs.streamingText).toBe("Answer: done.");
    expect(cs.streaming).toBe(true);
    expect(cs.activeTools?.[0]).toMatchObject({ id: "x", label: "BashTool" });
  });
});
