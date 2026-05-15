"use client";

import { useState } from "react";

const MODEL_KEYS = ["haiku", "sonnet", "opus", "kimi", "minimax", "gpt_5_nano", "gpt_5_mini", "gpt_5_1", "gpt_5_5", "gpt_5_5_pro", "gemini_3_1_flash", "gemini_3_1_pro", "gemini_2_5_flash", "gemini_2_5_pro", "codex"] as const;
type ModelKey = (typeof MODEL_KEYS)[number];

const MODEL_BAR_COLORS: Record<ModelKey, string> = {
  haiku: "bg-emerald-500/70",
  sonnet: "bg-blue-500/70",
  opus: "bg-violet-500/70",
  kimi: "bg-amber-500/70",
  minimax: "bg-rose-500/70",
  gpt_5_nano: "bg-cyan-500/70",
  gpt_5_mini: "bg-teal-500/70",
  gpt_5_1: "bg-sky-500/70",
  gpt_5_5: "bg-indigo-500/70",
  gpt_5_5_pro: "bg-fuchsia-500/70",
  gemini_3_1_flash: "bg-lime-500/70",
  gemini_3_1_pro: "bg-orange-500/70",
  gemini_2_5_flash: "bg-lime-400/70",
  gemini_2_5_pro: "bg-orange-400/70",
  codex: "bg-yellow-500/70",
};

const MODEL_LABEL_COLORS: Record<ModelKey, string> = {
  haiku: "text-emerald-600",
  sonnet: "text-blue-600",
  opus: "text-violet-600",
  kimi: "text-amber-600",
  minimax: "text-rose-600",
  gpt_5_nano: "text-cyan-600",
  gpt_5_mini: "text-teal-600",
  gpt_5_1: "text-sky-600",
  gpt_5_5: "text-indigo-600",
  gpt_5_5_pro: "text-fuchsia-600",
  gemini_3_1_flash: "text-lime-700",
  gemini_3_1_pro: "text-orange-600",
  gemini_2_5_flash: "text-lime-600",
  gemini_2_5_pro: "text-orange-600",
  codex: "text-yellow-700",
};

const MODEL_DISPLAY_NAMES: Record<ModelKey, string> = {
  haiku: "Haiku",
  sonnet: "Sonnet",
  opus: "Opus",
  kimi: "Kimi K2.6",
  minimax: "MiniMax M2.5",
  gpt_5_nano: "GPT-5.4 Nano",
  gpt_5_mini: "GPT-5.4 Mini",
  gpt_5_1: "GPT-5.4 Mini",
  gpt_5_5: "GPT-5.5",
  gpt_5_5_pro: "GPT-5.5 Pro",
  gemini_3_1_flash: "Gemini 3.1 Flash",
  gemini_3_1_pro: "Gemini 3.1 Pro",
  gemini_2_5_flash: "Gemini 2.5 Flash",
  gemini_2_5_pro: "Gemini 2.5 Pro",
  codex: "Codex",
};

interface ModelUsage {
  inputTokens: number;
  outputTokens: number;
  cacheCreationTokens?: number;
  cacheReadTokens?: number;
  costCents: number;
}

interface DailyUsage {
  date: string;
  inputTokens: number;
  outputTokens: number;
  cacheCreationTokens?: number;
  cacheReadTokens?: number;
  costCents: number;
  models: Partial<Record<ModelKey, ModelUsage>>;
}

interface UsageChartProps {
  daily: DailyUsage[];
  formatTokens: (n: number) => string;
  detailedView?: boolean;
}

