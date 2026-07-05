import type {
  ChatMessage,
  ResearchEvidenceSnapshot,
  ResponseUsage,
  ToolActivity,
  TranscriptSegment,
} from "./types";
import { normalizeResearchEvidenceSnapshot } from "./research-evidence";
import { normalizeResponseUsage } from "./response-usage";
import { deriveContentFromSegments as segmentsDeriveContent } from "./transcript-segments";

export interface HistoryPlaintextInput {
  role: "user" | "assistant";
  content: string;
  thinkingContent?: string;
  thinkingDuration?: number;
  researchEvidence?: ResearchEvidenceSnapshot;
  usage?: ResponseUsage;
  /**
   * Tool/skill activities captured during the streaming phase. When present and
   * non-empty, the envelope is bumped to `_v:4` and a COMPACT, terminal-only
   * projection is persisted (see `projectPersistedActivities`). `running`
   * activities are normalized to `done` (the turn is over), and `startedAt` /
   * `patchPreview` are dropped (relative time is meaningless after reload; patch
   * previews are too large for the transcript). This is what restores the
   * "Completed N actions" timeline rows after a reload / across a session.
   */
  activities?: ToolActivity[];
  /**
   * Ordered interleaved transcript segments (think -> tool -> text). Persisted as
   * an OPTIONAL `segments` key INSIDE the existing envelope with NO version bump
   * (Kevin decision: OPTION A). Old decoders ignore the unknown key and still
   * render the flat layout; upgraded decoders reconstruct the interleaved view.
   * Only persisted when derived-equal to `content` (content-authority check is
   * the caller's concern). Tool segments reference the persisted `activities` by
   * id.
   */
  segments?: TranscriptSegment[];
}

export interface DecodedHistoryPlaintext {
  content: string;
  thinkingContent?: string;
  thinkingDuration?: number;
  researchEvidence?: ResearchEvidenceSnapshot;
  usage?: ResponseUsage;
  activities?: ToolActivity[];
  segments?: TranscriptSegment[];
}

interface AssistantHistoryEnvelope {
  _v: number;
  content?: unknown;
  thinking?: unknown;
  thinkingDuration?: unknown;
  researchEvidence?: unknown;
  usage?: unknown;
  activities?: unknown;
  segments?: unknown;
}

/**
 * Compact, terminal-only activity shape stored INSIDE a `_v:4` envelope. A strict
 * subset of `ToolActivity`: no `startedAt` (relative, useless after reload), no
 * `patchPreview` (size), and `status` is restricted to terminal values.
 */
interface PersistedToolActivity {
  id: string;
  label: string;
  status: "done" | "error" | "denied";
  durationMs?: number;
  inputPreview?: string;
  outputPreview?: string;
}

/** Cap the number of persisted activities to bound transcript growth. */
const MAX_PERSISTED_ACTIVITIES = 50;
/** Cap preview lengths persisted into the transcript. */
const MAX_PERSISTED_PREVIEW_LEN = 200;

function truncatePreview(value: string | undefined): string | undefined {
  if (typeof value !== "string" || value.length === 0) return undefined;
  return value.length > MAX_PERSISTED_PREVIEW_LEN
    ? value.slice(0, MAX_PERSISTED_PREVIEW_LEN)
    : value;
}

/** Project live `ToolActivity[]` into the compact, terminal-only persisted form. */
function projectPersistedActivities(activities: ToolActivity[]): PersistedToolActivity[] {
  return activities.slice(0, MAX_PERSISTED_ACTIVITIES).map((activity) => {
    // The turn is finished; anything still "running" is reported as "done".
    const status: PersistedToolActivity["status"] =
      activity.status === "error" || activity.status === "denied" ? activity.status : "done";
    const inputPreview = truncatePreview(activity.inputPreview);
    const outputPreview = truncatePreview(activity.outputPreview);
    return {
      id: activity.id,
      label: activity.label,
      status,
      ...(typeof activity.durationMs === "number" ? { durationMs: activity.durationMs } : {}),
      ...(inputPreview ? { inputPreview } : {}),
      ...(outputPreview ? { outputPreview } : {}),
    };
  });
}

/** Validate + reconstruct `ToolActivity[]` from a decoded `_v:4` envelope. */
function normalizePersistedActivities(value: unknown): ToolActivity[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out: ToolActivity[] = [];
  for (const entry of value) {
    if (!entry || typeof entry !== "object") continue;
    const record = entry as Record<string, unknown>;
    if (typeof record.id !== "string" || typeof record.label !== "string") continue;
    const status =
      record.status === "error" || record.status === "denied" ? record.status : "done";
    const durationMs =
      typeof record.durationMs === "number" && Number.isFinite(record.durationMs)
        ? record.durationMs
        : undefined;
    const inputPreview = typeof record.inputPreview === "string" ? record.inputPreview : undefined;
    const outputPreview =
      typeof record.outputPreview === "string" ? record.outputPreview : undefined;
    out.push({
      id: record.id,
      label: record.label,
      status,
      // `startedAt` is not persisted; synthesize a stable 0 so the type holds.
      // The activity timeline groups by label/status and never reads this.
      startedAt: 0,
      ...(durationMs !== undefined ? { durationMs } : {}),
      ...(inputPreview ? { inputPreview } : {}),
      ...(outputPreview ? { outputPreview } : {}),
    });
    if (out.length >= MAX_PERSISTED_ACTIVITIES) break;
  }
  return out.length > 0 ? out : undefined;
}

/** Cap persisted segments to bound transcript growth. */
const MAX_PERSISTED_SEGMENTS = 200;
/** Cap persisted per-thinking-segment text length. */
const MAX_PERSISTED_THINKING_LEN = 20_000;

