"use client";

import { useEffect, useState } from "react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { GlassCard } from "@/components/ui/glass-card";
import { Select } from "@/components/ui/input";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { UsageChart } from "@/components/ui/usage-chart";
import { useMessages } from "@/lib/i18n";
// Pricing display removed for OSS

type Period = "7d" | "30d" | "90d";

const MODEL_KEYS = ["haiku", "sonnet", "opus", "kimi", "minimax", "gpt_5_nano", "gpt_5_mini", "gpt_5_1", "gpt_5_5", "gpt_5_5_pro", "gemini_3_1_flash", "gemini_3_1_pro", "gemini_2_5_flash", "gemini_2_5_pro", "codex"] as const;
type ModelKey = (typeof MODEL_KEYS)[number];

const MODEL_COLORS: Record<ModelKey, string> = {
  haiku: "text-emerald-400",
  sonnet: "text-blue-400",
  opus: "text-violet-400",
  kimi: "text-amber-400",
  minimax: "text-rose-400",
  gpt_5_nano: "text-cyan-400",
  gpt_5_mini: "text-teal-400",
  gpt_5_1: "text-sky-400",
  gpt_5_5: "text-indigo-400",
  gpt_5_5_pro: "text-fuchsia-400",
  gemini_3_1_flash: "text-lime-400",
  gemini_3_1_pro: "text-orange-400",
  gemini_2_5_flash: "text-lime-300",
  gemini_2_5_pro: "text-orange-300",
  codex: "text-yellow-400",
};

const MODEL_DISPLAY_NAMES: Record<ModelKey, string> = {
  haiku: "Haiku",
  sonnet: "Sonnet",
  opus: "Opus",
  kimi: "Kimi K2.6",
  minimax: "MiniMax M2.5",
  gpt_5_nano: "GPT-5.4 Nano",
  gpt_5_mini: "GPT-5.4 Mini",
  gpt_5_1: "GPT-5.4 Mini",
  gpt_5_5: "GPT-5.5",
  gpt_5_5_pro: "GPT-5.5 Pro",
  gemini_3_1_flash: "Gemini 3.1 Flash",
  gemini_3_1_pro: "Gemini 3.1 Pro",
  gemini_2_5_flash: "Gemini 2.5 Flash",
  gemini_2_5_pro: "Gemini 2.5 Pro",
  codex: "Codex",
};

interface ModelUsage {
  inputTokens: number;
  outputTokens: number;
  cacheCreationTokens: number;
  cacheReadTokens: number;
  costCents: number;
}

interface HourlyUsage {
  hour: string;
  inputTokens: number;
  outputTokens: number;
  cacheCreationTokens: number;
  cacheReadTokens: number;
  costCents: number;
  requests: number;
  models: Partial<Record<ModelKey, ModelUsage>>;
}

interface DailyUsage {
  date: string;
  inputTokens: number;
  outputTokens: number;
  cacheCreationTokens: number;
  cacheReadTokens: number;
  costCents: number;
  models: Partial<Record<ModelKey, ModelUsage>>;
  hours?: HourlyUsage[];
}

interface FirecrawlData {
  totalRequests: number;
  totalCostCents: number;
  daily: { date: string; count: number; costCents: number }[];
}

interface ServiceDailyEntry {
  date: string;
  count: number;
  costCents: number;
}

interface ServiceUsage {
  search: { total: number; totalCostCents: number; daily: ServiceDailyEntry[] };
  email: { total: number; totalCostCents: number; daily: ServiceDailyEntry[] };
}

interface UsageData {
  totalInputTokens: number;
  totalOutputTokens: number;
  totalCostCents: number;
  modelTotals: Partial<Record<ModelKey, ModelUsage>>;
  daily: DailyUsage[];
  firecrawl?: FirecrawlData;
}

