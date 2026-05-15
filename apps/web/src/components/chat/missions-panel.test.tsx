import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  buildMissionListUrl,
  MissionsPanel,
  shouldReloadMissionDetailForEvent,
} from "./missions-panel";
import type { MissionActivity } from "@/lib/chat/types";
import type { MissionDetail, MissionSummary } from "@/lib/missions/types";

const liveMissions: MissionActivity[] = [
  {
    id: "goal-1",
    title: "Research competitor launches",
    kind: "goal",
    status: "running",
    detail: "Continuation scheduled",
    updatedAt: 122,
  },
  {
    id: "mission-1",
    title: "Draft weekly research report",
    kind: "goal",
    status: "blocked",
    detail: "Waiting for approval",
    updatedAt: 123,
  },
  {
    id: "mission-2",
    title: "Collect browser QA evidence",
    kind: "browser_qa",
    status: "running",
    updatedAt: 124,
  },
];

function missionSummary(input: {
  id: string;
  title: string;
  kind: MissionSummary["kind"];
  status: MissionSummary["status"];
}): MissionSummary {
  return {
    id: input.id,
    bot_id: "bot-1",
    channel_type: "app",
    channel_id: "channel-1",
    kind: input.kind,
    title: input.title,
    summary: null,
    status: input.status,
    priority: 0,
    created_by: "agent",
    assignee_profile: null,
    parent_mission_id: null,
    root_mission_id: null,
    used_turns: 2,
    budget_turns: 5,
    last_event_at: null,
    completed_at: null,
    metadata: {},
    created_at: "2026-05-09T00:00:00.000Z",
    updated_at: "2026-05-09T00:00:00.000Z",
  };
}

const missionDetail: MissionDetail = {
  mission: missionSummary({
    id: "mission-1",
    title: "Draft weekly research report",
    kind: "goal",
    status: "blocked",
  }),
  runs: [
    {
      id: "run-1",
      mission_id: "mission-1",
      bot_id: "bot-1",
      trigger_type: "goal_continue",
      status: "failed",
      session_key: "session-1",
      turn_id: "turn-1",
      spawn_task_id: null,
      cron_id: null,
      started_at: "2026-05-09T00:00:00.000Z",
      finished_at: null,
      error_code: null,
      error_message: "Needs approval",
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
      session_key: "session-1",
      turn_id: "turn-2",
      spawn_task_id: "task-123",
      cron_id: null,
      started_at: "2026-05-09T00:03:00.000Z",
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
      id: "event-1",
      mission_id: "mission-1",
      run_id: "run-1",
      actor_type: "system",
      actor_id: null,
      event_type: "blocked",
      message: "Waiting for approval",
      payload: { private: "hidden payload" },
      created_at: "2026-05-09T00:01:00.000Z",
    },
  ],
  artifacts: [
    {
      id: "artifact-1",
      mission_id: "mission-1",
      run_id: "run-1",
      kind: "url",
      title: "Evidence URL",
      uri: "https://example.com/evidence",
      storage_key: null,
      preview: "Public evidence",
      metadata: {},
      created_at: "2026-05-09T00:02:00.000Z",
    },
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
      created_at: "2026-05-09T00:04:00.000Z",
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
      created_at: "2026-05-09T00:04:10.000Z",
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
      created_at: "2026-05-09T00:04:20.000Z",
    },
  ],
};

