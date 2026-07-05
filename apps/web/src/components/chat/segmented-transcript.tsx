import type { ReactNode } from "react";
import { AgentActivityTimeline } from "./agent-activity-timeline";
import { ThinkingBlock } from "./thinking-block";
import type { ToolActivity, TranscriptSegment } from "@/chat-core";

/**
 * A contiguous group of tool segments, collapsed into a single "Ran N actions"
 * timeline row. Interleaving is preserved at the group granularity: a run of
 * back-to-back tools renders as one timeline, but a thinking/text segment
 * between two tool runs splits them into two separate timelines in order.
 */
interface RenderGroup {
  kind: "thinking" | "text" | "tools";
  key: string;
  /** thinking */
  text?: string;
  /** tools: the resolved ToolActivity[] for this contiguous run. */
  activities?: ToolActivity[];
}

export interface SegmentedTranscriptProps {
  segments: TranscriptSegment[];
  /** Full activity list; tool segments are resolved against this by id. */
  activities?: ToolActivity[];
  /** True while the turn is still streaming (drives cursor + auto-expand). */
  isStreaming?: boolean;
  /** True on the live assistant turn (drives timeline live styling). */
  live?: boolean;
  /**
   * Render one text segment's body. Provided by the caller so the exact markdown
   * pipeline (charts, citations, KaTeX, cursor) lives in one place and is shared
   * with the flat layout. `isLast` marks the final text segment so only it shows
   * the streaming cursor.
   */
  renderText: (text: string, opts: { isLast: boolean }) => ReactNode;
}

/**
 * Live-only: a thinking segment shorter than this (trimmed) is treated as a
 * between-tool micro-burst rather than a real reasoning phase. Some models
 * (e.g. Kimi) emit a one-liner or a whitespace-only placeholder thought before
 * each tool call. Rendering each as its own collapsible `ThinkingBlock` (header
 * + `mb-3` margin) turns a streaming turn into a ladder of tiny "Thought" rows
 * AND, worse, splits every tool into its own "Ran 1 action" timeline because a
 * thinking group breaks the contiguous tool run. During streaming we suppress
 * these so consecutive tools re-coalesce into a single "Ran N actions" row,
 * matching the completed view. Empty/whitespace-only thoughts (length 0) are
 * always below this floor, so they never render a header. Nothing is lost: the
 * finalized message re-renders from the full segment list once the turn ends,
 * and the compaction is disabled entirely (`compact === false`) off the live
 * path, so the completed/persisted view is byte-identical to before.
 */
const LIVE_MICRO_THINKING_MAX_CHARS = 40;

/**
 * A thinking segment we drop from the LIVE segmented layout: a short/empty burst
 * that would otherwise ladder. Only ever true when `compact` (streaming).
 */
function isSuppressedLiveThinking(
  segment: TranscriptSegment,
  compact: boolean,
): boolean {
  if (!compact || segment.kind !== "thinking") return false;
  return segment.text.trim().length < LIVE_MICRO_THINKING_MAX_CHARS;
}

function buildGroups(
  segments: TranscriptSegment[],
  activityById: Map<string, ToolActivity>,
  compact: boolean,
): RenderGroup[] {
  const groups: RenderGroup[] = [];
  for (let i = 0; i < segments.length; i += 1) {
    const segment = segments[i];
    if (isSuppressedLiveThinking(segment, compact)) {
      // Drop the micro-burst entirely so the surrounding tool run stays
      // contiguous (the next tool segment coalesces into the prior tools group).
      continue;
    }
    if (segment.kind === "thinking") {
      groups.push({ kind: "thinking", key: `thinking-${i}`, text: segment.text });
    } else if (segment.kind === "text") {
      groups.push({ kind: "text", key: `text-${i}`, text: segment.text });
    } else {
      // Coalesce a contiguous run of tool segments into one timeline group.
      const last = groups[groups.length - 1];
      const resolved = activityById.get(segment.toolId);
      const activity = resolved ?? {
        id: segment.toolId,
        label: segment.toolId,
        status: "done" as const,
        startedAt: 0,
      };
      if (last && last.kind === "tools" && last.activities) {
        last.activities.push(activity);
      } else {
        groups.push({ kind: "tools", key: `tools-${i}`, activities: [activity] });
      }
    }
  }
  return groups;
}

/**
 * Ordered interleaved renderer for a completed OR live assistant turn. Drives
 * think -> tool -> think -> tool -> text exactly as captured. Each thinking
 * segment is an independently collapsible `ThinkingBlock`; each contiguous run
 * of tools is one collapsible `AgentActivityTimeline` ("Ran N actions"); text
 * renders via the caller's `renderText`.
 */
export function SegmentedTranscript({
  segments,
  activities,
  isStreaming,
  live,
  renderText,
}: SegmentedTranscriptProps) {
  const activityById = new Map<string, ToolActivity>();
  for (const activity of activities ?? []) activityById.set(activity.id, activity);

  const groups = buildGroups(segments, activityById, Boolean(isStreaming));
  const lastTextKey = [...groups].reverse().find((g) => g.kind === "text")?.key;

  return (
    <div className="flex flex-col gap-1" data-chat-segmented-transcript="true">
      {groups.map((group) => {
        if (group.kind === "thinking") {
          return (
            <div key={group.key} data-chat-segment="thinking">
              <ThinkingBlock content={group.text ?? ""} isLive={Boolean(isStreaming)} />
            </div>
          );
        }
        if (group.kind === "tools") {
          return (
            <div key={group.key} data-chat-segment="tools">
              <AgentActivityTimeline
                live={Boolean(live || isStreaming)}
                activities={group.activities}
                taskBoard={null}
                collapsedByDefault
              />
            </div>
          );
        }
        return (
          <div key={group.key} data-chat-segment="text">
            {renderText(group.text ?? "", { isLast: group.key === lastTextKey })}
          </div>
        );
      })}
    </div>
  );
}
