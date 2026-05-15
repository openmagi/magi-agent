"use client";

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import Link from "next/link";
import { GlassCard } from "@/components/ui/glass-card";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { usePrivy } from "@privy-io/react-auth";
import {
  eventSeverity,
  formatEventName,
  pipelineDisplayName,
} from "@/lib/bots/pipeline-types";
import type {
  PipelineEvent,
  PipelineMeta,
} from "@/lib/bots/pipeline-types";

const POLL_FALLBACK_MS = 5000;

function relativeTime(ts: number): string {
  const diff = Math.max(0, Date.now() - ts);
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

interface StepState {
  stepId: string;
  status: "spawned" | "verified" | "completed" | "failed" | "stalled" | "phantom";
  lastTs: number;
  attempts: number;
}

function deriveStepStates(events: PipelineEvent[]): StepState[] {
  const map = new Map<string, StepState>();
  for (const ev of events) {
    if (!ev.stepId) continue;
    const cur = map.get(ev.stepId) ?? { stepId: ev.stepId, status: "spawned" as const, lastTs: 0, attempts: 0 };
    cur.lastTs = Math.max(cur.lastTs, ev.ts);
    switch (ev.event) {
      case "step_spawned":
        cur.status = "spawned";
        cur.attempts += 1;
        break;
      case "step_verified":
        if (cur.status !== "completed" && cur.status !== "failed") cur.status = "verified";
        break;
      case "step_completed":
        cur.status = "completed";
        break;
      case "step_failed":
        cur.status = "failed";
        break;
      case "step_stalled":
        if (cur.status !== "completed" && cur.status !== "failed") cur.status = "stalled";
        break;
      case "step_phantom_detected":
        if (cur.status !== "completed" && cur.status !== "failed") cur.status = "phantom";
        break;
      default:
        break;
    }
    map.set(ev.stepId, cur);
  }
  return [...map.values()].sort((a, b) => a.stepId.localeCompare(b.stepId));
}

function StepStatusBadge({ state }: { state: StepState }): React.JSX.Element {
  const color =
    state.status === "completed" ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20" :
    state.status === "verified" ? "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20" :
    state.status === "spawned" ? "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20" :
    state.status === "stalled" ? "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20" :
    state.status === "phantom" ? "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20" :
    "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${color}`}>
      {state.status}
    </span>
  );
}

function EventRow({ ev }: { ev: PipelineEvent }): React.JSX.Element {
  const sev = eventSeverity(ev.event);
  const dotColor =
    sev === "success" ? "bg-emerald-500" :
    sev === "severe" ? "bg-rose-500" :
    "bg-sky-500";
  return (
    <li className="flex items-start gap-3 py-2">
      <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${dotColor}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{formatEventName(ev.event)}</span>
          {ev.stepId && <span className="font-mono text-xs text-gray-500">{ev.stepId}</span>}
          <span className="text-xs text-gray-400 ml-auto">{formatTime(ev.ts)}</span>
        </div>
        {ev.details && (
          <div className="text-xs text-gray-600 dark:text-gray-400 mt-0.5 truncate">{ev.details}</div>
        )}
      </div>
    </li>
  );
}

interface Props {
  botId: string;
  botName: string;
  pipelineId: string;
}

