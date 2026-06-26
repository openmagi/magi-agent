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

/**
 * Build the query string for /api/observability/v1/activity from the current
 * filter state. Always includes limit=100. Returns a string beginning with "?"
 * (or just "?limit=100" when no filters are active).
 */
export function buildActivityQuery(filters: ActivityFilters): string {
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

  return `?${params.toString()}`;
}
