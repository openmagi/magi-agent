import { afterEach, describe, expect, it, vi } from "vitest";
import { applyMissionEvent } from "./missions";

describe("applyMissionEvent", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("upserts mission_created into channel state", () => {
    vi.spyOn(Date, "now").mockReturnValue(1000);

    const state = applyMissionEvent(
      { pendingGoalMissionTitle: "Draft report" },
      {
        type: "mission_created",
        mission: { id: "m1", title: "Draft report", kind: "goal", status: "running" },
      },
    );

    expect(state.missions?.[0]).toMatchObject({
      id: "m1",
      title: "Draft report",
      kind: "goal",
      status: "running",
      updatedAt: 1000,
    });
    expect(state.activeGoalMissionId).toBe("m1");
    expect(state.pendingGoalMissionTitle).toBeNull();
    expect(state.missionRefreshSeq).toBe(1);
    expect(state.lastMissionEventMissionId).toBe("m1");
  });

  it("updates existing mission status from mission_event", () => {
    vi.spyOn(Date, "now").mockReturnValue(2000);

    const state = applyMissionEvent(
      {
        missions: [
          {
            id: "m1",
            title: "Draft report",
            kind: "goal",
            status: "running",
            updatedAt: 1,
          },
        ],
        activeGoalMissionId: "m1",
      },
      { type: "mission_event", missionId: "m1", eventType: "blocked", message: "Needs approval" },
    );

    expect(state.missions?.[0]).toMatchObject({
      id: "m1",
      status: "blocked",
      detail: "Needs approval",
      updatedAt: 2000,
    });
    expect(state.activeGoalMissionId).toBe("m1");
    expect(state.missionRefreshSeq).toBe(1);
    expect(state.lastMissionEventMissionId).toBe("m1");
  });

  it("refreshes mission ledgers for non-status evidence events without changing status", () => {
    vi.spyOn(Date, "now").mockReturnValue(3000);

    const state = applyMissionEvent(
      {
        missions: [
          {
            id: "m1",
            title: "Draft report",
            kind: "goal",
            status: "running",
            detail: "Running child research",
            updatedAt: 1,
          },
        ],
        activeGoalMissionId: "m1",
        missionRefreshSeq: 4,
      },
      {
        type: "mission_event",
        missionId: "m1",
        eventType: "evidence",
        message: "Child evidence attached",
      },
    );

    expect(state.missions?.[0]).toMatchObject({
      id: "m1",
      status: "running",
      detail: "Child evidence attached",
      updatedAt: 3000,
    });
    expect(state.activeGoalMissionId).toBe("m1");
    expect(state.missionRefreshSeq).toBe(5);
    expect(state.lastMissionEventMissionId).toBe("m1");
  });

  it("requests a mission list refresh when an unknown mission emits an event", () => {
    vi.spyOn(Date, "now").mockReturnValue(4000);

    const state = applyMissionEvent(
      { missions: [], missionRefreshSeq: 2 },
      { type: "mission_event", missionId: "unknown-mission", eventType: "evidence" },
    );

    expect(state.missions).toEqual([]);
    expect(state.missionRefreshSeq).toBe(3);
    expect(state.lastMissionEventMissionId).toBe("unknown-mission");
  });

  it("clears active goal when the goal mission reaches a terminal status", () => {
    const state = applyMissionEvent(
      {
        missions: [
          {
            id: "m1",
            title: "Draft report",
            kind: "goal",
            status: "running",
            updatedAt: 1,
          },
        ],
        activeGoalMissionId: "m1",
      },
      { type: "mission_event", missionId: "m1", eventType: "completed" },
    );

    expect(state.missions?.[0].status).toBe("completed");
    expect(state.activeGoalMissionId).toBeNull();
  });
});
