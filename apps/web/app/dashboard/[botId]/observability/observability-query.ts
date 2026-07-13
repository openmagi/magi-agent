/**
 * Pure helper for building activity query strings for the observability feed.
 *
 * Extracted as a standalone module so it can be unit-tested without a DOM
 * environment or React context. The page imports and calls this function when
 * constructing the fetch URL.
 *
 * Task 9: CATEGORY_KINDS is now a fallback constant. The page sources the live
 * taxonomy from the /meta endpoint's `categories` field via resolveKindCategories.
 * When /meta is unavailable or returns an older runtime payload without categories,
 * the functions in this module fall back to CATEGORY_KINDS and NOISE_KINDS.
 */

/** Noise kinds excluded when "Hide noise" is ON (default).
 * Must stay identical to the server NOISE_KINDS list in magi_agent/observability/taxonomy.py.
 */
export const NOISE_KINDS: readonly string[] = [
  "text_delta",
  "heartbeat",
  "thinking_delta", // B1: gated behind MAGI_STREAM_THINKING; high-volume sub-turn
  "turn_phase",
  "runtime_trace",
  "tool_progress",
];

/**
 * Kind taxonomy grouped by category — fallback constant for older runtimes that
 * do not yet return `categories` in the /meta payload.
 *
 * Live data is sourced from /meta via resolveKindCategories(). This constant is
 * the fallback used when /meta is unavailable or lacks the categories field.
 *
 * All kind names must match the actual event kinds emitted by the magi-agent
 * runtime into the activity store.
 *
 * NOTE: `aborted` appears in both Lifecycle and Errors in the canonical server
 * taxonomy; it is placed only under Errors here to avoid double-listing in the
 * kind multi-select UI.
 *
 * NOTE: Noise kinds (text_delta, heartbeat, etc.) are intentionally absent from
 * this map. They are available via NOISE_KINDS for the Hide-noise toggle only,
 * matching the server taxonomy which places them in `noise_kinds` (not in the
 * `categories` groups). This ensures the FE fallback matches the server's
 * grouping so the kind multi-select is consistent across runtime versions.
 */
export const CATEGORY_KINDS: Record<string, readonly string[]> = {
  Lifecycle: ["turn_start", "turn_end", "checkpoint", "compaction_start", "compaction_end"],
  Tools: ["tool_start", "tool_end", "source_inspected"],
  Policy: ["rule_check", "rule_violation"],
  Errors: ["error", "aborted"],
  Other: ["child_progress", "child_started", "artifact_created", "task_board"],
};

export interface ActivityFilters {
  /** When true, appends exclude_kind with the NOISE_KINDS set. */
  hideNoise: boolean;
  /** Comma-joined to kind= param when non-empty (ignored when policyEvidenceOnly is set). */
  selectedKinds: string[];
  /** Scopes feed to a specific session when set. */
  sessionId: string | null;
  /**
   * Quick toggle: when true, forces kind=rule_check,rule_violation regardless of
   * selectedKinds, scoping the feed to policy-applied events only.
   * URL key: policy=1
   */
  policyEvidenceOnly?: boolean;
  /**
   * Sub-toggle for policyEvidenceOnly: when true, also sends has_evidence=true so
   * only rule_check rows where evidence actually fired are returned.
   * URL key: evidence=1
   */
  evidenceOnly?: boolean;
  /**
   * Quick toggle: when true, adds status=error,blocked to scope the feed to
   * error/blocked events only.
   * URL key: errors=1
   */
  errorsOnly?: boolean;
}

/** Optional pagination cursors and query options for `buildActivityPageQuery`. */
export interface PaginationParams {
  /** Fetch events with `id > sinceId` ("load newer"). */
  sinceId?: number | null;
  /** Fetch events with `id < beforeId` ("load older"). */
  beforeId?: number | null;
  /**
   * Server-resolved noise kinds to use for `exclude_kind` when hideNoise is ON.
   * When provided and non-empty, uses this set instead of the hardcoded NOISE_KINDS
   * constant. This ensures the API param matches the server's live noise taxonomy
   * (sourced from /meta via resolveKindCategories) rather than the FE constant.
   * Falls back to NOISE_KINDS when absent or empty (backward compat for older runtimes).
   */
  noiseKinds?: readonly string[];
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

