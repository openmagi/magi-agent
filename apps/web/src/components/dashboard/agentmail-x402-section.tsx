"use client";

import { useState } from "react";
import { GlassCard } from "@/components/ui/glass-card";
import { useMessages } from "@/lib/i18n";

interface AgentMailX402SectionProps {
  botId: string;
  walletAddress: string | null;
}

type DomainTab = "dedicated" | "subdomain";

export function AgentMailX402Section({ walletAddress }: AgentMailX402SectionProps) {
  const t = useMessages();
  const [expanded, setExpanded] = useState(false);
  const [domainExpanded, setDomainExpanded] = useState(false);
  const [domainTab, setDomainTab] = useState<DomainTab>("dedicated");

  return (
    <GlassCard className="!p-0 overflow-hidden mt-4">
      {/* Collapsible header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-5 cursor-pointer hover:bg-black/[0.02] transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="font-medium text-foreground">{t.x402Email.title}</span>
          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 uppercase tracking-wider">
            {t.x402Email.badge}
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
            {t.x402Email.description}
          </p>

          {!walletAddress ? (
            /* No wallet state */
            <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/20">
              <p className="text-sm text-amber-300">{t.x402Email.noWallet}</p>
            </div>
          ) : (
            <>
              {/* Prerequisites */}
              <div className="p-3 rounded-lg bg-black/[0.04] border border-black/10 space-y-1.5">
                <p className="text-xs text-secondary uppercase tracking-wider mb-2">
                  {t.x402Email.prerequisiteWallet}
                </p>
                <div className="flex items-center gap-2 text-xs text-muted">
                  <svg className="w-3.5 h-3.5 text-emerald-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                  <span>{t.x402Email.prerequisiteUsdc}</span>
                </div>
                <p className="text-xs text-muted pl-5.5">{t.x402Email.walletLink}</p>
              </div>

              {/* How to use */}
              <div>
                <p className="text-xs text-secondary uppercase tracking-wider mb-3">
                  {t.x402Email.howToTitle}
                </p>
                <div className="space-y-2.5">
                  {[t.x402Email.step1, t.x402Email.step2, t.x402Email.step3, t.x402Email.step4].map((step, i) => (
                    <div key={i} className="flex items-start gap-2.5 text-sm">
                      <span className="shrink-0 w-5 h-5 rounded-full bg-primary/20 text-primary-light text-xs flex items-center justify-center font-medium mt-0.5">
                        {i + 1}
                      </span>
                      <span className="text-muted">{step}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Pricing table */}
              <div>
                <p className="text-xs text-secondary uppercase tracking-wider mb-3">
                  {t.x402Email.pricingTitle}
                </p>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { label: t.x402Email.pricingInbox, cost: t.x402Email.pricingInboxCost },
                    { label: t.x402Email.pricingMonthly, cost: t.x402Email.pricingMonthlyCost },
                    { label: t.x402Email.pricingDomain, cost: t.x402Email.pricingDomainCost },
                  ].map((item) => (
                    <div
                      key={item.label}
                      className="p-3 rounded-lg bg-black/[0.04] border border-black/10 text-center"
                    >
                      <p className="text-xs text-secondary mb-1">{item.label}</p>
                      <p className="text-sm text-foreground font-mono">{item.cost}</p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Custom domain (collapsible) */}
              <div className="rounded-lg border border-black/10 overflow-hidden">
                <button
                  onClick={() => setDomainExpanded(!domainExpanded)}
                  className="w-full flex items-center justify-between p-3 cursor-pointer hover:bg-black/[0.02] transition-colors"
                >
                  <span className="text-xs text-secondary uppercase tracking-wider">
                    {t.x402Email.customDomainTitle}
                  </span>
                  <svg
                    className={`w-3.5 h-3.5 text-secondary transition-transform duration-200 ${domainExpanded ? "rotate-180" : ""}`}
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
                {domainExpanded && (
                  <div className="px-3 pb-3 space-y-3 border-t border-black/[0.06] pt-3">
                    <p className="text-xs text-muted">
                      {t.x402Email.customDomainDescription}
                    </p>

                    {/* Tab selector */}
                    <div className="flex gap-1 p-0.5 rounded-lg bg-black/[0.04]">
                      {(["dedicated", "subdomain"] as const).map((tab) => (
                        <button
                          key={tab}
                          onClick={() => setDomainTab(tab)}
                          className={`flex-1 text-xs py-1.5 px-2 rounded-md transition-colors ${
                            domainTab === tab
                              ? "bg-primary/20 text-primary-light font-medium"
                              : "text-secondary hover:text-foreground"
                          }`}
                        >
                          {tab === "dedicated"
                            ? t.x402Email.customDomainOptionDedicated
                            : t.x402Email.customDomainOptionSubdomain}
                        </button>
                      ))}
                    </div>

                    {/* Context note */}
                    <p className="text-[11px] text-muted leading-relaxed">
                      {domainTab === "dedicated"
                        ? t.x402Email.customDomainDedicatedNote
                        : t.x402Email.customDomainSubdomainNote}
                    </p>

                    {/* DNS records */}
                    <div className="space-y-1.5">
                      <p className="text-xs text-secondary font-medium">{t.x402Email.customDomainDns}</p>
                      {domainTab === "dedicated"
                        ? [
                            t.x402Email.customDomainMx,
                            t.x402Email.customDomainSpf,
                            t.x402Email.customDomainDkim,
                            t.x402Email.customDomainDmarc,
                          ].map((record) => (
                            <div key={record} className="text-xs text-muted font-mono bg-black/[0.04] rounded px-2 py-1.5">
                              {record}
                            </div>
                          ))
                        : [
                            t.x402Email.customDomainSubMx,
                            t.x402Email.customDomainSubSpf,
                            t.x402Email.customDomainSubDkim,
                            t.x402Email.customDomainSubDmarc,
                          ].map((record) => (
                            <div key={record} className="text-xs text-muted font-mono bg-black/[0.04] rounded px-2 py-1.5">
                              {record}
                            </div>
                          ))}
                    </div>

                    {/* Subdomain tip */}
                    {domainTab === "subdomain" && (
                      <p className="text-[11px] text-amber-400/80">
                        {t.x402Email.customDomainSubdomainTip}
                      </p>
                    )}
                  </div>
                )}
              </div>

              {/* Platform email note */}
              <p className="text-xs text-muted leading-relaxed">
                {t.x402Email.platformNote}
              </p>
            </>
          )}
        </div>
      )}
    </GlassCard>
  );
}
