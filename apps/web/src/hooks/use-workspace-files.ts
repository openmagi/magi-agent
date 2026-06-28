"use client";

import { useCallback, useEffect, useState } from "react";
import { agentFetch } from "@/lib/local-api";
import {
  normalizeWorkspaceFileList,
  type WorkspaceFileApiRow,
  type WorkspaceFileEntry,
} from "@/lib/workspace/workspace-files";

const AUTO_REFRESH_MS = 300_000;

export function useWorkspaceFiles(botId: string): {
  files: WorkspaceFileEntry[];
  loading: boolean;
  refreshing: boolean;
  refresh: () => void;
} {
  const [files, setFiles] = useState<WorkspaceFileEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(() => setRefreshKey((key) => key + 1), []);

  useEffect(() => {
    let cancelled = false;
    const isInitial = files.length === 0;

    async function load(): Promise<void> {
      if (botId === "local") {
        // Local self-host: list the on-disk workspace files (root level + nested,
        // minus noise + the memory/knowledge subtrees) from the Python runtime.
        if (isInitial) setLoading(true);
        else setRefreshing(true);
        try {
          const res = await agentFetch("/v1/app/workspace");
          if (!res.ok) return;
          const data = await res.json() as { files?: WorkspaceFileApiRow[] };
          if (!cancelled) setFiles(normalizeWorkspaceFileList(data.files ?? []));
        } catch (err) {
          console.error("[use-workspace-files] Failed to load local workspace:", err);
        } finally {
          if (!cancelled) {
            setLoading(false);
            setRefreshing(false);
          }
        }
        return;
      }
      if (isInitial) setLoading(true);
      else setRefreshing(true);
      try {
        const res = await fetch(`/api/bots/${encodeURIComponent(botId)}/workspace-files`);
        if (!res.ok) return;
        const data = await res.json() as { files?: WorkspaceFileApiRow[] };
        if (!cancelled) setFiles(normalizeWorkspaceFileList(data.files ?? []));
      } catch (err) {
        console.error("[use-workspace-files] Failed to load:", err);
      } finally {
        if (!cancelled) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    }

    load();
    const interval = setInterval(load, AUTO_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId, refreshKey]);

  return { files, loading, refreshing, refresh };
}
