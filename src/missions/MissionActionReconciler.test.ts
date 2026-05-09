import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BackgroundTaskRegistry } from "../tasks/BackgroundTaskRegistry.js";
import { MissionActionReconciler } from "./MissionActionReconciler.js";
import type { MissionActionEvent } from "./types.js";

async function tmpRoot(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), "mission-actions-"));
}

describe("MissionActionReconciler", () => {
  let root: string;

  beforeEach(async () => {
    root = await tmpRoot();
  });

  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("cancels linked background tasks once and records a mission event", async () => {
    const backgroundTasks = new BackgroundTaskRegistry(root);
    const controller = new AbortController();
    await backgroundTasks.create({
      taskId: "task-1",
      parentTurnId: "turn-1",
      sessionKey: "session-1",
      persona: "researcher",
      prompt: "research",
      missionId: "mission-1",
      missionRunId: "run-1",
      abortController: controller,
    });
    const actionEvent: MissionActionEvent = {
      id: "event-1",
      mission_id: "mission-1",
      event_type: "cancel_requested",
      created_at: "2026-05-09T00:01:00.000Z",
    };
    const missionClient = {
      listActionEvents: vi.fn(async () => [actionEvent]),
      appendEvent: vi.fn(async () => ({})),
    };
    const reconciler = new MissionActionReconciler({
      workspaceRoot: root,
      missionClient,
      backgroundTasks,
    });

    await reconciler.pollOnce();
    await reconciler.pollOnce();

    expect(controller.signal.aborted).toBe(true);
    expect((await backgroundTasks.get("task-1"))?.status).toBe("aborted");
    expect(missionClient.appendEvent).toHaveBeenCalledTimes(1);
    expect(missionClient.appendEvent).toHaveBeenCalledWith(
      "mission-1",
      expect.objectContaining({
        runId: "run-1",
        actorType: "system",
        eventType: "cancelled",
        payload: expect.objectContaining({
          actionEventId: "event-1",
          taskId: "task-1",
        }),
      }),
    );
  });

  it("marks restart-abandoned background tasks failed in the mission ledger", async () => {
    const backgroundTasks = new BackgroundTaskRegistry(root);
    await backgroundTasks.create({
      taskId: "stale-task",
      parentTurnId: "turn-1",
      sessionKey: "session-1",
      persona: "researcher",
      prompt: "research",
      missionId: "mission-stale",
      missionRunId: "run-stale",
    });
    const reloadedTasks = new BackgroundTaskRegistry(root);
    await reloadedTasks.hydrate();
    const missionClient = {
      listActionEvents: vi.fn(async () => []),
      appendEvent: vi.fn(async () => ({})),
    };
    const reconciler = new MissionActionReconciler({
      workspaceRoot: root,
      missionClient,
      backgroundTasks: reloadedTasks,
    });

    const abandoned = await reconciler.reconcileAbandonedBackgroundTasks();

    expect(abandoned.map((record) => record.taskId)).toEqual(["stale-task"]);
    expect((await reloadedTasks.get("stale-task"))?.status).toBe("failed");
    expect(missionClient.appendEvent).toHaveBeenCalledWith(
      "mission-stale",
      expect.objectContaining({
        runId: "run-stale",
        actorType: "system",
        eventType: "failed",
        message: "abandoned_by_restart",
      }),
    );
  });

  it("disables linked crons on cancel and re-enables them on retry or unblock", async () => {
    const backgroundTasks = new BackgroundTaskRegistry(root);
    const cron = {
      cronId: "cron-1",
      missionId: "mission-cron",
      missionRunId: "run-cron",
      enabled: true,
    };
    const crons = {
      list: vi.fn(() => [cron]),
      update: vi.fn(async (_cronId: string, patch: { enabled?: boolean }) => {
        Object.assign(cron, patch);
        return cron;
      }),
    };
    const missionClient = {
      listActionEvents: vi.fn(async () => [
        {
          id: "event-cancel",
          mission_id: "mission-cron",
          event_type: "cancel_requested",
          created_at: "2026-05-09T00:01:00.000Z",
        },
        {
          id: "event-retry",
          mission_id: "mission-cron",
          event_type: "retry_requested",
          created_at: "2026-05-09T00:02:00.000Z",
        },
        {
          id: "event-unblock",
          mission_id: "mission-cron",
          event_type: "unblocked",
          created_at: "2026-05-09T00:03:00.000Z",
        },
      ] satisfies MissionActionEvent[]),
      appendEvent: vi.fn(async () => ({})),
    };
    const reconciler = new MissionActionReconciler({
      workspaceRoot: root,
      missionClient,
      backgroundTasks,
      crons,
    });

    await reconciler.pollOnce();

    expect(crons.update).toHaveBeenNthCalledWith(1, "cron-1", { enabled: false });
    expect(crons.update).toHaveBeenNthCalledWith(2, "cron-1", { enabled: true });
    expect(crons.update).toHaveBeenNthCalledWith(3, "cron-1", { enabled: true });
    expect(missionClient.appendEvent).toHaveBeenCalledWith(
      "mission-cron",
      expect.objectContaining({ eventType: "cancelled" }),
    );
    expect(missionClient.appendEvent).toHaveBeenCalledWith(
      "mission-cron",
      expect.objectContaining({ eventType: "resumed" }),
    );
    expect(cron.enabled).toBe(true);
  });
});
