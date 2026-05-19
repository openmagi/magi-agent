"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { GlassCard } from "@/components/ui/glass-card";
import { Button } from "@/components/ui/button";
import { useAgentFetch } from "@/lib/local-api";

interface RuntimePayload {
  sessions?: {
    count: number;
    items: Array<{
      sessionKey: string;
      channel?: { channelId?: string; type?: string };
      persona?: string;
      lastActivityAt?: number;
      budget?: {
        turns?: number;
        inputTokens?: number;
        outputTokens?: number;
        costUsd?: number;
      };
      maxTurns?: number;
      maxCostUsd?: number;
    }>;
  };
  tasks?: {
    count: number;
    items: Array<{
      taskId: string;
      status: string;
      persona?: string;
      promptPreview?: string;
      resultPreview?: string;
      startedAt?: number;
    }>;
  };
  crons?: {
    count: number;
    internalCount?: number;
    items: Array<{
      cronId: string;
      enabled?: boolean;
      expression?: string;
      promptPreview?: string;
      nextFireAt?: number;
    }>;
  };
  artifacts?: {
    count: number;
    items: Array<{
      artifactId: string;
      title?: string;
      kind?: string;
      updatedAt?: number;
    }>;
  };
  tools?: { count: number; skillCount?: number };
  skills?: { loadedCount: number; issueCount?: number; runtimeHookCount?: number };
}

function formatNumber(value: number | undefined): string {
  return Math.max(0, value ?? 0).toLocaleString();
}

function formatCost(value: number | undefined): string {
  return `$${(value ?? 0).toFixed(4)}`;
}

function formatTime(value: number | undefined): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString();
}

function StatCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <GlassCard>
      <p className="text-sm text-secondary mb-1">{label}</p>
      <p className="text-2xl font-bold text-foreground">{value}</p>
      {detail ? <p className="text-xs text-muted mt-1">{detail}</p> : null}
    </GlassCard>
  );
}

