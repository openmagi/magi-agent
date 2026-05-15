"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MissionActivity } from "@/lib/chat/types";
import {
  buildMissionWorkQueue,
  type MissionWorkQueueAction,
  type MissionWorkQueueFilter,
  type MissionWorkQueueRow,
  type MissionWorkQueueSection,
} from "@/lib/chat/mission-work-queue";
import type {
  MissionArtifact,
  MissionDetail,
  MissionEvent,
  MissionRun,
  MissionStatus,
  MissionSummary,
} from "@/lib/missions/types";
import {
  buildMissionTimeline,
  filterMissionTimelineItems,
  type MissionHandoffEvidence,
  type MissionTimelineActor,
  type MissionTimelineFilter,
  type MissionTimelineItem,
} from "@/lib/missions/timeline";

export type MissionChannelType = MissionSummary["channel_type"];

interface MissionsPanelProps {
  botId: string;
  channelType?: MissionChannelType;
  channelId?: string | null;
  liveMissions?: MissionActivity[];
  activeGoalMissionId?: string | null;
  missionRefreshSeq?: number;
  lastMissionEventMissionId?: string | null;
  focusMissionRequest?: MissionFocusRequest | null;
  pendingGoalTitle?: string | null;
  getAccessToken?: () => Promise<string | null>;
  initialFilter?: MissionWorkQueueFilter;
  initialSelectedMissionId?: string | null;
  initialDetail?: MissionDetail | null;
}

export interface MissionFocusRequest {
  missionId: string;
  nonce: number;
}

const FILTERS: Array<{ id: MissionWorkQueueFilter; label: string }> = [
  { id: "active", label: "Active" },
  { id: "needs_input", label: "Needs input" },
  { id: "done", label: "Done" },
  { id: "all", label: "All" },
];

const TIMELINE_FILTERS: Array<{ id: MissionTimelineFilter; label: string }> = [
  { id: "all", label: "All" },
  { id: "user", label: "User" },
  { id: "runtime", label: "Runtime" },
  { id: "evidence", label: "Evidence" },
];

export function buildMissionListUrl({
  botId,
  limit,
  channelType,
  channelId,
}: {
  botId: string;
  limit: number;
  channelType?: MissionChannelType;
  channelId?: string | null;
}): string {
  const params = new URLSearchParams({ limit: String(limit) });
  if (channelType && channelId) {
    params.set("channelType", channelType);
    params.set("channelId", channelId);
  }
  return `/api/bots/${encodeURIComponent(botId)}/missions?${params.toString()}`;
}

function countForFilter(
  filter: MissionWorkQueueFilter,
  counts: ReturnType<typeof buildMissionWorkQueue>["counts"],
): number {
  if (filter === "needs_input") return counts.needsInput;
  return counts[filter];
}

function statusDotClass(status: MissionStatus): string {
  switch (status) {
    case "running":
    case "queued":
      return "bg-[#7C3AED]";
    case "completed":
      return "bg-emerald-500";
    case "failed":
    case "cancelled":
      return "bg-red-500";
    case "blocked":
    case "waiting":
    case "paused":
    default:
      return "bg-amber-500";
  }
}

function sectionClass(section: MissionWorkQueueSection): string {
  if (section.kind === "needs_input") {
    return "border-amber-500/20 bg-amber-50/80";
  }
  if (section.kind === "running") {
    return "border-[#7C3AED]/15 bg-[#F8F6FF]";
  }
  return "border-black/[0.06] bg-white/75";
}

function actionLabel(action: MissionWorkQueueAction): string {
  if (action === "unblock") return "Unblock";
  if (action === "retry") return "Retry";
  return "Cancel";
}

function detailActionLabel(action: MissionWorkQueueAction, status: MissionStatus): string {
  if (action === "unblock" && (status === "paused" || status === "waiting")) return "Resume";
  return actionLabel(action);
}

function detailActionsForStatus(status: MissionStatus): MissionWorkQueueAction[] {
  if (status === "blocked" || status === "waiting" || status === "paused") {
    return ["unblock", "cancel"];
  }
  if (status === "failed" || status === "cancelled") return ["retry"];
  if (status === "queued" || status === "running") return ["cancel"];
  return [];
}

function detailText(row: MissionWorkQueueRow): string | undefined {
  return row.detail ?? row.summary;
}

function turnsLabel(row: MissionWorkQueueRow): string | null {
  if (!row.budgetTurns) return null;
  return `${row.usedTurns}/${row.budgetTurns} turns`;
}

function reasonFromStatus(status: MissionStatus): string | null {
  if (status === "blocked" || status === "waiting" || status === "paused") {
    return "Needs input";
  }
  if (status === "cancelled") return "Cancelled";
  if (status === "failed") return "Failed";
  if (status === "completed") return "Completed";
  if (status === "queued") return "Queued";
  return null;
}

