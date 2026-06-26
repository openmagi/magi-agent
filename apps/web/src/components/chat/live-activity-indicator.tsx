"use client";

import { useEffect, useState } from "react";
import type { ToolActivity, ChatResponseLanguage } from "@/chat-core";
import { TypingIndicator } from "./typing-indicator";

interface LiveActivityIndicatorProps {
  /**
   * Snapshot of in-flight tool activities for the current turn.  Reads from
   * ``channelState.activeTools``.  Only entries with ``status === "running"``
   * are rendered; finished/error tools fall out so the indicator shrinks as
   * tools wrap up.
   */
  activeTools?: ToolActivity[];
  language?: ChatResponseLanguage;
}

const KOREAN: Record<string, string> = {
  Working: "작업 중",
};

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function t(language: ChatResponseLanguage | undefined, en: string): string {
  return isKorean(language) ? KOREAN[en] ?? en : en;
}

function formatElapsed(seconds: number, language?: ChatResponseLanguage): string {
  if (seconds < 60) return isKorean(language) ? `${seconds}초` : `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (isKorean(language)) {
    return rest === 0 ? `${minutes}분` : `${minutes}분 ${rest}초`;
  }
  return rest === 0 ? `${minutes}m` : `${minutes}m ${rest}s`;
}

/**
 * Inline activity indicator shown in the centre chat in place of the bare
 * "..." typing dots when the assistant turn is in a tool-loop phase with no
 * text/thinking yet.  Non-thinking models (Kimi, gpt-mini family on
 * Fireworks, etc.) emit no ``thinking_delta`` and produce text only AFTER
 * the tool loop, so the bubble was previously an empty placeholder for many
 * seconds even though the Work panel showed real activity.
 *
 * Rendering rules:
 * - No running tools  → bare "..." dots (legacy behaviour).
 * - Running tools     → label list grouped by tool name with counts +
 *                       wall-clock elapsed since the oldest still-running
 *                       tool started, plus a small animated dot.
 *
 * Data source is ``channelState.activeTools`` which the chat-core reducer
 * already populates from the ``tool_start`` / ``tool_end`` event stream —
 * the same source the Work panel consumes, so the centre bubble and the
 * right panel stay coherent without a parallel data pipe.
 */
export function LiveActivityIndicator({
  activeTools,
  language,
}: LiveActivityIndicatorProps) {
  const running = (activeTools ?? []).filter((tool) => tool.status === "running");

  // Tick once a second so the elapsed counter freshens even when no other
  // event lands (long-running single tool calls).  Cleared when no running
  // tools so the bare-dots fallback is fully idle.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (running.length === 0) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running.length]);

  if (running.length === 0) {
    return <TypingIndicator />;
  }

  const counts = new Map<string, number>();
  for (const tool of running) {
    counts.set(tool.label, (counts.get(tool.label) ?? 0) + 1);
  }
  const parts = Array.from(counts.entries()).map(([label, count]) =>
    count > 1 ? `${label} ×${count}` : label,
  );
  const oldest = Math.min(...running.map((tool) => tool.startedAt));
  const elapsed = Math.max(0, Math.floor((now - oldest) / 1000));

  return (
    <div
      className="flex justify-start mb-3"
      data-live-activity="working"
      data-live-activity-tool-count={running.length}
    >
      <div className="bg-black/[0.04] border border-black/10 rounded-2xl px-4 py-2.5 max-w-full">
        <div className="flex items-center gap-2">
          <div
            className="h-2 w-2 shrink-0 rounded-full bg-[var(--color-accent)] animate-pulse"
            aria-hidden="true"
          />
          <span className="text-[12px] font-medium text-foreground/75">
            {t(language, "Working")}
          </span>
          <span className="text-[12px] text-secondary/55 truncate" data-live-activity-tools>
            {parts.join(" · ")}
          </span>
          <span
            className="text-[11px] tabular-nums text-secondary/40 shrink-0"
            data-live-activity-elapsed
          >
            {formatElapsed(elapsed, language)}
          </span>
        </div>
      </div>
    </div>
  );
}
