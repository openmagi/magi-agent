/**
 * Fetch live skills from the local agent's /v1/app/skills endpoint and expose
 * them as ChatInputCustomSkill entries suitable for the slash-autocomplete.
 *
 * Fetched once on mount (no polling). On error the list falls back to [] so
 * the composer's existing static catalog is unaffected.
 */

import { useEffect, useState } from "react";
import { agentFetch } from "@/lib/local-api";
import type { ChatInputCustomSkill } from "@/components/chat/chat-input";

interface LiveSkillItem {
  name: string;
  description: string;
  tags: string[];
  source: string;
}

function toCustomSkill(item: LiveSkillItem): ChatInputCustomSkill {
  return {
    name: item.name,
    title: item.name,
    description: item.description ?? "",
    tags: item.tags ?? [],
  };
}

export interface UseLiveSkillsResult {
  skills: ChatInputCustomSkill[];
  loading: boolean;
}

/**
 * Hook for the LOCAL dashboard only. Hits `agentFetch("/v1/app/skills")`,
 * maps every loaded skill to a `ChatInputCustomSkill`, and returns the list.
 *
 * Pass `enabled = false` (e.g. when `botId !== "local"`) to skip the fetch
 * and return an empty list immediately.
 */
export function useLiveSkills(enabled = true): UseLiveSkillsResult {
  const [skills, setSkills] = useState<ChatInputCustomSkill[]>([]);
  const [loading, setLoading] = useState(enabled);

  useEffect(() => {
    if (!enabled) {
      setSkills([]);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);

    agentFetch("/v1/app/skills")
      .then(async (res) => {
        if (!res.ok || cancelled) return;
        const json = (await res.json()) as { loaded?: unknown[] };
        if (cancelled) return;
        const items = Array.isArray(json.loaded) ? json.loaded : [];
        const mapped = items
          .filter((item): item is LiveSkillItem =>
            Boolean(item) &&
            typeof item === "object" &&
            typeof (item as Record<string, unknown>).name === "string",
          )
          .map(toCustomSkill);
        setSkills(mapped);
      })
      .catch(() => {
        // Silently fall back — autocomplete will use static catalog.
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return { skills, loading };
}
