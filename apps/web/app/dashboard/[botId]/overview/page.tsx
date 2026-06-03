"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Bot,
  CalendarClock,
  Database,
  FileBox,
  ListChecks,
  Puzzle,
  RefreshCw,
  ShieldCheck,
  Terminal,
} from "lucide-react";
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

function ActionCard({
  href,
  title,
  description,
  icon: Icon,
}: {
  href: string;
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string; strokeWidth?: number }>;
}) {
  return (
    <Link
      href={href}
      className="group rounded-xl border border-black/[0.06] bg-white/80 p-4 shadow-sm transition-colors hover:border-primary/25 hover:bg-white"
    >
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary/10 bg-primary/10 text-primary-light">
          <Icon className="h-4 w-4" strokeWidth={2} />
        </div>
        <div className="min-w-0">
          <div className="text-sm font-semibold text-foreground group-hover:text-primary-light">{title}</div>
          <p className="mt-1 text-xs leading-5 text-secondary">{description}</p>
        </div>
      </div>
    </Link>
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
    <div className="max-w-6xl space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">
            Local Runtime Console
          </div>
          <h1 className="text-2xl font-bold leading-tight text-foreground">Open Magi Agent</h1>
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

      <GlassCard className="overflow-hidden bg-white/80 shadow-sm">
        <div className="grid gap-6 lg:grid-cols-[1.4fr_0.9fr] lg:items-stretch">
          <div className="flex min-w-0 flex-col justify-between gap-8">
            <div>
              <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-black/[0.06] bg-black/[0.025] px-3 py-1 text-xs font-semibold text-secondary">
                <Bot className="h-3.5 w-3.5 text-primary-light" strokeWidth={2} />
                Self-hosted agent workspace
              </div>
              <h2 className="text-2xl font-semibold tracking-normal text-foreground">Local Agent</h2>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-secondary">
                Run chat, inspect execution state, tune runtime rules, and work with local knowledge without leaving
                your Magi Agent server.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Link href="/dashboard/local/chat/default">
                <Button variant="cta" size="sm">Start Chat</Button>
              </Link>
              <Link
                href="/dashboard/local/settings"
                className="inline-flex min-h-[36px] items-center justify-center rounded-xl border border-black/10 bg-white px-3 text-sm font-semibold text-foreground transition-colors hover:border-primary/35 hover:bg-gray-50"
              >
                Configure
              </Link>
            </div>
          </div>
          <div className="rounded-xl border border-black/[0.06] bg-gradient-to-br from-gray-950 to-gray-800 p-4 text-white shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-white/50">Runtime status</div>
              <StatusPill status={status} />
            </div>
            <div className="mt-5 grid grid-cols-2 gap-3">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-white/45">Sessions</div>
                <div className="mt-1 text-3xl font-semibold">{runtimeItemCount(runtime, "sessions")}</div>
              </div>
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-white/45">Tasks</div>
                <div className="mt-1 text-3xl font-semibold">{runtimeItemCount(runtime, "tasks")}</div>
              </div>
            </div>
            <div className="mt-6 rounded-xl border border-white/10 bg-white/10 px-3 py-2 font-mono text-xs text-white/80">
              magi-agent serve --port 8080
            </div>
          </div>
        </div>
      </GlassCard>

      <GlassCard>
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-foreground">Runtime</h2>
          <button
            type="button"
            onClick={() => void loadRuntime()}
            className="inline-flex min-h-[36px] cursor-pointer items-center gap-2 rounded-xl border border-black/10 bg-white px-3 text-xs font-semibold text-foreground transition-colors hover:border-primary/35 hover:bg-gray-50"
          >
            <RefreshCw className="h-3.5 w-3.5" strokeWidth={2} />
            Refresh
          </button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <MetricTile label="Sessions" value={runtimeItemCount(runtime, "sessions")} icon={Activity} />
          <MetricTile label="Tasks" value={runtimeItemCount(runtime, "tasks")} icon={ListChecks} />
          <MetricTile label="Schedules" value={runtimeItemCount(runtime, "crons")} icon={CalendarClock} />
          <MetricTile label="Artifacts" value={runtimeItemCount(runtime, "artifacts")} icon={FileBox} />
        </div>
      </GlassCard>

      <GlassCard>
        <h2 className="mb-4 text-sm font-semibold text-foreground">Workspace Inventory</h2>
        <div className="grid gap-3 sm:grid-cols-3">
          <MetricTile label="Skills" value={runtimeItemCount(runtime, "skills")} icon={Puzzle} />
          <MetricTile label="Knowledge" value={runtimeItemCount(runtime, "knowledge")} icon={Database} />
          <MetricTile label="Artifacts" value={runtimeItemCount(runtime, "artifacts")} icon={FileBox} />
        </div>
      </GlassCard>

      <div className="grid gap-3 md:grid-cols-3">
        <ActionCard
          href="/dashboard/local/chat/default"
          title="Chat with the agent"
          description="Use the local web chat connected to this running server."
          icon={Terminal}
        />
        <ActionCard
          href="/dashboard/local/customize"
          title="Tune rules and tools"
          description="Review safeguards, skills, and operator-facing configuration."
          icon={ShieldCheck}
        />
        <ActionCard
          href="/dashboard/knowledge"
          title="Manage knowledge"
          description="Open the local workspace knowledge and document console."
          icon={Database}
        />
      </div>
    </div>
  );
}
