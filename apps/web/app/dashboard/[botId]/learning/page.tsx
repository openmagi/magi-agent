"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, RefreshCw, RotateCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { GlassCard } from "@/components/ui/glass-card";
import { useAgentFetch } from "@/lib/local-api";

type JsonRecord = Record<string, unknown>;

interface LearningItemSummary {
  id: string;
  kind: string;
  status: string;
  scope?: JsonRecord;
  rationale: string;
  version: number;
  supersedes?: string | null;
}

interface LearningConflict {
  hasConflict?: boolean;
  conflictingIds?: string[];
  reason?: string | null;
}

interface LearningItemDetail extends LearningItemSummary {
  content?: JsonRecord;
  provenance?: JsonRecord;
  evalObservationRef?: string | null;
  approvalRef?: string | null;
  conflict?: LearningConflict;
}

interface ListLearningsResponse {
  items?: LearningItemSummary[];
  nextCursor?: string | null;
}

interface ReflectionRunResponse {
  status?: string;
  candidatesProduced?: number;
  itemsProposed?: number;
  itemsActivated?: number;
  watermark?: string | null;
}

const STATUS_FILTERS = ["all", "proposed", "active", "rejected", "deleted"] as const;

function asCount(value: number | undefined): string {
  return Math.max(0, value ?? 0).toLocaleString();
}

function compactJson(value: unknown): string {
  if (!value || typeof value !== "object") return "{}";
  return JSON.stringify(value, null, 2);
}

function scopeLabel(scope: JsonRecord | undefined): string {
  if (!scope) return "global";
  const taskKind = scope.taskKind;
  const channel = scope.channel;
  const labels = [
    typeof taskKind === "string" && taskKind ? taskKind : null,
    typeof channel === "string" && channel ? channel : null,
  ].filter(Boolean);
  return labels.length ? labels.join(" / ") : "global";
}

function statusTone(status: string): string {
  if (status === "active") return "border-emerald-500/20 bg-emerald-500/10 text-emerald-700";
  if (status === "proposed") return "border-amber-500/20 bg-amber-500/10 text-amber-700";
  if (status === "deleted" || status === "rejected") return "border-red-500/20 bg-red-500/10 text-red-600";
  return "border-black/10 bg-black/[0.035] text-secondary";
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <GlassCard>
      <p className="text-sm text-secondary">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-foreground">{value}</p>
    </GlassCard>
  );
}

