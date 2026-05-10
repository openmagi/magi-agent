import fs from "node:fs/promises";
import path from "node:path";
import type { CronRecord } from "../cron/CronScheduler.js";
import { atomicWriteJson } from "../storage/atomicWrite.js";
import type {
  BackgroundTaskRecord,
  BackgroundTaskRegistry,
} from "../tasks/BackgroundTaskRegistry.js";
import type { MissionClient } from "./MissionClient.js";
import type {
  GoalMissionResumeInput,
  MissionActionEvent,
  MissionChannelType,
} from "./types.js";

const DEFAULT_POLL_INTERVAL_MS = 15_000;
const DEFAULT_LIMIT = 50;
const MAX_PROCESSED_IDS = 500;

export interface MissionActionReconcilerOptions {
  workspaceRoot: string;
  missionClient: Pick<
    MissionClient,
    "listActionEvents" | "appendEvent" | "abandonRunningOnRestart"
  >;
  backgroundTasks: Pick<
    BackgroundTaskRegistry,
    "findByMissionId" | "reconcileAbandonedRunning" | "stop"
  >;
  crons?: MissionActionCronScheduler;
  goals?: MissionGoalResumer;
  pollIntervalMs?: number;
  startedAt?: Date;
}

interface MissionGoalResumer {
  resumeAfterRestart(input: GoalMissionResumeInput): Promise<void>;
  cancel?(
    missionId: string,
    input: { actionEventId: string; reason: "mission_cancel_requested" },
  ): Promise<void> | void;
}

interface MissionActionCronScheduler {
  list(filter?: { enabled?: boolean; includeInternal?: boolean }): Array<
    Pick<CronRecord, "cronId" | "enabled" | "missionId" | "missionRunId">
  >;
  update(cronId: string, patch: { enabled?: boolean }): Promise<unknown>;
}

interface ActionCheckpoint {
  lastSeenAt?: string;
  processedEventIds: string[];
}

export class MissionActionReconciler {
  private readonly checkpointPath: string;
  private readonly pollIntervalMs: number;
  private readonly startedAt: string;
  private timer: ReturnType<typeof setInterval> | null = null;
  private pollInProgress = false;

  constructor(private readonly options: MissionActionReconcilerOptions) {
    this.checkpointPath = path.join(
      options.workspaceRoot,
      "core-agent",
      "missions",
      "action-reconciler.json",
    );
    this.pollIntervalMs = Math.max(
      1_000,
      options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS,
    );
    this.startedAt = (options.startedAt ?? new Date()).toISOString();
  }

  async start(): Promise<void> {
    if (this.timer) return;
    await this.safeReconcileAbandonedBackgroundTasks();
    await this.safeReconcileAbandonedMissions();
    void this.safePollOnce();
    this.timer = setInterval(() => {
      void this.safePollOnce();
    }, this.pollIntervalMs);
    this.timer.unref?.();
  }

  stop(): void {
    if (!this.timer) return;
    clearInterval(this.timer);
    this.timer = null;
  }

  async reconcileAbandonedBackgroundTasks(): Promise<BackgroundTaskRecord[]> {
    const abandoned = await this.options.backgroundTasks.reconcileAbandonedRunning(
      "abandoned_by_restart",
    );
    for (const record of abandoned) {
      if (!record.missionId) continue;
      await this.options.missionClient.appendEvent(record.missionId, {
        ...(record.missionRunId ? { runId: record.missionRunId } : {}),
        actorType: "system",
        eventType: "failed",
        message: "abandoned_by_restart",
        payload: {
          taskId: record.taskId,
          reason: "abandoned_by_restart",
        },
      });
    }
    return abandoned;
  }

  async reconcileAbandonedMissions(): Promise<{ abandoned: number; missionIds: string[] }> {
    return this.options.missionClient.abandonRunningOnRestart({
      startedAt: this.startedAt,
      reason: "abandoned_by_restart",
    });
  }

