import { describe, expect, it } from "vitest";
import {
  channelStateFromActiveSnapshot,
  isLiveActiveSnapshot,
  shouldApplyActiveSnapshotAfterReset,
  shouldHydrateFromSnapshot,
  shouldReleaseStaleEmptyActiveSnapshot,
} from "./active-snapshot";
import type { ChatMessage } from "./types";

describe("active snapshot recovery", () => {
  it("treats metadata-only snapshots as visible current work without a reconnect banner", () => {
    const snapshot = {
      turnId: "turn-1",
      status: "running" as const,
      content: "",
      thinking: "",
      startedAt: 1000,
      updatedAt: 1000,
      turnPhase: "executing" as const,
      heartbeatElapsedMs: 30000,
      pendingInjectionCount: 1,
      activeTools: [
        { id: "tool-1", label: "FileRead", status: "running" as const, startedAt: 1000 },
      ],
      subagents: [
        {
          taskId: "blue",
          role: "explore",
          status: "running" as const,
          detail: "Searching sources",
          startedAt: 1000,
          updatedAt: 1100,
        },
      ],
      taskBoard: {
        receivedAt: 1000,
        tasks: [
          { id: "task-1", title: "Inspect runtime", description: "", status: "in_progress" as const },
        ],
      },
      missions: [
        { id: "m1", title: "Draft report", kind: "goal", status: "running" as const, updatedAt: 1000 },
      ],
      activeGoalMissionId: "m1",
      inspectedSources: [
        {
          sourceId: "src_1",
          kind: "subagent_result" as const,
          uri: "child-agent://bull-case",
          title: "Bull case partner",
          inspectedAt: 1000,
        },
      ],
      citationGate: {
        ruleId: "claim-citation-gate" as const,
        verdict: "pending" as const,
        checkedAt: 1000,
      },
    };

    expect(isLiveActiveSnapshot(snapshot)).toBe(true);
    expect(channelStateFromActiveSnapshot(snapshot, undefined)).toMatchObject({
      streaming: true,
      streamingText: "",
      thinkingText: "",
      hasTextContent: false,
      reconnecting: false,
      turnPhase: "executing",
      heartbeatElapsedMs: 30000,
      pendingInjectionCount: 1,
      activeTools: snapshot.activeTools,
      subagents: snapshot.subagents,
      taskBoard: snapshot.taskBoard,
      missions: snapshot.missions,
      activeGoalMissionId: "m1",
      inspectedSources: snapshot.inspectedSources,
      citationGate: snapshot.citationGate,
    });
  });

  it("rehydrates detached background work without marking the parent stream active", () => {
    const snapshot = {
      turnId: "turn-1",
      status: "running" as const,
      detached: true,
      content: "",
      thinking: "",
      startedAt: 1000,
      updatedAt: 2000,
      subagents: [
        {
          taskId: "task-running",
          role: "writer",
          status: "running" as const,
          detail: "Drafting chapter 4",
          startedAt: 1000,
          updatedAt: 2000,
        },
      ],
    };

    expect(isLiveActiveSnapshot(snapshot)).toBe(true);
    expect(channelStateFromActiveSnapshot(snapshot, undefined)).toMatchObject({
      streaming: false,
      streamingText: "",
      thinkingText: "",
      reconnecting: false,
      subagents: snapshot.subagents,
    });
  });

  it("does not treat terminal parent snapshots as live active work", () => {
    for (const turnPhase of ["aborted", "committed"] as const) {
      expect(
        isLiveActiveSnapshot({
          turnId: `turn-${turnPhase}`,
          status: "running",
          content: "The selected Python runtime is blocked.",
          thinking: "",
          startedAt: 1000,
          updatedAt: 2000,
          turnPhase,
        }),
      ).toBe(false);
    }
  });

  it("does not reintroduce reconnecting when a sparse snapshot preserves existing work", () => {
    const existingSubagent = {
      taskId: "task-running",
      role: "writer",
      status: "running" as const,
      detail: "Drafting chapter 4",
      startedAt: 1000,
      updatedAt: 2000,
    };
    const snapshot = {
      turnId: "turn-1",
      status: "running" as const,
      content: "",
      thinking: "",
      startedAt: 1000,
      updatedAt: 3000,
    };

    expect(channelStateFromActiveSnapshot(snapshot, {
      streaming: true,
      streamingText: "",
      thinkingText: "",
      error: null,
      reconnecting: false,
      subagents: [existingSubagent],
    })).toMatchObject({
      reconnecting: false,
      subagents: [existingSubagent],
    });
  });

  it("does not regress visible streaming text when a lagging active snapshot arrives", () => {
    const snapshot = {
      turnId: "turn-1",
      status: "running" as const,
      content: "",
      thinking: "",
      startedAt: 1000,
      updatedAt: 3000,
      turnPhase: "executing" as const,
    };

    expect(channelStateFromActiveSnapshot(snapshot, {
      streaming: true,
      streamingText: "Already visible answer text",
      thinkingText: "",
      error: null,
      hasTextContent: true,
    })).toMatchObject({
      streamingText: "Already visible answer text",
      hasTextContent: true,
    });
  });

  it("uses active snapshot text only when it advances the visible stream", () => {
    const baseSnapshot = {
      turnId: "turn-1",
      status: "running" as const,
      thinking: "",
      startedAt: 1000,
      updatedAt: 3000,
      turnPhase: "executing" as const,
    };
    const existing = {
      streaming: true,
      streamingText: "The report is being",
      thinkingText: "",
      error: null,
      hasTextContent: true,
    };

    expect(channelStateFromActiveSnapshot({
      ...baseSnapshot,
      content: "The report",
    }, existing)).toMatchObject({
      streamingText: "The report is being",
      hasTextContent: true,
    });
    expect(channelStateFromActiveSnapshot({
      ...baseSnapshot,
      content: "The report is being written now.",
    }, existing)).toMatchObject({
      streamingText: "The report is being written now.",
      hasTextContent: true,
    });
  });

  it("keeps live transcript text aligned with additive snapshot text", () => {
    const snapshot = {
      turnId: "turn-1",
      status: "running" as const,
      content: "The report is being written now.",
      thinking: "",
      startedAt: 1000,
      updatedAt: 3000,
      turnPhase: "executing" as const,
    };

    const state = channelStateFromActiveSnapshot(snapshot, {
      streaming: true,
      streamingText: "The report is being",
      thinkingText: "",
      error: null,
      hasTextContent: true,
      liveTranscriptItems: [
        {
          id: "text:old",
          kind: "text",
          content: "The report is being",
          receivedAt: 1000,
        },
      ],
    });

    expect(state.streamingText).toBe("The report is being written now.");
    expect(state.liveTranscriptItems).toMatchObject([
      {
        kind: "text",
        content: "The report is being written now.",
        receivedAt: 3000,
      },
    ]);
  });

  it("releases stale empty running snapshots so the composer can start a new turn", () => {
    expect(
      shouldReleaseStaleEmptyActiveSnapshot(
        {
          turnId: "turn-empty",
          status: "running",
          content: "",
          thinking: "",
          startedAt: 1_000,
          updatedAt: 1_000,
        },
        62_000,
      ),
    ).toBe(true);
  });

  it("releases stale empty pending snapshots because pending phase alone is not visible work", () => {
    expect(
      shouldReleaseStaleEmptyActiveSnapshot(
        {
          turnId: "turn-empty",
          status: "running",
          content: "",
          thinking: "",
          startedAt: 1_000,
          updatedAt: 1_000,
          turnPhase: "pending",
        },
        62_000,
      ),
    ).toBe(true);
  });

  it("marks a fresh empty pending snapshot as reconnecting rather than visible progress", () => {
    expect(
      channelStateFromActiveSnapshot(
        {
          turnId: "turn-empty",
          status: "running",
          content: "",
          thinking: "",
          startedAt: 1_000,
          updatedAt: 1_000,
          turnPhase: "pending",
        },
        undefined,
      ),
    ).toMatchObject({
      streaming: true,
      streamingText: "",
      hasTextContent: false,
      reconnecting: true,
      turnPhase: "pending",
    });
  });

  it("keeps fresh empty running snapshots during the initial connection window", () => {
    expect(
      shouldReleaseStaleEmptyActiveSnapshot(
        {
          turnId: "turn-empty",
          status: "running",
          content: "",
          thinking: "",
          startedAt: 1_000,
          updatedAt: 1_000,
        },
        6_000,
      ),
    ).toBe(false);
  });

  it("keeps empty running snapshots when heartbeat evidence is still fresh", () => {
    expect(
      shouldReleaseStaleEmptyActiveSnapshot(
        {
          turnId: "turn-empty",
          status: "running",
          content: "",
          thinking: "",
          startedAt: 1_000,
          updatedAt: 1_000,
          heartbeatElapsedMs: 5_000,
        },
        62_000,
      ),
    ).toBe(false);
  });

  it("keeps running snapshots that contain non-text work evidence", () => {
    expect(
      shouldReleaseStaleEmptyActiveSnapshot(
        {
          turnId: "turn-work",
          status: "running",
          content: "",
          thinking: "",
          startedAt: 1_000,
          updatedAt: 1_000,
          activeTools: [
            { id: "tool-1", label: "FileRead", status: "running", startedAt: 1_000 },
          ],
        },
        62_000,
      ),
    ).toBe(false);
  });

  it("ignores active snapshots from before the latest local reset divider", () => {
    const messages: ChatMessage[] = [
      { id: "old-user", role: "user", content: "old request", timestamp: 1000 },
      { id: "system-reset-1", role: "system", content: "Session ended - new conversation started", timestamp: 2000 },
      { id: "fresh-user", role: "user", content: "new request", timestamp: 3000 },
    ];

    expect(shouldApplyActiveSnapshotAfterReset({
      turnId: "old-turn",
      status: "running",
      content: "old answer",
      thinking: "",
      startedAt: 1500,
      updatedAt: 3500,
    }, messages)).toBe(false);

    expect(shouldApplyActiveSnapshotAfterReset({
      turnId: "new-turn",
      status: "running",
      content: "new answer",
      thinking: "",
      startedAt: 3100,
      updatedAt: 3500,
    }, messages)).toBe(true);
  });

  it("uses a persisted reset boundary even when the local divider is not loaded", () => {
    const messages: ChatMessage[] = [
      { id: "fresh-user", role: "user", content: "new request", timestamp: 3000 },
    ];

    expect(shouldApplyActiveSnapshotAfterReset({
      turnId: "old-turn",
      status: "running",
      content: "old answer",
      thinking: "",
      startedAt: 1500,
      updatedAt: 3500,
    }, messages, 2000)).toBe(false);

    expect(shouldApplyActiveSnapshotAfterReset({
      turnId: "new-turn",
      status: "running",
      content: "new answer",
      thinking: "",
      startedAt: 3100,
      updatedAt: 3500,
    }, messages, 2000)).toBe(true);
  });

  it("does not rehydrate any active snapshot after reset until a new visible turn exists", () => {
    const messages: ChatMessage[] = [
      { id: "old-user", role: "user", content: "old request", timestamp: 1000 },
      { id: "system-reset-1", role: "system", content: "Session ended - new conversation started", timestamp: 2000 },
    ];

    expect(shouldApplyActiveSnapshotAfterReset({
      turnId: "newer-but-unanchored",
      status: "running",
      content: "late old answer",
      thinking: "",
      startedAt: 2500,
      updatedAt: 2600,
    }, messages)).toBe(false);
  });
});

describe("shouldHydrateFromSnapshot", () => {
  const liveSnap = {
    turnId: "t1",
    status: "running" as const,
    content: "partial",
    thinking: "",
    startedAt: 2000,
    updatedAt: 2000,
  };
  const messages: ChatMessage[] = [
    { id: "system-reset-1", role: "system", content: "Session ended", timestamp: 1000 },
    { id: "user-1", role: "user", content: "hi", timestamp: 1500 },
  ];

  it("hydrates a cold view (no open stream) for a post-reset turn", () => {
    expect(shouldHydrateFromSnapshot(liveSnap, messages, { streamOpen: false })).toBe(true);
  });

  it("never hydrates while a live stream is open", () => {
    expect(shouldHydrateFromSnapshot(liveSnap, messages, { streamOpen: true })).toBe(false);
  });
});
