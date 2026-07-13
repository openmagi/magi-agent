"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Activity,
  Bot,
  ChevronDown,
  ChevronRight,
  Clock,
  ClipboardList,
  Globe,
  HeartPulse,
  Hash,
  MessageCircle,
  MessageSquare,
  RefreshCw,
  Rows3,
  Send,
  Terminal,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { GlassCard } from "@/components/ui/glass-card";
import { useAgentFetch } from "@/lib/local-api";
import {
  buildActivityQuery,
  buildActivityPageQuery,
  mergeEventsById,
  CATEGORY_KINDS,
  NOISE_KINDS,
  parseFiltersFromParams,
  filtersToParams,
  formatSessionBreakdown,
  extractVerdict,
  resolveKindCategories,
  deriveSessionChannel,
  deriveSessionTitle,
  buildSessionForest,
  flattenSessionForest,
  type ActivityFilters,
  type SessionChannel,
} from "./observability-query";

type JsonRecord = Record<string, unknown>;

interface ObservabilityMeta {
  version?: string;
  bot_id?: string;
  events?: number;
  /** /meta categories payload from get_meta_taxonomy() — sourced in Task 9. */
  categories?: unknown;
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
  /** ISO timestamp of the most recent event in this session (Task 5 field name). */
  last_active?: string;
  /**
   * @deprecated Backend now uses last_active. Kept here so older payloads that
   * still carry last_event_at render gracefully without a runtime crash.
   */
  last_event_at?: string;
  /** Deterministic human-readable session summary derived by the backend (Task 5). */
  label?: string;
  /** Per-kind event counts for this session (Task 5). */
  kind_breakdown?: Record<string, number>;
  /** Count of error/aborted lifecycle events (Task 5). */
  error_count?: number;
  /** Count of rule_check events (Task 5). */
  rule_check_count?: number;
  /**
   * Optional backend-provided human title. When absent (current runtimes) the
   * FE derives a title from the session id via deriveSessionTitle(). For spawned
   * subagents the backend supplies the delegated task title / agent name here.
   */
  title?: string;
  /**
   * Parent session id for a spawned subagent, resolved by the backend from the
   * `child_started` event. Drives the collapsible parent→child Sessions tree.
   */
  parent_session_id?: string;
}

interface SessionsResponse {
  sessions?: SessionRecord[];
}

interface BoardResponse {
  board?: JsonRecord | null;
}

const OBSERVABILITY_ENDPOINTS = {
  meta: "/api/observability/v1/meta",
  activity: "/api/observability/v1/activity",
  sessions: "/api/observability/v1/sessions?limit=50",
  health: "/api/observability/v1/health/live",
  board: "/api/observability/v1/board",
} as const;

const DEFAULT_FILTERS: ActivityFilters = {
  hideNoise: true,
  selectedKinds: [],
  sessionId: null,
  policyEvidenceOnly: false,
  evidenceOnly: false,
  errorsOnly: false,
};

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

/**
 * Per-channel presentation: icon + chip tone. Keyed on the `key` returned by
 * deriveSessionChannel() so the pure derivation stays UI-agnostic and testable.
 */
const CHANNEL_STYLE: Record<
  SessionChannel["key"],
  { icon: React.ComponentType<{ className?: string; strokeWidth?: number }>; chip: string }
> = {
  app: { icon: MessageSquare, chip: "border-blue-300/50 bg-blue-50 text-blue-700" },
  telegram: { icon: Send, chip: "border-sky-300/50 bg-sky-50 text-sky-700" },
  discord: { icon: MessageCircle, chip: "border-indigo-300/50 bg-indigo-50 text-indigo-700" },
  slack: { icon: Hash, chip: "border-purple-300/50 bg-purple-50 text-purple-700" },
  web: { icon: Globe, chip: "border-emerald-300/50 bg-emerald-50 text-emerald-700" },
  cli: { icon: Terminal, chip: "border-slate-300/60 bg-slate-50 text-slate-700" },
  cron: { icon: Clock, chip: "border-amber-300/50 bg-amber-50 text-amber-700" },
  subagent: { icon: Bot, chip: "border-violet-300/50 bg-violet-50 text-violet-700" },
  unknown: { icon: Rows3, chip: "border-black/10 bg-black/[0.04] text-secondary" },
};