export function UsageChart({ daily, formatTokens, detailedView = false }: UsageChartProps) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  // Sort oldest-first (left=oldest, right=newest)
  const sorted = [...daily].sort((a, b) => a.date.localeCompare(b.date));

  // Find which models actually have data
  const activeModels = MODEL_KEYS.filter((key) =>
    sorted.some((d) => d.models[key]),
  );

  const maxDayTokens = Math.max(
    ...sorted.map((d) => d.inputTokens + d.outputTokens),
    1,
  );

  return (
    <div>
      {/* Legend */}
      <div className="flex flex-wrap gap-3 mb-3">
        {activeModels.map((key) => (
          <div key={key} className="flex items-center gap-1.5 text-xs">
            <div className={`w-2.5 h-2.5 rounded-sm ${MODEL_BAR_COLORS[key]}`} />
            <span className="text-secondary">{MODEL_DISPLAY_NAMES[key]}</span>
          </div>
        ))}
      </div>

      {/* Vertical bar chart */}
      <div className="relative">
        <div className="flex items-end gap-1.5 h-48" role="img" aria-label="Usage chart">
          {sorted.map((day, i) => {
            const dayTotal = day.inputTokens + day.outputTokens;
            const heightPct = (dayTotal / maxDayTokens) * 100;

            return (
              <div
                key={day.date}
                className="flex-1 flex flex-col items-stretch justify-end h-full relative max-w-12"
                onMouseEnter={() => setHoveredIndex(i)}
                onMouseLeave={() => setHoveredIndex(null)}
              >
                {/* Tooltip */}
                {hoveredIndex === i && (
                  <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 z-10 bg-white/95 border border-black/10 rounded-lg px-3 py-2 text-xs whitespace-nowrap pointer-events-none shadow-lg shadow-black/10 backdrop-blur-sm">
                    <div className="text-foreground font-medium mb-1">{day.date}</div>
                    {activeModels.map((key) => {
                      const m = day.models[key];
                      if (!m) return null;
                      const tokens = m.inputTokens + m.outputTokens;
                      if (detailedView) {
                        const cached = (m.cacheCreationTokens || 0) + (m.cacheReadTokens || 0);
                        const baseInput = m.inputTokens;
                        return (
                          <div key={key} className="mb-1">
                            <div className={`${MODEL_LABEL_COLORS[key]} font-medium`}>{MODEL_DISPLAY_NAMES[key]}</div>
                            <div className="flex justify-between gap-3 pl-2">
                              <span className="text-secondary">Base</span>
                              <span className="text-foreground">{formatTokens(baseInput)}</span>
                            </div>
                            {cached > 0 && (
                              <div className="flex justify-between gap-3 pl-2">
                                <span className="text-secondary">Cached</span>
                                <span className="text-foreground">{formatTokens(cached)}</span>
                              </div>
                            )}
                            <div className="flex justify-between gap-3 pl-2">
                              <span className="text-secondary">Output</span>
                              <span className="text-foreground">{formatTokens(m.outputTokens)}</span>
                            </div>
                          </div>
                        );
                      }
                      return (
                        <div key={key} className="flex items-center justify-between gap-3">
                          <span className={`${MODEL_LABEL_COLORS[key]}`}>{MODEL_DISPLAY_NAMES[key]}</span>
                          <span className="text-foreground">{formatTokens(tokens)}</span>
                        </div>
                      );
                    })}
                    <div className="border-t border-black/10 mt-1 pt-1 flex justify-between gap-3">
                      <span className="text-secondary">Total</span>
                      <span className="text-foreground font-medium">{formatTokens(dayTotal)}</span>
                    </div>
                  </div>
                )}

                {/* Bar (stacked vertically) */}
                <div
                  className="flex flex-col justify-end rounded-t-sm overflow-hidden bg-black/[0.03] transition-all duration-300"
                  style={{ height: `${Math.max(heightPct, dayTotal > 0 ? 2 : 0)}%` }}
                >
                  {activeModels.map((key) => {
                    const m = day.models[key];
                    if (!m) return null;
                    const tokens = m.inputTokens + m.outputTokens;
                    const segmentPct = dayTotal > 0 ? (tokens / dayTotal) * 100 : 0;
                    return (
                      <div
                        key={key}
                        className={`${MODEL_BAR_COLORS[key]} ${hoveredIndex === i ? "opacity-100" : "opacity-80"} transition-opacity duration-150`}
                        style={{ height: `${segmentPct}%` }}
                      />
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>

        {/* X-axis date labels */}
        <div className="flex gap-1.5 mt-1.5">
          {sorted.map((day, i) => {
            // Show labels for first, last, and roughly every Nth bar to avoid overcrowding
            const showLabel = sorted.length <= 10 || i === 0 || i === sorted.length - 1 || i % Math.ceil(sorted.length / 7) === 0;
            return (
              <div key={day.date} className="flex-1 text-center max-w-12">
                {showLabel && (
                  <span className="text-[10px] text-muted">{day.date.slice(5)}</span>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
