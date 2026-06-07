import { describe, expect, it } from "vitest";
import {
  buildMissionTimeline,
  filterMissionTimelineItems,
} from "./timeline";
import type { MissionDetail, MissionSummary } from "./types";

function missionSummary(input: {
  id: string;
  status: MissionSummary["status"];
}): MissionSummary {
  return {
    id: input.id,
    bot_id: "bot-1",
    channel_type: "app",
    channel_id: "general",
    kind: "goal",
    title: "Research Hermes handoff ledgers",
    summary: "Find the durable mission detail primitive we should build.",
    status: input.status,
    priority: 0,
    created_by: "user",
    assignee_profile: null,
    parent_mission_id: null,
    root_mission_id: null,
    used_turns: 2,
    budget_turns: 30,
    last_event_at: null,
    completed_at: null,
    metadata: {},
    created_at: "2026-05-11T00:00:00.000Z",
    updated_at: "2026-05-11T00:05:00.000Z",
  };
}

function detailFixture(): MissionDetail {
  return {
    mission: missionSummary({ id: "mission-1", status: "running" }),
    runs: [
      {
        id: "run-1",
        mission_id: "mission-1",
        bot_id: "bot-1",
        trigger_type: "user",
        status: "failed",
        session_key: "app:general",
        turn_id: "turn-1",
        spawn_task_id: null,
        cron_id: null,
        started_at: "2026-05-11T00:00:00.000Z",
        finished_at: "2026-05-11T00:02:00.000Z",
        error_code: "judge_failed",
        error_message: "Goal judge failed",
        stdout_preview: null,
        result_preview: null,
        metadata: {},
      },
      {
        id: "run-2",
        mission_id: "mission-1",
        bot_id: "bot-1",
        trigger_type: "handoff",
        status: "running",
        session_key: "app:general",
        turn_id: "turn-2",
        spawn_task_id: "task-123",
        cron_id: null,
        started_at: "2026-05-11T00:03:00.000Z",
        finished_at: null,
        error_code: null,
        error_message: null,
        stdout_preview: null,
        result_preview: "Child research in progress",
        metadata: { persona: "research" },
      },
    ],
    events: [
      {
        id: "event-created",
        mission_id: "mission-1",
        run_id: null,
        actor_type: "user",
        actor_id: "user-1",
        event_type: "created",
        message: "Mission created",
        payload: {},
        created_at: "2026-05-11T00:00:00.000Z",
      },
      {
        id: "event-retry",
        mission_id: "mission-1",
        run_id: null,
        actor_type: "user",
        actor_id: "user-1",
        event_type: "retry_requested",
        message: "Retry with child research",
        payload: { reason: "manual_retry", private: "do-not-render" },
        created_at: "2026-05-11T00:02:30.000Z",
      },
    ],
    artifacts: [
      {
        id: "artifact-child",
        mission_id: "mission-1",
        run_id: "run-2",
        kind: "subagent_output",
        title: "Research evidence",
        uri: null,
        storage_key: null,
        preview: "Found three comparable products.",
        metadata: { persona: "research", taskId: "task-123" },
        created_at: "2026-05-11T00:04:00.000Z",
      },
      {
        id: "artifact-parallel",
        mission_id: "mission-1",
        run_id: "run-2",
        kind: "subagent_output",
        title: "Bull partner evidence",
        uri: "child-agent://bull-case",
        storage_key: null,
        preview: "Bull partner found expansion upside.",
        metadata: {
          category: "parallel_research_evidence",
          persona: "bull partner",
          taskId: "task-bull",
          sourceId: "src_child_1",
          parallelGroup: "market-map",
        },
        created_at: "2026-05-11T00:04:10.000Z",
      },
      {
        id: "artifact-synthesis",
        mission_id: "mission-1",
        run_id: "run-2",
        kind: "artifact",
        title: "Parallel synthesis",
        uri: null,
        storage_key: "missions/mission-1/synthesis.md",
        preview: "Synthesized bull and bear cases.",
        metadata: {
          category: "parallel_synthesis",
          synthesisId: "syn-1",
          parallelGroup: "market-map",
        },
        created_at: "2026-05-11T00:04:20.000Z",
      },
    ],
  };
}

