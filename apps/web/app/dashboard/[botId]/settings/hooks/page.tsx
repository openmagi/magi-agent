"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { GlassCard } from "@/components/ui/glass-card";
import { useAgentFetch } from "@/lib/local-api";

interface RuntimeHook {
  name: string;
  point?: string;
  source?: string;
  path?: string;
}

interface SkillsPayload {
  runtimeHooks?: RuntimeHook[];
  runtimeHookCount?: number;
  issueCount?: number;
}

function pointLabel(point: string | undefined): string {
  const labels: Record<string, string> = {
    beforeTurnStart: "Before turn start",
    beforeLLMCall: "Before LLM call",
    afterLLMCall: "After LLM call",
    beforeToolUse: "Before tool use",
    afterToolUse: "After tool use",
    beforeCommit: "Before commit",
    afterCommit: "After commit",
    afterTurnEnd: "After turn end",
  };
  return point ? labels[point] ?? point : "Runtime hook";
}

export default function HooksSettingsPage() {
  const agentFetch = useAgentFetch();
  const [hooks, setHooks] = useState<RuntimeHook[]>([]);
  const [issueCount, setIssueCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadHooks = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await agentFetch("/v1/app/skills");
      const data = (await response.json().catch(() => null)) as SkillsPayload | null;
      if (!response.ok) throw new Error("Failed to load runtime hooks");
      setHooks(data?.runtimeHooks ?? []);
      setIssueCount(data?.issueCount ?? 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load runtime hooks");
    } finally {
      setLoading(false);
    }
  }, [agentFetch]);

  useEffect(() => {
    void loadHooks();
  }, [loadHooks]);

  return (
    <div className="max-w-4xl space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold mb-1">Runtime Hooks</h2>
          <p className="text-sm text-secondary">
            Hooks are loaded from local runtime skills. Hosted bot hook management is not used in OSS mode.
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={loadHooks} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh"}
        </Button>
      </div>

      {error ? (
        <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
      ) : null}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <GlassCard>
          <p className="text-sm text-secondary">Runtime Hooks</p>
          <p className="text-2xl font-bold text-foreground mt-1">{hooks.length}</p>
        </GlassCard>
        <GlassCard>
          <p className="text-sm text-secondary">Skill Issues</p>
          <p className="text-2xl font-bold text-foreground mt-1">{issueCount}</p>
        </GlassCard>
        <GlassCard>
          <p className="text-sm text-secondary">Source</p>
          <p className="text-base font-semibold text-foreground mt-2">/v1/app/skills</p>
        </GlassCard>
      </div>

      <GlassCard>
        {loading ? (
          <p className="text-sm text-secondary">Loading hooks...</p>
        ) : hooks.length === 0 ? (
          <p className="text-sm text-secondary">No runtime hooks are loaded.</p>
        ) : (
          <div className="space-y-2">
            {hooks.map((hook) => (
              <div key={`${hook.name}:${hook.point ?? ""}`} className="rounded-lg border border-gray-200 px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-medium text-foreground truncate">{hook.name}</p>
                  <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-secondary">
                    {pointLabel(hook.point)}
                  </span>
                </div>
                {hook.path ? (
                  <p className="text-xs text-secondary mt-1 truncate">{hook.path}</p>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </GlassCard>
    </div>
  );
}