export default function LearningPage() {
  const agentFetch = useAgentFetch();
  const [items, setItems] = useState<LearningItemSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<LearningItemDetail | null>(null);
  const [statusFilter, setStatusFilter] = useState<(typeof STATUS_FILTERS)[number]>("all");
  const [kindFilter, setKindFilter] = useState("");
  const [approver, setApprover] = useState("local-operator");
  const [reflection, setReflection] = useState<ReflectionRunResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (statusFilter !== "all") params.set("status", statusFilter);
      if (kindFilter.trim()) params.set("kind", kindFilter.trim());
      const response = await agentFetch(`/v1/learning/learnings?${params.toString()}`);
      const data = (await response.json().catch(() => null)) as ListLearningsResponse | null;
      if (!response.ok) {
        throw new Error("Learning dashboard API is unavailable for this local runtime.");
      }
      const nextItems = Array.isArray(data?.items) ? data.items : [];
      setItems(nextItems);
      setSelectedId((current) => current ?? nextItems[0]?.id ?? null);
    } catch (err) {
      setItems([]);
      setDetail(null);
      setError(err instanceof Error ? err.message : "Failed to load learning governance data");
    } finally {
      setLoading(false);
    }
  }, [agentFetch, kindFilter, statusFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setDetailLoading(true);
    setError(null);
    try {
      const response = await agentFetch(`/v1/learning/learnings/${encodeURIComponent(id)}`);
      const data = (await response.json().catch(() => null)) as LearningItemDetail | null;
      if (!response.ok) throw new Error("Failed to load learning detail");
      setDetail(data);
    } catch (err) {
      setDetail(null);
      setError(err instanceof Error ? err.message : "Failed to load learning detail");
    } finally {
      setDetailLoading(false);
    }
  }, [agentFetch]);

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    void loadDetail(selectedId);
  }, [loadDetail, selectedId]);

  const counts = useMemo(() => {
    return items.reduce(
      (acc, item) => {
        acc.total += 1;
        if (item.status === "proposed") acc.proposed += 1;
        if (item.status === "active") acc.active += 1;
        if (item.status === "deleted" || item.status === "rejected") acc.closed += 1;
        return acc;
      },
      { total: 0, proposed: 0, active: 0, closed: 0 },
    );
  }, [items]);

  const runReflection = useCallback(async () => {
    if (!approver.trim()) {
      setError("Approver is required for reflection writes.");
      return;
    }
    setMutating(true);
    setError(null);
    try {
      const response = await agentFetch("/v1/learning/reflection/run", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-approver": approver.trim(),
        },
      });
      const data = (await response.json().catch(() => null)) as ReflectionRunResponse | null;
      if (!response.ok) throw new Error("Reflection run was rejected by the local runtime.");
      setReflection(data ?? {});
      await loadItems();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run reflection");
    } finally {
      setMutating(false);
    }
  }, [agentFetch, approver, loadItems]);

  const approveSelected = useCallback(async () => {
    if (!selectedId) return;
    if (!approver.trim()) {
      setError("Approver is required to approve learnings.");
      return;
    }
    setMutating(true);
    setError(null);
    try {
      const response = await agentFetch(`/v1/learning/learnings/${encodeURIComponent(selectedId)}/approve`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-approver": approver.trim(),
        },
        body: JSON.stringify({ force: false }),
      });
      if (!response.ok) throw new Error("Learning approval was rejected by the local runtime.");
      await loadItems();
      await loadDetail(selectedId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to approve learning");
    } finally {
      setMutating(false);
    }
  }, [agentFetch, approver, loadDetail, loadItems, selectedId]);

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">
            Local runtime
          </div>
          <h1 className="text-2xl font-bold text-foreground">Learning Governance</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-secondary">
            Review proposed runtime learnings, inspect provenance, and approve local policy-safe updates.
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <label className="flex min-h-[44px] items-center gap-2 rounded-xl border border-black/10 bg-white px-3 text-xs font-semibold text-secondary">
            Approver
            <input
              value={approver}
              onChange={(event) => setApprover(event.target.value)}
              className="h-8 min-w-0 rounded-lg border border-black/10 bg-white px-2 text-sm font-medium text-foreground outline-none focus:border-primary/40"
            />
          </label>
          <Button variant="secondary" size="sm" onClick={loadItems} disabled={loading || mutating}>
            <RefreshCw className="mr-2 h-4 w-4" strokeWidth={2} />
            Refresh
          </Button>
          <Button variant="primary" size="sm" onClick={runReflection} disabled={loading || mutating}>
            <RotateCw className="mr-2 h-4 w-4" strokeWidth={2} />
            Reflect
          </Button>
        </div>
      </div>

      {error ? (
        <div className="rounded-xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-600">
          {error}
        </div>
      ) : null}

      {reflection ? (
        <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-700">
          Reflection {reflection.status ?? "finished"}: {asCount(reflection.itemsProposed)} proposed,{" "}
          {asCount(reflection.itemsActivated)} activated.
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Items" value={asCount(counts.total)} />
        <StatCard label="Proposed" value={asCount(counts.proposed)} />
        <StatCard label="Active" value={asCount(counts.active)} />
        <StatCard label="Closed" value={asCount(counts.closed)} />
      </div>

      <GlassCard>
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-foreground">Learning Queue</h2>
            <p className="mt-1 text-xs text-secondary">{asCount(items.length)} records from the local governance store</p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as (typeof STATUS_FILTERS)[number])}
              className="min-h-[44px] rounded-xl border border-black/10 bg-white px-3 text-sm font-medium text-foreground"
            >
              {STATUS_FILTERS.map((status) => (
                <option key={status} value={status}>{status}</option>
              ))}
            </select>
            <label className="flex min-h-[44px] items-center gap-2 rounded-xl border border-black/10 bg-white px-3 text-sm text-secondary">
              <Search className="h-4 w-4" strokeWidth={2} />
              <input
                value={kindFilter}
                onChange={(event) => setKindFilter(event.target.value)}
                placeholder="kind"
                className="w-28 bg-transparent font-medium text-foreground outline-none"
              />
            </label>
          </div>
        </div>
      </GlassCard>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <GlassCard className="min-h-[420px]">
          <h2 className="mb-3 text-sm font-semibold text-foreground">Learnings</h2>
          {loading ? (
            <div className="space-y-3">
              <div className="skeleton h-20" />
              <div className="skeleton h-20" />
              <div className="skeleton h-20" />
            </div>
          ) : items.length === 0 ? (
            <p className="text-sm text-secondary">No learning records match this view.</p>
          ) : (
            <div className="space-y-2">
              {items.map((item) => {
                const active = item.id === selectedId;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setSelectedId(item.id)}
                    className={`w-full cursor-pointer rounded-xl border p-3 text-left transition-colors ${
                      active
                        ? "border-primary/25 bg-primary/10"
                        : "border-black/[0.06] bg-white/70 hover:border-primary/20 hover:bg-white"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-foreground">{item.kind}</p>
                        <p className="mt-1 line-clamp-2 text-xs leading-5 text-secondary">{item.rationale}</p>
                      </div>
                      <span className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-semibold ${statusTone(item.status)}`}>
                        {item.status}
                      </span>
                    </div>
                    <div className="mt-2 flex items-center justify-between gap-3 text-xs text-muted">
                      <span className="truncate">{scopeLabel(item.scope)}</span>
                      <span>v{item.version}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </GlassCard>

        <GlassCard className="min-h-[420px]">
          <div className="mb-4 flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-foreground">Detail</h2>
              <p className="mt-1 text-xs text-secondary">{detail?.id ?? "Select a learning record"}</p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={approveSelected}
              disabled={!detail || detail.status !== "proposed" || detailLoading || mutating}
            >
              <CheckCircle2 className="mr-2 h-4 w-4" strokeWidth={2} />
              Approve
            </Button>
          </div>
          {detailLoading ? (
            <div className="space-y-3">
              <div className="skeleton h-24" />
              <div className="skeleton h-40" />
            </div>
          ) : detail ? (
            <div className="space-y-4">
              <div className="rounded-xl border border-black/[0.06] bg-white/70 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${statusTone(detail.status)}`}>
                    {detail.status}
                  </span>
                  <span className="rounded-full border border-black/10 bg-black/[0.035] px-2 py-0.5 text-xs text-secondary">
                    {detail.kind}
                  </span>
                  {detail.conflict?.hasConflict ? (
                    <span className="rounded-full border border-red-500/20 bg-red-500/10 px-2 py-0.5 text-xs font-semibold text-red-600">
                      conflict
                    </span>
                  ) : null}
                </div>
                <p className="mt-3 text-sm leading-6 text-secondary">{detail.rationale}</p>
              </div>
              <div className="grid gap-4 xl:grid-cols-2">
                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-secondary/70">Content</h3>
                  <pre className="max-h-80 overflow-auto rounded-xl border border-black/[0.06] bg-black/[0.025] p-3 text-xs leading-5 text-secondary">
                    {compactJson(detail.content)}
                  </pre>
                </div>
                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-secondary/70">Provenance</h3>
                  <pre className="max-h-80 overflow-auto rounded-xl border border-black/[0.06] bg-black/[0.025] p-3 text-xs leading-5 text-secondary">
                    {compactJson({
                      provenance: detail.provenance,
                      evalObservationRef: detail.evalObservationRef,
                      approvalRef: detail.approvalRef,
                      conflict: detail.conflict,
                    })}
                  </pre>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-sm text-secondary">No learning detail loaded.</p>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
