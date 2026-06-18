import { describe, expect, it } from "vitest";
import { shouldDrainQueueAfterTurn, shouldRetryEmptyCompletion } from "./empty-response";
import type { ChannelState } from "./types";

function state(overrides: Partial<ChannelState> = {}): ChannelState {
  return {
    streaming: true,
    streamingText: "",
    thinkingText: "",
    error: null,
    hasTextContent: false,
    thinkingStartedAt: 123,
    turnPhase: "committing",
    heartbeatElapsedMs: null,
    currentGoal: null,
    pendingInjectionCount: 0,
    activeTools: [],
    browserFrame: null,
    documentDraft: null,
    subagents: [],
    taskBoard: null,
    missions: [],
    activeGoalMissionId: null,
    inspectedSources: [],
    citationGate: null,
    fileProcessing: false,
    ...overrides,
  };
}

describe("empty response retry policy", () => {
  it("retries an empty completion before non-text work starts", () => {
    expect(shouldRetryEmptyCompletion(state(), 0, 8)).toBe(true);
  });

  it("does not retry empty completions after tool work starts", () => {
    expect(
      shouldRetryEmptyCompletion(
        state({
          activeTools: [{
            id: "tool-1",
            label: "Browser",
            status: "done",
            startedAt: 1,
          }],
        }),
        0,
        8,
      ),
    ).toBe(false);
  });

  it("does not retry empty completions after thinking text arrives", () => {
    expect(shouldRetryEmptyCompletion(state({ thinkingText: "reasoning" }), 0, 8)).toBe(false);
  });

  it("does not retry empty completions while a document draft is streaming", () => {
    expect(
      shouldRetryEmptyCompletion(
        state({
          documentDraft: {
            id: "tu_doc",
            filename: "docs/report.md",
            format: "md",
            status: "streaming",
            contentPreview: "# Draft",
            contentLength: 7,
            truncated: false,
            updatedAt: 123,
          },
        }),
        0,
        8,
      ),
    ).toBe(false);
  });

  it("stops retrying once the retry budget is exhausted", () => {
    expect(shouldRetryEmptyCompletion(state(), 8, 8)).toBe(false);
  });
});

describe("queue drain policy after a finalized turn", () => {
  it("drains the queue after a turn that produced a final answer", () => {
    expect(shouldDrainQueueAfterTurn(state({ streamingText: "the answer", hasTextContent: true }))).toBe(true);
  });

  it("drains the queue after a truly empty turn with no work (nothing to continue)", () => {
    expect(shouldDrainQueueAfterTurn(state())).toBe(true);
  });

  it("does NOT drain the queue when the turn ended with work but no final answer text", () => {
    // The mid-task stop: draining here would feed the next (newer) queued
    // message into the SAME unfinished backend task, so old work surfaces as a
    // reply to the new message.
    expect(
      shouldDrainQueueAfterTurn(
        state({
          activeTools: [{ id: "tool-1", label: "Bash", status: "done", startedAt: 1 }],
        }),
      ),
    ).toBe(false);
  });

  it("does NOT drain when subagents ran but no final text arrived", () => {
    expect(
      shouldDrainQueueAfterTurn(
        state({
          subagents: [{ id: "s1", label: "child", status: "completed", startedAt: 1 }],
        }),
      ),
    ).toBe(false);
  });

  it("drains when work happened AND a final answer text arrived", () => {
    expect(
      shouldDrainQueueAfterTurn(
        state({
          streamingText: "done: 2",
          hasTextContent: true,
          activeTools: [{ id: "tool-1", label: "Bash", status: "done", startedAt: 1 }],
        }),
      ),
    ).toBe(true);
  });
});
