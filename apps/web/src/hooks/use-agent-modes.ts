"use client";

import { useCallback, useEffect, useState } from "react";

import { agentFetch } from "@/lib/local-api";
import { getModes } from "@/lib/agent-modes-api";
import { toAgentModeSummary, type AgentModeSummary } from "@/chat-core";

/**
 * Fetches the user-authored agent modes (postures) for the composer selector.
 *
 * Returns the id+displayName summaries plus the server's sticky `activeMode`
 * (so the composer can seed its initial selection to the stored default). The
 * heavy fields (system prompt, tool delta) are dropped — the composer never
 * renders them; the Customize Modes panel owns full CRUD.
 *
 * Silent-empty on failure: a runtime without the modes endpoint (or a transient
 * error) yields an empty list, which hides the selector entirely — a bot with
 * no modes behaves exactly as before this feature.
 */
export function useAgentModes(botId: string): {
  modes: AgentModeSummary[];
  activeMode: string | null;
  loading: boolean;
  refresh: () => void;
} {
  const [modes, setModes] = useState<AgentModeSummary[]>([]);
  const [activeMode, setActiveMode] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  useEffect(() => {
    let cancelled = false;
    async function load(): Promise<void> {
      setLoading(true);
      try {
        const data = await getModes(agentFetch);
        if (cancelled) return;
        setModes(data.modes.map(toAgentModeSummary));
        setActiveMode(data.activeMode ?? null);
      } catch {
        if (cancelled) return;
        setModes([]);
        setActiveMode(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
    // botId is included so switching bots re-fetches; refreshKey forces reload
    // after a Customize CRUD change.
  }, [botId, refreshKey]);

  return { modes, activeMode, loading, refresh };
}