export default function PipelineDetail({ botId, botName, pipelineId }: Props): React.JSX.Element {
  const authFetch = useAuthFetch();
  const { getAccessToken } = usePrivy();
  const [meta, setMeta] = useState<PipelineMeta | null>(null);
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const fallbackTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Sort helpers
  const sortedEvents = useMemo(() => [...events].sort((a, b) => b.ts - a.ts), [events]);
  const stepStates = useMemo(() => deriveStepStates(events), [events]);

  // Polling fallback
  const pollOnce = useCallback(async () => {
    try {
      const res = await authFetch(`/api/bots/${botId}/pipelines/${pipelineId}`);
      if (!res.ok) {
        setError(`Failed to load pipeline (${res.status})`);
        return;
      }
      const body = await res.json();
      setMeta((body.meta as PipelineMeta) ?? null);
      setEvents((body.events as PipelineEvent[]) ?? []);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [authFetch, botId, pipelineId]);

  // SSE preferred, polling fallback
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = await getAccessToken();
        if (!token || cancelled) return;
        // EventSource does not support custom headers; use query-param-less path and
        // rely on cookie-based session OR fall back to polling. Since Privy uses
        // Bearer only, use fetch+ReadableStream for SSE.
        const controller = new AbortController();
        const res = await fetch(
          `/api/bots/${botId}/pipelines/${pipelineId}/stream`,
          {
            method: "GET",
            headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
            signal: controller.signal,
          },
        );
        if (!res.ok || !res.body) {
          // Fall back to polling
          void pollOnce();
          fallbackTimerRef.current = setInterval(() => { void pollOnce(); }, POLL_FALLBACK_MS);
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        const flush = () => {
          const chunks = buffer.split("\n\n");
          buffer = chunks.pop() ?? "";
          for (const chunk of chunks) {
            if (!chunk.trim() || chunk.startsWith(":")) continue; // heartbeat or empty
            let eventName = "message";
            let data = "";
            for (const line of chunk.split("\n")) {
              if (line.startsWith("event: ")) eventName = line.slice(7).trim();
              else if (line.startsWith("data: ")) data += line.slice(6);
            }
            let parsed: unknown;
            try { parsed = JSON.parse(data); } catch { continue; }
            if (eventName === "snapshot" && parsed && typeof parsed === "object") {
              const obj = parsed as { meta?: PipelineMeta; events?: PipelineEvent[] };
              setMeta(obj.meta ?? null);
              setEvents(obj.events ?? []);
            } else if (eventName === "event" && parsed && typeof parsed === "object") {
              setEvents((prev) => {
                const next = [...prev];
                const e = parsed as PipelineEvent;
                // Dedupe by ts+event+stepId
                if (!next.some((x) => x.ts === e.ts && x.event === e.event && x.stepId === e.stepId)) {
                  next.push(e);
                }
                return next;
              });
            } else if (eventName === "meta" && parsed && typeof parsed === "object") {
              setMeta(parsed as PipelineMeta);
            } else if (eventName === "end") {
              // Stream closed on server; start fallback polling in case more events arrive later
              if (!fallbackTimerRef.current) {
                fallbackTimerRef.current = setInterval(() => { void pollOnce(); }, POLL_FALLBACK_MS);
              }
            }
          }
        };
        while (!cancelled) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          flush();
        }
      } catch {
        if (!cancelled) {
          void pollOnce();
          fallbackTimerRef.current = setInterval(() => { void pollOnce(); }, POLL_FALLBACK_MS);
        }
      }
    })();
    return () => {
      cancelled = true;
      if (fallbackTimerRef.current) clearInterval(fallbackTimerRef.current);
    };
  }, [botId, pipelineId, getAccessToken, pollOnce]);

  const intervene = useCallback(async (action: string, stepId?: string) => {
    setPending(action + (stepId ? `:${stepId}` : ""));
    try {
      const res = await authFetch(
        `/api/bots/${botId}/pipelines/${pipelineId}/intervene`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action, step_id: stepId ?? null }),
        },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(`Intervention failed: ${body.error ?? res.status}`);
      } else {
        setError(null);
        // Optimistic refresh
        await pollOnce();
      }
    } finally {
      setPending(null);
    }
  }, [authFetch, botId, pipelineId, pollOnce]);

  const isActive = meta?.status === "in_progress";
  const severe = stepStates.filter((s) => s.status === "stalled" || s.status === "phantom" || s.status === "failed");

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <Link href={`/dashboard/${botId}/pipelines`} className="text-sm text-sky-600 hover:underline">← Pipelines</Link>
          <h1 className="text-xl font-semibold font-mono mt-1">{pipelineId}</h1>
          <div className="text-sm text-gray-600 dark:text-gray-400">
            {botName} · started {pipelineDisplayName(pipelineId)}
          </div>
        </div>
        {isActive && (
          <div className="flex gap-2">
            <button
              className="px-3 py-1.5 text-sm rounded-md border border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300 hover:bg-amber-500/20 disabled:opacity-50"
              disabled={pending === "pause"}
              onClick={() => void intervene("pause")}
            >
              {pending === "pause" ? "Pausing…" : "Pause"}
            </button>
            <button
              className="px-3 py-1.5 text-sm rounded-md border border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300 hover:bg-rose-500/20 disabled:opacity-50"
              disabled={pending === "cancel"}
              onClick={() => {
                if (confirm("Cancel this pipeline? Completed steps will be preserved.")) {
                  void intervene("cancel");
                }
              }}
            >
              {pending === "cancel" ? "Cancelling…" : "Cancel"}
            </button>
          </div>
        )}
        {meta?.status === "paused" && (
          <button
            className="px-3 py-1.5 text-sm rounded-md border border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300 hover:bg-sky-500/20 disabled:opacity-50"
            disabled={pending === "resume"}
            onClick={() => void intervene("resume")}
          >
            {pending === "resume" ? "Resuming…" : "Resume"}
          </button>
        )}
      </div>

      {error && <GlassCard className="p-3 text-sm text-rose-600">{error}</GlassCard>}

      {/* Meta card */}
      <GlassCard className="p-4">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="text-sm">
            <div className="text-gray-500">Status</div>
            <div className="font-medium">{meta?.status ?? "—"}</div>
          </div>
          <div className="text-sm">
            <div className="text-gray-500">Last event</div>
            <div className="font-mono text-xs">{meta?.last_event ?? "—"}</div>
          </div>
          <div className="text-sm">
            <div className="text-gray-500">Updated</div>
            <div>{meta?.updated_at ? `${relativeTime(meta.updated_at)} ago` : "—"}</div>
          </div>
          <div className="text-sm">
            <div className="text-gray-500">Severe events</div>
            <div className={meta && meta.severe_count > 0 ? "text-rose-600 font-semibold" : ""}>{meta?.severe_count ?? 0}</div>
          </div>
        </div>
      </GlassCard>

      {/* Step states */}
      {stepStates.length > 0 && (
        <GlassCard className="p-4">
          <h2 className="text-sm font-semibold mb-3">Steps</h2>
          <ul className="space-y-2">
            {stepStates.map((s) => (
              <li key={s.stepId} className="flex items-center gap-3 flex-wrap">
                <span className="font-mono text-xs text-gray-600 dark:text-gray-300 min-w-[8rem]">{s.stepId}</span>
                <StepStatusBadge state={s} />
                {s.attempts > 1 && (
                  <span className="text-xs text-gray-500">× {s.attempts} attempts</span>
                )}
                <span className="text-xs text-gray-400">
                  last {relativeTime(s.lastTs)} ago
                </span>
                {isActive && (s.status === "stalled" || s.status === "phantom" || s.status === "failed") && (
                  <button
                    className="ml-auto px-2 py-0.5 text-xs rounded border border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300 hover:bg-sky-500/20 disabled:opacity-50"
                    disabled={pending === `retry_step:${s.stepId}`}
                    onClick={() => void intervene("retry_step", s.stepId)}
                  >
                    {pending === `retry_step:${s.stepId}` ? "Queuing…" : "Retry"}
                  </button>
                )}
              </li>
            ))}
          </ul>
          {severe.length > 0 && (
            <div className="mt-3 text-xs text-rose-600">
              {severe.length} step{severe.length === 1 ? "" : "s"} need attention. Retry or cancel to resolve.
            </div>
          )}
        </GlassCard>
      )}

      {/* Event timeline */}
      <GlassCard className="p-4">
        <h2 className="text-sm font-semibold mb-2">Event timeline</h2>
        {sortedEvents.length === 0 ? (
          <div className="text-sm text-gray-500 py-6 text-center">Waiting for events…</div>
        ) : (
          <ul className="divide-y divide-gray-200/50 dark:divide-gray-700/50">
            {sortedEvents.map((ev, i) => (
              <EventRow key={`${ev.ts}-${ev.event}-${ev.stepId ?? "nil"}-${i}`} ev={ev} />
            ))}
          </ul>
        )}
      </GlassCard>
    </div>
  );
}