export default function UsagePage() {
  const agentFetch = useAgentFetch();
  const [runtime, setRuntime] = useState<RuntimePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadRuntime = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await agentFetch("/v1/app/runtime");
      const data = (await response.json().catch(() => null)) as RuntimePayload | null;
      if (!response.ok) {
        throw new Error("Failed to load local runtime usage");
      }
      setRuntime(data ?? {});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load local runtime usage");
    } finally {
      setLoading(false);
    }
  }, [agentFetch]);

  useEffect(() => {
    void loadRuntime();
  }, [loadRuntime]);

  const totals = useMemo(() => {
    const sessions = runtime?.sessions?.items ?? [];
    return sessions.reduce(
      (acc, session) => {
        acc.turns += session.budget?.turns ?? 0;
        acc.inputTokens += session.budget?.inputTokens ?? 0;
        acc.outputTokens += session.budget?.outputTokens ?? 0;
        acc.costUsd += session.budget?.costUsd ?? 0;
        return acc;
      },
      { turns: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 },
    );
  }, [runtime]);

  const activeTasks = (runtime?.tasks?.items ?? []).filter((task) =>
    ["running", "queued", "needs_input"].includes(task.status),
  );

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Runtime Usage</h1>
          <p className="text-secondary mt-1">
            Local OSS usage is read from the running agent, not from cloud billing or credits.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={loadRuntime} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </Button>
          <Link href="/dashboard/local/chat/default">
            <Button variant="primary" size="sm">Open Chat</Button>
          </Link>
        </div>
      </div>

      {error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </div>
      ) : null}

      {loading && !runtime ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="skeleton h-24" />
          <div className="skeleton h-24" />
          <div className="skeleton h-24" />
          <div className="skeleton h-24" />
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard
              label="Turns"
              value={formatNumber(totals.turns)}
              detail={`${formatNumber(totals.inputTokens)} in / ${formatNumber(totals.outputTokens)} out`}
            />
            <StatCard
              label="Runtime Cost"
              value={formatCost(totals.costUsd)}
              detail="Estimated by the local session budget tracker"
            />
            <StatCard
              label="Sessions"
              value={formatNumber(runtime?.sessions?.count)}
              detail={`${formatNumber(activeTasks.length)} active background tasks`}
            />
            <StatCard
              label="Capabilities"
              value={formatNumber(runtime?.tools?.count)}
              detail={`${formatNumber(runtime?.skills?.loadedCount)} skills, ${formatNumber(runtime?.skills?.runtimeHookCount)} hooks`}
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <GlassCard>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-semibold text-foreground">Sessions</h2>
                <span className="text-xs text-muted">{formatNumber(runtime?.sessions?.count)} total</span>
              </div>
              <div className="space-y-2">
                {(runtime?.sessions?.items ?? []).slice(0, 8).map((session) => (
                  <div key={session.sessionKey} className="rounded-lg border border-gray-200 px-3 py-2">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm font-medium text-foreground truncate">
                        {session.channel?.channelId ?? session.sessionKey}
                      </p>
                      <span className="text-xs text-muted">{formatNumber(session.budget?.turns)} turns</span>
                    </div>
                    <p className="text-xs text-secondary mt-1">
                      Last activity: {formatTime(session.lastActivityAt)}
                    </p>
                  </div>
                ))}
                {(runtime?.sessions?.items ?? []).length === 0 ? (
                  <p className="text-sm text-secondary">No local sessions yet.</p>
                ) : null}
              </div>
            </GlassCard>

            <GlassCard>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-semibold text-foreground">Background Work</h2>
                <span className="text-xs text-muted">{formatNumber(runtime?.tasks?.count)} tasks</span>
              </div>
              <div className="space-y-2">
                {(runtime?.tasks?.items ?? []).slice(0, 8).map((task) => (
                  <div key={task.taskId} className="rounded-lg border border-gray-200 px-3 py-2">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm font-medium text-foreground truncate">
                        {task.persona ?? task.taskId}
                      </p>
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-secondary">
                        {task.status}
                      </span>
                    </div>
                    {task.promptPreview ? (
                      <p className="text-xs text-secondary mt-1 line-clamp-2">{task.promptPreview}</p>
                    ) : null}
                  </div>
                ))}
                {(runtime?.tasks?.items ?? []).length === 0 ? (
                  <p className="text-sm text-secondary">No background work is running.</p>
                ) : null}
              </div>
            </GlassCard>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <GlassCard>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-semibold text-foreground">Schedules</h2>
                <span className="text-xs text-muted">
                  {formatNumber(runtime?.crons?.count)} total, {formatNumber(runtime?.crons?.internalCount)} internal
                </span>
              </div>
              <div className="space-y-2">
                {(runtime?.crons?.items ?? []).slice(0, 8).map((cron) => (
                  <div key={cron.cronId} className="rounded-lg border border-gray-200 px-3 py-2">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm font-medium text-foreground truncate">{cron.expression ?? cron.cronId}</p>
                      <span className="text-xs text-secondary">{cron.enabled === false ? "disabled" : "enabled"}</span>
                    </div>
                    <p className="text-xs text-secondary mt-1 line-clamp-2">{cron.promptPreview ?? "No prompt preview"}</p>
                  </div>
                ))}
                {(runtime?.crons?.items ?? []).length === 0 ? (
                  <p className="text-sm text-secondary">No schedules configured.</p>
                ) : null}
              </div>
            </GlassCard>

            <GlassCard>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-semibold text-foreground">Artifacts</h2>
                <span className="text-xs text-muted">{formatNumber(runtime?.artifacts?.count)} total</span>
              </div>
              <div className="space-y-2">
                {(runtime?.artifacts?.items ?? []).slice(0, 8).map((artifact) => (
                  <div key={artifact.artifactId} className="rounded-lg border border-gray-200 px-3 py-2">
                    <p className="text-sm font-medium text-foreground truncate">
                      {artifact.title ?? artifact.artifactId}
                    </p>
                    <p className="text-xs text-secondary mt-1">
                      {artifact.kind ?? "artifact"} · {formatTime(artifact.updatedAt)}
                    </p>
                  </div>
                ))}
                {(runtime?.artifacts?.items ?? []).length === 0 ? (
                  <p className="text-sm text-secondary">No artifacts created yet.</p>
                ) : null}
              </div>
            </GlassCard>
          </div>
        </>
      )}
    </div>
  );
}