interface HourlyUsageTableProps {
  daily: DailyUsage[];
  formatTokens: (t: number) => string;
  formatCents: (c: number) => string;
  hideCost?: boolean;
  detailedView?: boolean;
}

function HourlyUsageTable({ daily, formatTokens, formatCents, hideCost, detailedView }: HourlyUsageTableProps) {
  const [expandedDate, setExpandedDate] = useState<string | null>(null);

  return (
    <div className="space-y-0.5">
      {daily.map((day) => {
        const isExpanded = expandedDate === day.date;
        const modelSummary = MODEL_KEYS
          .filter((k) => day.models[k])
          .map((k) => {
            const m = day.models[k]!;
            return `${MODEL_DISPLAY_NAMES[k]} ${formatTokens(m.inputTokens + (m.cacheCreationTokens ?? 0) + (m.cacheReadTokens ?? 0) + m.outputTokens)}`;
          })
          .join(", ");

        return (
          <div key={day.date}>
            {/* Day header row */}
            <button
              type="button"
              onClick={() => setExpandedDate(isExpanded ? null : day.date)}
              className="w-full flex items-center justify-between py-2.5 px-3 rounded-lg hover:bg-black/[0.04] transition-colors text-left"
            >
              <div className="flex items-center gap-3 min-w-0">
                <span className="text-sm font-medium text-foreground whitespace-nowrap">{day.date}</span>
                <span className="text-xs text-secondary truncate">{modelSummary}</span>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <span className="text-xs text-muted">{formatTokens(day.inputTokens + (day.cacheCreationTokens ?? 0) + (day.cacheReadTokens ?? 0) + day.outputTokens)} tokens</span>
                {!hideCost && <span className="text-sm font-medium text-foreground">{formatCents(day.costCents)}</span>}
                <svg
                  className={`w-4 h-4 text-secondary transition-transform ${isExpanded ? "rotate-180" : ""}`}
                  fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                </svg>
              </div>
            </button>

            {/* Hourly breakdown */}
            {isExpanded && day.hours && (
              <div className="ml-3 mb-2 border-l border-black/[0.08] pl-3">
                {day.hours.map((hr) => (
                  <div key={hr.hour} className="py-2 border-b border-black/[0.06] last:border-0">
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium text-muted w-12">{hr.hour}</span>
                        <span className="text-[10px] text-secondary">{hr.requests} req</span>
                      </div>
                      <div className="flex items-center gap-3 text-xs">
                        {detailedView ? (
                          <span className="text-muted">
                            {formatTokens(hr.inputTokens)} base / {formatTokens(hr.cacheCreationTokens ?? 0)} write / {formatTokens(hr.cacheReadTokens ?? 0)} read / {formatTokens(hr.outputTokens)} out
                          </span>
                        ) : (
                          <span className="text-muted">{formatTokens(hr.inputTokens + (hr.cacheCreationTokens ?? 0) + (hr.cacheReadTokens ?? 0))} in / {formatTokens(hr.outputTokens)} out</span>
                        )}
                        {!hideCost && <span className="font-medium text-foreground">{formatCents(hr.costCents)}</span>}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-x-4 gap-y-0.5">
                      {MODEL_KEYS.filter((k) => hr.models[k]).map((k) => {
                        const m = hr.models[k]!;
                        return (
                          <div key={k} className="flex items-center gap-1.5 text-[10px]">
                            <span className={`font-medium ${MODEL_COLORS[k]}`}>{MODEL_DISPLAY_NAMES[k]}</span>
                            {detailedView ? (
                              <span className="text-muted">{formatTokens(m.inputTokens)} + {formatTokens(m.cacheCreationTokens ?? 0)} write + {formatTokens(m.cacheReadTokens ?? 0)} read / {formatTokens(m.outputTokens)} out</span>
                            ) : (
                              <span className="text-muted">{formatTokens(m.inputTokens + (m.cacheCreationTokens ?? 0) + (m.cacheReadTokens ?? 0) + m.outputTokens)}</span>
                            )}
                            {!hideCost && <span className="text-secondary">{formatCents(m.costCents)}</span>}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function UsagePage() {
  const authFetch = useAuthFetch();
  const t = useMessages();
  const [botId, setBotId] = useState<string | null>(null);
  const [isByok, setIsByok] = useState(false);
  const [period, setPeriod] = useState<Period>("7d");
  const [usage, setUsage] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showPricing, setShowPricing] = useState(false);
  const [detailedView, setDetailedView] = useState(true);
  const [hourlyView, setHourlyView] = useState(false);
  const [searchQuota, setSearchQuota] = useState<{ monthly_limit: number; used_count: number } | null>(null);
  const [emailQuota, setEmailQuota] = useState<{ monthly_limit: number; used_count: number } | null>(null);
  const [creditGrant, setCreditGrant] = useState<{ granted_cents: number; used_cents: number; is_trialing?: boolean } | null>(null);
  const [storage, setStorage] = useState<{ usedBytes: number; limitBytes: number } | null>(null);
  const [serviceUsage, setServiceUsage] = useState<ServiceUsage | null>(null);

  const periodOptions: { value: Period; label: string }[] = [
    { value: "7d", label: t.usagePage.last7d },
    { value: "30d", label: t.usagePage.last30d },
    { value: "90d", label: t.usagePage.last90d },
  ];

  useEffect(() => {
    async function fetchBot() {
      try {
        const res = await authFetch("/api/bots");
        if (!res.ok) throw new Error("Failed to fetch bots");
        const data = await res.json();
        const bots = data.bots ?? data;
        if (Array.isArray(bots) && bots.length > 0) {
          setBotId(bots[0].id);
          setIsByok(bots[0].api_key_mode === "byok");
        } else {
          setLoading(false);
        }
      } catch {
        setError(t.errors.loadUsage);
        setLoading(false);
      }
    }

    async function fetchQuotaAndGrant() {
      const [quotaRes, emailQuotaRes, grantRes] = await Promise.all([
        authFetch("/api/search-quota").catch(() => null),
        authFetch("/api/email-quota").catch(() => null),
        authFetch("/api/credits/grant").catch(() => null),
      ]);
      if (quotaRes?.ok) {
        const q = await quotaRes.json();
        if (q.monthly_limit > 0) setSearchQuota(q);
      }
      if (emailQuotaRes?.ok) {
        const eq = await emailQuotaRes.json();
        if (eq.monthly_limit > 0) setEmailQuota(eq);
      }
      if (grantRes?.ok) {
        const g = await grantRes.json();
        setCreditGrant(g);
      }
    }

    fetchBot();
    fetchQuotaAndGrant();
  }, [authFetch, t.errors.loadUsage]);

  useEffect(() => {
    if (!botId) return;
    authFetch(`/api/bots/${botId}/storage`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setStorage(d); })
      .catch(() => {});
  }, [botId, authFetch]);

  useEffect(() => {
    if (!botId) return;

    async function fetchUsage() {
      setLoading(true);
      setError(null);

      try {
        const [res, svcRes] = await Promise.all([
          authFetch(`/api/bots/${botId}/usage?period=${period}`),
          authFetch(`/api/usage/services?period=${period}`).catch(() => null),
        ]);
        const data = await res.json();

        if (!res.ok) {
          throw new Error(data.error || "Failed to fetch usage data");
        }

        setUsage(data);
        if (svcRes?.ok) {
          const svcData = await svcRes.json();
          setServiceUsage(svcData);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : t.errors.unexpected);
      } finally {
        setLoading(false);
      }
    }

    fetchUsage();
  }, [botId, period, authFetch, t.errors.unexpected]);

  function formatTokens(tokens: number): string {
    if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(2)}M`;
    if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`;
    return tokens.toLocaleString();
  }

  function formatCents(cents: number): string {
    return `$${(cents / 100).toFixed(2)}`;
  }

  function formatBytes(bytes: number): string {
    if (bytes >= 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(1)}GB`;
    if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(0)}MB`;
    if (bytes >= 1_024) return `${(bytes / 1_024).toFixed(0)}KB`;
    return `${bytes}B`;
  }

  return (
    <div>
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 sm:justify-between mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-foreground">{t.usagePage.title}</h1>
          {!isByok && (
            <button
              onClick={() => setShowPricing((p) => !p)}
              className="text-xs text-muted hover:text-secondary transition-colors"
            >
              {t.usagePage.viewPricing}
            </button>
          )}
        </div>
        <Select
          value={period}
          onChange={(e) => setPeriod(e.target.value as Period)}
          className="w-44"
        >
          {periodOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </Select>
      </div>

      {showPricing && (
        <GlassCard className="mb-6">
          <h2 className="text-sm font-semibold text-foreground mb-3">{t.usagePage.pricingTitle}</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-black/[0.08]">
                  <th className="text-left py-1.5 pr-3 font-medium text-secondary">{t.usagePage.model}</th>
                  <th className="text-right py-1.5 pr-3 font-medium text-secondary">{t.usagePage.input}</th>
                  <th className="text-right py-1.5 pr-3 font-medium text-secondary">{t.usagePage.cacheCreation}</th>
                  <th className="text-right py-1.5 pr-3 font-medium text-secondary">{t.usagePage.cacheRead}</th>
                  <th className="text-right py-1.5 font-medium text-secondary">{t.usagePage.output}</th>
                </tr>
              </thead>
              <tbody>
                <tr><td colSpan={5} className="py-3 text-center text-secondary text-xs">Pricing not available in OSS mode.</td></tr>
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-muted mt-2">{t.usagePage.pricingNote}</p>
        </GlassCard>
      )}

      {error && (
        <div className="glass border border-red-500/20 text-red-400 px-4 py-3 rounded-xl text-sm mb-6">
          {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            <div className="skeleton h-20" />
            <div className="skeleton h-20" />
            <div className="skeleton h-20" />
          </div>
          <div className="skeleton h-48" />
        </div>
      ) : !botId ? (
        <GlassCard className="text-center py-10">
          <p className="text-secondary mb-4">{t.usagePage.noBot}</p>
          <Link href="/onboarding/model">
            <Button variant="cta" size="md">
              {t.dashboard.deployFirstBot}
            </Button>
          </Link>
        </GlassCard>
      ) : !usage || (usage.totalInputTokens === 0 && usage.totalOutputTokens === 0) ? (
        <GlassCard className="text-center py-10">
          <p className="text-secondary">{t.usagePage.noData}</p>
        </GlassCard>
      ) : (
        <>
          {/* Summary + quota cards — unified grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
            <GlassCard>
              <p className="text-sm text-secondary mb-1">{t.usagePage.totalTokens}</p>
              <p className="text-2xl font-bold text-foreground">
                {formatTokens(usage.totalInputTokens + usage.totalOutputTokens)}
              </p>
              <p className="text-xs text-muted mt-1">
                {formatTokens(usage.totalInputTokens)} in / {formatTokens(usage.totalOutputTokens)} out
              </p>
            </GlassCard>
            {!isByok && (
              <GlassCard>
                <p className="text-sm text-secondary mb-1">{t.usagePage.estimatedCost}</p>
                <p className="text-2xl font-bold text-foreground">
                  {formatCents(usage.totalCostCents)}
                </p>
              </GlassCard>
            )}
            <GlassCard>
              <p className="text-sm text-secondary mb-1">{t.usagePage.modelBreakdown}</p>
              <div className="space-y-1 mt-1">
                {MODEL_KEYS.map((key) => {
                  const m = usage.modelTotals[key];
                  if (!m) return null;
                  return (
                    <div key={key} className="flex items-center justify-between text-xs">
                      <span className={`font-medium ${MODEL_COLORS[key]}`}>{MODEL_DISPLAY_NAMES[key]}</span>
                      <span className="text-foreground">{formatTokens(m.inputTokens + m.outputTokens)}</span>
                    </div>
                  );
                })}
              </div>
            </GlassCard>
            {searchQuota && (
              <GlassCard>
                <p className="text-sm text-secondary mb-1">{t.usagePage.braveSearch}</p>
                <p className="text-2xl font-bold text-foreground">
                  {searchQuota.used_count} / {searchQuota.monthly_limit}
                </p>
                <p className="text-xs text-muted mt-1">{t.usagePage.queriesUsed}</p>
                <div className="w-full h-1.5 bg-black/[0.04] rounded-full overflow-hidden mt-2">
                  <div
                    className="h-full bg-blue-400/60 rounded-full transition-all"
                    style={{ width: `${Math.min((searchQuota.used_count / searchQuota.monthly_limit) * 100, 100)}%` }}
                  />
                </div>
                <div className="flex items-center justify-between mt-1">
                  <p className="text-[10px] text-muted">{t.usagePage.resetsMonthly}</p>
                  {searchQuota.used_count > searchQuota.monthly_limit && (
                    <p className="text-[10px] text-amber-400">{t.usagePage.searchOverageNote}</p>
                  )}
                </div>
              </GlassCard>
            )}
            {emailQuota && (
              <GlassCard>
                <p className="text-sm text-secondary mb-1">{t.usagePage.agentEmail}</p>
                <p className="text-2xl font-bold text-foreground">
                  {emailQuota.used_count} / {emailQuota.monthly_limit}
                </p>
                <p className="text-xs text-muted mt-1">{t.usagePage.emailsUsed}</p>
                <div className="w-full h-1.5 bg-black/[0.04] rounded-full overflow-hidden mt-2">
                  <div
                    className="h-full bg-orange-400/60 rounded-full transition-all"
                    style={{ width: `${Math.min((emailQuota.used_count / emailQuota.monthly_limit) * 100, 100)}%` }}
                  />
                </div>
                <div className="flex items-center justify-between mt-1">
                  <p className="text-[10px] text-muted">{t.usagePage.resetsMonthly}</p>
                  {emailQuota.used_count > emailQuota.monthly_limit && (
                    <p className="text-[10px] text-amber-400">{t.usagePage.emailOverageNote}</p>
                  )}
                </div>
              </GlassCard>
            )}
            {usage.firecrawl && usage.firecrawl.totalRequests > 0 && (
              <GlassCard>
                <p className="text-sm text-secondary mb-1">{t.usagePage.firecrawl}</p>
                <p className="text-2xl font-bold text-foreground">
                  {usage.firecrawl.totalRequests}
                </p>
                <p className="text-xs text-muted mt-1">{t.usagePage.firecrawlRequests}</p>
                {!isByok && usage.firecrawl.totalCostCents > 0 && (
                  <p className="text-xs text-amber-400 mt-1">{formatCents(usage.firecrawl.totalCostCents)} credits</p>
                )}
              </GlassCard>
            )}
            {creditGrant && creditGrant.granted_cents > 0 && (
              <GlassCard>
                <p className="text-sm text-secondary mb-1">{t.usagePage.monthlyCredits}</p>
                {creditGrant.is_trialing ? (
                  <>
                    <p className="text-2xl font-bold text-foreground">
                      — / ${(creditGrant.granted_cents / 100).toFixed(2)}
                    </p>
                    <div className="w-full h-1.5 bg-black/[0.04] rounded-full overflow-hidden mt-2">
                      <div className="h-full bg-emerald-400/60 rounded-full" style={{ width: "0%" }} />
                    </div>
                    <p className="text-[10px] text-muted mt-1">{t.usagePage.creditsAfterTrial}</p>
                  </>
                ) : (
                  <>
                    <p className="text-2xl font-bold text-foreground">
                      ${((Math.max(creditGrant.granted_cents - creditGrant.used_cents, 0)) / 100).toFixed(2)} / ${(creditGrant.granted_cents / 100).toFixed(2)}
                    </p>
                    <div className="w-full h-1.5 bg-black/[0.04] rounded-full overflow-hidden mt-2">
                      <div
                        className="h-full bg-emerald-400/60 rounded-full transition-all"
                        style={{ width: `${Math.min((creditGrant.used_cents / creditGrant.granted_cents) * 100, 100)}%` }}
                      />
                    </div>
                    <p className="text-[10px] text-muted mt-1">{t.usagePage.resetsMonthly}</p>
                  </>
                )}
              </GlassCard>
            )}
            {storage && (
              <GlassCard>
                <p className="text-sm text-secondary mb-1">{t.usagePage.storage}</p>
                <p className="text-2xl font-bold text-foreground">
                  {formatBytes(storage.usedBytes)} / {formatBytes(storage.limitBytes)}
                </p>
                <p className="text-xs text-muted mt-1">{t.usagePage.storageUsed}</p>
                <div className="w-full h-1.5 bg-black/[0.04] rounded-full overflow-hidden mt-2">
                  <div
                    className="h-full bg-violet-400/60 rounded-full transition-all"
                    style={{ width: `${Math.min((storage.usedBytes / storage.limitBytes) * 100, 100)}%` }}
                  />
                </div>
              </GlassCard>
            )}
          </div>

          {/* Daily bar chart */}
          <GlassCard className="mb-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-foreground">{t.usagePage.dailyBreakdown}</h2>
              <button
                onClick={() => setDetailedView((v) => !v)}
                className="text-xs text-muted hover:text-secondary transition-colors"
              >
                {detailedView ? t.usagePage.simpleView : t.usagePage.detailedView}
              </button>
            </div>
            <UsageChart daily={usage.daily} formatTokens={formatTokens} detailedView={detailedView} />
          </GlassCard>

          {/* Service usage (search, email, firecrawl) daily breakdown */}
          {(serviceUsage || usage.firecrawl) && (() => {
            const svcSearchMap = new Map((serviceUsage?.search.daily ?? []).map((d) => [d.date, d]));
            const svcEmailMap = new Map((serviceUsage?.email.daily ?? []).map((d) => [d.date, d]));
            const fcMap = new Map((usage.firecrawl?.daily ?? []).map((d) => [d.date, d]));
            const allDates = [...new Set([
              ...svcSearchMap.keys(), ...svcEmailMap.keys(), ...fcMap.keys(),
            ])].sort((a, b) => b.localeCompare(a));

            return allDates.length > 0 ? (
              <GlassCard className="mb-6">
                <h2 className="text-lg font-semibold text-foreground mb-4">{t.usagePage.serviceUsage}</h2>
                <div className="space-y-0.5">
                  {allDates.map((date) => {
                    const search = svcSearchMap.get(date);
                    const email = svcEmailMap.get(date);
                    const fc = fcMap.get(date);
                    return (
                      <div key={date} className="flex items-center justify-between py-2 px-3 rounded-lg hover:bg-black/[0.04] transition-colors">
                        <span className="text-sm font-medium text-foreground whitespace-nowrap">{date}</span>
                        <div className="flex items-center gap-4 text-xs">
                          {search && search.count > 0 && (
                            <span className="text-blue-400">
                              {search.count} {t.usagePage.searches}
                              {search.costCents > 0 && <span className="text-amber-400 ml-1">({formatCents(search.costCents)})</span>}
                            </span>
                          )}
                          {email && email.count > 0 && (
                            <span className="text-orange-400">
                              {email.count} {t.usagePage.emails}
                              {email.costCents > 0 && <span className="text-amber-400 ml-1">({formatCents(email.costCents)})</span>}
                            </span>
                          )}
                          {fc && fc.count > 0 && (
                            <span className="text-teal-400">
                              {fc.count} {t.usagePage.firecrawl}
                              {fc.costCents > 0 && <span className="text-amber-400 ml-1">({formatCents(fc.costCents)})</span>}
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </GlassCard>
            ) : null;
          })()}

          {/* Usage table */}
          <GlassCard>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-foreground">{t.usagePage.detailedTable}</h2>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setDetailedView((v) => !v)}
                  className="text-xs text-muted hover:text-secondary transition-colors"
                >
                  {detailedView ? t.usagePage.simpleView : t.usagePage.detailedView}
                </button>
                <span className="text-white/10">|</span>
                <button
                  onClick={() => setHourlyView((v) => !v)}
                  className="text-xs text-muted hover:text-secondary transition-colors"
                >
                  {hourlyView ? t.usagePage.dailyView : t.usagePage.hourlyView}
                </button>
              </div>
            </div>
            {hourlyView ? (
              <HourlyUsageTable daily={usage.daily} formatTokens={formatTokens} formatCents={formatCents} hideCost={isByok} detailedView={detailedView} />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-black/[0.08]">
                      <th className="text-left py-2 pr-4 font-medium text-secondary">{t.usagePage.date}</th>
                      <th className="text-left py-2 pr-4 font-medium text-secondary">{t.usagePage.model}</th>
                      {detailedView ? (
                        <>
                          <th className="text-right py-2 pr-4 font-medium text-secondary">{t.usagePage.baseInput}</th>
                          <th className="text-right py-2 pr-4 font-medium text-secondary">{t.usagePage.cacheCreation}</th>
                          <th className="text-right py-2 pr-4 font-medium text-secondary">{t.usagePage.cacheRead}</th>
                        </>
                      ) : (
                        <th className="text-right py-2 pr-4 font-medium text-secondary">{t.usagePage.inputTokens}</th>
                      )}
                      <th className="text-right py-2 pr-4 font-medium text-secondary">{t.usagePage.outputTokens}</th>
                      {!isByok && <th className="text-right py-2 font-medium text-secondary">{t.usagePage.cost}</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {usage.daily.map((day) =>
                      MODEL_KEYS.filter((key) => day.models[key]).map((key, idx) => {
                        const m = day.models[key]!;
                        return (
                          <tr key={`${day.date}-${key}`} className="border-b border-black/[0.06] last:border-0">
                            {idx === 0 ? (
                              <td
                                className="py-2 pr-4 text-muted align-top"
                                rowSpan={MODEL_KEYS.filter((k) => day.models[k]).length}
                              >
                                {day.date}
                              </td>
                            ) : null}
                            <td className={`py-2 pr-4 font-medium ${MODEL_COLORS[key]}`}>{MODEL_DISPLAY_NAMES[key]}</td>
                            {detailedView ? (
                              <>
                                <td className="py-2 pr-4 text-right text-foreground">{formatTokens(m.inputTokens)}</td>
                                <td className="py-2 pr-4 text-right text-foreground">{formatTokens(m.cacheCreationTokens ?? 0)}</td>
                                <td className="py-2 pr-4 text-right text-foreground">{formatTokens(m.cacheReadTokens ?? 0)}</td>
                              </>
                            ) : (
                              <td className="py-2 pr-4 text-right text-foreground">{formatTokens(m.inputTokens + (m.cacheCreationTokens ?? 0) + (m.cacheReadTokens ?? 0))}</td>
                            )}
                            <td className="py-2 pr-4 text-right text-foreground">{formatTokens(m.outputTokens)}</td>
                            {!isByok && <td className="py-2 text-right font-medium text-foreground">{formatCents(m.costCents)}</td>}
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </GlassCard>
        </>
      )}
    </div>
  );
}