function reasonFromText(text: string | null | undefined): string | null {
  if (!text) return null;
  const normalized = text.toLowerCase();
  if (normalized.includes("resumed after restart")) return "Resumed after restart";
  if (normalized.includes("restart_recovery") || normalized.includes("restart recovery")) {
    return normalized.includes("requested")
      ? "Restart recovery requested"
      : "Resumed after restart";
  }
  if (normalized.includes("continuation scheduled")) return "Continuation scheduled";
  if (normalized.includes("quiet tick")) return "Quiet tick";
  if (
    normalized.includes("cancel") &&
    (normalized.includes("mission request") || normalized.includes("user"))
  ) {
    return "Cancelled by user";
  }
  return null;
}

function eventPayloadString(event: MissionEvent, key: string): string | null {
  const value = event.payload[key];
  return typeof value === "string" ? value : null;
}

function missionEventReason(event: MissionEvent): string | null {
  const payloadReason = eventPayloadString(event, "reason");
  const sourceEventType = eventPayloadString(event, "sourceEventType");
  const messageReason = reasonFromText(event.message);

  if (event.event_type === "cancel_requested") return "Cancelled by user";
  if (event.event_type === "cancelled") {
    if (
      event.actor_type === "user" ||
      payloadReason === "mission_cancel_requested" ||
      messageReason === "Cancelled by user"
    ) {
      return "Cancelled by user";
    }
    return "Cancelled";
  }
  if (event.event_type === "retry_requested") {
    if (payloadReason === "restart_recovery" || messageReason === "Restart recovery requested") {
      return "Restart recovery requested";
    }
    if (payloadReason === "manual_retry") {
      return event.actor_type === "user" ? "Retry requested by user" : "Retry requested";
    }
    return event.actor_type === "user" ? "Retry requested by user" : "Retry requested";
  }
  if (event.event_type === "resumed") {
    if (payloadReason === "restart_recovery" || messageReason === "Resumed after restart") {
      return "Resumed after restart";
    }
    if (sourceEventType === "retry_requested") return "Retry run started";
    if (sourceEventType === "unblocked") return "Resumed after unblock";
    return event.actor_type === "user" ? "Resumed by user" : "Resumed";
  }
  if (event.event_type === "unblocked") {
    return event.actor_type === "user" ? "Unblocked by user" : "Unblocked";
  }
  if (event.event_type === "blocked" || event.event_type === "paused") return "Needs input";
  if (event.event_type === "failed") return "Failed";
  if (event.event_type === "completed") return messageReason ?? "Completed";
  return messageReason;
}

function missionRowStatusReason(row: MissionWorkQueueRow): string | null {
  return reasonFromText(row.detail)
    ?? reasonFromText(row.summary)
    ?? reasonFromStatus(row.status);
}

function missionDetailStatusReason(
  detail: MissionDetail | null,
  fallbackRow: MissionWorkQueueRow | null,
): string | null {
  if (detail) {
    for (const event of [...detail.events].reverse()) {
      const reason = missionEventReason(event);
      if (reason) return reason;
    }
    return reasonFromStatus(detail.mission.status);
  }
  return fallbackRow ? missionRowStatusReason(fallbackRow) : null;
}

function runLabel(run: MissionRun): string {
  return `${run.trigger_type} ${run.status}`;
}

function runDetail(run: MissionRun): string | undefined {
  if (run.error_message) return run.error_message;
  if (run.trigger_type === "script_cron") {
    return run.stdout_preview ?? run.result_preview ?? undefined;
  }
  return run.result_preview ?? run.stdout_preview ?? undefined;
}

function runMeta(run: MissionRun, index: number): string {
  return [
    `attempt ${index + 1}`,
    `run ${run.id}`,
    run.turn_id ? `turn ${run.turn_id}` : null,
    run.spawn_task_id ? `spawn ${run.spawn_task_id}` : null,
    run.cron_id ? `cron ${run.cron_id}` : null,
  ].filter(Boolean).join(" · ");
}

function eventLabel(event: MissionEvent): string {
  const reason = missionEventReason(event);
  const rawLabel = `${event.actor_type} ${event.event_type}`;
  return reason ? `${reason} - ${rawLabel}` : rawLabel;
}

function artifactTarget(artifact: MissionArtifact): string | null {
  return artifact.uri ?? artifact.storage_key ?? null;
}

function actorLabel(actor: MissionTimelineActor): string {
  if (actor === "parent_agent") return "parent agent";
  if (actor === "child_agent") return "child agent";
  return actor;
}