describe("MissionsPanel", () => {
  it("builds mission list requests scoped to the active app channel", () => {
    expect(buildMissionListUrl({
      botId: "bot-1",
      limit: 50,
      channelType: "app",
      channelId: "stock",
    })).toBe("/api/bots/bot-1/missions?limit=50&channelType=app&channelId=stock");

    expect(buildMissionListUrl({
      botId: "bot 1",
      limit: 50,
      channelType: "app",
      channelId: "내외디스틸러",
    })).toBe(
      "/api/bots/bot%201/missions?limit=50&channelType=app&channelId=%EB%82%B4%EC%99%B8%EB%94%94%EC%8A%A4%ED%8B%B8%EB%9F%AC",
    );
  });

  it("renders live durable mission state as a compact work queue", () => {
    const html = renderToStaticMarkup(
      <MissionsPanel
        botId="bot-1"
        liveMissions={liveMissions}
        activeGoalMissionId="goal-1"
        initialDetail={missionDetail}
        initialSelectedMissionId="mission-1"
        getAccessToken={vi.fn()}
      />,
    );

    expect(html).toContain("Work Queue");
    expect(html).toContain("Missions");
    expect(html).toContain("Active");
    expect(html).toContain("Needs input");
    expect(html).toContain("Done");
    expect(html).toContain("All");
    expect(html).toContain("Active goal");
    expect(html).toContain("Research competitor launches");
    expect(html).toContain("Continuation scheduled");
    expect(html).toContain("Draft weekly research report");
    expect(html).toContain("Waiting for approval");
    expect(html).toContain("goal");
    expect(html).toContain("blocked");
    expect(html).toContain("Unblock");
    expect(html).toContain("Collect browser QA evidence");
    expect(html).toContain("Cancel");
    expect(html).toContain("Mission reason: Needs input");
    expect(html).toContain("Mission ledger");
    expect(html).toContain("Timeline");
    expect(html).toContain('aria-label="Timeline filters"');
    expect(html).toContain('data-mission-timeline-filter="all"');
    expect(html).toContain('data-mission-timeline-filter="user"');
    expect(html).toContain('data-mission-timeline-filter="runtime"');
    expect(html).toContain('data-mission-timeline-filter="evidence"');
    expect(html).toContain("Attempt 1 · goal_continue failed");
    expect(html).toContain("Attempt 2 · handoff running");
    expect(html).toContain("Attempt 1");
    expect(html).toContain("Attempt 2");
    expect(html).toContain("run-1");
    expect(html).toContain("run-2");
    expect(html).toContain("child spawned");
    expect(html).toContain("Handoff evidence");
    expect(html).toContain("Runs");
    expect(html).toContain("run run-2");
    expect(html).toContain("turn turn-2");
    expect(html).toContain("spawn task-123");
    expect(html).toContain("Events");
    expect(html).toContain("Artifacts");
    expect(html).toContain("Add comment");
    expect(html).toContain("Human controls");
    expect(html).toContain("Action reason");
    expect(html).toContain('data-mission-detail-action="cancel"');
    expect(html).toContain('aria-disabled="false"');
    expect(html).toContain("Evidence URL");
    expect(html).toContain("URL evidence");
    expect(html).toContain("Research evidence");
    expect(html).toContain("Child-agent evidence");
    expect(html).toContain("Bull partner evidence");
    expect(html).toContain("Parallel research evidence");
    expect(html).toContain("src_child_1");
    expect(html).toContain("market-map");
    expect(html).toContain("Parallel synthesis");
    expect(html).toContain("syn-1");
    expect(html).toContain('aria-label="Close mission ledger"');
    expect(html).not.toContain("payload");
    expect(html).not.toContain("private");
    expect(html).not.toContain("parallel_research_evidence");
    expect(html).not.toContain("parallel_synthesis");
  });

  it("decides when a selected mission detail should reload after a live mission event", () => {
    expect(shouldReloadMissionDetailForEvent({
      selectedMissionId: "mission-1",
      lastMissionEventMissionId: "mission-1",
    })).toBe(true);
    expect(shouldReloadMissionDetailForEvent({
      selectedMissionId: "mission-1",
      lastMissionEventMissionId: "mission-2",
    })).toBe(false);
    expect(shouldReloadMissionDetailForEvent({
      selectedMissionId: "mission-1",
      lastMissionEventMissionId: null,
    })).toBe(true);
    expect(shouldReloadMissionDetailForEvent({
      selectedMissionId: null,
      lastMissionEventMissionId: "mission-1",
    })).toBe(false);
  });

  it("surfaces user cancellation reasons without exposing raw payload values", () => {
    const cancelledMission: MissionActivity = {
      id: "mission-cancelled",
      title: "Stop stale browser QA",
      kind: "browser_qa",
      status: "cancelled",
      updatedAt: 125,
    };
    const cancelledDetail: MissionDetail = {
      mission: missionSummary({
        id: "mission-cancelled",
        title: "Stop stale browser QA",
        kind: "browser_qa",
        status: "cancelled",
      }),
      runs: [],
      events: [
        {
          id: "event-cancel",
          mission_id: "mission-cancelled",
          run_id: null,
          actor_type: "user",
          actor_id: "user-1",
          event_type: "cancel_requested",
          message: null,
          payload: { private: "hidden payload", reason: "mission_cancel_requested" },
          created_at: "2026-05-09T00:03:00.000Z",
        },
      ],
      artifacts: [],
    };

    const html = renderToStaticMarkup(
      <MissionsPanel
        botId="bot-1"
        liveMissions={[cancelledMission]}
        initialFilter="done"
        initialDetail={cancelledDetail}
        initialSelectedMissionId="mission-cancelled"
        getAccessToken={vi.fn()}
      />,
    );

    expect(html).toContain("Mission reason: Cancelled by user");
    expect(html).toContain("Cancelled by user - user cancel_requested");
    expect(html).not.toContain("mission_cancel_requested");
    expect(html).not.toContain("hidden payload");
  });

  it("surfaces restart recovery and resume reasons in the mission ledger", () => {
    const resumedMission: MissionActivity = {
      id: "mission-resumed",
      title: "Continue LP diligence",
      kind: "goal",
      status: "running",
      detail: "Goal mission resumed after restart",
      updatedAt: 126,
    };
    const resumedDetail: MissionDetail = {
      mission: missionSummary({
        id: "mission-resumed",
        title: "Continue LP diligence",
        kind: "goal",
        status: "running",
      }),
      runs: [],
      events: [
        {
          id: "event-retry",
          mission_id: "mission-resumed",
          run_id: null,
          actor_type: "user",
          actor_id: "user-1",
          event_type: "retry_requested",
          message: null,
          payload: { reason: "restart_recovery" },
          created_at: "2026-05-09T00:03:00.000Z",
        },
        {
          id: "event-resumed",
          mission_id: "mission-resumed",
          run_id: "run-resume",
          actor_type: "system",
          actor_id: null,
          event_type: "resumed",
          message: "Goal mission resumed after restart",
          payload: { reason: "restart_recovery" },
          created_at: "2026-05-09T00:04:00.000Z",
        },
      ],
      artifacts: [],
    };

    const html = renderToStaticMarkup(
      <MissionsPanel
        botId="bot-1"
        liveMissions={[resumedMission]}
        activeGoalMissionId="mission-resumed"
        initialDetail={resumedDetail}
        initialSelectedMissionId="mission-resumed"
        getAccessToken={vi.fn()}
      />,
    );

    expect(html).toContain("Mission reason: Resumed after restart");
    expect(html).toContain("Restart recovery requested - user retry_requested");
    expect(html).toContain("Resumed after restart - system resumed");
    expect(html).not.toContain("restart_recovery");
  });

  it("surfaces user retry and unblock action reasons as audit labels", () => {
    const actionMission: MissionActivity = {
      id: "mission-actions",
      title: "Continue LP diligence",
      kind: "goal",
      status: "running",
      detail: "Goal mission resumed by user",
      updatedAt: 127,
    };
    const actionDetail: MissionDetail = {
      mission: missionSummary({
        id: "mission-actions",
        title: "Continue LP diligence",
        kind: "goal",
        status: "running",
      }),
      runs: [
        {
          id: "run-retry",
          mission_id: "mission-actions",
          bot_id: "bot-1",
          trigger_type: "retry",
          status: "completed",
          session_key: "session-1",
          turn_id: "turn-retry",
          spawn_task_id: null,
          cron_id: null,
          started_at: "2026-05-09T00:03:00.000Z",
          finished_at: "2026-05-09T00:03:30.000Z",
          error_code: null,
          error_message: null,
          stdout_preview: null,
          result_preview: "Retry completed",
          metadata: {},
        },
        {
          id: "run-resume",
          mission_id: "mission-actions",
          bot_id: "bot-1",
          trigger_type: "resume",
          status: "running",
          session_key: "session-1",
          turn_id: "turn-resume",
          spawn_task_id: null,
          cron_id: null,
          started_at: "2026-05-09T00:05:00.000Z",
          finished_at: null,
          error_code: null,
          error_message: null,
          stdout_preview: null,
          result_preview: "Resume in progress",
          metadata: {},
        },
      ],
      events: [
        {
          id: "event-retry",
          mission_id: "mission-actions",
          run_id: null,
          actor_type: "user",
          actor_id: "user-1",
          event_type: "retry_requested",
          message: "retry after adding the deck",
          payload: { reason: "manual_retry", private: "hidden payload" },
          created_at: "2026-05-09T00:02:30.000Z",
        },
        {
          id: "event-retry-resumed",
          mission_id: "mission-actions",
          run_id: "run-retry",
          actor_type: "system",
          actor_id: null,
          event_type: "resumed",
          message: "Goal mission retry requested by user",
          payload: { reason: "manual_retry", sourceEventType: "retry_requested" },
          created_at: "2026-05-09T00:03:00.000Z",
        },
        {
          id: "event-unblocked",
          mission_id: "mission-actions",
          run_id: null,
          actor_type: "user",
          actor_id: "user-1",
          event_type: "unblocked",
          message: "token restored",
          payload: { reason: "user_unblocked" },
          created_at: "2026-05-09T00:04:00.000Z",
        },
        {
          id: "event-unblock-resumed",
          mission_id: "mission-actions",
          run_id: "run-resume",
          actor_type: "system",
          actor_id: null,
          event_type: "resumed",
          message: "Goal mission resumed by user",
          payload: { reason: "user_unblocked", sourceEventType: "unblocked" },
          created_at: "2026-05-09T00:05:00.000Z",
        },
      ],
      artifacts: [],
    };

    const html = renderToStaticMarkup(
      <MissionsPanel
        botId="bot-1"
        liveMissions={[actionMission]}
        activeGoalMissionId="mission-actions"
        initialDetail={actionDetail}
        initialSelectedMissionId="mission-actions"
        getAccessToken={vi.fn()}
      />,
    );

    expect(html).toContain("Mission reason: Resumed after unblock");
    expect(html).toContain("Retry requested by user");
    expect(html).toContain("Retry run started");
    expect(html).toContain("Unblocked by user");
    expect(html).toContain("Resumed after unblock");
    expect(html).toContain("Retry requested by user - user retry_requested");
    expect(html).toContain("Retry run started - system resumed");
    expect(html).toContain("Unblocked by user - user unblocked");
    expect(html).toContain("Resumed after unblock - system resumed");
    expect(html).not.toContain("manual_retry");
    expect(html).not.toContain("user_unblocked");
    expect(html).not.toContain("hidden payload");
  });

  it("surfaces script cron quiet ticks and stdout previews in the mission ledger", () => {
    const scriptDetail: MissionDetail = {
      mission: missionSummary({
        id: "mission-script",
        title: "Quiet watchdog",
        kind: "script_cron",
        status: "completed",
      }),
      runs: [
        {
          id: "run-script",
          mission_id: "mission-script",
          bot_id: "bot-1",
          trigger_type: "script_cron",
          status: "completed",
          session_key: "agent:cron:app:general:cron-script",
          turn_id: null,
          spawn_task_id: null,
          cron_id: "cron-script",
          started_at: "2026-05-11T00:00:00.000Z",
          finished_at: "2026-05-11T00:00:01.000Z",
          error_code: null,
          error_message: null,
          stdout_preview: "Inventory changed",
          result_preview: "Delivered stdout to app:general",
          metadata: {},
        },
      ],
      events: [
        {
          id: "event-quiet",
          mission_id: "mission-script",
          run_id: "run-script",
          actor_type: "cron",
          actor_id: "bot-1",
          event_type: "completed",
          message: "Quiet tick: script produced no stdout",
          payload: { mode: "script", delivery: "quiet", quietReason: "empty_stdout" },
          created_at: "2026-05-11T00:00:01.000Z",
        },
      ],
      artifacts: [],
    };

    const html = renderToStaticMarkup(
      <MissionsPanel
        botId="bot-1"
        initialDetail={scriptDetail}
        initialSelectedMissionId="mission-script"
        getAccessToken={vi.fn()}
      />,
    );

    expect(html).toContain("Mission reason: Quiet tick");
    expect(html).toContain("Quiet tick - cron completed");
    expect(html).toContain("script_cron completed");
    expect(html).toContain("Inventory changed");
  });
});