/**
 * Project live `TranscriptSegment[]` into the compact persisted form. Thinking
 * text is length-capped; `openedAt`/`closedAt` timestamps are dropped (relative,
 * meaningless after reload) and re-synthesized to 0 on decode. Tool segments
 * keep only the `toolId` (which references the persisted `activities`). Empty
 * text/thinking segments are dropped.
 */
function projectPersistedSegments(
  segments: TranscriptSegment[],
): Array<Record<string, unknown>> {
  const out: Array<Record<string, unknown>> = [];
  for (const segment of segments) {
    if (out.length >= MAX_PERSISTED_SEGMENTS) break;
    if (segment.kind === "text") {
      if (!segment.text) continue;
      out.push({ k: "text", t: segment.text });
    } else if (segment.kind === "thinking") {
      if (!segment.text) continue;
      const text =
        segment.text.length > MAX_PERSISTED_THINKING_LEN
          ? segment.text.slice(0, MAX_PERSISTED_THINKING_LEN)
          : segment.text;
      out.push({ k: "thinking", t: text });
    } else if (segment.kind === "tool") {
      if (!segment.toolId) continue;
      out.push({ k: "tool", id: segment.toolId });
    }
  }
  return out;
}

/** Validate + reconstruct `TranscriptSegment[]` from a persisted `segments` key. */
function normalizePersistedSegments(value: unknown): TranscriptSegment[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out: TranscriptSegment[] = [];
  for (const entry of value) {
    if (out.length >= MAX_PERSISTED_SEGMENTS) break;
    if (!entry || typeof entry !== "object") continue;
    const record = entry as Record<string, unknown>;
    const kind = record.k;
    if (kind === "text") {
      const text = typeof record.t === "string" ? record.t : "";
      if (text) out.push({ kind: "text", text });
    } else if (kind === "thinking") {
      const text = typeof record.t === "string" ? record.t : "";
      // openedAt is not persisted; synthesize a stable 0. The renderer keys
      // collapsible state by index, never by this timestamp.
      if (text) out.push({ kind: "thinking", text, openedAt: 0 });
    } else if (kind === "tool") {
      const id = typeof record.id === "string" ? record.id : "";
      if (id) out.push({ kind: "tool", toolId: id });
    }
  }
  return out.length > 0 ? out : undefined;
}

export function encodeHistoryPlaintext(message: HistoryPlaintextInput): string {
  if (message.role !== "assistant") return message.content;
  const hasActivities = !!message.activities && message.activities.length > 0;
  const hasSegments = !!message.segments && message.segments.length > 0;
  if (!message.thinkingContent && !message.researchEvidence && !message.usage && !hasActivities) {
    return message.content;
  }
  // Version is monotonic in capability: v4 ⊇ v3 (research/usage) ⊇ v2 (thinking).
  // `segments` rides as an OPTIONAL key with NO version bump (OPTION A): old
  // decoders at any _v ignore it and render the flat layout.
  const version = hasActivities ? 4 : message.researchEvidence || message.usage ? 3 : 2;
  const envelope = {
    _v: version,
    content: message.content,
    ...(message.thinkingContent ? { thinking: message.thinkingContent } : {}),
    ...(typeof message.thinkingDuration === "number"
      ? { thinkingDuration: message.thinkingDuration }
      : {}),
    ...(message.researchEvidence ? { researchEvidence: message.researchEvidence } : {}),
    ...(message.usage ? { usage: message.usage } : {}),
    ...(hasActivities ? { activities: projectPersistedActivities(message.activities!) } : {}),
    ...(hasSegments ? { segments: projectPersistedSegments(message.segments!) } : {}),
  };
  return JSON.stringify(envelope);
}

export function decodeHistoryPlaintext(
  role: ChatMessage["role"],
  raw: string,
): DecodedHistoryPlaintext {
  if (role !== "assistant" || !raw.startsWith('{"_v":')) {
    return { content: raw };
  }

  try {
    const envelope = JSON.parse(raw) as AssistantHistoryEnvelope;
    if (envelope._v !== 2 && envelope._v !== 3 && envelope._v !== 4) return { content: raw };
    if (typeof envelope.content !== "string") return { content: raw };
    const thinkingContent = typeof envelope.thinking === "string" ? envelope.thinking : undefined;
    const thinkingDuration =
      typeof envelope.thinkingDuration === "number" && Number.isFinite(envelope.thinkingDuration)
        ? envelope.thinkingDuration
        : undefined;
    const researchEvidence = envelope._v >= 3
      ? normalizeResearchEvidenceSnapshot(envelope.researchEvidence)
      : undefined;
    const usage = envelope._v >= 3 ? normalizeResponseUsage(envelope.usage) : undefined;
    const activities = envelope._v >= 4
      ? normalizePersistedActivities(envelope.activities)
      : undefined;
    // `segments` is version-independent (OPTION A): read it at any known _v.
    // Only keep it when its derived text matches the decoded content, so a
    // stale/mismatched persisted segment set can never override the flat body.
    const decodedSegments = normalizePersistedSegments(envelope.segments);
    const segments =
      decodedSegments && segmentsDeriveContent(decodedSegments) === envelope.content
        ? decodedSegments
        : undefined;
    return {
      content: envelope.content,
      ...(thinkingContent ? { thinkingContent } : {}),
      ...(thinkingDuration !== undefined ? { thinkingDuration } : {}),
      ...(researchEvidence ? { researchEvidence } : {}),
      ...(usage ? { usage } : {}),
      ...(activities ? { activities } : {}),
      ...(segments ? { segments } : {}),
    };
  } catch {
    return { content: raw };
  }
}
