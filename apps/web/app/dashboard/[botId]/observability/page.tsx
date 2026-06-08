"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, ClipboardList, HeartPulse, RefreshCw, Rows3 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { GlassCard } from "@/components/ui/glass-card";
import { useAgentFetch } from "@/lib/local-api";

type JsonRecord = Record<string, unknown>;

interface ObservabilityMeta {
  version?: string;
  bot_id?: string;
  events?: number;
}

interface ActivityEventRecord extends JsonRecord {
  id?: number;
  kind?: string;
  session_id?: string;
  tool_name?: string;
  status?: string;
  summary?: string;
  created_at?: string;
}

interface ActivityResponse {
  events?: ActivityEventRecord[];
}

interface SessionRecord extends JsonRecord {
  id?: string;
  event_count?: number;
  tool_count?: number;
  last_event_at?: string;
}

interface SessionsResponse {
  sessions?: SessionRecord[];
}

interface BoardResponse {
  board?: JsonRecord | null;
}

const OBSERVABILITY_ENDPOINTS = {
  meta: "/api/observability/v1/meta",
  activity: "/api/observability/v1/activity?limit=100",
  sessions: "/api/observability/v1/sessions?limit=50",
  health: "/api/observability/v1/health/live",
  board: "/api/observability/v1/board",
} as const;

function numberLabel(value: number | undefined): string {
  return Math.max(0, value ?? 0).toLocaleString();
}

function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function eventTime(value: unknown): string {
  const raw = stringValue(value);
  if (!raw) return "-";
  const time = new Date(raw);
  if (Number.isNaN(time.getTime())) return raw;
  return time.toLocaleString();
}

function prettyJson(value: unknown): string {
  if (value === null || value === undefined) return "{}";
  return JSON.stringify(value, null, 2);
}

async function readJson<T>(response: Response, fallback: T): Promise<T> {
  const payload = await response.json().catch(() => fallback);
  return (payload ?? fallback) as T;
}

function StatCard({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string;
  value: string;
  detail?: string;
  icon: React.ComponentType<{ className?: string; strokeWidth?: number }>;
}) {
  return (
    <GlassCard>
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-primary-light" strokeWidth={2} />
        <p className="text-sm text-secondary">{label}</p>
      </div>
      <p className="mt-2 text-2xl font-semibold text-foreground">{value}</p>
      {detail ? <p className="mt-1 text-xs text-muted">{detail}</p> : null}
    </GlassCard>
  );
}

