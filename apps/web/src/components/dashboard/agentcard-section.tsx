"use client";

import { useState } from "react";
import { GlassCard } from "@/components/ui/glass-card";
import { useMessages } from "@/lib/i18n";

export function AgentCardSection() {
  const t = useMessages();
  const [expanded, setExpanded] = useState(false);

  return (
    <GlassCard className="!p-0 overflow-hidden mt-4">
      {/* Collapsible header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-5 cursor-pointer hover:bg-black/[0.02] transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="font-medium text-foreground">{t.agentCard.title}</span>
          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 uppercase tracking-wider">
            {t.agentCard.badge}
          </span>
        </div>
        <svg
          className={`w-4 h-4 text-secondary transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-black/[0.06] px-5 pb-5 pt-4 space-y-5">
          {/* Description */}
          <p className="text-sm text-secondary leading-relaxed">
            {t.agentCard.description}
          </p>

          {/* How to use */}
          <div>
            <p className="text-xs text-secondary uppercase tracking-wider mb-3">
              {t.agentCard.howToTitle}
            </p>
            <div className="space-y-2.5">
              {[t.agentCard.step1, t.agentCard.step2, t.agentCard.step3, t.agentCard.step4].map((step, i) => (
                <div key={i} className="flex items-start gap-2.5 text-sm">
                  <span className="shrink-0 w-5 h-5 rounded-full bg-blue-500/20 text-blue-400 text-xs flex items-center justify-center font-medium mt-0.5">
                    {i + 1}
                  </span>
                  <span className="text-muted">{step}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Use cases */}
          <div>
            <p className="text-xs text-secondary uppercase tracking-wider mb-3">
              {t.agentCard.useCasesTitle}
            </p>
            <div className="grid grid-cols-2 gap-2">
              {[
                { label: t.agentCard.useCaseApi, icon: "M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" },
                { label: t.agentCard.useCaseSaas, icon: "M3 15a4 4 0 004 4h9a5 5 0 10-.1-9.999 5.002 5.002 0 10-9.78 2.096A4.001 4.001 0 003 15z" },
                { label: t.agentCard.useCaseDomain, icon: "M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" },
                { label: t.agentCard.useCaseData, icon: "M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4" },
              ].map((item) => (
                <div
                  key={item.label}
                  className="p-3 rounded-lg bg-black/[0.04] border border-black/10 flex items-center gap-2"
                >
                  <svg className="w-4 h-4 text-blue-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d={item.icon} />
                  </svg>
                  <span className="text-xs text-muted">{item.label}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Pricing note */}
          <div className="p-3 rounded-lg bg-black/[0.04] border border-black/10">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-medium text-foreground">{t.agentCard.pricingTitle}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400">
                {t.agentCard.pricingBeta}
              </span>
            </div>
            <p className="text-xs text-muted">{t.agentCard.pricingDescription}</p>
          </div>

          {/* Platform note */}
          <p className="text-xs text-muted leading-relaxed">
            {t.agentCard.platformNote}
          </p>
        </div>
      )}
    </GlassCard>
  );
}
