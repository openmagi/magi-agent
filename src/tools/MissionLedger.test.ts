import { describe, expect, it, vi } from "vitest";
import type { ToolContext } from "../Tool.js";
import type { MissionClient } from "../missions/MissionClient.js";
import { makeMissionLedgerTool } from "./MissionLedger.js";

function makeCtx(events: unknown[] = []): ToolContext {
  return {
    botId: "bot-1",
    sessionKey: "app:general",
    turnId: "turn-1",
    workspaceRoot: "/tmp/core-agent-mission-ledger-test",
    askUser: async () => ({ selectedId: "ok" }),
    emitProgress: () => {},
    emitAgentEvent: (event) => events.push(event),
    abortSignal: new AbortController().signal,
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

describe("MissionLedger tool", () => {
  it("creates a mission in the current source channel and emits public state", async () => {
    const events: unknown[] = [];
    const client = {
      createMission: vi.fn(async () => ({
        id: "mission-1",
        title: "Draft report",
        kind: "manual",
        status: "queued",
      })),
      appendEvent: vi.fn(),
    } as unknown as MissionClient;
    const tool = makeMissionLedgerTool({
      client,
      getSourceChannel: () => ({ type: "app", channelId: "general" }),
    });

    const result = await tool.execute(
      {
        action: "create",
        title: "Draft report",
        kind: "manual",
        metadata: { topic: "Hermes" },
      },
      makeCtx(events),
    );

    expect(result.status).toBe("ok");
    expect(client.createMission).toHaveBeenCalledWith({
      channelType: "app",
      channelId: "general",
      kind: "manual",
      title: "Draft report",
      createdBy: "agent",
      metadata: { topic: "Hermes" },
    });
    expect(events).toEqual([
      {
        type: "mission_created",
        mission: {
          id: "mission-1",
          title: "Draft report",
          kind: "manual",
          status: "queued",
        },
      },
    ]);
  });

  it("appends mission events for heartbeat, block, complete, and fail actions", async () => {
    const events: unknown[] = [];
    const client = {
      createMission: vi.fn(),
      appendEvent: vi.fn(async () => ({ id: "event-1" })),
    } as unknown as MissionClient;
    const tool = makeMissionLedgerTool({
      client,
      getSourceChannel: () => ({ type: "app", channelId: "general" }),
    });

    for (const action of ["heartbeat", "block", "complete", "fail"] as const) {
      await tool.execute(
        { action, missionId: "mission-1", message: action, metadata: { action } },
        makeCtx(events),
      );
    }

    expect(client.appendEvent).toHaveBeenCalledTimes(4);
    expect(client.appendEvent).toHaveBeenNthCalledWith(1, "mission-1", {
      actorType: "agent",
      eventType: "heartbeat",
      message: "heartbeat",
      payload: { action: "heartbeat" },
    });
    expect(client.appendEvent).toHaveBeenNthCalledWith(2, "mission-1", {
      actorType: "agent",
      eventType: "blocked",
      message: "block",
      payload: { action: "block" },
    });
    expect(events.at(-1)).toEqual({
      type: "mission_event",
      missionId: "mission-1",
      eventType: "failed",
      message: "fail",
    });
  });

  it("rejects create when the source channel is unavailable", async () => {
    const tool = makeMissionLedgerTool({
      client: { createMission: vi.fn(), appendEvent: vi.fn() } as unknown as MissionClient,
      getSourceChannel: () => null,
    });

    const result = await tool.execute(
      { action: "create", title: "Draft report", kind: "manual" },
      makeCtx(),
    );

    expect(result).toMatchObject({
      status: "error",
      errorCode: "no_channel",
    });
  });

  it("requires a mission id for non-create actions", async () => {
    const tool = makeMissionLedgerTool({
      client: { createMission: vi.fn(), appendEvent: vi.fn() } as unknown as MissionClient,
      getSourceChannel: () => ({ type: "app", channelId: "general" }),
    });

    const result = await tool.execute({ action: "heartbeat" }, makeCtx());

    expect(result).toMatchObject({
      status: "error",
      errorCode: "mission_required",
    });
  });
});