/** Small channel badge (icon + label) shown on session cards and feed rows. */
function ChannelChip({ channel, compact = false }: { channel: SessionChannel; compact?: boolean }) {
  const style = CHANNEL_STYLE[channel.key] ?? CHANNEL_STYLE.unknown;
  const Icon = style.icon;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${style.chip}`}
    >
      <Icon className="h-3 w-3" strokeWidth={2} />
      {!compact ? channel.label : null}
    </span>
  );
}

/** Filter bar above the Activity Feed. State is lifted to the page component. */
interface FilterBarProps {
  filters: ActivityFilters;
  onFiltersChange: (next: ActivityFilters) => void;
  sessions: SessionRecord[];
  /** /meta categories payload — used to drive kind groups and noise set; falls back to FE constants. */
  metaCategories?: unknown;
}

function FilterBar({ filters, onFiltersChange, sessions, metaCategories }: FilterBarProps) {
  // Source kind categories from /meta when available; fall back to the FE constant
  // for older runtimes that do not yet return the categories field.
  const { categories: kindCategories, noiseKinds: activeNoiseKinds } =
    resolveKindCategories(metaCategories);

  const hasActiveFilters =
    !filters.hideNoise ||
    filters.selectedKinds.length > 0 ||
    filters.sessionId !== null ||
    !!filters.policyEvidenceOnly ||
    !!filters.evidenceOnly ||
    !!filters.errorsOnly;

  function toggleKind(kind: string) {
    const next = filters.selectedKinds.includes(kind)
      ? filters.selectedKinds.filter((k) => k !== kind)
      : [...filters.selectedKinds, kind];
    onFiltersChange({ ...filters, selectedKinds: next });
  }

  function reset() {
    onFiltersChange(DEFAULT_FILTERS);
  }

  return (
    <div className="flex flex-wrap items-start gap-3 rounded-xl border border-black/[0.06] bg-white/50 px-4 py-3">
      {/* Hide noise toggle */}
      <label className="flex cursor-pointer items-center gap-2 select-none">
        <input
          type="checkbox"
          className="h-4 w-4 rounded"
          checked={filters.hideNoise}
          onChange={(e) =>
            onFiltersChange({ ...filters, hideNoise: e.target.checked })
          }
        />
        <span className="text-xs font-medium text-foreground">Hide noise</span>
        <span className="text-xs text-muted">({activeNoiseKinds.join(", ")})</span>
      </label>

      <div className="mx-1 h-4 w-px self-center bg-black/10" />

      {/* Quick toggles: Policy & Evidence / Errors only */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Policy & Evidence only */}
        <label className="flex cursor-pointer items-center gap-1.5 select-none">
          <input
            type="checkbox"
            className="h-4 w-4 rounded"
            checked={!!filters.policyEvidenceOnly}
            onChange={(e) =>
              onFiltersChange({
                ...filters,
                policyEvidenceOnly: e.target.checked,
                // clear evidenceOnly when turning off the parent toggle
                evidenceOnly: e.target.checked ? filters.evidenceOnly : false,
              })
            }
          />
          <span className="text-xs font-medium text-foreground">Policy &amp; Evidence only</span>
        </label>
        {/* Evidence fired only — sub-toggle, visible when policyEvidenceOnly is active */}
        {filters.policyEvidenceOnly ? (
          <label className="ml-4 flex cursor-pointer items-center gap-1.5 select-none">
            <input
              type="checkbox"
              className="h-4 w-4 rounded"
              checked={!!filters.evidenceOnly}
              onChange={(e) =>
                onFiltersChange({ ...filters, evidenceOnly: e.target.checked })
              }
            />
            <span className="text-xs text-secondary">Evidence fired only</span>
          </label>
        ) : null}

        {/* Errors only */}
        <label className="flex cursor-pointer items-center gap-1.5 select-none">
          <input
            type="checkbox"
            className="h-4 w-4 rounded"
            checked={!!filters.errorsOnly}
            onChange={(e) =>
              onFiltersChange({ ...filters, errorsOnly: e.target.checked })
            }
          />
          <span className="text-xs font-medium text-foreground">Errors only</span>
        </label>
      </div>

      <div className="mx-1 h-4 w-px self-center bg-black/10" />

      {/* Session selector */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-secondary">Session</span>
        <select
          className="rounded-lg border border-black/10 bg-white px-2 py-1 text-xs text-foreground"
          value={filters.sessionId ?? ""}
          onChange={(e) =>
            onFiltersChange({
              ...filters,
              sessionId: e.target.value || null,
            })
          }
        >
          <option value="">All sessions</option>
          {sessions.map((s) => {
            const channel = deriveSessionChannel(s.id);
            const title = s.title?.trim() || deriveSessionTitle(s.id, s.label);
            return (
              <option key={s.id ?? String(s.last_active ?? s.last_event_at)} value={s.id ?? ""}>
                {channel.label} · {title}
              </option>
            );
          })}
        </select>
      </div>

      <div className="mx-1 h-4 w-px self-center bg-black/10" />

      {/* Kind multi-select grouped by category.
          Categories sourced from /meta via resolveKindCategories(); falls back to
          the CATEGORY_KINDS constant for older runtimes without the taxonomy field. */}
      <div className="flex flex-wrap items-center gap-2">
        {Object.entries(kindCategories).map(([category, kinds]) => (
          <div key={category} className="flex flex-wrap items-center gap-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">
              {category}
            </span>
            {(kinds as readonly string[]).map((kind) => {
              const active = filters.selectedKinds.includes(kind);
              return (
                <button
                  key={kind}
                  type="button"
                  onClick={() => toggleKind(kind)}
                  className={`rounded-full border px-2 py-0.5 text-[10px] font-medium transition-colors ${
                    active
                      ? "border-primary-light/40 bg-primary-light/10 text-primary-light"
                      : "border-black/10 bg-black/[0.03] text-secondary hover:bg-black/[0.06]"
                  }`}
                >
                  {kind}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {/* Reset */}
      {hasActiveFilters ? (
        <>
          <div className="mx-1 h-4 w-px self-center bg-black/10" />
          <button
            type="button"
            onClick={reset}
            className="flex items-center gap-1 rounded-full border border-black/10 bg-black/[0.03] px-2 py-0.5 text-[10px] font-medium text-secondary hover:bg-black/[0.06]"
          >
            <X className="h-3 w-3" />
            Reset
          </button>
        </>
      ) : null}
    </div>
  );
}

/**
 * Inner page body. Requires a Suspense boundary in the parent because it calls
 * useSearchParams() — Next.js App Router requirement for static-safe rendering.
 */
function ObservabilityPageInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const agentFetch = useAgentFetch();
  const [meta, setMeta] = useState<ObservabilityMeta | null>(null);
  const [events, setEvents] = useState<ActivityEventRecord[]>([]);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [health, setHealth] = useState<JsonRecord | null>(null);
  const [board, setBoard] = useState<JsonRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** Tracks in-flight pagination direction; null when no page load is active. */
  const [paginatingDir, setPaginatingDir] = useState<"older" | "newer" | null>(null);
  /** Session ids whose spawned-subagent children are collapsed in the tree. */
  const [collapsedSessions, setCollapsedSessions] = useState<Set<string>>(
    () => new Set(),
  );

  // Filter state backed by URL query params so audit views are shareable.
  // Initial state is read from the URL on mount; changes are written back via
  // router.replace (no history push — avoids polluting back-stack).
  const [filters, setFilters] = useState<ActivityFilters>(() =>
    parseFiltersFromParams(sp),
  );

  /**
   * Centralized filter apply: updates state + syncs URL (replace, not push).
   *
   * F2: wrapped in useCallback so identity is stable across renders, preventing
   * unnecessary FilterBar re-renders and avoiding stale-closure bugs.
   * Deps are [router] only — setFilters is stable, and `next` is passed in
   * directly (no capture of the `filters` state variable needed).
   */
  const applyFilters = useCallback((next: ActivityFilters) => {
    setFilters(next);
    const params = filtersToParams(next);
    const qs = params.toString();
    router.replace(qs ? `?${qs}` : "?", { scroll: false });
  }, [router]);

  // Resolve the server's noise kinds from /meta when available. Used for the
  // exclude_kind API param so the actual request matches the server's live taxonomy
  // rather than the hardcoded FE constant. Falls back to NOISE_KINDS when /meta
  // is unavailable or older runtimes don't return the categories field.
  const { noiseKinds: activeNoiseKinds } = useMemo(
    () => resolveKindCategories(meta?.categories),
    [meta],
  );

  const activityUrl = useMemo(
    () => OBSERVABILITY_ENDPOINTS.activity + buildActivityQuery(filters, activeNoiseKinds),
    [filters, activeNoiseKinds],
  );

  /**
   * F3 — double-fetch guard.
   *
   * The flow: loadObservability (deps include activityUrl) -> on success setMeta
   * -> activeNoiseKinds memo recomputes from server noise_kinds -> if the
   * resulting activityUrl string changes, loadObservability's identity changes
   * -> the effect refires -> second full reload of all 5 endpoints.
   *
   * Guard: track the last URL that was actually fetched. When loadObservability
   * fires again and the URL equals the last-fetched URL, skip the reload.
   * This is purely a noise-URL dedup — filter changes and pagination cursors
   * always produce a different URL so they are never suppressed.
   *
   * After B1, the server and FE noise sets are identical so this rarely triggers
   * in practice, but the structural race is closed regardless.
   */
  const lastFetchedUrlRef = useRef<string | null>(null);

  /**
   * Cursor ids derived from the currently loaded events (id ASC ordering).
   * Used to build `before_id`/`since_id` params for paginated fetches.
   * Both are null when no events with a numeric id are loaded.
   */
  const { oldestId, newestId } = useMemo(() => {
    let oldest: number | null = null;
    let newest: number | null = null;
    for (const e of events) {
      if (e.id != null) {
        if (oldest === null) oldest = e.id;
        newest = e.id;
      }
    }
    return { oldestId: oldest, newestId: newest };
  }, [events]);

  const loadObservability = useCallback(async () => {
    // F3: skip reload if the activity URL hasn't changed since the last fetch.
    // This prevents the structural double-fetch caused by /meta returning a
    // noise_kinds list that produces an identical activityUrl after B1.
    if (lastFetchedUrlRef.current === activityUrl) return;
    lastFetchedUrlRef.current = activityUrl;

    setLoading(true);
    setPaginatingDir(null);
    setError(null);
    try {
      const [metaResponse, activityResponse, sessionsResponse, healthResponse, boardResponse] =
        await Promise.all([
          agentFetch(OBSERVABILITY_ENDPOINTS.meta),
          agentFetch(activityUrl),
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
  }, [agentFetch, activityUrl]);

  /**
   * Fetch the page of events older than the current oldest loaded event.
   * Uses `before_id=<oldestId>` so the API returns events with `id < oldestId`.
   * Preserves all active filters; prepends results to the existing list.
   * Filter changes reset pagination via `loadObservability` (full reload).
   */
  const loadOlderPage = useCallback(async () => {
    if (oldestId == null || paginatingDir != null) return;
    setPaginatingDir("older");
    try {
      const url =
        OBSERVABILITY_ENDPOINTS.activity +
        buildActivityPageQuery(filters, { beforeId: oldestId, noiseKinds: activeNoiseKinds });
      const response = await agentFetch(url);
      if (!response.ok) return;
      const activity = await readJson<ActivityResponse>(response, { events: [] });
      const incoming = Array.isArray(activity.events) ? activity.events : [];
      setEvents((prev) => mergeEventsById(incoming, prev));
    } finally {
      setPaginatingDir(null);
    }
  }, [agentFetch, activeNoiseKinds, filters, oldestId, paginatingDir]);

  /**
   * Fetch the page of events newer than the current newest loaded event.
   * Uses `since_id=<newestId>` so the API returns events with `id > newestId`.
   * Preserves all active filters; appends results after the existing list.
   */
  const loadNewerPage = useCallback(async () => {
    if (newestId == null || paginatingDir != null) return;
    setPaginatingDir("newer");
    try {
      const url =
        OBSERVABILITY_ENDPOINTS.activity +
        buildActivityPageQuery(filters, { sinceId: newestId, noiseKinds: activeNoiseKinds });
      const response = await agentFetch(url);
      if (!response.ok) return;
      const activity = await readJson<ActivityResponse>(response, { events: [] });
      const incoming = Array.isArray(activity.events) ? activity.events : [];
      setEvents((prev) => mergeEventsById(prev, incoming));
    } finally {
      setPaginatingDir(null);
    }
  }, [agentFetch, activeNoiseKinds, filters, newestId, paginatingDir]);

  useEffect(() => {
    void loadObservability();
  }, [loadObservability]);

  const healthState = useMemo(() => {
    if (!health) return "unknown";
    if (health.ok === true) return "ready";
    if (typeof health.status === "string") return health.status;
    return "not ready";
  }, [health]);

  /**
   * Handle clicking a session card — toggles sessionId filter and re-fetches.
   *
   * F2: wrapped in useCallback to stabilize identity. Uses the functional form
   * of setFilters so it never captures stale `filters` state — the toggle
   * decision (deselect if already active, else select) is computed from `prev`
   * inside the updater. The URL is synced via a derived value produced inline.
   */
  const handleSessionClick = useCallback((sessionId: string) => {
    setFilters((prev) => {
      const next: ActivityFilters = {
        ...prev,
        sessionId: prev.sessionId === sessionId ? null : sessionId,
      };
      // Sync URL alongside state using the same derived `next` value.
      const params = filtersToParams(next);
      const qs = params.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
      return next;
    });
  }, [router]);

  /** Collapse/expand a parent session's spawned-subagent subtree. */
  const toggleSessionCollapse = useCallback((sessionId: string) => {
    setCollapsedSessions((prev) => {
      const next = new Set(prev);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  }, []);

  // Group sessions into a parent→child forest, then flatten to render rows in
  // tree order (children of collapsed parents omitted).
  const sessionRows = useMemo(
    () => flattenSessionForest(buildSessionForest(sessions), collapsedSessions),
    [sessions, collapsedSessions],
  );

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

          {/* Filter bar */}
          <div className="mb-4">
            <FilterBar
              filters={filters}
              onFiltersChange={applyFilters}
              sessions={sessions}
              metaCategories={meta?.categories}
            />
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
            <>
              {/* Load older button — fetch events with id < oldestId */}
              <div className="mb-3 flex justify-center">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={loadOlderPage}
                  disabled={paginatingDir === "older" || oldestId == null}
                >
                  {paginatingDir === "older" ? "Loading..." : "Load older"}
                </Button>
              </div>

              <div className="overflow-x-auto">
                <div className="min-w-[760px] space-y-2">
                  {events.map((event, index) => {
                    const ruleVerdict = extractVerdict(event);
                    return (
                      <div
                        key={`${event.id ?? index}-${event.kind ?? "event"}`}
                        className={`grid grid-cols-[130px_150px_160px_120px_minmax(0,1fr)] items-start gap-3 rounded-xl border px-3 py-2 text-xs ${
                          ruleVerdict
                            ? "border-amber-300/40 bg-amber-50/70"
                            : "border-black/[0.06] bg-white/70"
                        }`}
                      >
                        <span className="font-mono text-muted">{eventTime(event.created_at)}</span>
                        <span className="truncate font-semibold text-foreground">{stringValue(event.kind, "event")}</span>
                        {/* Session column: channel chip + human title (raw id on hover). */}
                        <span
                          className="flex min-w-0 items-center gap-1.5"
                          title={stringValue(event.session_id, "session")}
                        >
                          <ChannelChip channel={deriveSessionChannel(event.session_id)} compact />
                          <span className="truncate text-secondary">
                            {deriveSessionTitle(event.session_id) || "session"}
                          </span>
                        </span>
                        {/* Status column: show verdict badge for rule_check events */}
                        {ruleVerdict ? (
                          <span
                            className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
                              ruleVerdict.verdict === "ok"
                                ? "border-green-300/60 bg-green-50 text-green-700"
                                : ruleVerdict.verdict === "violation"
                                  ? "border-red-300/60 bg-red-50 text-red-700"
                                  : "border-yellow-300/60 bg-yellow-50 text-yellow-700"
                            }`}
                          >
                            {ruleVerdict.verdict}
                          </span>
                        ) : (
                          <span className="truncate text-secondary">{stringValue(event.status, "-")}</span>
                        )}
                        {/* Detail column: for rule_check show evidenceRef + detail; else summary/tool_name */}
                        {ruleVerdict ? (
                          <span className="truncate text-secondary">
                            {ruleVerdict.evidenceRef
                              ? `evidence: ${ruleVerdict.evidenceRef}${ruleVerdict.detail ? ` · ${ruleVerdict.detail}` : ""}`
                              : ruleVerdict.detail ?? "-"}
                          </span>
                        ) : (
                          <span className="truncate text-secondary">
                            {stringValue(event.summary) || stringValue(event.tool_name) || prettyJson(event).slice(0, 120)}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Load newer button — fetch events with id > newestId */}
              <div className="mt-3 flex justify-center">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={loadNewerPage}
                  disabled={paginatingDir === "newer" || newestId == null}
                >
                  {paginatingDir === "newer" ? "Loading..." : "Load newer"}
                </Button>
              </div>
            </>
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
              {sessionRows.map(({ session, depth, childCount, descendantCount }) => {
                const isActive = filters.sessionId === session.id;
                const channel = deriveSessionChannel(session.id);
                const title = session.title?.trim() || deriveSessionTitle(session.id, session.label);
                const isCollapsed = session.id != null && collapsedSessions.has(session.id);
                return (
                  <div
                    key={session.id ?? String(session.last_active ?? session.last_event_at)}
                    className="flex items-stretch gap-1"
                    // Indent nested subagent rows; a left border gives a subtle
                    // tree connector without extra DOM.
                    style={depth > 0 ? { paddingLeft: depth * 16 } : undefined}
                  >
                    {childCount > 0 ? (
                      <button
                        type="button"
                        aria-label={isCollapsed ? "Expand subagents" : "Collapse subagents"}
                        aria-expanded={!isCollapsed}
                        onClick={() => session.id && toggleSessionCollapse(session.id)}
                        className="flex w-5 shrink-0 items-center justify-center rounded-md text-muted hover:bg-black/[0.05] hover:text-foreground"
                      >
                        {isCollapsed ? (
                          <ChevronRight className="h-4 w-4" strokeWidth={2} />
                        ) : (
                          <ChevronDown className="h-4 w-4" strokeWidth={2} />
                        )}
                      </button>
                    ) : (
                      <span
                        aria-hidden
                        className={`w-5 shrink-0 ${depth > 0 ? "border-l border-black/10" : ""}`}
                      />
                    )}
                    {/* F1: session cards are interactive — use <button> so they are
                        keyboard-reachable (Enter/Space) and properly announced by
                        screen readers. text-left + w-full preserve card layout. */}
                    <button
                      type="button"
                      onClick={() => session.id && handleSessionClick(session.id)}
                      title={session.id ?? undefined}
                      className={`min-w-0 flex-1 cursor-pointer rounded-xl border p-3 text-left transition-colors ${
                        isActive
                          ? "border-primary-light/40 bg-primary-light/10"
                          : "border-black/[0.06] bg-white/70 hover:bg-white/90"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex min-w-0 items-center gap-2">
                          <ChannelChip channel={channel} />
                          <p className="min-w-0 truncate text-sm font-semibold text-foreground">
                            {title}
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-1.5">
                          {descendantCount > 0 ? (
                            <span className="rounded-full border border-violet-300/50 bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-700">
                              {descendantCount} {descendantCount === 1 ? "subagent" : "subagents"}
                            </span>
                          ) : null}
                          <span className="rounded-full border border-black/10 bg-black/[0.035] px-2 py-0.5 text-xs text-secondary">
                            {numberLabel(session.event_count)} events
                          </span>
                        </div>
                      </div>
                      <p className="mt-1 text-xs text-secondary">
                        {formatSessionBreakdown(session)} · {eventTime(session.last_active ?? session.last_event_at)}
                      </p>
                    </button>
                  </div>
                );
              })}
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

/**
 * Default export wraps the inner component in a Suspense boundary.
 * Required because ObservabilityPageInner calls useSearchParams(), which
 * Next.js App Router requires to be inside Suspense for static-safe rendering.
 */
export default function ObservabilityPage() {
  return (
    <Suspense fallback={null}>
      <ObservabilityPageInner />
    </Suspense>
  );
}
