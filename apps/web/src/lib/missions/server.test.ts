import { describe, expect, it, vi } from "vitest";
import type { AppError } from "@/lib/errors";
import {
  assertMissionBelongsToBot,
  buildMissionActionEvent,
  loadMissionDetail,
  sanitizeMissionMessage,
  validateMissionAction,
} from "./server";
import type { MissionSummary } from "./types";

const mission: MissionSummary = {
  id: "mission-1",
  bot_id: "bot-1",
  channel_type: "app",
  channel_id: "general",
  kind: "manual",
  title: "Research Hermes",
  summary: null,
  status: "running",
  priority: 0,
  created_by: "user",
  assignee_profile: null,
  parent_mission_id: null,
  root_mission_id: null,
  used_turns: 0,
  budget_turns: null,
  last_event_at: null,
  completed_at: null,
  metadata: {},
  created_at: "2026-05-07T00:00:00.000Z",
  updated_at: "2026-05-07T00:00:00.000Z",
};

describe("mission server helpers", () => {
  it("truncates long human comments before insertion", () => {
    expect(sanitizeMissionMessage("x".repeat(5000))).toHaveLength(2000);
  });

  it("normalizes empty and non-string mission messages to null", () => {
    expect(sanitizeMissionMessage("   ")).toBeNull();
    expect(sanitizeMissionMessage({ message: "not accepted" })).toBeNull();
  });

  it("validates human mission controls by action and status", () => {
    expect(validateMissionAction({
      eventType: "retry_requested",
      mission: { ...mission, status: "running" },
      message: "try again",
    })).toMatchObject({
      ok: false,
      status: 409,
      message: "Mission cannot be retried while running",
    });

    expect(validateMissionAction({
      eventType: "unblocked",
      mission: { ...mission, status: "failed" },
      message: "token restored",
    })).toMatchObject({
      ok: false,
      status: 409,
      message: "Mission cannot be unblocked while failed",
    });

    expect(validateMissionAction({
      eventType: "cancel_requested",
      mission: { ...mission, status: "running" },
      message: "wrong target",
    })).toEqual({ ok: true });

    expect(validateMissionAction({
      eventType: "comment",
      mission: { ...mission, status: "completed" },
      message: "looks good",
    })).toEqual({ ok: true });
  });

  it("allows cancel without a reason", () => {
    expect(validateMissionAction({
      eventType: "cancel_requested",
      mission: { ...mission, status: "running" },
      message: "   ",
    })).toEqual({ ok: true });
  });

  it("builds retry action events with user actor metadata", () => {
    expect(
      buildMissionActionEvent({
        missionId: "m1",
        actorId: "did:privy:user",
        eventType: "retry_requested",
        message: " retry after fixing token ",
      }),
    ).toEqual({
      mission_id: "m1",
      actor_type: "user",
      actor_id: "did:privy:user",
      event_type: "retry_requested",
      message: "retry after fixing token",
      payload: {},
    });
  });

  it("builds cancellation events with audit-safe user reasons", () => {
    expect(
      buildMissionActionEvent({
        missionId: "m1",
        actorId: "did:privy:user",
        eventType: "cancel_requested",
        message: " wrong target ",
        payload: {
          reason: "mission_cancel_requested",
          userReason: sanitizeMissionMessage(" wrong target "),
        },
      }),
    ).toEqual({
      mission_id: "m1",
      actor_type: "user",
      actor_id: "did:privy:user",
      event_type: "cancel_requested",
      message: "wrong target",
      payload: {
        reason: "mission_cancel_requested",
        userReason: "wrong target",
      },
    });
  });

  it("loads a mission only when it belongs to the requested bot", async () => {
    const filters: Array<[string, string]> = [];
    const single = vi.fn(async () => ({ data: mission, error: null }));
    const query = {
      select: vi.fn(() => ({
        eq(column: string, value: string) {
          filters.push([column, value]);
          return {
            eq(column2: string, value2: string) {
              filters.push([column2, value2]);
              return { single };
            },
          };
        },
      })),
    };
    const supabase = {
      from: vi.fn(() => query),
    };

    await expect(
      assertMissionBelongsToBot(supabase, "mission-1", "bot-1"),
    ).resolves.toEqual(mission);
    expect(supabase.from).toHaveBeenCalledWith("agent_missions");
    expect(query.select).toHaveBeenCalledWith("*");
    expect(filters).toEqual([
      ["id", "mission-1"],
      ["bot_id", "bot-1"],
    ]);
  });

  it("throws a 404 AppError when the mission cannot be loaded", async () => {
    const single = vi.fn(async () => ({ data: null, error: { message: "no rows" } }));
    const query = {
      select: vi.fn(() => ({
        eq: () => ({ eq: () => ({ single }) }),
      })),
    };
    const supabase = {
      from: vi.fn(() => query),
    };

    await expect(assertMissionBelongsToBot(supabase, "missing", "bot-1")).rejects.toMatchObject({
      name: "AppError",
      message: "Mission not found",
      statusCode: 404,
    } satisfies Partial<AppError>);
  });

  it("loads mission runs, events, and artifacts in display order", async () => {
    const calls: Array<{ table: string; missionId: string; order: string; ascending: boolean }> = [];
    const tableData = {
      agent_mission_runs: [{ id: "run-1" }],
      agent_mission_events: [{ id: "event-1" }],
      agent_mission_artifacts: [{ id: "artifact-1" }],
    };
    const supabase = {
      from(table: string) {
        if (!(table in tableData)) throw new Error(`Unexpected mission table: ${table}`);
        const missionTable = table as keyof typeof tableData;
        return {
          select: () => ({
            eq(_column: string, missionId: string) {
              return {
                async order(order: string, opts: { ascending: boolean }) {
                  calls.push({ table: missionTable, missionId, order, ascending: opts.ascending });
                  return { data: tableData[missionTable] };
                },
              };
            },
          }),
        };
      },
    };

    await expect(loadMissionDetail(supabase, mission)).resolves.toEqual({
      mission,
      runs: [{ id: "run-1" }],
      events: [{ id: "event-1" }],
      artifacts: [{ id: "artifact-1" }],
    });
    expect(calls).toEqual([
      {
        table: "agent_mission_runs",
        missionId: "mission-1",
        order: "started_at",
        ascending: false,
      },
      {
        table: "agent_mission_events",
        missionId: "mission-1",
        order: "created_at",
        ascending: true,
      },
      {
        table: "agent_mission_artifacts",
        missionId: "mission-1",
        order: "created_at",
        ascending: true,
      },
    ]);
  });
});
