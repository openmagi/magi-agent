"use client";

import { useMemo } from "react";
import dynamic from "next/dynamic";

// Dynamic import keeps echarts out of initial chat bundle. Loaded lazily
// only when a message actually contains a ```echarts code block.
const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
  loading: () => (
    <div className="my-2 rounded-xl bg-black/[0.04] animate-pulse" style={{ height: 360 }} />
  ),
});

interface EChartRendererProps {
  /** Raw JSON string from the fenced code block body. */
  source: string;
}

/**
 * Parse the fenced block body. Accepts either a bare ECharts option object
 * or `{ "option": {...} }` wrapper for forward-compat with routing metadata.
 */
function parseOption(source: string): { ok: true; option: Record<string, unknown> } | { ok: false; error: string } {
  const trimmed = source.trim();
  if (!trimmed) return { ok: false, error: "Empty chart block" };
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (!parsed || typeof parsed !== "object") {
      return { ok: false, error: "Chart JSON must be an object" };
    }
    const obj = parsed as Record<string, unknown>;
    const option = (obj.option && typeof obj.option === "object")
      ? (obj.option as Record<string, unknown>)
      : obj;
    return { ok: true, option };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Invalid JSON" };
  }
}

export function EChartRenderer({ source }: EChartRendererProps) {
  const parsed = useMemo(() => parseOption(source), [source]);

  if (!parsed.ok) {
    return (
      <details className="my-2 rounded-xl bg-black/[0.04] border border-black/[0.08] p-3 text-xs">
        <summary className="cursor-pointer text-red-600">
          Chart render failed: {parsed.error}
        </summary>
        <pre className="mt-2 whitespace-pre-wrap break-words text-secondary/80">{source}</pre>
      </details>
    );
  }

  return (
    <div className="my-2 rounded-xl bg-white border border-black/[0.06] p-2 overflow-hidden">
      <ReactECharts
        option={parsed.option}
        style={{ height: 360, width: "100%" }}
        opts={{ renderer: "canvas" }}
        notMerge
        lazyUpdate
      />
    </div>
  );
}
