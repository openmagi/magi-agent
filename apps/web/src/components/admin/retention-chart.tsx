"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { GlassCard } from "@/components/ui/glass-card";

interface CohortRow {
  cohortLabel: string;
  userCount: number;
  retention: number[];
}

interface RetentionData {
  cohorts: CohortRow[];
  averageRetention: number[];
  cohortGranularity: string;
  periodGranularity: string;
}

type Granularity = "daily" | "weekly" | "monthly";

const COHORT_OPTIONS: { key: Granularity; label: string }[] = [
  { key: "daily", label: "Daily" },
  { key: "weekly", label: "Weekly" },
  { key: "monthly", label: "Monthly" },
];

const PERIOD_OPTIONS: { key: Granularity; label: string }[] = [
  { key: "daily", label: "D" },
  { key: "weekly", label: "W" },
  { key: "monthly", label: "M" },
];

const PERIOD_PREFIX: Record<Granularity, string> = {
  daily: "D",
  weekly: "W",
  monthly: "M",
};

function getOpacityClass(pct: number): string {
  if (pct >= 80) return "bg-emerald-500/90";
  if (pct >= 60) return "bg-emerald-500/70";
  if (pct >= 40) return "bg-emerald-500/50";
  if (pct >= 20) return "bg-emerald-500/30";
  if (pct >= 10) return "bg-emerald-500/20";
  if (pct > 0) return "bg-emerald-500/10";
  return "bg-black/[0.02]";
}

function formatCohortLabel(label: string, granularity: Granularity): string {
  switch (granularity) {
    case "daily": return label.slice(5); // "03-07"
    case "weekly": return label.replace(/^\d{4}-/, ""); // "W09"
    case "monthly": return label.slice(2); // "26-02"
    default: return label;
  }
}

