/**
 * View-model for the `RunInBackground` tool result that the model emits when
 * it pushes work onto the durable work-queue (PR #732). The chat surface
 * picks this up to render a distinctive "started in background" ack card with
 * a link to the work-queue board, instead of a plain tool-result blob.
 *
 * Pure logic — no React / Next / DOM imports. The renderer (in
 * components/chat) consumes the shape this module produces.
 */

const RUN_IN_BACKGROUND_TOOL = "RunInBackground";
const BOARD_HREF_FALLBACK = "/dashboard/work-queue";

export interface BackgroundTaskAckCard {
  /** Full work-queue task id. */
  taskId: string;
  /** First 6 chars — what the model surfaces in conversation. */
  shortId: string;
  /** User-visible task title (clamped by the server). */
  title: string;
  /** Initial status — almost always "todo" right after enqueue. */
  status: string;
  /** Whether this task runs the Ralph-style goal-judge loop. */
  goalMode: boolean;
  /** The ack sentence the tool returned, suitable to render under the card. */
  ack: string;
  /** Where to open the live board view for this task. */
  boardHref: string;
}

export interface BuildBackgroundTaskAckOptions {
  /** Optional bot id so the board link is per-bot. Falls back to a flat path. */
  botId?: string | null;
}

interface ToolResultLike {
  toolName?: unknown;
  output?: unknown;
}

interface MessageLike {
  tool_results?: unknown;
}

export function isBackgroundTaskAck(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const { toolName } = value as ToolResultLike;
  return typeof toolName === "string" && toolName === RUN_IN_BACKGROUND_TOOL;
}

function readString(record: Record<string, unknown>, key: string): string | null {
  const raw = record[key];
  return typeof raw === "string" && raw.length > 0 ? raw : null;
}

function readBoolean(record: Record<string, unknown>, key: string): boolean {
  return record[key] === true;
}

function boardHref(botId: string | null | undefined): string {
  const trimmed = typeof botId === "string" ? botId.trim() : "";
  return trimmed ? `/dashboard/${encodeURIComponent(trimmed)}/work-queue` : BOARD_HREF_FALLBACK;
}

export function buildBackgroundTaskAckCard(
  toolResult: unknown,
  options: BuildBackgroundTaskAckOptions = {},
): BackgroundTaskAckCard | null {
  if (!isBackgroundTaskAck(toolResult)) return null;
  const { output } = toolResult as ToolResultLike;
  if (!output || typeof output !== "object") return null;

  const record = output as Record<string, unknown>;
  const taskId = readString(record, "taskId");
  const title = readString(record, "title");
  const status = readString(record, "status");
  if (!taskId || !title || !status) return null;

  const goalMode = readBoolean(record, "goalMode");
  const ack = readString(record, "ack") ?? "";

  return {
    taskId,
    shortId: taskId.slice(0, 6),
    title,
    status,
    goalMode,
    ack,
    boardHref: boardHref(options.botId),
  };
}

export function parseBackgroundTaskAckFromMessage(
  message: unknown,
  options: BuildBackgroundTaskAckOptions = {},
): BackgroundTaskAckCard | null {
  if (!message || typeof message !== "object") return null;
  const { tool_results: results } = message as MessageLike;
  if (!Array.isArray(results)) return null;
  for (const candidate of results) {
    const card = buildBackgroundTaskAckCard(candidate, options);
    if (card) return card;
  }
  return null;
}
