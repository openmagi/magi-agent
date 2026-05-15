import { describe, expect, it } from "vitest";
import { channelStateFromActiveSnapshot, isLiveActiveSnapshot } from "./active-snapshot";

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
});
