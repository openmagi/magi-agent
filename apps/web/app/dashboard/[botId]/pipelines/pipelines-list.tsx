"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { GlassCard } from "@/components/ui/glass-card";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import type { PipelineListItem, PipelineStatus } from "@/lib/bots/pipeline-types";
import { pipelineDisplayName } from "@/lib/bots/pipeline-types";

const POLL_MS = 5000;

function relativeTime(ts: number): string {
  const diff = Math.max(0, Date.now() - ts);
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}

function StatusBadge({ status, severeCount }: { status: PipelineStatus; severeCount: number }): React.JSX.Element {
  const tone =
    status === "completed" ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20" :
    status === "paused" ? "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20" :
    severeCount > 0 ? "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20" :
    "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20";
  const label =
    status === "completed" ? "Completed" :
    status === "paused" ? "Paused" :
    severeCount > 0 ? `Running (${severeCount} issue${severeCount === 1 ? "" : "s"})` :
    "Running";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${tone}`}>
      {label}
    </span>
  );
}

interface Props {
  botId: string;
  botName: string;
}

export default function PipelinesList({ botId, botName }: Props): React.JSX.Element {
  const authFetch = useAuthFetch();
  const [items, setItems] = useState<PipelineListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await authFetch(`/api/bots/${botId}/pipelines?limit=50`);
      if (!res.ok) {
        setError(`Failed to load pipelines (${res.status})`);
        return;
      }
      const body = await res.json();
      setItems((body.pipelines ?? []) as PipelineListItem[]);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [authFetch, botId]);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => { void refresh(); }, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">Pipelines</h1>
        <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
          Multi-step workflows running on <span className="font-medium">{botName}</span>. Updates every {POLL_MS / 1000}s.
        </p>
      </div>

      {loading && items.length === 0 ? (
        <GlassCard className="p-6 text-center text-sm text-gray-500">Loading pipelines…</GlassCard>
      ) : error ? (
        <GlassCard className="p-6 text-sm text-rose-600">{error}</GlassCard>
      ) : items.length === 0 ? (
        <GlassCard className="p-6 text-center text-sm text-gray-500">
          No active pipelines. Ask your bot to run a multi-step workflow to get started.
        </GlassCard>
      ) : (
        <div className="space-y-3">
          {items.map((p) => (
            <Link
              key={p.pipeline_id}
              href={`/dashboard/${botId}/pipelines/${p.pipeline_id}`}
              className="block"
            >
              <GlassCard className="p-4 hover:shadow-md transition-shadow">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-mono text-xs text-gray-500 truncate">{p.pipeline_id}</span>
                      <StatusBadge status={p.status} severeCount={p.severe_count ?? 0} />
                    </div>
                    <div className="text-sm text-gray-700 dark:text-gray-300">
                      Started {pipelineDisplayName(p.pipeline_id)}
                    </div>
                    <div className="text-xs text-gray-500 mt-1">
                      Last event: <span className="font-mono">{p.last_event}</span>
                      {p.last_step_id ? <> on <span className="font-mono">{p.last_step_id}</span></> : null}
                      {" · "}
                      updated {relativeTime(p.updated_at)} ago
                    </div>
                  </div>
                  <div className="shrink-0 text-gray-400">→</div>
                </div>
              </GlassCard>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
