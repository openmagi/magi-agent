import { describe, expect, it } from "vitest";
import {
  shouldDrainQueueAfterTurn,
  shouldForceReleaseStaleTransientConnection,
  shouldReleaseMissingActiveSnapshot,
  shouldRetryEmptyCompletion,
} from "./empty-response";
import type { ChannelState } from "./types";

function state(overrides: Partial<ChannelState> = {}): ChannelState {
  return {
    streaming: true,
    streamingText: "",
    thinkingText: "",
    error: null,
    hasTextContent: false,
    thinkingStartedAt: 123,
    turnPhase: "pending",
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

  it("does not retry after runtime turn events prove server-side work started", () => {
    expect(shouldRetryEmptyCompletion(state({ turnPhase: "executing" }), 0, 8)).toBe(false);
    expect(shouldRetryEmptyCompletion(state({ turnPhase: "committed" }), 0, 8)).toBe(false);
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

describe("missing active snapshot release policy", () => {
  it("releases a transient connecting retry state when no server snapshot exists", () => {
    expect(
      shouldReleaseMissingActiveSnapshot(
        state({
          error: "Connecting to bot... (2/8)",
          reconnecting: false,
        }),
        false,
      ),
    ).toBe(true);
  });

  it("does not release a fresh stream before a snapshot has appeared", () => {
    expect(
      shouldReleaseMissingActiveSnapshot(
        state({
          error: null,
          reconnecting: false,
        }),
        false,
      ),
    ).toBe(false);
  });

  it("releases reconnecting recovery once the server snapshot is gone", () => {
    expect(
      shouldReleaseMissingActiveSnapshot(
        state({
          reconnecting: true,
          streamingText: "partial answer",
          hasTextContent: true,
        }),
        false,
      ),
    ).toBe(true);
  });
});

describe("stale transient connection release policy", () => {
  it("releases an old connecting retry with no visible run progress", () => {
    expect(
      shouldForceReleaseStaleTransientConnection(
        state({
          error: "Connecting to bot... (7/8)",
          thinkingStartedAt: 1_000,
        }),
        62_000,
      ),
    ).toBe(true);
  });

  it("keeps a fresh connecting retry while retry scheduling is still active", () => {
    expect(
      shouldForceReleaseStaleTransientConnection(
        state({
          error: "Connecting to bot... (2/8)",
          thinkingStartedAt: 1_000,
        }),
        6_000,
      ),
    ).toBe(false);
  });

  it("keeps transient connection state when non-text work is visible", () => {
    expect(
      shouldForceReleaseStaleTransientConnection(
        state({
          error: "Connecting to bot... (7/8)",
          thinkingStartedAt: 1_000,
          activeTools: [{
            id: "tool-1",
            label: "Browser",
            status: "running",
            startedAt: 1_000,
          }],
        }),
        62_000,
      ),
    ).toBe(false);
  });

  it("releases an old reconnecting state with no visible run progress", () => {
    expect(
      shouldForceReleaseStaleTransientConnection(
        state({
          error: null,
          reconnecting: true,
          thinkingStartedAt: 1_000,
        }),
        62_000,
      ),
    ).toBe(true);
  });

  it("keeps a fresh reconnecting state while snapshot recovery is still active", () => {
    expect(
      shouldForceReleaseStaleTransientConnection(
        state({
          error: null,
          reconnecting: true,
          thinkingStartedAt: 1_000,
        }),
        6_000,
      ),
    ).toBe(false);
  });
});

describe("queue drain policy after a finalized turn", () => {
  it("drains the queue after a turn that produced a final answer", () => {
    expect(
      shouldDrainQueueAfterTurn(state({ streamingText: "the answer", hasTextContent: true })),
    ).toBe(true);
  });

  it("drains the queue after a truly empty turn with no work (nothing to continue)", () => {
    expect(shouldDrainQueueAfterTurn(state())).toBe(true);
  });

  it("does NOT drain when the turn ended with tool work but no final answer text", () => {
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

  it("does NOT drain when server-side turn work started but no final text arrived", () => {
    expect(shouldDrainQueueAfterTurn(state({ turnPhase: "executing" }))).toBe(false);
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

  it("does NOT drain when the turn ended with an error and no answer text", () => {
    // Repro for the dashboard symptom: after a turn ends with an error (e.g.
    // litellm "Unsupported value: reasoning_effort='max'"), the next queued
    // user message must NOT be drained — otherwise every backlogged message is
    // dispatched at once against the broken session and the bot replies to a
    // past message instead of the latest one.
    expect(
      shouldDrainQueueAfterTurn(
        state({ error: "litellm.BadRequestError: Unsupported value: 'reasoning_effort'" }),
      ),
    ).toBe(false);
  });

  it("does NOT drain when turnPhase committed to aborted with no answer text", () => {
    // Belt-and-suspenders: an explicitly aborted turn (different from an error
    // string) is also a mid-task stop from the queue's perspective.
    expect(shouldDrainQueueAfterTurn(state({ turnPhase: "aborted" }))).toBe(false);
  });
});
