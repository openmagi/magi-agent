/**
 * Pure helper for building activity query strings for the observability feed.
 *
 * Extracted as a standalone module so it can be unit-tested without a DOM
 * environment or React context. The page imports and calls this function when
 * constructing the fetch URL.
 *
 * TODO(Task 9): source CATEGORY_KINDS from /meta instead of this static constant.
 */

/** Noise kinds excluded when "Hide noise" is ON (default). */
export const NOISE_KINDS: readonly string[] = [
  "text_delta",
  "heartbeat",
  "turn_phase",
  "runtime_trace",
  "tool_progress",
];

/**
 * Kind taxonomy grouped by category.
 * The categories and kind lists are a FE constant until Task 9 replaces this
 * with the /meta endpoint's `kind_categories` map.
 *
 * All kind names must match the actual event kinds emitted by the magi-agent
 * runtime into the activity store.
 *
 * NOTE: `aborted` appears in both Lifecycle and Errors in the canonical server
 * taxonomy; it is placed only under Errors here to avoid double-listing in the
 * kind multi-select UI.
 *
 * TODO(Task 9): source categories from /meta kind_categories.
 */
export const CATEGORY_KINDS: Record<string, readonly string[]> = {
  Noise: ["text_delta", "heartbeat", "turn_phase", "runtime_trace", "tool_progress"],
  Lifecycle: ["turn_start", "turn_end", "checkpoint", "compaction_start", "compaction_end"],
  Tools: ["tool_start", "tool_end", "source_inspected"],
  Policy: ["rule_check", "rule_violation"],
  Errors: ["error", "aborted"],
  Other: ["child_progress", "artifact_created", "task_board"],
};

export interface ActivityFilters {
  /** When true, appends exclude_kind with the NOISE_KINDS set. */
  hideNoise: boolean;
  /** Comma-joined to kind= param when non-empty. */
  selectedKinds: string[];
  /** Scopes feed to a specific session when set. */
  sessionId: string | null;
}

/** Optional pagination cursors for `buildActivityPageQuery`. */
export interface PaginationParams {
  /** Fetch events with `id > sinceId` ("load newer"). */
  sinceId?: number | null;
  /** Fetch events with `id < beforeId` ("load older"). */
  beforeId?: number | null;
}

/**
 * Build the query string for a paginated activity request, composing filter
 * params with optional `since_id`/`before_id` pagination cursors.
 *
 * Pass an empty `pagination` object (or omit it) to get the same result as
 * `buildActivityQuery`. Always includes `limit=100`. Returns a string
 * beginning with "?".
 *
 * Usage patterns:
 *   Initial/filter-changed load: `buildActivityPageQuery(filters)` (no cursor)
 *   Load older page:             `buildActivityPageQuery(filters, { beforeId: oldestId })`
 *   Load newer page:             `buildActivityPageQuery(filters, { sinceId: newestId })`
 */
export function buildActivityPageQuery(
  filters: ActivityFilters,
  pagination: PaginationParams = {}
): string {
  const params = new URLSearchParams();
  params.set("limit", "100");

  if (filters.sessionId) {
    params.set("session_id", filters.sessionId);
  }

  if (filters.selectedKinds.length > 0) {
    params.set("kind", filters.selectedKinds.join(","));
  }

  if (filters.hideNoise) {
    params.set("exclude_kind", NOISE_KINDS.join(","));
  }

  if (pagination.sinceId != null) {
    params.set("since_id", String(pagination.sinceId));
  }

  if (pagination.beforeId != null) {
    params.set("before_id", String(pagination.beforeId));
  }

  return `?${params.toString()}`;
}

/**
 * Build the query string for /api/observability/v1/activity from the current
 * filter state. Always includes limit=100. Returns a string beginning with "?"
 * (or just "?limit=100" when no filters are active).
 *
 * Delegates to `buildActivityPageQuery` with no pagination cursor.
 */
export function buildActivityQuery(filters: ActivityFilters): string {
  return buildActivityPageQuery(filters);
}

/**
 * Merge two arrays of events, deduplicating by numeric `id`.
 *
 * Result order is `[...a, ...b]` — events in `b` whose `id` already appears
 * in `a` are dropped. Events without an `id` field are never deduplicated
 * (both copies are kept).
 *
 * Usage patterns:
 *   Load older:  `mergeEventsById(olderPage, existing)` — older events first.
 *   Load newer:  `mergeEventsById(existing, newerPage)` — newer events last.
 *
 * Neither input array is mutated.
 */
export function mergeEventsById<T extends { id?: number }>(
  a: T[],
  b: T[]
): T[] {
  const seenIds = new Set<number>(
    a.flatMap((e) => (e.id != null ? [e.id] : []))
  );
  const filtered = b.filter((e) => e.id == null || !seenIds.has(e.id));
  return [...a, ...filtered];
}

/**
 * Parse ActivityFilters from URL search params (for URL-backed filter state).
 *
 * Param names used in the shareable URL:
 *   hideNoise=0   → hideNoise false  (absent or any other value → true, matching DEFAULT_FILTERS)
 *   kind=a,b      → selectedKinds
 *   session_id=x  → sessionId
 *
 * The `exclude_kind` expansion is intentionally kept out of the URL to avoid
 * duplicating the NOISE_KINDS literal; `hideNoise=0` is the canonical signal.
 */
export function parseFiltersFromParams(sp: URLSearchParams): ActivityFilters {
  const noiseParam = sp.get("hideNoise");
  return {
    hideNoise: noiseParam === null || noiseParam !== "0",
    selectedKinds: sp.get("kind")?.split(",").filter(Boolean) ?? [],
    sessionId: sp.get("session_id") ?? null,
  };
}

/**
 * Serialize ActivityFilters to URL search params for `router.replace`.
 * Only non-default values are written so the URL stays minimal.
 *   hideNoise true (default) → param omitted
 *   hideNoise false          → hideNoise=0
 */
export function filtersToParams(filters: ActivityFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (!filters.hideNoise) {
    p.set("hideNoise", "0");
  }
  if (filters.selectedKinds.length > 0) {
    p.set("kind", filters.selectedKinds.join(","));
  }
  if (filters.sessionId) {
    p.set("session_id", filters.sessionId);
  }
  return p;
}
