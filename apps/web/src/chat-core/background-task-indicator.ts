/**
 * Compact "N running" / "N queued" indicator for the chat header. Pure
 * view-model — the chat surface (in components/chat) renders the chip and
 * links it to the work-queue board.
 *
 * Reads a minimal subset of the WorkQueueTask shape that the existing
 * `lib/work-queue-api` client exposes; that shape is inlined here so the
 * chat-core boundary forbidding `@/lib` / `../lib` imports stays clean
 * (see mission-work-queue.ts for the same pattern). The runtime field that
 * matters is just `status`.
 */

/** Minimal task shape this view-model needs. Byte-compatible subset of
 *  `lib/work-queue-api.WorkQueueTask` — only `status` is consulted. */
export interface BackgroundTaskInput {
  status: string;
}

const RUNNING_STATUS = "running";
const QUEUED_STATUSES = new Set(["triage", "todo", "ready"]);
const BOARD_HREF_FALLBACK = "/dashboard/work-queue";

export interface BackgroundTaskIndicator {
  /** Count of tasks in `running`. */
  running: number;
  /** Count of tasks in `triage`/`todo`/`ready`. */
  queued: number;
  /** running + queued (anything terminal is excluded). */
  active: number;
  /** A short label like "1 running" or "2 running · 1 queued". */
  label: string;
  /** Where to open the board. Per-bot when `botId` is supplied. */
  boardHref: string;
}

export interface BuildBackgroundTaskIndicatorOptions {
  botId?: string | null;
}

function boardHref(botId: string | null | undefined): string {
  const trimmed = typeof botId === "string" ? botId.trim() : "";
  return trimmed ? `/dashboard/${encodeURIComponent(trimmed)}/work-queue` : BOARD_HREF_FALLBACK;
}

function formatLabel(running: number, queued: number): string {
  if (running > 0 && queued > 0) return `${running} running · ${queued} queued`;
  if (running > 0) return `${running} running`;
  return `${queued} queued`;
}

export function buildBackgroundTaskIndicator(
  tasks: readonly BackgroundTaskInput[],
  options: BuildBackgroundTaskIndicatorOptions = {},
): BackgroundTaskIndicator | null {
  let running = 0;
  let queued = 0;
  for (const t of tasks) {
    if (t.status === RUNNING_STATUS) running += 1;
    else if (QUEUED_STATUSES.has(t.status)) queued += 1;
  }
  const active = running + queued;
  if (active === 0) return null;
  return {
    running,
    queued,
    active,
    label: formatLabel(running, queued),
    boardHref: boardHref(options.botId),
  };
}