  async pollOnce(): Promise<void> {
    if (this.pollInProgress) return;
    this.pollInProgress = true;
    try {
      let checkpoint = await this.readCheckpoint();
      const events = await this.options.missionClient.listActionEvents({
        ...(checkpoint.lastSeenAt ? { since: checkpoint.lastSeenAt } : {}),
        limit: DEFAULT_LIMIT,
      });
      const processed = new Set(checkpoint.processedEventIds);
      const ordered = [...events].sort(compareMissionActionEvents);
      for (const event of ordered) {
        if (processed.has(event.id)) continue;
        await this.handleActionEvent(event);
        processed.add(event.id);
        checkpoint = {
          lastSeenAt: latestTimestamp(checkpoint.lastSeenAt, event.created_at),
          processedEventIds: [...processed].slice(-MAX_PROCESSED_IDS),
        };
        await this.writeCheckpoint(checkpoint);
      }
    } finally {
      this.pollInProgress = false;
    }
  }

  private async safeReconcileAbandonedBackgroundTasks(): Promise<void> {
    try {
      await this.reconcileAbandonedBackgroundTasks();
    } catch (err) {
      console.warn(
        `[core-agent] mission restart recovery failed: ${(err as Error).message}`,
      );
    }
  }

  private async safeReconcileAbandonedMissions(): Promise<void> {
    try {
      await this.reconcileAbandonedMissions();
    } catch (err) {
      console.warn(
        `[core-agent] mission restart ledger recovery failed: ${(err as Error).message}`,
      );
    }
  }

  private async safePollOnce(): Promise<void> {
    try {
      await this.pollOnce();
    } catch (err) {
      console.warn(
        `[core-agent] mission action poll failed: ${(err as Error).message}`,
      );
    }
  }

  private async handleActionEvent(event: MissionActionEvent): Promise<void> {
    if (event.event_type === "cancel_requested") {
      await this.handleCancelRequested(event);
      return;
    }
    const goalResume = parseRestartGoalResumeEvent(event);
    if (goalResume && this.options.goals) {
      await this.options.goals.resumeAfterRestart(goalResume);
      return;
    }
    await this.handleResumeRequested(event);
  }

  private async handleCancelRequested(event: MissionActionEvent): Promise<void> {
    await this.options.goals?.cancel?.(event.mission_id, {
      actionEventId: event.id,
      reason: "mission_cancel_requested",
    });

    const linkedTasks = await this.options.backgroundTasks.findByMissionId(
      event.mission_id,
    );
    for (const task of linkedTasks) {
      if (task.status === "running") {
        const stopped = await this.options.backgroundTasks.stop(
          task.taskId,
          "mission_cancel_requested",
        );
        if (!stopped) continue;
      } else if (task.error !== "stopped: mission_cancel_requested") {
        continue;
      }
      await this.options.missionClient.appendEvent(event.mission_id, {
        ...(task.missionRunId ? { runId: task.missionRunId } : {}),
        actorType: "system",
        eventType: "cancelled",
        message: `Background task ${task.taskId} cancelled by mission request.`,
        payload: {
          actionEventId: event.id,
          taskId: task.taskId,
          reason: "mission_cancel_requested",
        },
      });
    }

    for (const cron of this.linkedCrons(event.mission_id)) {
      if (cron.enabled) {
        await this.options.crons?.update(cron.cronId, { enabled: false });
      }
      await this.options.missionClient.appendEvent(event.mission_id, {
        ...(cron.missionRunId ? { runId: cron.missionRunId } : {}),
        actorType: "system",
        eventType: "cancelled",
        message: `Cron ${cron.cronId} disabled by mission cancel request.`,
        payload: {
          actionEventId: event.id,
          cronId: cron.cronId,
          reason: "mission_cancel_requested",
        },
      });
    }
  }