  // policyEvidenceOnly overrides selectedKinds: forces kind=rule_check,rule_violation.
  // D2: "Policy & Evidence" includes all policy/rule_check events — both user-authored
  // verifier rules (ruleId prefix "verifier:") and system evidence checks (ruleId prefix
  // "evidence:"). The ruleId prefix distinguishes them; future enhancement may filter by
  // source. For now both are surfaced together under this toggle.
  if (filters.policyEvidenceOnly) {
    params.set("kind", "rule_check,rule_violation");
  } else if (filters.selectedKinds.length > 0) {
    params.set("kind", filters.selectedKinds.join(","));
  }

  if (filters.evidenceOnly) {
    // D1: Evidence-fired filtering (has_evidence=true) narrows to rule_check rows where
    // evidence actually fired (evidenceRef present and non-empty, or detail.matched_evidence>0).
    // rule_violation events (hook-emitted via onRuleViolation, no evidenceRef) are intentionally
    // excluded — they carry no evidence signals and are never returned by the has_evidence path.
    params.set("has_evidence", "true");
  }

  if (filters.errorsOnly) {
    params.set("status", "error,blocked");
  }

  if (filters.hideNoise) {
    // Use server-resolved noise kinds when provided and non-empty; else fall back
    // to the hardcoded constant (backward compat for older runtimes without /meta).
    const effectiveNoiseKinds =
      pagination.noiseKinds != null && pagination.noiseKinds.length > 0
        ? pagination.noiseKinds
        : NOISE_KINDS;
    params.set("exclude_kind", effectiveNoiseKinds.join(","));
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
 *
 * @param noiseKinds Optional server-resolved noise kinds (from resolveKindCategories).
 *   When provided and non-empty, used for the exclude_kind param instead of NOISE_KINDS.
 */
export function buildActivityQuery(
  filters: ActivityFilters,
  noiseKinds?: readonly string[]
): string {
  return buildActivityPageQuery(filters, { noiseKinds });
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
 *   policy=1      → policyEvidenceOnly true
 *   evidence=1    → evidenceOnly true
 *   errors=1      → errorsOnly true
 *
 * The `exclude_kind` expansion is intentionally kept out of the URL to avoid
 * duplicating the NOISE_KINDS literal; `hideNoise=0` is the canonical signal.
 *
 * Optional boolean flags (policyEvidenceOnly, evidenceOnly, errorsOnly) are only
 * set on the returned object when they are true — this preserves round-trip
 * symmetry for callers that create ActivityFilters without these optional keys.
 */
export function parseFiltersFromParams(sp: URLSearchParams): ActivityFilters {
  const noiseParam = sp.get("hideNoise");
  const filters: ActivityFilters = {
    hideNoise: noiseParam === null || noiseParam !== "0",
    selectedKinds: sp.get("kind")?.split(",").filter(Boolean) ?? [],
    sessionId: sp.get("session_id") ?? null,
  };
  if (sp.get("policy") === "1") filters.policyEvidenceOnly = true;
  if (sp.get("evidence") === "1") filters.evidenceOnly = true;
  if (sp.get("errors") === "1") filters.errorsOnly = true;
  return filters;
}

/**
 * Serialize ActivityFilters to URL search params for `router.replace`.
 * Only non-default values are written so the URL stays minimal.
 *   hideNoise true (default) → param omitted
 *   hideNoise false          → hideNoise=0
 *   policyEvidenceOnly true  → policy=1
 *   evidenceOnly true        → evidence=1
 *   errorsOnly true          → errors=1
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
  if (filters.policyEvidenceOnly) {
    p.set("policy", "1");
  }
  if (filters.evidenceOnly) {
    p.set("evidence", "1");
  }
  if (filters.errorsOnly) {
    p.set("errors", "1");
  }
  return p;
}

/** Input shape for formatSessionBreakdown. All fields are optional for back-compat. */
export interface SessionBreakdownInput {
  tool_count?: number;
  rule_check_count?: number;
  error_count?: number;
}

/**
 * Format a compact one-line breakdown string for a session card.
 *
 * Format: "N tool[s] [· N rule[s]] [· N error[s]]"
 *
 * tools — always shown (even 0), since it is the primary activity metric.
 * rules — shown only when > 0 to avoid zero-noise clutter in rule-free sessions.
 * errors — shown only when > 0 so the absence of the indicator means "clean session".
 *
 * Missing / undefined fields are treated as 0 for back-compat with older payloads
 * (e.g. a backend that has not yet deployed the Task 5 enrichment).
 */
export function formatSessionBreakdown(session: SessionBreakdownInput): string {
  const tools = Math.max(0, session.tool_count ?? 0);
  const rules = Math.max(0, session.rule_check_count ?? 0);
  const errors = Math.max(0, session.error_count ?? 0);

  const parts: string[] = [`${tools} ${tools === 1 ? "tool" : "tools"}`];
  if (rules > 0) parts.push(`${rules} ${rules === 1 ? "rule" : "rules"}`);
  if (errors > 0) parts.push(`${errors} ${errors === 1 ? "error" : "errors"}`);

  return parts.join(" · ");
}

/**
 * Verdict extracted from a rule_check or rule_violation event's payload.
 * Returned by extractVerdict when the event kind qualifies; null otherwise.
 */
export interface RuleCheckVerdict {
  /** Rule verdict string: "ok" | "pending" | "violation". Defaults to "pending" if absent. */
  verdict: string;
  /** Evidence reference hash/receipt when evidence actually fired; null otherwise. */
  evidenceRef: string | null;
  /** Human-readable detail string when present; null otherwise. */
  detail: string | null;
}

/**
 * Extract rule_check verdict information from an activity event.
 *
 * Returns a RuleCheckVerdict when the event's kind is "rule_check" or
 * "rule_violation" and the payload contains verdict-shaped data. Returns null
 * for all other event kinds, or when the input is not a valid event object.
 *
 * Confirmed payload field names from magi_agent/runtime/public_events.py and
 * magi_agent/evidence/event_projection.py:
 *   payload.verdict    — RuleVerdict string ("ok" | "pending" | "violation")
 *   payload.evidenceRef — sha256/receipt digest string (optional, top-level key)
 *   payload.detail     — human-readable string (optional)
 *   payload.ruleId     — rule identifier string (not surfaced here)
 */
export function extractVerdict(event: unknown): RuleCheckVerdict | null {
  if (typeof event !== "object" || event === null) return null;
  const ev = event as Record<string, unknown>;
  if (ev.kind !== "rule_check" && ev.kind !== "rule_violation") return null;
  const payload =
    typeof ev.payload === "object" && ev.payload !== null
      ? (ev.payload as Record<string, unknown>)
      : {};
  const verdict = typeof payload.verdict === "string" ? payload.verdict : "pending";
  const evidenceRef =
    typeof payload.evidenceRef === "string" && payload.evidenceRef ? payload.evidenceRef : null;
  const detail =
    typeof payload.detail === "string" && payload.detail ? payload.detail : null;
  return { verdict, evidenceRef, detail };
}

/**
 * Stable channel classification for a session, derived purely from its
 * `session_id`. The runtime encodes the originating channel in the session key
 * (see magi_agent/runtime/session_identity.py and channels/turn_bridge.py):
 *
 *   agent:main:<channel>:<channelId>[:<reset>]   → a live channel conversation
 *   child-session-<hash>                          → a delegated subagent turn
 *   agent:cron:<suffix>  |  cron:<suffix>         → a scheduled (cron) run
 *   cli-session                                   → the local CLI/headless turn
 *
 * `key` drives the icon + tone in the UI; `label` is the human-readable channel
 * name shown on the chip. This is intentionally UI-agnostic (no JSX/colors) so
 * it stays unit-testable — the page maps `key` to an icon + tone class.
 */
export interface SessionChannel {
  key:
    | "app"
    | "telegram"
    | "discord"
    | "slack"
    | "web"
    | "cli"
    | "cron"
    | "subagent"
    | "unknown";
  label: string;
}

/** Known `agent:main:<type>:…` channel types → chip key + label. */
const CHANNEL_TYPE_LABELS: Record<string, { key: SessionChannel["key"]; label: string }> = {
  app: { key: "app", label: "App chat" },
  telegram: { key: "telegram", label: "Telegram" },
  discord: { key: "discord", label: "Discord" },
  slack: { key: "slack", label: "Slack" },
  web: { key: "web", label: "Web" },
  cli: { key: "cli", label: "CLI" },
};

function titleCase(value: string): string {
  const s = value.trim();
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * Classify a session's originating channel from its `session_id`.
 * Never throws; returns the `unknown` channel for empty/opaque ids.
 */
export function deriveSessionChannel(sessionId: string | null | undefined): SessionChannel {
  const id = (sessionId ?? "").trim();
  if (!id) return { key: "unknown", label: "Session" };
  if (id.startsWith("child-session-")) return { key: "subagent", label: "Subagent" };

  const parts = id.split(":");
  if (parts[0] === "cron" || (parts[0] === "agent" && parts[1] === "cron")) {
    return { key: "cron", label: "Scheduled" };
  }
  if (parts[0] === "agent" && parts.length >= 4) {
    const type = (parts[2] || "app").toLowerCase();
    const mapped = CHANNEL_TYPE_LABELS[type];
    return mapped ?? { key: "unknown", label: titleCase(type) };
  }
  if (id === "cli-session" || parts[0] === "cli") return { key: "cli", label: "CLI" };
  return { key: "unknown", label: "Session" };
}

/**
 * Derive a short, human-readable session title from its `session_id`.
 *
 * The goal is a ChatGPT-style recognizable name rather than a raw hash or a
 * jumble of tool names:
 *   agent:main:app:default:<bot>       → "Main chat"
 *   agent:main:app:demo:32             → "demo · #32"      (trailing reset/turn #)
 *   agent:main:telegram:987654         → "987654"          (chip carries platform)
 *   child-session-95e2d5f6b14962fb…    → "Subagent 95e2d5f6"
 *   agent:cron:daily-digest            → "Scheduled: daily-digest"
 *   cli-session                        → "CLI session"
 *
 * `fallbackLabel` (the backend-derived `label`) is used only for genuinely
 * opaque ids that match none of the known shapes, so the tool-name jumble never
 * surfaces as the title of a recognizable session.
 */
export function deriveSessionTitle(
  sessionId: string | null | undefined,
  fallbackLabel?: string | null,
): string {
  const id = (sessionId ?? "").trim();
  const fallback = (fallbackLabel ?? "").trim();
  if (!id) return fallback || "Session";

  if (id.startsWith("child-session-")) {
    const hash = id.slice("child-session-".length);
    const short = hash.slice(0, 8);
    return short ? `Subagent ${short}` : "Subagent";
  }

  const parts = id.split(":");
  if (parts[0] === "cron") return cronTitle(parts.slice(1).join(":"));
  if (parts[0] === "agent" && parts[1] === "cron") return cronTitle(parts.slice(2).join(":"));

  if (parts[0] === "agent" && parts.length >= 4) {
    const channelId = parts[3] || "default";
    // A trailing numeric segment is the reset/turn counter — surface it as "#N"
    // so otherwise-identical conversations stay distinguishable.
    const last = parts[parts.length - 1];
    const resetSuffix = parts.length >= 5 && /^\d+$/.test(last) ? ` · #${last}` : "";
    if (channelId === "default") return `Main chat${resetSuffix}`;
    return `${channelId}${resetSuffix}`;
  }

  if (id === "cli-session") return "CLI session";
  return fallback || id;
}

function cronTitle(suffix: string): string {
  const s = suffix.trim();
  return s ? `Scheduled: ${s}` : "Scheduled run";
}

/** Minimal shape needed to build the parent→child session tree. */
export interface SessionTreeInput {
  id?: string | null;
  /** Parent session id (subagent linkage), when the backend resolved one. */
  parent_session_id?: string | null;
}

/** A node in the session forest: a session plus its nested child sessions. */
export interface SessionNode<T> {
  session: T;
  children: SessionNode<T>[];
}

/** A flattened, render-ready row produced by `flattenSessionForest`. */
export interface FlatSessionRow<T> {
  session: T;
  /** Nesting depth (0 = top-level). */
  depth: number;
  /** Number of direct child sessions. */
  childCount: number;
  /** Total number of nested descendant sessions. */
  descendantCount: number;
}

/**
 * Group a flat session list into a parent→child forest.
 *
 * A session nests under `parent_session_id` when that parent is present in the
 * same list; otherwise it stays at the top level (so a subagent whose parent
 * fell outside the fetched window still renders, just un-nested). Input order
 * is preserved for both roots and each parent's children, and self/cycle links
 * are defended against (treated as roots). Sessions without an `id` are always
 * roots. Neither the input array nor its elements are mutated.
 */
export function buildSessionForest<T extends SessionTreeInput>(
  sessions: T[],
): SessionNode<T>[] {
  const nodeById = new Map<string, SessionNode<T>>();
  const nodes: SessionNode<T>[] = sessions.map((session) => {
    const node: SessionNode<T> = { session, children: [] };
    if (session.id) nodeById.set(session.id, node);
    return node;
  });

  const roots: SessionNode<T>[] = [];
  for (const node of nodes) {
    const id = node.session.id ?? null;
    const parentId = node.session.parent_session_id ?? null;
    const parent =
      parentId && parentId !== id && !wouldCycle(nodeById, id, parentId)
        ? nodeById.get(parentId)
        : undefined;
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

/** True when attaching `childId` under `parentId` would form a parent cycle. */
function wouldCycle<T extends SessionTreeInput>(
  nodeById: Map<string, SessionNode<T>>,
  childId: string | null,
  parentId: string,
): boolean {
  if (!childId) return false;
  let cursor: string | null = parentId;
  const seen = new Set<string>();
  while (cursor) {
    if (cursor === childId) return true;
    if (seen.has(cursor)) return true;
    seen.add(cursor);
    cursor = nodeById.get(cursor)?.session.parent_session_id ?? null;
  }
  return false;
}

function countDescendants<T>(node: SessionNode<T>): number {
  let total = 0;
  for (const child of node.children) total += 1 + countDescendants(child);
  return total;
}

/**
 * Flatten a session forest into display rows in tree order, skipping the
 * children of any session id present in `collapsed`. Each row carries its
 * nesting `depth` plus direct-child and total-descendant counts so the UI can
 * render an expand/collapse affordance and a "N subagents" badge.
 */
export function flattenSessionForest<T extends SessionTreeInput>(
  roots: SessionNode<T>[],
  collapsed: ReadonlySet<string>,
): FlatSessionRow<T>[] {
  const out: FlatSessionRow<T>[] = [];
  const visit = (node: SessionNode<T>, depth: number): void => {
    out.push({
      session: node.session,
      depth,
      childCount: node.children.length,
      descendantCount: countDescendants(node),
    });
    const id = node.session.id ?? null;
    const isCollapsed = id != null && collapsed.has(id);
    if (!isCollapsed) {
      for (const child of node.children) visit(child, depth + 1);
    }
  };
  for (const root of roots) visit(root, 0);
  return out;
}

/**
 * Resolve kind categories and noise kinds from the /meta endpoint's `categories`
 * payload, falling back to the FE constants (CATEGORY_KINDS / NOISE_KINDS) when
 * the server payload is absent or malformed (older runtimes without Task 7).
 *
 * The /meta categories payload shape (from get_meta_taxonomy()):
 *   {
 *     "categories": { "lifecycle": [...], "tools": [...], "policy": [...], ... },
 *     "noise_kinds": ["text_delta", "heartbeat", ...]
 *   }
 *
 * Usage: const { categories, noiseKinds } = resolveKindCategories(meta?.categories);
 */
export function resolveKindCategories(metaCategories: unknown): {
  categories: Record<string, readonly string[]>;
  noiseKinds: readonly string[];
} {
  if (
    typeof metaCategories === "object" &&
    metaCategories !== null &&
    "categories" in metaCategories
  ) {
    const mc = metaCategories as Record<string, unknown>;
    const cats = mc.categories;
    if (typeof cats === "object" && cats !== null && !Array.isArray(cats)) {
      const noiseKinds = Array.isArray(mc.noise_kinds)
        ? (mc.noise_kinds as string[])
        : NOISE_KINDS;
      return {
        categories: cats as Record<string, readonly string[]>,
        noiseKinds,
      };
    }
  }
  // Fallback: older runtime or malformed payload — use the FE constants.
  return { categories: CATEGORY_KINDS, noiseKinds: NOISE_KINDS };
}
