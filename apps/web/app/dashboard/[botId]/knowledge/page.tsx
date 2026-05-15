"use client";

import { GlassCard } from "@/components/ui/glass-card";
import { useMessages } from "@/lib/i18n";

export default function KnowledgePage() {
  const t = useMessages();

  return (
    <div className="max-w-2xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-foreground">{t.dashboard.knowledge}</h1>
        <p className="text-secondary text-sm mt-1">
          Manage your agent&apos;s knowledge base
        </p>
      </div>

      <GlassCard>
        <div className="text-center py-8">
          <p className="text-sm text-secondary">
            Knowledge base management coming soon.
          </p>
          <p className="text-xs text-muted mt-2">
            Upload documents, manage collections, and search your agent&apos;s knowledge.
          </p>
        </div>
      </GlassCard>
    </div>
  );
}
