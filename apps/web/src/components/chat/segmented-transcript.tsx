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

function buildGroups(
  segments: TranscriptSegment[],
  activityById: Map<string, ToolActivity>,
): RenderGroup[] {
  const groups: RenderGroup[] = [];
  for (let i = 0; i < segments.length; i += 1) {
    const segment = segments[i];
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

  const groups = buildGroups(segments, activityById);
  const lastTextKey = [...groups].reverse().find((g) => g.kind === "text")?.key;

  return (
    <div className="flex flex-col gap-1" data-chat-segmented-transcript="true">
      {groups.map((group) => {
        if (group.kind === "thinking") {
          return (
            <ThinkingBlock
              key={group.key}
              content={group.text ?? ""}
              isLive={Boolean(isStreaming)}
            />
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