  private async handleResumeRequested(event: MissionActionEvent): Promise<void> {
    const linkedCrons = this.linkedCrons(event.mission_id);
    for (const cron of linkedCrons) {
      await this.options.crons?.update(cron.cronId, { enabled: true });
      await this.options.missionClient.appendEvent(event.mission_id, {
        ...(cron.missionRunId ? { runId: cron.missionRunId } : {}),
        actorType: "system",
        eventType: "resumed",
        message: `Cron ${cron.cronId} resumed by mission ${event.event_type}.`,
        payload: {
          actionEventId: event.id,
          cronId: cron.cronId,
          sourceEventType: event.event_type,
        },
      });
    }

    if (linkedCrons.length > 0) return;
    const linkedTasks = await this.options.backgroundTasks.findByMissionId(
      event.mission_id,
    );
    for (const task of linkedTasks.filter((record) => record.status !== "running")) {
      await this.options.missionClient.appendEvent(event.mission_id, {
        ...(task.missionRunId ? { runId: task.missionRunId } : {}),
        actorType: "system",
        eventType: "blocked",
        message: "Background task replay requires a fresh parent handoff.",
        payload: {
          actionEventId: event.id,
          taskId: task.taskId,
          sourceEventType: event.event_type,
          reason: "background_replay_context_missing",
        },
      });
    }
  }

  private linkedCrons(missionId: string): Array<
    Pick<CronRecord, "cronId" | "enabled" | "missionId" | "missionRunId">
  > {
    return (this.options.crons?.list({ includeInternal: true }) ?? []).filter(
      (cron) => cron.missionId === missionId,
    );
  }

  private async readCheckpoint(): Promise<ActionCheckpoint> {
    try {
      const raw = await fs.readFile(this.checkpointPath, "utf8");
      const parsed = JSON.parse(raw) as Partial<ActionCheckpoint>;
      return {
        ...(typeof parsed.lastSeenAt === "string"
          ? { lastSeenAt: parsed.lastSeenAt }
          : {}),
        processedEventIds: Array.isArray(parsed.processedEventIds)
          ? parsed.processedEventIds.filter((id): id is string => typeof id === "string")
          : [],
      };
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
        console.warn(
          `[core-agent] mission action checkpoint read failed: ${(err as Error).message}`,
        );
      }
      return { processedEventIds: [] };
    }
  }

  private async writeCheckpoint(checkpoint: ActionCheckpoint): Promise<void> {
    await atomicWriteJson(this.checkpointPath, checkpoint);
  }
}

function compareMissionActionEvents(
  left: MissionActionEvent,
  right: MissionActionEvent,
): number {
  const leftTime = left.created_at ?? "";
  const rightTime = right.created_at ?? "";
  if (leftTime < rightTime) return -1;
  if (leftTime > rightTime) return 1;
  return left.id.localeCompare(right.id);
}

function latestTimestamp(
  current: string | undefined,
  next: string | undefined,
): string | undefined {
  if (!next) return current;
  if (!current) return next;
  return next > current ? next : current;
}

function parseRestartGoalResumeEvent(
  event: MissionActionEvent,
): GoalMissionResumeInput | null {
  if (event.event_type !== "retry_requested") return null;
  const payload = event.payload ?? {};
  if (payload.reason !== "restart_recovery") return null;
  const goal = payload.goal;
  if (!goal || typeof goal !== "object" || Array.isArray(goal)) return null;
  const record = goal as Record<string, unknown>;
  const sessionKey = stringValue(record.sessionKey);
  const channelType = missionChannelType(record.channelType);
  const channelId = stringValue(record.channelId);
  const objective = stringValue(record.objective);
  const resumeContext = stringValue(record.resumeContext);
  if (!sessionKey || !channelType || !channelId || !objective) return null;
  return {
    actionEventId: event.id,
    missionId: event.mission_id,
    ...(typeof payload.startedAt === "string" ? { startedAt: payload.startedAt } : {}),
    sessionKey,
    channel: {
      type: channelType,
      channelId,
    },
    objective,
    ...(stringValue(record.sourceRequest) ? { sourceRequest: stringValue(record.sourceRequest) } : {}),
    ...(stringValue(record.title) ? { title: stringValue(record.title) } : {}),
    completionCriteria: stringArray(record.completionCriteria),
    turnsUsed: numberValue(record.turnsUsed) ?? 0,
    ...(numberValue(record.maxTurns) !== undefined ? { maxTurns: numberValue(record.maxTurns) } : {}),
    ...(resumeContext ? { resumeContext } : {}),
  };
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0
    ? value.trim()
    : undefined;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
}

function missionChannelType(value: unknown): MissionChannelType | undefined {
  return value === "app" || value === "telegram" || value === "discord" || value === "internal"
    ? value
    : undefined;
}