describe("mission timeline", () => {
  it("builds an attempt-aware timeline without exposing raw payload internals", () => {
    const ledger = buildMissionTimeline(detailFixture());

    expect(ledger.runsById.get("run-1")?.attempt).toBe(1);
    expect(ledger.runsById.get("run-2")?.attempt).toBe(2);
    expect(ledger.items.map((item) => item.kind)).toContain("started");
    expect(ledger.items.map((item) => item.kind)).toContain("child_spawned");
    expect(ledger.items.map((item) => item.kind)).toContain("evidence_attached");
    expect(ledger.items.map((item) => item.kind)).toContain("retry_requested");
    expect(ledger.items.some((item) => item.detail?.includes("do-not-render"))).toBe(false);
    expect(ledger.items.some((item) => item.message?.includes("do-not-render"))).toBe(false);
    expect(ledger.evidence[0]).toMatchObject({
      kind: "subagent_output",
      attempt: 2,
      runId: "run-2",
      title: "Research evidence",
      preview: "Found three comparable products.",
      sourceLabel: "Child-agent evidence",
    });
  });

  it("groups timeline rows by attempt and filters audit categories", () => {
    const ledger = buildMissionTimeline(detailFixture());

    expect(ledger.attemptGroups.map((group) => group.label)).toEqual([
      "Mission events",
      "Attempt 1 · user failed",
      "Attempt 2 · handoff running",
    ]);
    expect(ledger.attemptGroups.find((group) => group.runId === "run-2")).toMatchObject({
      attempt: 2,
      shortRunId: "run-2",
      itemCount: expect.any(Number),
      evidenceCount: 4,
    });
    expect(filterMissionTimelineItems(ledger, "user").map((item) => item.id)).toEqual([
      "event-created",
      "event-retry",
    ]);
    expect(filterMissionTimelineItems(ledger, "evidence").map((item) => item.kind)).toEqual(
      expect.arrayContaining(["child_spawned", "evidence_attached"]),
    );
    expect(ledger.filterCounts.all).toBe(ledger.items.length);
    expect(ledger.filterCounts.user).toBe(2);
  });

  it("labels child-agent, parallel research, and synthesis evidence with handoff metadata", () => {
    const ledger = buildMissionTimeline(detailFixture());
    const byId = new Map(ledger.evidence.map((item) => [item.id, item]));

    expect(byId.get("artifact-child")).toMatchObject({
      sourceLabel: "Child-agent evidence",
      persona: "research",
      taskId: "task-123",
    });
    expect(byId.get("artifact-parallel")).toMatchObject({
      sourceLabel: "Parallel research evidence",
      sourceId: "src_child_1",
      parallelGroup: "market-map",
      persona: "bull partner",
      taskId: "task-bull",
    });
    expect(byId.get("artifact-synthesis")).toMatchObject({
      sourceLabel: "Parallel synthesis",
      synthesisId: "syn-1",
      parallelGroup: "market-map",
      storageKey: "missions/mission-1/synthesis.md",
    });

    const serialized = JSON.stringify(ledger);
    expect(serialized).not.toContain("parallel_research_evidence");
    expect(serialized).not.toContain("parallel_synthesis");
  });

  it("humanizes user action commands and runtime follow-up events", () => {
    const detail = detailFixture();
    detail.runs = [
      detail.runs[0],
      {
        ...detail.runs[1],
        id: "run-retry-123456",
        trigger_type: "retry",
        status: "running",
        started_at: "2026-05-11T00:03:00.000Z",
        result_preview: "Retry run in progress",
      },
      {
        ...detail.runs[1],
        id: "run-resume-234567",
        trigger_type: "resume",
        status: "running",
        started_at: "2026-05-11T00:05:00.000Z",
        result_preview: "Resume run in progress",
      },
    ];
    detail.events = [
      {
        id: "event-retry-manual",
        mission_id: "mission-1",
        run_id: null,
        actor_type: "user",
        actor_id: "user-1",
        event_type: "retry_requested",
        message: "retry after adding the deck",
        payload: { reason: "manual_retry", private: "hidden payload" },
        created_at: "2026-05-11T00:02:30.000Z",
      },
      {
        id: "event-retry-resumed",
        mission_id: "mission-1",
        run_id: "run-retry-123456",
        actor_type: "system",
        actor_id: null,
        event_type: "resumed",
        message: "Goal mission retry requested by user",
        payload: { reason: "manual_retry", sourceEventType: "retry_requested" },
        created_at: "2026-05-11T00:03:00.000Z",
      },
      {
        id: "event-unblocked",
        mission_id: "mission-1",
        run_id: null,
        actor_type: "user",
        actor_id: "user-1",
        event_type: "unblocked",
        message: "token restored",
        payload: { reason: "user_unblocked" },
        created_at: "2026-05-11T00:04:00.000Z",
      },
      {
        id: "event-unblock-resumed",
        mission_id: "mission-1",
        run_id: "run-resume-234567",
        actor_type: "system",
        actor_id: null,
        event_type: "resumed",
        message: "Goal mission resumed by user",
        payload: { reason: "user_unblocked", sourceEventType: "unblocked" },
        created_at: "2026-05-11T00:05:00.000Z",
      },
      {
        id: "event-cancel",
        mission_id: "mission-1",
        run_id: null,
        actor_type: "user",
        actor_id: "user-1",
        event_type: "cancel_requested",
        message: "wrong target",
        payload: { reason: "mission_cancel_requested" },
        created_at: "2026-05-11T00:06:00.000Z",
      },
    ];

    const ledger = buildMissionTimeline(detail);
    const byId = new Map(ledger.items.map((item) => [item.id, item]));

    expect(byId.get("event-retry-manual")).toMatchObject({
      label: "Retry requested by user",
      message: "retry after adding the deck",
    });
    expect(byId.get("event-retry-resumed")).toMatchObject({
      label: "Retry run started",
      detail: "from user retry",
      attempt: 2,
      runId: "run-retry-123456",
    });
    expect(byId.get("event-unblocked")).toMatchObject({
      label: "Unblocked by user",
      message: "token restored",
    });
    expect(byId.get("event-unblock-resumed")).toMatchObject({
      label: "Resumed after unblock",
      detail: "from user unblock",
      attempt: 3,
      runId: "run-resume-234567",
    });
    expect(byId.get("event-cancel")).toMatchObject({
      label: "Cancel requested by user",
      message: "wrong target",
      detail: "user cancellation",
    });

    const serialized = JSON.stringify(ledger.items);
    expect(serialized).not.toContain("manual_retry");
    expect(serialized).not.toContain("user_unblocked");
    expect(serialized).not.toContain("mission_cancel_requested");
    expect(serialized).not.toContain("hidden payload");
  });

  it("synthesizes mission creation when the event stream has no created event", () => {
    const detail = detailFixture();
    detail.events = [];

    const ledger = buildMissionTimeline(detail);

    expect(ledger.items[0]).toMatchObject({
      kind: "created",
      actorType: "user",
      message: "Research Hermes handoff ledgers",
    });
  });
});
