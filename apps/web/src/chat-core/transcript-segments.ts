// Pure builder + derivation helpers for ordered interleaved transcript segments.
// NO React, NO fetch, NO DOM. Captures an assistant turn as an ordered list of
// think/tool/text slices in true chronological order so a completed message can
// render think -> tool -> think -> tool -> text exactly as it happened.
//
// The flat fields (thinkingContent / activities / content) stay the source of
// truth for every existing consumer. These helpers keep segments DERIVED-EQUAL:
// concatenating text segments === content, concatenating thinking segments ===
// thinkingContent, and each tool segment references a ToolActivity by id.

import type {
  TextSegment,
  ThinkingSegment,
  ToolActivity,
  ToolSegment,
  TranscriptSegment,
} from "./types";

/**
 * Synthetic model-progress / heartbeat activity ids use this prefix (the stream
 * reducer + chat-client convention). They are transient progress noise, never a
 * real tool action, so they never earn an ordered tool segment.
 */
const SYNTHETIC_ACTIVITY_ID_PREFIX = "llm:";

/**
 * Append user-visible text to the ordered segment list. Coalesces into the
 * trailing text segment when the previous segment is also text (so a turn's
 * text does not shatter into one segment per token). Closing an open thinking
 * segment is the caller's concern via `closeOpenThinking` when a real boundary
 * is crossed; a bare text delta after thinking implicitly ends the thinking
 * phase, so we close it here too.
 */
export function appendSegmentText(
  segments: TranscriptSegment[] | undefined,
  text: string,
  now: number = Date.now(),
): TranscriptSegment[] {
  if (!text) return segments ?? [];
  const next = closeOpenThinking(segments, now);
  const last = next[next.length - 1];
  if (last && last.kind === "text") {
    const merged: TextSegment = { kind: "text", text: last.text + text };
    return [...next.slice(0, -1), merged];
  }
  return [...next, { kind: "text", text }];
}

/**
 * Append reasoning text to the ordered segment list. Coalesces into the trailing
 * thinking segment when it is still open (no `closedAt`); otherwise opens a new
 * thinking segment stamped with `openedAt`. A closed thinking segment is never
 * reopened: a fresh reasoning burst after a tool/text is a NEW thinking phase,
 * which is exactly what lets the UI render multiple independently-collapsible
 * thinking blocks in order.
 */
export function appendSegmentThinking(
  segments: TranscriptSegment[] | undefined,
  text: string,
  openedAt: number = Date.now(),
): TranscriptSegment[] {
  if (!text) return segments ?? [];
  const next = [...(segments ?? [])];
  const last = next[next.length - 1];
  if (last && last.kind === "thinking" && last.closedAt === undefined) {
    const merged: ThinkingSegment = { ...last, text: last.text + text };
    return [...next.slice(0, -1), merged];
  }
  return [...next, { kind: "thinking", text, openedAt }];
}

/**
 * Append a tool reference to the ordered segment list. Closes any open thinking
 * segment first (a tool boundary ends the current reasoning phase). Deduplicates
 * a repeated reference to the SAME tool id when it is already the trailing tool
 * segment (tool_start followed by tool_progress/tool_end for the same id must
 * not create three segments); the tool's live state is looked up by id from the
 * sibling activity list, so one segment per tool id is sufficient.
 */
export function appendSegmentTool(
  segments: TranscriptSegment[] | undefined,
  toolId: string,
  now: number = Date.now(),
): TranscriptSegment[] {
  if (!toolId) return segments ?? [];
  const next = closeOpenThinking(segments, now);
  const last = next[next.length - 1];
  if (last && last.kind === "tool" && last.toolId === toolId) {
    return next;
  }
  const segment: ToolSegment = { kind: "tool", toolId };
  return [...next, segment];
}

/**
 * Close a trailing open thinking segment (stamp `closedAt`). No-op when the last
 * segment is not an open thinking segment. Returns a NEW array only when a
 * change is made, otherwise the same reference so callers can compare cheaply.
 */