export function shouldReloadMissionDetailForEvent({
  selectedMissionId,
  lastMissionEventMissionId,
}: {
  selectedMissionId: string | null;
  lastMissionEventMissionId: string | null | undefined;
}): boolean {
  if (!selectedMissionId) return false;
  return !lastMissionEventMissionId || selectedMissionId === lastMissionEventMissionId;
}

function StatusReasonLabel({ reason }: { reason: string }): React.ReactElement {
  return (
    <span
      className="inline-flex max-w-full items-center rounded bg-black/[0.035] px-1.5 py-0.5 text-[10px] font-medium text-secondary/60"
      aria-label={`Mission reason: ${reason}`}
      title={reason}
    >
      <span className="truncate">{reason}</span>
    </span>
  );
}

export function MissionsPanel({
  botId,
  channelType,
  channelId,
  liveMissions = [],
  activeGoalMissionId = null,
  missionRefreshSeq = 0,
  lastMissionEventMissionId = null,
  focusMissionRequest = null,
  pendingGoalTitle = null,
  getAccessToken,
  initialFilter = "active",
  initialSelectedMissionId = null,
  initialDetail = null,
}: MissionsPanelProps): React.ReactElement {
  const [missions, setMissions] = useState<MissionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [filter, setFilter] = useState<MissionWorkQueueFilter>(initialFilter);
  const [query, setQuery] = useState("");
  const [selectedMissionId, setSelectedMissionId] = useState<string | null>(
    initialSelectedMissionId,
  );
  const [detail, setDetail] = useState<MissionDetail | null>(initialDetail);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [commentDraft, setCommentDraft] = useState("");
  const [actionReasonDraft, setActionReasonDraft] = useState("");
  const detailRequestSeq = useRef(0);
  const observedMissionRefreshSeq = useRef(missionRefreshSeq);
  const observedFocusNonce = useRef(focusMissionRequest?.nonce ?? 0);

  const loadMissions = useCallback(async () => {
    if (!botId) return;
    setLoading(true);
    setError(null);
    try {
      const token = await getAccessToken?.();
      const response = await fetch(buildMissionListUrl({
        botId,
        limit: 50,
        channelType,
        channelId,
      }), {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      const body = (await response.json().catch(() => null)) as {
        missions?: MissionSummary[];
        error?: string;
      } | null;
      if (!response.ok) {
        throw new Error(body?.error ?? "Failed to load missions");
      }
      setMissions(Array.isArray(body?.missions) ? body.missions : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load missions");
    } finally {
      setLoading(false);
    }
  }, [botId, channelId, channelType, getAccessToken]);

  const closeDetail = useCallback(() => {
    detailRequestSeq.current += 1;
    setSelectedMissionId(null);
    setDetail(null);
    setDetailLoading(false);
    setDetailError(null);
    setCommentDraft("");
    setActionReasonDraft("");
  }, []);

  const loadDetail = useCallback(async (missionId: string) => {
    const requestSeq = detailRequestSeq.current + 1;
    detailRequestSeq.current = requestSeq;
    setDetailLoading(true);
    setDetailError(null);
    try {
      const token = await getAccessToken?.();
      const response = await fetch(
        `/api/bots/${encodeURIComponent(botId)}/missions/${encodeURIComponent(missionId)}`,
        { headers: token ? { Authorization: `Bearer ${token}` } : undefined },
      );
      const body = (await response.json().catch(() => null)) as
        | (MissionDetail & { error?: string })
        | null;
      if (!response.ok || !body) {
        throw new Error(body?.error ?? "Failed to load mission detail");
      }
      if (detailRequestSeq.current === requestSeq) {
        setDetail(body);
      }
    } catch (err) {
      if (detailRequestSeq.current === requestSeq) {
        setDetailError(err instanceof Error ? err.message : "Failed to load mission detail");
      }
    } finally {
      if (detailRequestSeq.current === requestSeq) {
        setDetailLoading(false);
      }
    }
  }, [botId, getAccessToken]);

  useEffect(() => {
    void loadMissions();
  }, [loadMissions]);

  const queue = useMemo(
    () =>
      buildMissionWorkQueue({
        summaries: missions,
        liveMissions,
        filter,
        query,
        activeGoalMissionId,
      }),
    [activeGoalMissionId, filter, liveMissions, missions, query],
  );

  const openDetail = useCallback((missionId: string) => {
    setSelectedMissionId(missionId);
    if (detail?.mission.id !== missionId) {
      setDetail(null);
      void loadDetail(missionId);
    }
  }, [detail?.mission.id, loadDetail]);

  useEffect(() => {
    if (!focusMissionRequest) return;
    if (focusMissionRequest.nonce === observedFocusNonce.current) return;
    observedFocusNonce.current = focusMissionRequest.nonce;
    openDetail(focusMissionRequest.missionId);
  }, [focusMissionRequest, openDetail]);

  useEffect(() => {
    if (missionRefreshSeq <= observedMissionRefreshSeq.current) return;
    observedMissionRefreshSeq.current = missionRefreshSeq;
    void loadMissions();
    const detailMissionId = selectedMissionId;
    if (shouldReloadMissionDetailForEvent({
      selectedMissionId: detailMissionId,
      lastMissionEventMissionId,
    }) && detailMissionId) {
      void loadDetail(detailMissionId);
    }
  }, [
    lastMissionEventMissionId,
    loadDetail,
    loadMissions,
    missionRefreshSeq,
    selectedMissionId,
  ]);

  const selectFilter = useCallback((nextFilter: MissionWorkQueueFilter) => {
    setFilter(nextFilter);
    closeDetail();
  }, [closeDetail]);

  const submitAction = useCallback(async (
    missionId: string,
    action: MissionWorkQueueAction,
    message?: string,
  ) => {
    setBusyAction(`${missionId}:${action}`);
    setError(null);
    try {
      const token = await getAccessToken?.();
      const trimmedMessage = message?.trim() ?? "";
      const response = await fetch(
        `/api/bots/${encodeURIComponent(botId)}/missions/${encodeURIComponent(missionId)}/${action}`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({ message: trimmedMessage }),
        },
      );
      const body = (await response.json().catch(() => null)) as { error?: string } | null;
      if (!response.ok) {
        throw new Error(body?.error ?? `Failed to ${action} mission`);
      }
      setActionReasonDraft("");
      await loadMissions();
      if (selectedMissionId === missionId) await loadDetail(missionId);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${action} mission`);
    } finally {
      setBusyAction(null);
    }
  }, [botId, getAccessToken, loadDetail, loadMissions, selectedMissionId]);

  const submitComment = useCallback(async () => {
    const message = commentDraft.trim();
    if (!selectedMissionId || !message) return;
    setBusyAction(`${selectedMissionId}:comment`);
    setDetailError(null);
    try {
      const token = await getAccessToken?.();
      const response = await fetch(
        `/api/bots/${encodeURIComponent(botId)}/missions/${encodeURIComponent(selectedMissionId)}/comments`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({ message }),
        },
      );
      const body = (await response.json().catch(() => null)) as { error?: string } | null;
      if (!response.ok) {
        throw new Error(body?.error ?? "Failed to add comment");
      }
      setCommentDraft("");
      await loadDetail(selectedMissionId);
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "Failed to add comment");
    } finally {
      setBusyAction(null);
    }
  }, [botId, commentDraft, getAccessToken, loadDetail, selectedMissionId]);

  const selectedRow = selectedMissionId
    ? queue.rows.find((row) => row.id === selectedMissionId)
      ?? (queue.activeGoal?.id === selectedMissionId ? queue.activeGoal : null)
    : null;
  const activeGoalReason = queue.activeGoal
    ? missionRowStatusReason(queue.activeGoal)
    : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col" aria-label="Missions ledger">
      <div className="border-b border-black/[0.06] px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-secondary/70">
              Work Queue
            </div>
            <div className="mt-0.5 text-[10px] font-medium text-secondary/40">
              Missions
            </div>
          </div>
          <button
            type="button"
            onClick={() => void loadMissions()}
            disabled={loading}
            className="rounded-md px-2 py-1 text-[10px] font-medium text-secondary/55 transition-colors hover:bg-black/[0.04] hover:text-foreground disabled:cursor-wait disabled:opacity-50"
          >
            Refresh
          </button>
        </div>
        <p className="mt-1 text-[11px] leading-snug text-secondary/45">
          Durable background work, handoffs, and goal continuations for this bot.
        </p>
      </div>

      <div className="border-b border-black/[0.06] px-2 py-2">
        <div className="grid grid-cols-4 rounded-lg bg-black/[0.04] p-0.5" role="tablist" aria-label="Mission filters">
          {FILTERS.map((item) => {
            const active = filter === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => selectFilter(item.id)}
                className={`min-w-0 rounded-md px-1.5 py-1.5 text-[10.5px] font-medium transition-colors ${
                  active
                    ? "bg-white text-foreground shadow-sm"
                    : "text-secondary/60 hover:text-foreground"
                }`}
                role="tab"
                aria-selected={active}
              >
                <span className="inline-flex max-w-full items-center gap-1">
                  <span className="truncate">{item.label}</span>
                  <span className={active ? "text-secondary/60" : "text-secondary/35"}>
                    {countForFilter(item.id, queue.counts)}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
        <label className="mt-2 block">
          <span className="sr-only">Search missions</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search mission title, kind, or status"
            className="h-8 w-full rounded-md border border-black/[0.06] bg-white/80 px-2 text-[11px] text-foreground outline-none transition-colors placeholder:text-secondary/35 focus:border-[#7C3AED]/40 focus:bg-white"
          />
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {error && (
          <div className="mb-2 rounded-lg border border-red-500/15 bg-red-50 px-2.5 py-2 text-[11px] text-red-700">
            {error}
          </div>
        )}

        {queue.activeGoal ? (
          <div className="mb-2 rounded-lg border border-[#7C3AED]/20 bg-[#F8F6FF] px-2.5 py-2">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-[#6D28D9]/70">
                  Active goal
                </div>
                <div className="mt-0.5 truncate text-[12px] font-medium text-foreground/85">
                  {queue.activeGoal.title}
                </div>
              </div>
              <span className="shrink-0 rounded-md bg-white/80 px-1.5 py-1 text-[10px] font-medium text-secondary/60">
                {queue.activeGoal.status}
              </span>
            </div>
            {activeGoalReason && (
              <div className="mt-1">
                <StatusReasonLabel reason={activeGoalReason} />
              </div>
            )}
            {detailText(queue.activeGoal) && (
              <p className="mt-1 line-clamp-2 break-words text-[11px] leading-snug text-secondary/65">
                {detailText(queue.activeGoal)}
              </p>
            )}
            {turnsLabel(queue.activeGoal) && (
              <div className="mt-1 text-[10px] text-secondary/45">
                {turnsLabel(queue.activeGoal)}
              </div>
            )}
          </div>
        ) : pendingGoalTitle ? (
          <div className="mb-2 rounded-lg border border-[#7C3AED]/20 bg-[#F8F6FF] px-2.5 py-2">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-[#6D28D9]/70">
                  Active goal
                </div>
                <div className="mt-0.5 truncate text-[12px] font-medium text-foreground/85">
                  {pendingGoalTitle}
                </div>
              </div>
              <span className="shrink-0 rounded-md bg-white/80 px-1.5 py-1 text-[10px] font-medium text-secondary/60">
                Starting mission
              </span>
            </div>
            <p className="mt-1 text-[11px] leading-snug text-secondary/60">
              Waiting for the runtime to create the durable ledger entry.
            </p>
          </div>
        ) : null}

        {queue.sections.length === 0 ? (
          <div className="rounded-lg border border-black/[0.06] bg-white/70 px-3 py-3 text-[11.5px] text-secondary/55">
            No durable missions match this view.
          </div>
        ) : (
          <div className="space-y-2">
            {queue.sections.map((section) => (
              <section
                key={section.kind}
                className={`rounded-lg border px-2 py-2 ${sectionClass(section)}`}
              >
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <h3 className="text-[10px] font-semibold uppercase tracking-wide text-secondary/55">
                    {section.label}
                  </h3>
                  <span className="text-[10px] text-secondary/35">{section.rows.length}</span>
                </div>
                <ul className="space-y-1.5">
                  {section.rows.map((mission) => {
                    const actionKey = mission.action ? `${mission.id}:${mission.action}` : null;
                    const statusReason = missionRowStatusReason(mission);
                    return (
                      <li
                        key={mission.id}
                        className="rounded-lg border border-black/[0.06] bg-white/85 px-2.5 py-2 shadow-[0_1px_3px_rgba(15,23,42,0.04)]"
                        data-mission-row="true"
                      >
                        <div className="flex min-w-0 items-start gap-2">
                          <span
                            className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${statusDotClass(mission.status)}`}
                            aria-hidden="true"
                          />
                          <button
                            type="button"
                            onClick={() => openDetail(mission.id)}
                            className="min-w-0 flex-1 text-left"
                            aria-label={`Open mission ${mission.title}`}
                          >
                            <span className="flex min-w-0 items-baseline gap-2">
                              <span className="min-w-0 truncate text-[12px] font-medium text-foreground/85">
                                {mission.title}
                              </span>
                              <span className="shrink-0 text-[10px] text-secondary/40">
                                {mission.kind}
                              </span>
                            </span>
                            <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-secondary/45">
                              <span>{mission.status}</span>
                              {statusReason && <StatusReasonLabel reason={statusReason} />}
                              <span>{mission.updatedLabel}</span>
                              {turnsLabel(mission) && <span>{turnsLabel(mission)}</span>}
                            </span>
                            {detailText(mission) && (
                              <span className="mt-1 line-clamp-3 break-words text-[11px] leading-snug text-secondary/65">
                                {detailText(mission)}
                              </span>
                            )}
                          </button>
                          {mission.action && (
                            <button
                              type="button"
                              onClick={() => void submitAction(mission.id, mission.action!)}
                              disabled={busyAction === actionKey}
                              className="shrink-0 rounded-md border border-black/[0.06] bg-black/[0.03] px-2 py-1 text-[10px] font-medium text-secondary/70 transition-colors hover:bg-black/[0.06] hover:text-foreground disabled:cursor-wait disabled:opacity-50"
                              aria-label={`${actionLabel(mission.action)} mission ${mission.title}`}
                            >
                              {actionLabel(mission.action)}
                            </button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        )}

        {(selectedMissionId || selectedRow || detail || detailLoading || detailError) && (
          <MissionDetailLedger
            detail={detail}
            detailLoading={detailLoading}
            detailError={detailError}
            fallbackRow={selectedRow}
            busyAction={busyAction}
            commentDraft={commentDraft}
            actionReasonDraft={actionReasonDraft}
            onCommentDraftChange={setCommentDraft}
            onActionReasonDraftChange={setActionReasonDraft}
            onSubmitComment={() => void submitComment()}
            onSubmitAction={(missionId, action, message) => void submitAction(missionId, action, message)}
            onClose={closeDetail}
          />
        )}
      </div>
    </div>
  );
}

function MissionDetailLedger({
  detail,
  detailLoading,
  detailError,
  fallbackRow,
  busyAction,
  commentDraft,
  actionReasonDraft,
  onCommentDraftChange,
  onActionReasonDraftChange,
  onSubmitComment,
  onSubmitAction,
  onClose,
}: {
  detail: MissionDetail | null;
  detailLoading: boolean;
  detailError: string | null;
  fallbackRow: MissionWorkQueueRow | null;
  busyAction: string | null;
  commentDraft: string;
  actionReasonDraft: string;
  onCommentDraftChange: (value: string) => void;
  onActionReasonDraftChange: (value: string) => void;
  onSubmitComment: () => void;
  onSubmitAction: (missionId: string, action: MissionWorkQueueAction, message?: string) => void;
  onClose: () => void;
}): React.ReactElement {
  const [timelineFilter, setTimelineFilter] = useState<MissionTimelineFilter>("all");
  const missionId = detail?.mission.id ?? fallbackRow?.id ?? null;
  const title = detail?.mission.title ?? fallbackRow?.title ?? "Mission";
  const status = detail?.mission.status ?? fallbackRow?.status ?? "running";
  const statusReason = missionDetailStatusReason(detail, fallbackRow);
  const timeline = detail ? buildMissionTimeline(detail) : null;
  const timelineGroups = useMemo(() => {
    if (!timeline) return [];
    return timeline.attemptGroups
      .map((group) => ({
        ...group,
        items: filterMissionTimelineItems({ items: group.items }, timelineFilter),
      }))
      .filter((group) => group.items.length > 0);
  }, [timeline, timelineFilter]);
  const detailActions = detail ? detailActionsForStatus(detail.mission.status) : [];
  const actionReason = actionReasonDraft.trim();

  useEffect(() => {
    setTimelineFilter("all");
  }, [missionId]);

  return (
    <aside className="mt-3 rounded-lg border border-black/[0.08] bg-white/90 px-2.5 py-2.5" aria-label="Mission ledger">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-secondary/55">
            Mission ledger
          </div>
          <h3 className="mt-0.5 truncate text-[12px] font-semibold text-foreground/85">
            {title}
          </h3>
          <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-secondary/45">
            <span>{status}</span>
            {statusReason && <StatusReasonLabel reason={statusReason} />}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={onClose}
            aria-label="Close mission ledger"
            title="Close"
            className="rounded-md p-1 text-secondary/45 transition-colors hover:bg-black/[0.04] hover:text-foreground"
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
      </div>

      {detailLoading && (
        <div className="mt-2 rounded-md bg-black/[0.03] px-2 py-2 text-[11px] text-secondary/55">
          Loading mission detail...
        </div>
      )}
      {detailError && (
        <div className="mt-2 rounded-md border border-red-500/15 bg-red-50 px-2 py-2 text-[11px] text-red-700">
          {detailError}
        </div>
      )}

      {detail && (
        <div className="mt-2 space-y-2">
          {detail.mission.summary && (
            <p className="break-words rounded-md bg-black/[0.03] px-2 py-1.5 text-[11px] leading-snug text-secondary/65">
              {detail.mission.summary}
            </p>
          )}
          {detail.mission.budget_turns && (
            <div className="text-[10px] text-secondary/45">
              {detail.mission.used_turns}/{detail.mission.budget_turns} turns used
            </div>
          )}
          <MissionDetailSection title="Timeline">
            {timeline && (
              <TimelineFilterControls
                activeFilter={timelineFilter}
                counts={timeline.filterCounts}
                onChange={setTimelineFilter}
              />
            )}
            {timeline && timelineGroups.length > 0 ? (
              timelineGroups.map((group) => (
                <div key={group.id} className="space-y-1" data-mission-timeline-group={group.id}>
                  <div className="flex min-w-0 items-center justify-between gap-2 rounded bg-black/[0.025] px-2 py-1">
                    <span className="truncate text-[10px] font-semibold text-secondary/60">
                      {group.label}
                    </span>
                    <span className="shrink-0 text-[10px] text-secondary/35">
                      {group.items.length}
                    </span>
                  </div>
                  {group.items.map((item) => (
                    <TimelineLine key={item.id} item={item} />
                  ))}
                </div>
              ))
            ) : (
              <EmptyDetailText>No timeline events recorded.</EmptyDetailText>
            )}
          </MissionDetailSection>
          <MissionDetailSection title="Handoff evidence">
            {timeline && timeline.evidence.length > 0 ? (
              timeline.evidence.slice(0, 6).map((evidence) => (
                <HandoffEvidenceLine key={evidence.id} evidence={evidence} />
              ))
            ) : (
              <EmptyDetailText>No handoff evidence recorded.</EmptyDetailText>
            )}
          </MissionDetailSection>
          <MissionDetailSection title="Runs">
            {detail.runs.length === 0 ? (
              <EmptyDetailText>No runs recorded.</EmptyDetailText>
            ) : (
              detail.runs.slice(0, 5).map((run, index) => (
                <DetailLine
                  key={run.id}
                  label={runLabel(run)}
                  meta={runMeta(run, index)}
                  detail={runDetail(run)}
                />
              ))
            )}
          </MissionDetailSection>
          <MissionDetailSection title="Events">
            {detail.events.length === 0 ? (
              <EmptyDetailText>No events recorded.</EmptyDetailText>
            ) : (
              detail.events.slice(-6).map((event) => (
                <DetailLine key={event.id} label={eventLabel(event)} detail={event.message ?? undefined} />
              ))
            )}
          </MissionDetailSection>
          <MissionDetailSection title="Artifacts">
            {detail.artifacts.length === 0 ? (
              <EmptyDetailText>No artifacts recorded.</EmptyDetailText>
            ) : (
              detail.artifacts.slice(0, 5).map((artifact) => (
                <ArtifactLine key={artifact.id} artifact={artifact} />
              ))
            )}
          </MissionDetailSection>
          <div>
            <label className="text-[10px] font-semibold uppercase tracking-wide text-secondary/55">
              Add comment
            </label>
            <textarea
              value={commentDraft}
              onChange={(event) => onCommentDraftChange(event.target.value)}
              rows={2}
              className="mt-1 w-full resize-none rounded-md border border-black/[0.06] bg-white px-2 py-1.5 text-[11px] text-foreground outline-none placeholder:text-secondary/35 focus:border-[#7C3AED]/40"
              placeholder="Leave an unblock note, retry reason, or audit comment"
            />
            <button
              type="button"
              onClick={onSubmitComment}
              disabled={!commentDraft.trim() || busyAction === `${detail.mission.id}:comment`}
              className="mt-1 rounded-md border border-black/[0.06] bg-black/[0.03] px-2 py-1 text-[10px] font-medium text-secondary/70 transition-colors hover:bg-black/[0.06] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
            >
              Add comment
            </button>
          </div>
          {missionId && detailActions.length > 0 && (
            <MissionDetailSection title="Human controls">
              <label className="text-[10px] font-semibold uppercase tracking-wide text-secondary/55">
                Action reason
              </label>
              <textarea
                value={actionReasonDraft}
                onChange={(event) => onActionReasonDraftChange(event.target.value)}
                rows={2}
                className="mt-1 w-full resize-none rounded-md border border-black/[0.06] bg-white px-2 py-1.5 text-[11px] text-foreground outline-none placeholder:text-secondary/35 focus:border-[#7C3AED]/40"
                placeholder="Record why this mission is being retried, resumed, or cancelled"
              />
              <div className="mt-1 flex flex-wrap gap-1">
                {detailActions.map((detailAction) => {
                  const actionKey = `${missionId}:${detailAction}`;
                  const disabled = busyAction === actionKey;
                  return (
                    <button
                      key={detailAction}
                      type="button"
                      onClick={() => onSubmitAction(missionId, detailAction, actionReason || undefined)}
                      disabled={disabled}
                      aria-disabled={disabled}
                      aria-label={`${detailActionLabel(detailAction, status)} mission`}
                      data-mission-detail-action={detailAction}
                      className="rounded-md border border-black/[0.06] bg-black/[0.03] px-2 py-1 text-[10px] font-medium text-secondary/70 transition-colors hover:bg-black/[0.06] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {detailActionLabel(detailAction, status)}
                    </button>
                  );
                })}
              </div>
            </MissionDetailSection>
          )}
        </div>
      )}
    </aside>
  );
}

function TimelineFilterControls({
  activeFilter,
  counts,
  onChange,
}: {
  activeFilter: MissionTimelineFilter;
  counts: Record<MissionTimelineFilter, number>;
  onChange: (filter: MissionTimelineFilter) => void;
}) {
  return (
    <div
      className="mb-1 grid grid-cols-4 rounded-md bg-black/[0.035] p-0.5"
      aria-label="Timeline filters"
      role="tablist"
    >
      {TIMELINE_FILTERS.map((filter) => {
        const active = activeFilter === filter.id;
        return (
          <button
            key={filter.id}
            type="button"
            data-mission-timeline-filter={filter.id}
            onClick={() => onChange(filter.id)}
            role="tab"
            aria-selected={active}
            className={`min-w-0 rounded px-1 py-1 text-[10px] font-medium transition-colors ${
              active
                ? "bg-white text-foreground shadow-sm"
                : "text-secondary/55 hover:text-foreground"
            }`}
          >
            <span className="inline-flex max-w-full items-center gap-1">
              <span className="truncate">{filter.label}</span>
              <span className={active ? "text-secondary/55" : "text-secondary/35"}>
                {counts[filter.id]}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function TimelineLine({ item }: { item: MissionTimelineItem }) {
  return (
    <div className="rounded-md bg-black/[0.03] px-2 py-1.5">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[10.5px] font-medium text-foreground/75">
            {item.label}
          </div>
          <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-1 text-[10px] text-secondary/45">
            <span>{actorLabel(item.actorType)}</span>
            {item.attempt && <span>Attempt {item.attempt}</span>}
            {item.shortRunId && <span>{item.shortRunId}</span>}
          </div>
        </div>
      </div>
      {(item.message || item.detail) && (
        <div className="mt-0.5 line-clamp-3 break-words text-[10.5px] leading-snug text-secondary/60">
          {[item.message, item.detail].filter(Boolean).join(" · ")}
        </div>
      )}
    </div>
  );
}

function HandoffEvidenceLine({ evidence }: { evidence: MissionHandoffEvidence }) {
  const target = evidence.uri ?? evidence.storageKey ?? null;
  return (
    <div className="rounded-md bg-black/[0.03] px-2 py-1.5">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[10.5px] font-medium text-foreground/75">
            {target ? (
              <a href={target} target="_blank" rel="noreferrer" className="hover:underline">
                {evidence.title}
              </a>
            ) : (
              evidence.title
            )}
          </div>
          <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-1 text-[10px] text-secondary/45">
            <span>{evidence.sourceLabel}</span>
            {evidence.attempt && <span>Attempt {evidence.attempt}</span>}
            {evidence.shortRunId && <span>{evidence.shortRunId}</span>}
            {evidence.persona && <span>{evidence.persona}</span>}
            {evidence.taskId && <span>{evidence.taskId}</span>}
            {evidence.sourceId && <span>{evidence.sourceId}</span>}
            {evidence.parallelGroup && <span>{evidence.parallelGroup}</span>}
            {evidence.synthesisId && <span>{evidence.synthesisId}</span>}
          </div>
        </div>
      </div>
      {evidence.preview && (
        <div className="mt-0.5 line-clamp-3 break-words text-[10.5px] leading-snug text-secondary/60">
          {evidence.preview}
        </div>
      )}
    </div>
  );
}

function MissionDetailSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h4 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-secondary/55">
        {title}
      </h4>
      <div className="space-y-1">{children}</div>
    </section>
  );
}

function EmptyDetailText({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md bg-black/[0.03] px-2 py-1.5 text-[11px] text-secondary/45">
      {children}
    </div>
  );
}

function DetailLine({
  label,
  meta,
  detail,
}: {
  label: string;
  meta?: string;
  detail?: string;
}) {
  return (
    <div className="rounded-md bg-black/[0.03] px-2 py-1.5">
      <div className="text-[10.5px] font-medium text-foreground/75">{label}</div>
      {meta && (
        <div className="mt-0.5 break-words text-[10px] leading-snug text-secondary/45">
          {meta}
        </div>
      )}
      {detail && (
        <div className="mt-0.5 line-clamp-3 break-words text-[10.5px] leading-snug text-secondary/60">
          {detail}
        </div>
      )}
    </div>
  );
}

function ArtifactLine({ artifact }: { artifact: MissionArtifact }) {
  const target = artifactTarget(artifact);
  return (
    <div className="rounded-md bg-black/[0.03] px-2 py-1.5">
      <div className="text-[10.5px] font-medium text-foreground/75">
        {target ? (
          <a href={target} target="_blank" rel="noreferrer" className="hover:underline">
            {artifact.title}
          </a>
        ) : (
          artifact.title
        )}
      </div>
      {artifact.preview && (
        <div className="mt-0.5 line-clamp-3 break-words text-[10.5px] leading-snug text-secondary/60">
          {artifact.preview}
        </div>
      )}
    </div>
  );
}
