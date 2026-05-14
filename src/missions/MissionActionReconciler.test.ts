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
      abandonRunningOnRestart: vi.fn(async () => ({ abandoned: 0, missionIds: [] })),
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
      abandonRunningOnRestart: vi.fn(async () => ({ abandoned: 0, missionIds: [] })),
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
      abandonRunningOnRestart: vi.fn(async () => ({ abandoned: 0, missionIds: [] })),
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

  it("marks stale runtime missions abandoned before polling action events", async () => {
    const backgroundTasks = new BackgroundTaskRegistry(root);
    const missionClient = {
      listActionEvents: vi.fn(async () => []),
      appendEvent: vi.fn(async () => ({})),
      abandonRunningOnRestart: vi.fn(async () => ({
        abandoned: 1,
        missionIds: ["mission-stale"],
      })),
    };
    const reconciler = new MissionActionReconciler({
      workspaceRoot: root,
      missionClient,
      backgroundTasks,
      startedAt: new Date("2026-05-09T15:15:14.000Z"),
      pollIntervalMs: 60_000,
    });

    await reconciler.start();
    await vi.waitFor(() => expect(missionClient.listActionEvents).toHaveBeenCalled());
    reconciler.stop();

    expect(missionClient.abandonRunningOnRestart).toHaveBeenCalledWith({
      startedAt: "2026-05-09T15:15:14.000Z",
      reason: "abandoned_by_restart",
    });
    expect(missionClient.abandonRunningOnRestart.mock.invocationCallOrder[0])
      .toBeLessThan(missionClient.listActionEvents.mock.invocationCallOrder[0]);
  });

  it("dispatches restart goal retry events to the goal resumer", async () => {
    const actionEvent: MissionActionEvent = {
      id: "event-goal-retry",
      mission_id: "mission-goal",
      event_type: "retry_requested",
      created_at: "2026-05-09T15:16:00.000Z",
      payload: {
        reason: "restart_recovery",
        startedAt: "2026-05-09T15:15:14.000Z",
        goal: {
          sessionKey: "agent:main:app:general:32",
          channelType: "app",
          channelId: "general",
          objective: "Finish the IC memo",
          sourceRequest: "Run the investment committee workflow",
          title: "Investment memo",
          completionCriteria: ["Final IC memo delivered"],
          turnsUsed: 2,
          maxTurns: 30,
          resumeContext: "Recent mission ledger before restart:\n- heartbeat: Drafted partner critique.",
        },
      },
    };
    const backgroundTasks = new BackgroundTaskRegistry(root);
    const missionClient = {
      listActionEvents: vi.fn(async () => [actionEvent]),
      appendEvent: vi.fn(async () => ({})),
      abandonRunningOnRestart: vi.fn(async () => ({ abandoned: 0, missionIds: [] })),
    };
    const goalResumer = {
      resumeAfterRestart: vi.fn(async () => undefined),
    };
    const reconciler = new MissionActionReconciler({
      workspaceRoot: root,
      missionClient,
      backgroundTasks,
      goals: goalResumer,
      pollIntervalMs: 60_000,
    });

    await reconciler.pollOnce();

    expect(goalResumer.resumeAfterRestart).toHaveBeenCalledWith({
      actionEventId: "event-goal-retry",
      missionId: "mission-goal",
      startedAt: "2026-05-09T15:15:14.000Z",
      sessionKey: "agent:main:app:general:32",
      channel: { type: "app", channelId: "general" },
      objective: "Finish the IC memo",
      sourceRequest: "Run the investment committee workflow",
      title: "Investment memo",
      completionCriteria: ["Final IC memo delivered"],
      turnsUsed: 2,
      maxTurns: 30,
      resumeContext: "Recent mission ledger before restart:\n- heartbeat: Drafted partner critique.",
    });
    expect(missionClient.appendEvent).not.toHaveBeenCalled();
  });

  it("notifies the goal controller when a mission cancel is requested", async () => {
    const actionEvent: MissionActionEvent = {
      id: "event-goal-cancel",
      mission_id: "mission-goal",
      event_type: "cancel_requested",
      created_at: "2026-05-09T15:17:00.000Z",
    };
    const backgroundTasks = new BackgroundTaskRegistry(root);
    const missionClient = {
      listActionEvents: vi.fn(async () => [actionEvent]),
      appendEvent: vi.fn(async () => ({})),
      abandonRunningOnRestart: vi.fn(async () => ({ abandoned: 0, missionIds: [] })),
    };
    const goalController = {
      resumeAfterRestart: vi.fn(async () => undefined),
      cancel: vi.fn(async () => undefined),
    };
    const reconciler = new MissionActionReconciler({
      workspaceRoot: root,
      missionClient,
      backgroundTasks,
      goals: goalController,
      pollIntervalMs: 60_000,
    });

    await reconciler.pollOnce();

    expect(goalController.cancel).toHaveBeenCalledWith("mission-goal", {
      actionEventId: "event-goal-cancel",
      reason: "mission_cancel_requested",
    });
  });

  it("does not block startup while restart goal resume is still running", async () => {
    const actionEvent: MissionActionEvent = {
      id: "event-goal-retry",
      mission_id: "mission-goal",
      event_type: "retry_requested",
      created_at: "2026-05-09T15:16:00.000Z",
      payload: {
        reason: "restart_recovery",
        startedAt: "2026-05-09T15:15:14.000Z",
        goal: {
          sessionKey: "agent:main:app:general:32",
          channelType: "app",
          channelId: "general",
          objective: "Finish the IC memo",
          sourceRequest: "Run the investment committee workflow",
          title: "Investment memo",
          completionCriteria: ["Final IC memo delivered"],
          turnsUsed: 2,
          maxTurns: 30,
          resumeContext: "Recent mission ledger before restart:\n- heartbeat: Drafted partner critique.",
        },
      },
    };
    const backgroundTasks = new BackgroundTaskRegistry(root);
    const missionClient = {
      listActionEvents: vi.fn(async () => [actionEvent]),
      appendEvent: vi.fn(async () => ({})),
      abandonRunningOnRestart: vi.fn(async () => ({ abandoned: 0, missionIds: [] })),
    };
    let resolveResume: () => void = () => {};
    const resumePending = new Promise<void>((resolve) => {
      resolveResume = resolve;
    });
    const goalResumer = {
      resumeAfterRestart: vi.fn(async () => resumePending),
    };
    const reconciler = new MissionActionReconciler({
      workspaceRoot: root,
      missionClient,
      backgroundTasks,
      goals: goalResumer,
      pollIntervalMs: 60_000,
    });

    const started = reconciler.start();

    await vi.waitFor(() => expect(goalResumer.resumeAfterRestart).toHaveBeenCalled());
    const result = await Promise.race([
      started.then(() => "resolved"),
      new Promise<"blocked">((resolve) => setTimeout(() => resolve("blocked"), 30)),
    ]);

    resolveResume();
    await started;
    reconciler.stop();
    expect(result).toBe("resolved");
  });
});
