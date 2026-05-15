"use client";

import { useState, useEffect } from "react";
import { GlassCard } from "@/components/ui/glass-card";
import { useMessages } from "@/lib/i18n";
import { agentFetch } from "@/lib/local-api";

interface AgentStatus {
  status: string;
  version?: string;
  uptime?: number;
}

export function DashboardOverview() {
  const t = useMessages();
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function fetchStatus(): Promise<void> {
      try {
        const res = await agentFetch("/v1/health");
        if (!res.ok) throw new Error("Agent offline");
        const data = await res.json();
        if (!cancelled) setAgentStatus(data);
      } catch {
        if (!cancelled) setAgentStatus({ status: "offline" });
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    fetchStatus();
    return () => { cancelled = true; };
  }, []);

  const isOnline = agentStatus?.status === "ok" || agentStatus?.status === "healthy";

  return (
    <div className="max-w-2xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-foreground">{t.dashboard.title}</h1>
        <p className="text-secondary text-sm mt-1">
          Local agent dashboard
        </p>
      </div>

      <div className="space-y-6">
        {/* Agent Status Card */}
        <GlassCard>
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-foreground mb-1">Agent Status</h3>
              <div className="flex items-center gap-2">
                <div className={`w-2 h-2 rounded-full ${loading ? "bg-gray-300 animate-pulse" : isOnline ? "bg-emerald-500" : "bg-red-400"}`} />
                <span className="text-sm text-secondary">
                  {loading ? "Checking..." : isOnline ? "Online" : "Offline"}
                </span>
              </div>
            </div>
            {agentStatus?.version && (
              <span className="text-xs text-muted bg-gray-100 px-2 py-1 rounded-lg">
                v{agentStatus.version}
              </span>
            )}
          </div>
        </GlassCard>

        {/* Quick Actions */}
        <GlassCard>
          <h3 className="text-sm font-semibold text-foreground mb-3">Quick Actions</h3>
          <div className="grid grid-cols-2 gap-3">
            <a
              href="/dashboard/local/chat"
              className="p-3 rounded-xl border border-gray-200 hover:border-primary/30 hover:bg-primary/[0.02] transition-all text-center"
            >
              <span className="text-sm font-medium text-foreground">Open Chat</span>
            </a>
            <a
              href="/dashboard/local/settings"
              className="p-3 rounded-xl border border-gray-200 hover:border-primary/30 hover:bg-primary/[0.02] transition-all text-center"
            >
              <span className="text-sm font-medium text-foreground">Settings</span>
            </a>
            <a
              href="/dashboard/local/skills"
              className="p-3 rounded-xl border border-gray-200 hover:border-primary/30 hover:bg-primary/[0.02] transition-all text-center"
            >
              <span className="text-sm font-medium text-foreground">Skills</span>
            </a>
            <a
              href="/dashboard/local/memory"
              className="p-3 rounded-xl border border-gray-200 hover:border-primary/30 hover:bg-primary/[0.02] transition-all text-center"
            >
              <span className="text-sm font-medium text-foreground">Memory</span>
            </a>
          </div>
        </GlassCard>
      </div>
    </div>
  );
}