export function RetentionChart() {
  const authFetch = useAuthFetch();
  const [data, setData] = useState<RetentionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [cohortGranularity, setCohortGranularity] = useState<Granularity>("daily");
  const [periodGranularity, setPeriodGranularity] = useState<Granularity>("daily");
  const [hoveredCell, setHoveredCell] = useState<{ row: number; col: number } | null>(null);

  const fetchRetention = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(
        `/api/admin/analytics/retention?cohortGranularity=${cohortGranularity}&periodGranularity=${periodGranularity}`
      );
      if (res.ok) {
        setData(await res.json());
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [authFetch, cohortGranularity, periodGranularity]);

  useEffect(() => {
    fetchRetention();
  }, [fetchRetention]);

  const prefix = PERIOD_PREFIX[periodGranularity];

  return (
    <div className="space-y-6">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-4">
        {/* Cohort granularity */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-secondary font-medium">Cohort</span>
          <div className="flex gap-1">
            {COHORT_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                onClick={() => setCohortGranularity(opt.key)}
                className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                  cohortGranularity === opt.key
                    ? "bg-primary/10 text-primary-light border border-primary/20"
                    : "bg-black/[0.04] text-secondary border border-black/10 hover:border-black/[0.12]"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Period granularity */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-secondary font-medium">Period</span>
          <div className="flex gap-1">
            {PERIOD_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                onClick={() => setPeriodGranularity(opt.key)}
                className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                  periodGranularity === opt.key
                    ? "bg-primary/10 text-primary-light border border-primary/20"
                    : "bg-black/[0.04] text-secondary border border-black/10 hover:border-black/[0.12]"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {loading ? (
        <div className="h-64 bg-black/[0.04] rounded-2xl animate-pulse" />
      ) : !data || data.cohorts.length === 0 ? (
        <GlassCard>
          <p className="text-secondary text-sm">No retention data available yet</p>
        </GlassCard>
      ) : (
        <>
          {/* Average retention line */}
          <GlassCard>
            <h3 className="text-sm font-semibold text-secondary uppercase tracking-wider mb-4">
              Average Retention ({COHORT_OPTIONS.find((o) => o.key === cohortGranularity)?.label} cohorts)
            </h3>
            <RetentionLine data={data.averageRetention} prefix={prefix} />
          </GlassCard>

          {/* Cohort heatmap */}
          <GlassCard>
            <h3 className="text-sm font-semibold text-secondary uppercase tracking-wider mb-4">Cohort Heatmap</h3>
            <div className="overflow-x-auto">
              <table className="text-xs">
                <thead>
                  <tr>
                    <th className="text-left py-1 pr-3 text-secondary font-medium min-w-[64px]">Cohort</th>
                    <th className="text-center py-1 px-1 text-secondary font-medium min-w-[28px]">N</th>
                    {Array.from({ length: data.averageRetention.length }, (_, i) => (
                      <th key={i} className="text-center py-1 px-1 text-secondary font-medium min-w-[28px]">
                        {prefix}{i}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.cohorts.map((cohort, rowIdx) => (
                    <tr key={cohort.cohortLabel}>
                      <td className="py-1 pr-3 text-secondary whitespace-nowrap">
                        {formatCohortLabel(cohort.cohortLabel, cohortGranularity)}
                      </td>
                      <td className="py-1 px-1 text-center text-foreground font-medium">{cohort.userCount}</td>
                      {cohort.retention.map((count, colIdx) => {
                        const pct = cohort.userCount > 0 ? Math.round((count / cohort.userCount) * 100) : 0;
                        const isHovered = hoveredCell?.row === rowIdx && hoveredCell?.col === colIdx;

                        return (
                          <td
                            key={colIdx}
                            className="py-1 px-1 text-center relative"
                            onMouseEnter={() => setHoveredCell({ row: rowIdx, col: colIdx })}
                            onMouseLeave={() => setHoveredCell(null)}
                          >
                            <div className={`w-7 h-7 rounded flex items-center justify-center text-[10px] font-medium ${getOpacityClass(pct)} ${pct > 0 ? "text-white" : "text-secondary/30"}`}>
                              {pct}
                            </div>
                            {isHovered && (
                              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 bg-[#1a1a2e] rounded-lg p-2 text-xs whitespace-nowrap z-10 border border-white/20 shadow-lg">
                                <div className="text-foreground">{cohort.cohortLabel} — {prefix}{colIdx}</div>
                                <div className="text-emerald-400">{count}/{cohort.userCount} users ({pct}%)</div>
                              </div>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </GlassCard>
        </>
      )}
    </div>
  );
}

function RetentionLine({ data, prefix }: { data: number[]; prefix: string }) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const maxPeriods = data.length;
  if (maxPeriods === 0) return null;

  const chartWidth = maxPeriods * 40;

  return (
    <>
      <div className="h-32 relative">
        <svg viewBox={`0 0 ${chartWidth} 100`} className="w-full h-full" preserveAspectRatio="none">
          {[25, 50, 75, 100].map((y) => (
            <line key={y} x1="0" y1={100 - y} x2={chartWidth} y2={100 - y} stroke="white" strokeOpacity="0.05" />
          ))}
          <defs>
            <linearGradient id="retention-grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgb(16,185,129)" stopOpacity="0.3" />
              <stop offset="100%" stopColor="rgb(16,185,129)" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path
            d={`M0,${100 - data[0]} ${data.map((pct, i) => `L${i * 40},${100 - pct}`).join(" ")} L${(maxPeriods - 1) * 40},100 L0,100 Z`}
            fill="url(#retention-grad)"
          />
          <path
            d={`M0,${100 - data[0]} ${data.map((pct, i) => `L${i * 40},${100 - pct}`).join(" ")}`}
            fill="none"
            stroke="rgb(16,185,129)"
            strokeWidth="2"
            strokeLinejoin="round"
          />
          {data.map((pct, i) => (
            <circle
              key={i}
              cx={i * 40}
              cy={100 - pct}
              r={hoveredIdx === i ? 5 : 3}
              fill="rgb(16,185,129)"
              className="transition-all duration-150"
            />
          ))}
          {/* Invisible hit areas for hover */}
          {data.map((_, i) => (
            <rect
              key={`hit-${i}`}
              x={i * 40 - 20}
              y={0}
              width={40}
              height={100}
              fill="transparent"
              onMouseEnter={() => setHoveredIdx(i)}
              onMouseLeave={() => setHoveredIdx(null)}
            />
          ))}
        </svg>
        {/* Hover tooltip */}
        {hoveredIdx !== null && (
          <div
            className="absolute -translate-x-1/2 pointer-events-none z-10"
            style={{
              left: `${(hoveredIdx / (maxPeriods - 1 || 1)) * 100}%`,
              bottom: `${data[hoveredIdx] + 8}%`,
            }}
          >
            <div className="bg-[#1a1a2e] border border-white/20 rounded-lg px-2 py-1 text-xs whitespace-nowrap shadow-lg">
              <span className="text-emerald-400 font-medium">{prefix}{hoveredIdx}: {data[hoveredIdx]}%</span>
            </div>
          </div>
        )}
      </div>
      <div className="flex justify-between text-[10px] text-secondary mt-1 px-1">
        {data.map((pct, i) => (
          <span key={i}>{prefix}{i}: {pct}%</span>
        ))}
      </div>
    </>
  );
}
