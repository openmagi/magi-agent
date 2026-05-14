import { describe, expect, it } from "vitest";
import { channelStateFromActiveSnapshot, isLiveActiveSnapshot } from "./active-snapshot";

describe("active snapshot recovery", () => {
  it("treats mission snapshots as visible current work without a reconnect banner", () => {
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
      missions: [
        { id: "m1", title: "Draft report", kind: "goal", status: "running" as const, updatedAt: 1000 },
      ],
      activeGoalMissionId: "m1",
    };

    expect(isLiveActiveSnapshot(snapshot)).toBe(true);
    expect(channelStateFromActiveSnapshot(snapshot, undefined)).toMatchObject({
      streaming: true,
      reconnecting: false,
      turnPhase: "executing",
      heartbeatElapsedMs: 30000,
      pendingInjectionCount: 1,
      missions: snapshot.missions,
      activeGoalMissionId: "m1",
    });
  });

  it("preserves existing mission state through sparse snapshots", () => {
    const existingMission = {
      id: "m1",
      title: "Draft report",
      kind: "goal",
      status: "running" as const,
      updatedAt: 1000,
    };

    expect(channelStateFromActiveSnapshot({
      turnId: "turn-1",
      status: "running" as const,
      content: "",
      thinking: "",
      startedAt: 1000,
      updatedAt: 3000,
    }, {
      streaming: true,
      streamingText: "",
      thinkingText: "",
      error: null,
      reconnecting: false,
      missions: [existingMission],
      activeGoalMissionId: "m1",
    })).toMatchObject({
      reconnecting: false,
      missions: [existingMission],
      activeGoalMissionId: "m1",
    });
  });
});
