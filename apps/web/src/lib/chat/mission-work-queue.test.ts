import { describe, expect, it } from "vitest";
import type { MissionActivity } from "./types";
import type { MissionKind, MissionStatus, MissionSummary } from "@/lib/missions/types";
import {
  buildMissionWorkQueue,
  missionActionForStatus,
  missionStatusBucket,
} from "./mission-work-queue";

function summary(input: {
  id: string;
  title: string;
  kind: MissionKind;
  status: MissionStatus;
  summary?: string | null;
  usedTurns?: number;
  budgetTurns?: number | null;
  updatedAt?: string;
}): MissionSummary {
  return {
    id: input.id,
    bot_id: "bot-1",
    channel_type: "app",
    channel_id: "channel-1",
    kind: input.kind,
    title: input.title,
    summary: input.summary ?? null,
    status: input.status,
    priority: 0,
    created_by: "agent",
    assignee_profile: null,
    parent_mission_id: null,
    root_mission_id: null,
    used_turns: input.usedTurns ?? 0,
    budget_turns: input.budgetTurns ?? null,
    last_event_at: null,
    completed_at: null,
    metadata: {},
    created_at: "2026-05-09T00:00:00.000Z",
    updated_at: input.updatedAt ?? "2026-05-09T00:00:00.000Z",
  };
}

describe("mission work queue model", () => {
  it("merges live mission state with durable summaries and groups by operational urgency", () => {
    const durable = [
      summary({
        id: "goal-1",
        title: "Research competitor launches",
        kind: "goal",
        status: "queued",
        summary: "Persistent objective",
        usedTurns: 2,
        budgetTurns: 5,
        updatedAt: "2026-05-09T00:00:10.000Z",
      }),
      summary({
        id: "blocked-1",
        title: "Browser QA checkout",
        kind: "browser_qa",
        status: "blocked",
        summary: "Needs login approval",
        updatedAt: "2026-05-09T00:00:20.000Z",
      }),
      summary({
        id: "done-1",
        title: "Generate launch notes",
        kind: "document",
        status: "completed",
        summary: "Delivered docs",
        updatedAt: "2026-05-09T00:00:30.000Z",
      }),
    ];
    const live: MissionActivity[] = [
      {
        id: "goal-1",
        title: "Research competitor launches",
        kind: "goal",
        status: "running",
        detail: "Continuation scheduled",
        updatedAt: Date.parse("2026-05-09T00:01:00.000Z"),
      },
    ];

    const model = buildMissionWorkQueue({
      summaries: durable,
      liveMissions: live,
      filter: "active",
      query: "",
      activeGoalMissionId: "goal-1",
      now: Date.parse("2026-05-09T00:02:00.000Z"),
    });

    expect(model.counts).toEqual({
      active: 2,
      needsInput: 1,
      done: 1,
      all: 3,
    });
    expect(model.activeGoal?.id).toBe("goal-1");
    expect(model.activeGoal).toMatchObject({
      status: "running",
      detail: "Continuation scheduled",
      summary: "Persistent objective",
      usedTurns: 2,
      budgetTurns: 5,
      action: "cancel",
    });
    expect(model.rows.map((row) => row.id)).toEqual(["blocked-1", "goal-1"]);
    expect(model.sections.map((section) => section.kind)).toEqual([
      "needs_input",
      "running",
    ]);
    expect(model.sections[0]?.rows.map((row) => row.id)).toEqual(["blocked-1"]);
    expect(model.sections[1]?.rows.map((row) => row.id)).toEqual(["goal-1"]);
  });

  it("filters by done state and searches public row text only", () => {
    const model = buildMissionWorkQueue({
      summaries: [
        summary({
          id: "done-1",
          title: "Generate launch notes",
          kind: "document",
          status: "completed",
          summary: "Delivered public notes",
        }),
        summary({
          id: "blocked-1",
          title: "Browser QA checkout",
          kind: "browser_qa",
          status: "blocked",
          summary: "Needs approval",
        }),
      ],
      liveMissions: [],
      filter: "done",
      query: "launch",
      activeGoalMissionId: null,
      now: Date.parse("2026-05-09T00:02:00.000Z"),
    });

    expect(model.rows.map((row) => row.id)).toEqual(["done-1"]);
    expect(JSON.stringify(model)).not.toContain("payload");
    expect(JSON.stringify(model)).not.toContain("private");
  });

  it("maps mission statuses to buckets and safe actions", () => {
    expect(missionStatusBucket("blocked")).toBe("needs_input");
    expect(missionStatusBucket("waiting")).toBe("needs_input");
    expect(missionStatusBucket("paused")).toBe("needs_input");
    expect(missionStatusBucket("running")).toBe("running");
    expect(missionStatusBucket("queued")).toBe("running");
    expect(missionStatusBucket("completed")).toBe("done");
    expect(missionActionForStatus("blocked")).toBe("unblock");
    expect(missionActionForStatus("running")).toBe("cancel");
    expect(missionActionForStatus("failed")).toBe("retry");
    expect(missionActionForStatus("completed")).toBeNull();
  });
});