export function closeOpenThinking(
  segments: TranscriptSegment[] | undefined,
  now: number = Date.now(),
): TranscriptSegment[] {
  const list = segments ?? [];
  const last = list[list.length - 1];
  if (!last || last.kind !== "thinking" || last.closedAt !== undefined) {
    return list;
  }
  const closed: ThinkingSegment = { ...last, closedAt: now };
  return [...list.slice(0, -1), closed];
}

/**
 * Live-path helper: the live callback delivers the FULL activity list on every
 * change (not a single tool_start), so this appends a tool segment for every
 * real (non-synthetic) activity id that does not already have a tool segment,
 * in list order. Existing tool segments are preserved in place, so a tool's
 * chronological position is fixed at first appearance and later status updates
 * (its ToolActivity is looked up by id) never reorder it.
 *
 * Any open thinking segment is closed before the first newly-appended tool.
 */
export function appendNewToolSegments(
  segments: TranscriptSegment[] | undefined,
  activities: readonly ToolActivity[] | undefined,
  now: number = Date.now(),
): TranscriptSegment[] {
  const current = segments ?? [];
  if (!activities || activities.length === 0) return current;
  const seen = new Set<string>();
  for (const segment of current) {
    if (segment.kind === "tool") seen.add(segment.toolId);
  }
  let next = current;
  for (const activity of activities) {
    const id = activity.id;
    if (!id || id.startsWith(SYNTHETIC_ACTIVITY_ID_PREFIX) || seen.has(id)) {
      continue;
    }
    next = appendSegmentTool(next, id, now);
    seen.add(id);
  }
  return next;
}

/** Concatenate all text segments (the derived user-visible content). */
export function deriveContentFromSegments(
  segments: TranscriptSegment[] | undefined,
): string {
  if (!segments) return "";
  let out = "";
  for (const segment of segments) {
    if (segment.kind === "text") out += segment.text;
  }
  return out;
}

/** Concatenate all thinking segments (the derived chain-of-thought text). */
export function deriveThinkingFromSegments(
  segments: TranscriptSegment[] | undefined,
): string {
  if (!segments) return "";
  let out = "";
  for (const segment of segments) {
    if (segment.kind === "thinking") out += segment.text;
  }
  return out;
}

/** Ordered list of tool ids referenced by the segments, in appearance order. */
export function deriveToolIdsFromSegments(
  segments: TranscriptSegment[] | undefined,
): string[] {
  if (!segments) return [];
  const ids: string[] = [];
  for (const segment of segments) {
    if (segment.kind === "tool") ids.push(segment.toolId);
  }
  return ids;
}

/**
 * Content-authority check: are these segments a faithful ordered decomposition
 * of `content`? The renderer uses this to decide whether to trust the
 * interleaved segments or fall back to the flat layout. If some catch-up/error
 * path mutated `content` after segments were captured (e.g. a snapshot repair
 * replaced the visible text, or an interrupted suffix was appended), the derived
 * text will no longer equal `content` and the flat layout is authoritative.
 *
 * Only text authority is checked: thinking and tools are additive chrome, but
 * the visible answer body is what the copy/export/dedupe consumers read, so it
 * is the one that must match exactly.
 */
export function segmentsMatchContent(
  segments: TranscriptSegment[] | undefined,
  content: string,
): boolean {
  if (!segments || segments.length === 0) return false;
  return deriveContentFromSegments(segments) === content;
}

/**
 * Produce the segments to persist on a FINALIZED assistant message, or
 * `undefined` when the flat layout must be authoritative.
 *
 * Closes any trailing open thinking segment, then applies the content-authority
 * check against `finalContent` (the exact string the message will carry). When
 * a catch-up/snapshot-repair/error path replaced or suffixed the visible text
 * after segments were captured, the derived text no longer equals `finalContent`
 * and this returns `undefined` so the renderer falls back to the flat layout.
 * Returns `undefined` for an empty/absent live segment list too.
 */
export function finalizedSegmentsForMessage(
  liveSegments: TranscriptSegment[] | undefined,
  finalContent: string,
  now: number = Date.now(),
): TranscriptSegment[] | undefined {
  if (!liveSegments || liveSegments.length === 0) return undefined;
  const closed = closeOpenThinking(liveSegments, now);
  return segmentsMatchContent(closed, finalContent) ? closed : undefined;
}
