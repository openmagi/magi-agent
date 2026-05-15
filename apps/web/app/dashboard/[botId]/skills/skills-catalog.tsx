"use client";

import { useState, useEffect } from "react";
import { GlassCard } from "@/components/ui/glass-card";
import { useMessages } from "@/lib/i18n";
import { agentFetch } from "@/lib/local-api";

interface SkillItem {
  id: string;
  name: string;
  description?: string;
  enabled: boolean;
}

interface SkillsCatalogProps {
  botId: string | null;
  initialDisabledSkills: string[];
  initialCustomSkills: unknown[];
}

export default function SkillsCatalog({ botId }: SkillsCatalogProps) {
  const t = useMessages();
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function fetchSkills(): Promise<void> {
      try {
        const res = await agentFetch("/v1/skills");
        if (!res.ok) throw new Error("Failed to fetch skills");
        const data = await res.json();
        if (!cancelled) {
          const items = Array.isArray(data.skills) ? data.skills : [];
          setSkills(items);
        }
      } catch {
        if (!cancelled) setSkills([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    fetchSkills();
    return () => { cancelled = true; };
  }, [botId]);

  return (
    <div className="max-w-2xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-foreground">{t.dashboard.skills}</h1>
        <p className="text-secondary text-sm mt-1">
          Discover what your AI agent can do
        </p>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton h-16 rounded-xl" />
          ))}
        </div>
      ) : skills.length === 0 ? (
        <GlassCard>
          <div className="text-center py-8">
            <p className="text-sm text-secondary">
              No skills loaded. Connect to a running agent to view available skills.
            </p>
          </div>
        </GlassCard>
      ) : (
        <div className="space-y-2">
          {skills.map((skill) => (
            <GlassCard key={skill.id} className="!py-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-foreground">{skill.name}</p>
                  {skill.description && (
                    <p className="text-xs text-secondary mt-0.5">{skill.description}</p>
                  )}
                </div>
                <div className={`w-2 h-2 rounded-full ${skill.enabled ? "bg-emerald-500" : "bg-gray-300"}`} />
              </div>
            </GlassCard>
          ))}
        </div>
      )}
    </div>
  );
}
