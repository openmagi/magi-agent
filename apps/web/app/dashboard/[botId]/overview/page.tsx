"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Activity, CalendarClock, FileBox, ListChecks, Puzzle } from "lucide-react";
import { GlassCard } from "@/components/ui/glass-card";
import { Button } from "@/components/ui/button";
import { useAgentFetch } from "@/lib/local-api";

type JsonRecord = Record<string, unknown>;
type RuntimeStatus = "checking" | "active" | "unavailable";

interface RuntimeSnapshot extends JsonRecord {
  sessions?: { count?: number };
  tasks?: { count?: number };
  crons?: { count?: number };
  artifacts?: { count?: number };
  skills?: { loadedCount?: number; count?: number };
}

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : {};
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function runtimeItemCount(snapshot: RuntimeSnapshot | null, key: string): number {
  const section = asRecord(snapshot?.[key]);
  const directCount = asNumber(section.count, Number.NaN);
  if (Number.isFinite(directCount)) return directCount;
  const loadedCount = asNumber(section.loadedCount, Number.NaN);
  if (Number.isFinite(loadedCount)) return loadedCount;
  const items = Array.isArray(section.items) ? section.items : [];
  return items.length;
}

function MetricTile({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: number;
  icon: React.ComponentType<{ className?: string; strokeWidth?: number }>;
}) {
  return (
    <div className="rounded-xl border border-black/[0.04] bg-black/[0.025] px-4 py-3">
      <div className="flex items-center gap-2">
        <Icon className="h-3.5 w-3.5 text-secondary/50" strokeWidth={2} />
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
          {label}
        </div>
      </div>
      <div className="mt-1 text-2xl font-semibold text-foreground">{value}</div>
    </div>
  );
}

function StatusPill({ status }: { status: RuntimeStatus }) {
  const tone =
    status === "active"
      ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-700"
      : status === "checking"
        ? "border-amber-500/20 bg-amber-500/10 text-amber-700"
        : "border-red-500/20 bg-red-500/10 text-red-600";
  const label = status === "active" ? "active" : status === "checking" ? "checking" : "offline";
  return (
    <span className={`inline-flex min-h-7 items-center rounded-full border px-2.5 text-xs font-semibold ${tone}`}>
      {label}
    </span>
  );
}

export default function OverviewPage() {
  const agentFetch = useAgentFetch();
  const [runtime, setRuntime] = useState<RuntimeSnapshot | null>(null);
  const [status, setStatus] = useState<RuntimeStatus>("checking");
  const [error, setError] = useState<string | null>(null);

  const loadRuntime = useCallback(async () => {
    setStatus("checking");
    setError(null);
    try {
      const res = await agentFetch("/v1/app/runtime");
      if (!res.ok) throw new Error("Failed to load local runtime");
      setRuntime((await res.json()) as RuntimeSnapshot);
      setStatus("active");
    } catch (err) {
      setRuntime(null);
      setStatus("unavailable");
      setError(err instanceof Error ? err.message : "Failed to load local runtime");
    }
  }, [agentFetch]);

  useEffect(() => {
    void loadRuntime();
  }, [loadRuntime]);

  return (
    <div className="max-w-5xl space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">
            Local Runtime
          </div>
          <h1 className="text-2xl font-bold leading-tight text-foreground">Dashboard</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">
            Manage your local Magi agent, runtime state, workspace knowledge, and operator files from one console.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusPill status={status} />
          <Link href="/dashboard/local/chat/default">
            <Button variant="cta" size="sm">Open Chat</Button>
          </Link>
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-500">
          {error}
        </div>
      )}

      <GlassCard>
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex items-center gap-3">
              <span className={`h-2.5 w-2.5 rounded-full ${status === "active" ? "bg-emerald-400" : "bg-gray-300"}`} />
              <h2 className="text-xl font-semibold text-foreground">Local Agent</h2>
              <StatusPill status={status} />
            </div>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">
              Self-hosted Magi runtime with local chat, workspace knowledge, runtime proof, editable operator files,
              and your configured LLM provider.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadRuntime()}
            className="inline-flex min-h-[40px] cursor-pointer items-center justify-center rounded-xl border border-black/10 bg-white px-4 py-2 text-sm font-semibold text-foreground transition-colors hover:border-primary/35 hover:bg-gray-50"
          >
            Refresh
          </button>
        </div>
      </GlassCard>

      <GlassCard>
        <h2 className="mb-4 text-sm font-semibold text-foreground">Runtime</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <MetricTile label="Sessions" value={runtimeItemCount(runtime, "sessions")} icon={Activity} />
          <MetricTile label="Tasks" value={runtimeItemCount(runtime, "tasks")} icon={ListChecks} />
          <MetricTile label="Schedules" value={runtimeItemCount(runtime, "crons")} icon={CalendarClock} />
          <MetricTile label="Artifacts" value={runtimeItemCount(runtime, "artifacts")} icon={FileBox} />
        </div>
      </GlassCard>

      <GlassCard>
        <h2 className="mb-4 text-sm font-semibold text-foreground">Local Assets</h2>
        <div className="grid gap-3 sm:grid-cols-3">
          <MetricTile label="Skills" value={runtimeItemCount(runtime, "skills")} icon={Puzzle} />
          <MetricTile label="Tasks" value={runtimeItemCount(runtime, "tasks")} icon={ListChecks} />
          <MetricTile label="Artifacts" value={runtimeItemCount(runtime, "artifacts")} icon={FileBox} />
        </div>
      </GlassCard>
    </div>
  );
}
