"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  CalendarClock,
  Database,
  FileBox,
  ListChecks,
  Puzzle,
  RefreshCw,
  ShieldCheck,
  Terminal,
} from "lucide-react";
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
    <div className="rounded-lg border border-black/[0.05] bg-[var(--glass-regular-bg)] backdrop-blur-xl px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-secondary/70">
        <Icon className="h-3 w-3" strokeWidth={2} />
        <div className="text-[10.5px] font-semibold uppercase tracking-[0.12em]">{label}</div>
      </div>
      <div className="mt-0.5 text-lg font-semibold leading-snug text-foreground">{value}</div>
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
      className="group flex items-center gap-3 rounded-lg border border-black/[0.06] bg-[var(--glass-regular-bg)] backdrop-blur-xl px-3 py-2.5 transition-colors hover:border-primary/30 hover:bg-gray-50"
    >
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-primary/10 bg-primary/10 text-primary-light">
        <Icon className="h-3.5 w-3.5" strokeWidth={2} />
      </div>
      <div className="min-w-0">
        <div className="text-sm font-semibold text-foreground group-hover:text-primary-light">{title}</div>
        <p className="truncate text-xs text-secondary">{description}</p>
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
  const dotTone =
    status === "active" ? "bg-emerald-500" : status === "checking" ? "bg-amber-500" : "bg-red-500";
  const label = status === "active" ? "active" : status === "checking" ? "checking" : "offline";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${tone}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${dotTone}`} />
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
    <div className="max-w-5xl space-y-5">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-gray-400">
            Local runtime
          </div>
          <h1 className="mt-0.5 text-lg font-semibold leading-tight text-foreground">Open Magi Agent</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <code className="hidden rounded-md border border-black/[0.06] bg-black/[0.025] px-2 py-1 font-mono text-[11px] text-secondary sm:inline">
            magi-agent serve --port 8080
          </code>
          <StatusPill status={status} />
          <Link href="/dashboard/local/chat/default">
            <Button variant="cta" size="sm">Open chat</Button>
          </Link>
          <Link
            href="/dashboard/local/settings"
            className="inline-flex h-8 items-center rounded-md border border-black/10 bg-white px-2.5 text-xs font-semibold text-foreground transition-colors hover:border-primary/35 hover:bg-gray-50"
          >
            Configure
          </Link>
        </div>
      </header>

      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-500">
          {error}
        </div>
      )}

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-secondary">Workspace</h2>
          <button
            type="button"
            onClick={() => void loadRuntime()}
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-black/10 bg-white px-2 text-[11px] font-semibold text-secondary transition-colors hover:border-primary/35 hover:text-foreground"
          >
            <RefreshCw className="h-3 w-3" strokeWidth={2} />
            Refresh
          </button>
        </div>
        <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <MetricTile label="Sessions" value={runtimeItemCount(runtime, "sessions")} icon={Activity} />
          <MetricTile label="Tasks" value={runtimeItemCount(runtime, "tasks")} icon={ListChecks} />
          <MetricTile label="Schedules" value={runtimeItemCount(runtime, "crons")} icon={CalendarClock} />
          <MetricTile label="Skills" value={runtimeItemCount(runtime, "skills")} icon={Puzzle} />
          <MetricTile label="Knowledge" value={runtimeItemCount(runtime, "knowledge")} icon={Database} />
          <MetricTile label="Artifacts" value={runtimeItemCount(runtime, "artifacts")} icon={FileBox} />
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-secondary">Quick actions</h2>
        <div className="grid gap-2 md:grid-cols-3">
          <ActionCard
            href="/dashboard/local/chat/default"
            title="Chat with the agent"
            description="Local web chat over the running server."
            icon={Terminal}
          />
          <ActionCard
            href="/dashboard/local/customize"
            title="Tune rules and tools"
            description="Safeguards, skills, operator config."
            icon={ShieldCheck}
          />
          <ActionCard
            href="/dashboard/knowledge"
            title="Manage knowledge"
            description="Workspace knowledge and documents."
            icon={Database}
          />
        </div>
      </section>
    </div>
  );
}