export default function ObservabilityPage() {
  const agentFetch = useAgentFetch();
  const [meta, setMeta] = useState<ObservabilityMeta | null>(null);
  const [events, setEvents] = useState<ActivityEventRecord[]>([]);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [health, setHealth] = useState<JsonRecord | null>(null);
  const [board, setBoard] = useState<JsonRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadObservability = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [metaResponse, activityResponse, sessionsResponse, healthResponse, boardResponse] =
        await Promise.all([
          agentFetch(OBSERVABILITY_ENDPOINTS.meta),
          agentFetch(OBSERVABILITY_ENDPOINTS.activity),
          agentFetch(OBSERVABILITY_ENDPOINTS.sessions),
          agentFetch(OBSERVABILITY_ENDPOINTS.health),
          agentFetch(OBSERVABILITY_ENDPOINTS.board),
        ]);

      const failed = [metaResponse, activityResponse, sessionsResponse, healthResponse, boardResponse]
        .find((response) => !response.ok);
      if (failed) {
        throw new Error("Observability API is unavailable for this local runtime.");
      }

      const nextMeta = await readJson<ObservabilityMeta>(metaResponse, {});
      const activity = await readJson<ActivityResponse>(activityResponse, { events: [] });
      const sessionData = await readJson<SessionsResponse>(sessionsResponse, { sessions: [] });
      const healthData = await readJson<JsonRecord>(healthResponse, {});
      const boardData = await readJson<BoardResponse>(boardResponse, { board: null });

      setMeta(nextMeta);
      setEvents(Array.isArray(activity.events) ? activity.events : []);
      setSessions(Array.isArray(sessionData.sessions) ? sessionData.sessions : []);
      setHealth(healthData);
      setBoard(boardData.board ?? null);
    } catch (err) {
      setMeta(null);
      setEvents([]);
      setSessions([]);
      setHealth(null);
      setBoard(null);
      setError(err instanceof Error ? err.message : "Failed to load observability data");
    } finally {
      setLoading(false);
    }
  }, [agentFetch]);

  useEffect(() => {
    void loadObservability();
  }, [loadObservability]);

  const healthState = useMemo(() => {
    if (!health) return "unknown";
    if (health.ok === true) return "ready";
    if (typeof health.status === "string") return health.status;
    return "not ready";
  }, [health]);

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">
            Local runtime
          </div>
          <h1 className="text-2xl font-bold text-foreground">Runtime Observability</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-secondary">
            Inspect local activity events, sessions, health, and board state emitted by this running agent.
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={loadObservability} disabled={loading}>
          <RefreshCw className="mr-2 h-4 w-4" strokeWidth={2} />
          {loading ? "Refreshing..." : "Refresh"}
        </Button>
      </div>

      {error ? (
        <div className="rounded-xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-700">
          {error}
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Events"
          value={numberLabel(meta?.events ?? events.length)}
          detail={`${numberLabel(events.length)} loaded`}
          icon={Activity}
        />
        <StatCard
          label="Sessions"
          value={numberLabel(sessions.length)}
          detail={meta?.bot_id ? `bot ${meta.bot_id}` : "local bot"}
          icon={Rows3}
        />
        <StatCard
          label="Health"
          value={healthState}
          detail={meta?.version ? `observability ${meta.version}` : "local endpoint"}
          icon={HeartPulse}
        />
        <StatCard
          label="Board"
          value={board ? "available" : "empty"}
          detail="latest board event"
          icon={ClipboardList}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <GlassCard className="min-h-[420px]">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-foreground">Activity Feed</h2>
            <span className="text-xs text-muted">{numberLabel(events.length)} events</span>
          </div>
          {loading && events.length === 0 ? (
            <div className="space-y-3">
              <div className="skeleton h-16" />
              <div className="skeleton h-16" />
              <div className="skeleton h-16" />
            </div>
          ) : events.length === 0 ? (
            <p className="text-sm text-secondary">No activity events are recorded yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <div className="min-w-[760px] space-y-2">
                {events.map((event, index) => (
                  <div
                    key={`${event.id ?? index}-${event.kind ?? "event"}`}
                    className="grid grid-cols-[130px_150px_160px_120px_minmax(0,1fr)] items-start gap-3 rounded-xl border border-black/[0.06] bg-white/70 px-3 py-2 text-xs"
                  >
                    <span className="font-mono text-muted">{eventTime(event.created_at)}</span>
                    <span className="truncate font-semibold text-foreground">{stringValue(event.kind, "event")}</span>
                    <span className="truncate text-primary-light">{stringValue(event.session_id, "session")}</span>
                    <span className="truncate text-secondary">{stringValue(event.status, "-")}</span>
                    <span className="truncate text-secondary">
                      {stringValue(event.summary) || stringValue(event.tool_name) || prettyJson(event).slice(0, 120)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </GlassCard>

        <GlassCard className="min-h-[420px]">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-foreground">Sessions</h2>
            <span className="text-xs text-muted">{numberLabel(sessions.length)} total</span>
          </div>
          {loading && sessions.length === 0 ? (
            <div className="space-y-3">
              <div className="skeleton h-14" />
              <div className="skeleton h-14" />
              <div className="skeleton h-14" />
            </div>
          ) : sessions.length === 0 ? (
            <p className="text-sm text-secondary">No sessions have emitted observability events.</p>
          ) : (
            <div className="space-y-2">
              {sessions.map((session) => (
                <div key={session.id ?? String(session.last_event_at)} className="rounded-xl border border-black/[0.06] bg-white/70 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <p className="min-w-0 truncate text-sm font-semibold text-foreground">{session.id ?? "session"}</p>
                    <span className="shrink-0 rounded-full border border-black/10 bg-black/[0.035] px-2 py-0.5 text-xs text-secondary">
                      {numberLabel(session.event_count)} events
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-secondary">
                    {numberLabel(session.tool_count)} tool events · {eventTime(session.last_event_at)}
                  </p>
                </div>
              ))}
            </div>
          )}
        </GlassCard>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <GlassCard>
          <h2 className="mb-3 text-sm font-semibold text-foreground">Health</h2>
          <pre className="max-h-96 overflow-auto rounded-xl border border-black/[0.06] bg-black/[0.025] p-3 text-xs leading-5 text-secondary">
            {prettyJson(health)}
          </pre>
        </GlassCard>
        <GlassCard>
          <h2 className="mb-3 text-sm font-semibold text-foreground">Board</h2>
          <pre className="max-h-96 overflow-auto rounded-xl border border-black/[0.06] bg-black/[0.025] p-3 text-xs leading-5 text-secondary">
            {prettyJson(board)}
          </pre>
        </GlassCard>
      </div>
    </div>
  );
}
