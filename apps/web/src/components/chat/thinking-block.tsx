"use client";

import { useState, useEffect, useRef } from "react";
import type { ToolActivity } from "@/lib/chat/types";

interface ThinkingBlockProps {
  /** Thinking text content */
  content: string;
  /** Whether thinking is still in progress */
  isLive?: boolean;
  /** Thinking start timestamp (for elapsed timer) */
  startedAt?: number | null;
  /** Completed thinking duration in seconds */
  duration?: number;
  /** Real-time tool activity from the bot */
  activities?: ToolActivity[];
}

/** Synthetic fallback steps shown only when no real tool activity has been emitted */
const FALLBACK_STEPS = [
  { after: 5, text: "Routing request..." },
  { after: 15, text: "Working..." },
  { after: 40, text: "Still working..." },
  { after: 80, text: "Almost there..." },
];

function ElapsedTimer({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval>>(undefined);

  useEffect(() => {
    setElapsed(Math.round((Date.now() - startedAt) / 1000));
    intervalRef.current = setInterval(() => {
      setElapsed(Math.round((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => clearInterval(intervalRef.current);
  }, [startedAt]);

  return <span>{elapsed}s</span>;
}

/** Returns synthetic fallback lines based on how long thinking has been active */
function useFallbackSteps(isLive: boolean | undefined, startedAt: number | null | undefined): string[] {
  const [steps, setSteps] = useState<string[]>([]);

  useEffect(() => {
    if (!isLive || !startedAt) {
      setSteps([]);
      return;
    }

    const update = () => {
      const elapsed = (Date.now() - startedAt) / 1000;
      const active = FALLBACK_STEPS.filter((s) => elapsed >= s.after).map((s) => s.text);
      setSteps(active);
    };

    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, [isLive, startedAt]);

  return steps;
}

export function ThinkingBlock({ content, isLive, startedAt, duration, activities }: ThinkingBlockProps) {
  const [expanded, setExpanded] = useState(isLive ?? false);
  const fallbackSteps = useFallbackSteps(isLive, startedAt);
  const hasActivity = !!activities && activities.length > 0;

  // Auto-expand when live, auto-collapse when done
  useEffect(() => {
    if (isLive) setExpanded(true);
  }, [isLive]);

  // Auto-collapse when response starts (isLive becomes false)
  const prevLive = useRef(isLive);
  useEffect(() => {
    if (prevLive.current && !isLive) {
      setExpanded(false);
    }
    prevLive.current = isLive;
  }, [isLive]);

  return (
    <div className="mb-3">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-[12px] text-secondary/60 hover:text-secondary/90 transition-colors duration-200 cursor-pointer group py-1"
      >
        {/* Thinking icon */}
        {isLive ? (
          <span className="relative flex h-3.5 w-3.5 items-center justify-center">
            <span className="absolute inline-flex h-full w-full rounded-full bg-primary/30 animate-ping" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-primary/60" />
          </span>
        ) : (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-secondary/40">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 0 0-2.455 2.456ZM16.894 20.567 16.5 21.75l-.394-1.183a2.25 2.25 0 0 0-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 0 0 1.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 0 0 1.423 1.423l1.183.394-1.183.394a2.25 2.25 0 0 0-1.423 1.423Z" />
          </svg>
        )}

        <span className="font-medium">
          {isLive ? (
            <>
              Thinking
              {startedAt && (
                <span className="text-secondary/40 font-normal ml-1">
                  <ElapsedTimer startedAt={startedAt} />
                </span>
              )}
            </>
          ) : (
            <>
              Thought{duration ? ` for ${duration}s` : ""}
            </>
          )}
        </span>

        {/* Chevron */}
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className={`transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      {/* Expandable content */}
      <div
        className={`overflow-hidden transition-all duration-300 ease-out ${
          expanded ? "max-h-[1200px] opacity-100" : "max-h-0 opacity-0"
        }`}
      >
        <div className="mt-1 ml-5 pl-3 border-l-2 border-black/[0.06] text-[13px] text-secondary/70 leading-relaxed max-h-[60vh] sm:max-h-[1180px] overflow-y-auto space-y-3">
          {content && (
            <div className="whitespace-pre-wrap text-secondary/50">{content}</div>
          )}
          {hasActivity && (
            <div className="space-y-1.5">
              {activities!.map((a) => (
                <ToolActivityCard key={a.id} activity={a} />
              ))}
            </div>
          )}
          {isLive && !hasActivity && fallbackSteps.length > 0 && (
            <div className="whitespace-pre-wrap text-secondary/40">
              {fallbackSteps.join("\n")}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Tool activity card ────────────────────────────────────────────────

function StatusIcon({ status }: { status: ToolActivity["status"] }) {
  if (status === "running") {
    return (
      <span className="relative inline-flex h-2.5 w-2.5 items-center justify-center mt-1">
        <span className="absolute inline-flex h-full w-full rounded-full bg-primary/30 animate-ping" />
        <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-primary/70" />
      </span>
    );
  }
  if (status === "error") {
    return (
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-red-500 mt-0.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
      </svg>
    );
  }
  if (status === "denied") {
    return (
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-secondary/40 mt-0.5">
        <circle cx="12" cy="12" r="10" />
        <line x1="4.93" y1="4.93" x2="19.07" y2="19.07" />
      </svg>
    );
  }
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-emerald-500 mt-0.5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
    </svg>
  );
}

function ToolActivityCard({ activity }: { activity: ToolActivity }) {
  const [open, setOpen] = useState(false);
  const hasDetails = !!(activity.inputPreview || activity.outputPreview);
  const duration = activity.durationMs;
  return (
    <div className="text-[12px]">
      <button
        type="button"
        onClick={() => hasDetails && setOpen((v) => !v)}
        className={`w-full flex items-start gap-2 text-left ${hasDetails ? "cursor-pointer hover:bg-black/[0.02]" : "cursor-default"} rounded px-1 py-0.5 -mx-1 transition-colors`}
      >
        <StatusIcon status={activity.status} />
        <span className="font-medium text-secondary/80 font-mono text-[11.5px]">
          {activity.label}
        </span>
        {typeof duration === "number" && (
          <span className="text-secondary/40 text-[10.5px] ml-auto tabular-nums">
            {duration < 1000 ? `${duration}ms` : `${(duration / 1000).toFixed(1)}s`}
          </span>
        )}
        {hasDetails && (
          <svg
            width="10"
            height="10"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className={`text-secondary/30 transition-transform duration-150 flex-shrink-0 mt-1 ${open ? "rotate-180" : ""}`}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
          </svg>
        )}
      </button>
      {open && hasDetails && (
        <div className="mt-1 ml-5 space-y-1.5">
          {activity.inputPreview && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-secondary/40 mb-0.5">
                Input
              </div>
              <pre className="text-[11px] bg-black/[0.04] rounded px-2 py-1.5 whitespace-pre-wrap font-mono text-secondary/80 overflow-x-auto">
                {activity.inputPreview}
              </pre>
            </div>
          )}
          {activity.outputPreview && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-secondary/40 mb-0.5">
                Output
              </div>
              <pre className="text-[11px] bg-black/[0.04] rounded px-2 py-1.5 whitespace-pre-wrap font-mono text-secondary/80 overflow-x-auto">
                {activity.outputPreview}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
