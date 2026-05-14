import { describe, expect, it, vi } from "vitest";
import { MissionClient } from "./MissionClient.js";

describe("MissionClient", () => {
  it("sends gateway-token authenticated mission creation requests", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify([{ id: "m1" }]), { status: 201 }));
    const client = new MissionClient({
      chatProxyUrl: "http://chat-proxy/",
      gatewayToken: "gw-token",
      fetchImpl: fetchMock as unknown as typeof fetch,
    });

    const result = await client.createMission({
      channelType: "app",
      channelId: "general",
      kind: "manual",
      title: "Draft report",
      createdBy: "agent",
    });

    expect(result.id).toBe("m1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://chat-proxy/v1/missions",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          Authorization: "Bearer gw-token",
        }),
        body: JSON.stringify({
          channelType: "app",
          channelId: "general",
          kind: "manual",
          title: "Draft report",
          createdBy: "agent",
        }),
      }),
    );
  });

  it("creates runs and appends events under encoded mission ids", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify([{ id: "row-1" }]), { status: 201 }));
    const client = new MissionClient({
      chatProxyUrl: "http://chat-proxy",
      gatewayToken: "gw-token",
      fetchImpl: fetchMock as unknown as typeof fetch,
    });

    await client.createRun("mission/1", { triggerType: "retry" });
    await client.appendEvent("mission/1", { eventType: "heartbeat" });

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://chat-proxy/v1/missions/mission%2F1/runs",
      "http://chat-proxy/v1/missions/mission%2F1/events",
    ]);
  });

  it("updates mission runs with gateway-token authentication", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify([{ id: "run-1", status: "completed" }]), { status: 200 }),
    );
    const client = new MissionClient({
      chatProxyUrl: "http://chat-proxy",
      gatewayToken: "gw-token",
      fetchImpl: fetchMock as unknown as typeof fetch,
    });

    await client.updateRun("mission/1", "run/1", {
      status: "completed",
      finishedAt: "2026-05-11T00:00:00.000Z",
      stdoutPreview: "watchdog changed",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://chat-proxy/v1/missions/mission%2F1/runs/run%2F1",
      expect.objectContaining({
        method: "PATCH",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          Authorization: "Bearer gw-token",
        }),
        body: JSON.stringify({
          status: "completed",
          finishedAt: "2026-05-11T00:00:00.000Z",
          stdoutPreview: "watchdog changed",
        }),
      }),
    );
  });

  it("lists action events with gateway-token authentication", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      events: [
        {
          id: "event-1",
          mission_id: "mission-1",
          event_type: "cancel_requested",
          created_at: "2026-05-09T00:00:01.000Z",
        },
      ],
    }), { status: 200 }));
    const client = new MissionClient({
      chatProxyUrl: "http://chat-proxy",
      gatewayToken: "gw-token",
      fetchImpl: fetchMock as unknown as typeof fetch,
    });

    const events = await client.listActionEvents({
      since: "2026-05-09T00:00:00.000Z",
      limit: 25,
    });

    expect(events.map((event) => event.id)).toEqual(["event-1"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://chat-proxy/v1/missions/actions?since=2026-05-09T00%3A00%3A00.000Z&limit=25",
      expect.objectContaining({
        method: "GET",
        headers: expect.objectContaining({
          Authorization: "Bearer gw-token",
        }),
      }),
    );
  });

  it("requests restart recovery for runtime missions", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      abandoned: 1,
      missionIds: ["mission-1"],
    }), { status: 200 }));
    const client = new MissionClient({
      chatProxyUrl: "http://chat-proxy",
      gatewayToken: "gw-token",
      fetchImpl: fetchMock as unknown as typeof fetch,
    });

    const result = await client.abandonRunningOnRestart({
      startedAt: "2026-05-09T15:15:14.000Z",
      reason: "abandoned_by_restart",
    });

    expect(result).toEqual({
      abandoned: 1,
      missionIds: ["mission-1"],
      resumeRequested: 0,
      resumeMissionIds: [],
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://chat-proxy/v1/missions/restart-recovery",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          Authorization: "Bearer gw-token",
        }),
        body: JSON.stringify({
          startedAt: "2026-05-09T15:15:14.000Z",
          reason: "abandoned_by_restart",
        }),
      }),
    );
  });

  it("throws an actionable error on failed proxy requests", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ error: "bad" }), { status: 400 }));
    const client = new MissionClient({
      chatProxyUrl: "http://chat-proxy",
      gatewayToken: "gw-token",
      fetchImpl: fetchMock as unknown as typeof fetch,
    });

    await expect(
      client.appendEvent("mission-1", { eventType: "heartbeat" }),
    ).rejects.toThrow("mission request failed: HTTP 400");
  });
});
