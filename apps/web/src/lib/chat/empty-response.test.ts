import { describe, expect, it } from "vitest";
import { shouldRetryEmptyCompletion } from "./empty-response";
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
