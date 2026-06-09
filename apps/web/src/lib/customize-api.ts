"use client";

import { useCallback, useEffect, useState } from "react";
import { useAgentFetch } from "./local-api";

/**
 * Types and data hook for the local runtime customization surface.
 *
 * Mirrors the `GET /v1/app/customize` contract served by the local Python
 * runtime. The catalog enumerates everything the runtime can do (recipes,
 * harness presets, hooks, tools); the overrides record the locally configured
 * deltas on top of the defaults. Note the asymmetry intentionally preserved
 * from the backend contract: the catalog uses camelCase `harnessPresets` while
 * the overrides file uses snake_case `harness_presets`.
 */

export interface RecipeItem {
  id: string;
  title: string;
  description: string;
  category: string;
  source: string;
  enabled: boolean;
}

export interface HarnessPresetItem {
  id: string;
  title: string;
  description: string;
  category: string;
  enabled: boolean;
}

export interface HookItem {
  name: string;
  point: string;
  title: string;
  category: string;
  alwaysOn: boolean;
  enabled: boolean;
}

export interface ToolItem {
  name: string;
  description: string;
  enabled: boolean;
  source: string;
  dangerous: boolean;
}

export interface CustomizeCatalog {
  verification: {
    recipes: RecipeItem[];
    harnessPresets: HarnessPresetItem[];
    hooks: HookItem[];
  };
  tools: ToolItem[];
}

export interface CustomizeOverrides {
  verification: {
    recipes: string[];
    harness_presets: string[];
    hooks: Record<string, boolean>;
    custom_rules: unknown[];
  };
  tools: Record<string, boolean>;
}

export interface CustomizeResponse {
  overrides: CustomizeOverrides;
  catalog: CustomizeCatalog;
}

interface UseCustomizeResult {
  data: CustomizeResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Persists a single tool enable/disable toggle via `PATCH /v1/app/customize/tools/{name}`.
 *
 * Returns the updated `CustomizeOverrides` on success so the caller can
 * reconcile local state from the backend's authoritative view.
 * Throws on non-2xx responses so the caller can surface the error and revert.
 */
export async function patchToolOverride(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  name: string,
  enabled: boolean,
): Promise<CustomizeOverrides> {
  const res = await fetch(`/v1/app/customize/tools/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error(`Failed to update tool (${res.status})`);
  const data = (await res.json()) as { overrides: CustomizeOverrides };
  return data.overrides;
}

/**
 * Loads the local runtime customization snapshot from `/v1/app/customize`.
 *
 * Handles loading/error state and exposes a `reload` callback so the UI can
 * retry after a failed fetch. This phase is read-only — override mutations are
 * held in component state by the consumer and are not persisted here.
 */
export function useCustomize(): UseCustomizeResult {
  const agentFetch = useAgentFetch();
  const [data, setData] = useState<CustomizeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(() => {
    setReloadKey((value) => value + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    agentFetch("/v1/app/customize")
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Failed to load /v1/app/customize (${response.status})`);
        }
        const payload = (await response.json()) as CustomizeResponse;
        if (!cancelled) {
          setData(payload);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load /v1/app/customize",
          );
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [agentFetch, reloadKey]);

  return { data, loading, error, reload };
}
