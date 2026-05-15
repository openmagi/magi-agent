"use client";

import { useState, useEffect, useCallback } from "react";
import { usePrivy } from "@privy-io/react-auth";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { Button } from "@/components/ui/button";
import { GlassCard } from "@/components/ui/glass-card";
import { useMessages } from "@/lib/i18n";

interface ReferralStats {
  referralCount: number;
  totalEarnedCents: number;
  settledCents: number;
  availableCents: number;
  paidOutCents: number;
}

interface ReferralData {
  code: string | null;
  isCustom?: boolean;
  stats?: ReferralStats;
}

interface Payout {
  id: string;
  amount_cents: number;
  amount_usdc: string;
  destination_address: string;
  tx_hash: string | null;
  status: string;
  claimed_at: string;
}

export function ReferralPanel(): React.ReactElement {
  const { user } = usePrivy();
  const authFetch = useAuthFetch();
  const t = useMessages();
  const r = t.referralPage;
  const [data, setData] = useState<ReferralData | null>(null);
  const [payouts, setPayouts] = useState<Payout[]>([]);
  const [customCode, setCustomCode] = useState("");
  const [payoutAddress, setPayoutAddress] = useState("");
  const [loading, setLoading] = useState(true);
  const [claiming, setClaiming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const fetchData = useCallback(async () => {
    const [codeRes, payoutsRes] = await Promise.all([
      authFetch("/api/referral/code"),
      authFetch("/api/referral/payouts"),
    ]);
    const codeData = await codeRes.json();
    const payoutsData = await payoutsRes.json();
    setData(codeData);
    setPayouts(payoutsData.payouts ?? []);
    setLoading(false);
  }, [authFetch]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const createCode = async () => {
    setError(null);
    const res = await authFetch("/api/referral/code", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      setError(err.error);
      return;
    }
    fetchData();
  };

  const updateCode = async () => {
    setError(null);
    const res = await authFetch("/api/referral/code", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: customCode }),
    });
    if (!res.ok) {
      const err = await res.json();
      setError(err.error);
      return;
    }
    setCustomCode("");
    fetchData();
  };

  const claimPayout = async () => {
    setError(null);
    setClaiming(true);
    const wallet = user?.wallet;
    const walletAddress = wallet?.address;

    const res = await authFetch("/api/referral/claim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ walletAddress }),
    });
    setClaiming(false);
    if (!res.ok) {
      const err = await res.json();
      setError(err.error);
      return;
    }
    fetchData();
  };

  const savePayoutAddress = async () => {
    setError(null);
    const res = await authFetch("/api/referral/payout-address", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address: payoutAddress || null }),
    });
    if (!res.ok) {
      const err = await res.json();
      setError(err.error);
      return;
    }
  };

  const siteUrl = typeof window !== "undefined" ? window.location.origin : "https://openmagi.ai";
  const availableCents = data?.stats?.availableCents ?? 0;
  const claimDisabled = claiming || availableCents < 1000;
  const claimAmount = ((availableCents) / 100).toFixed(2);
  const claimLabel = r.claimUsdc.replace("${amount}", `$${claimAmount}`);

  if (loading) {
    return <div className="animate-pulse text-muted text-sm">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {!data?.code ? (
        <GlassCard className="border-primary/15 bg-white/80">
          <p className="mb-4 text-sm text-secondary">{r.earnCta}</p>
          <Button onClick={createCode} size="sm">
            {r.createCode}
          </Button>
        </GlassCard>
      ) : (
        <>
          <GlassCard className="border-primary/15 bg-white/80">
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <span className="text-sm text-secondary">{r.yourCode}</span>
              <code className="rounded-lg bg-primary/10 px-3 py-1.5 font-mono text-lg font-bold text-primary-light">
                {data.code}
              </code>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(`${siteUrl}/?ref=${data.code}`);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 2000);
                }}
                className={`min-h-[44px] rounded-lg px-3 text-xs font-semibold transition ${
                  copied
                    ? "border border-emerald-200 bg-emerald-50 text-emerald-700"
                    : "border border-black/10 bg-white text-foreground hover:border-primary/30 hover:bg-primary/5"
                }`}
              >
                {copied ? r.copied : r.copyLink}
              </button>
            </div>
            <p className="mb-4 break-all text-xs text-muted">{siteUrl}/?ref={data.code}</p>

            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                type="text"
                value={customCode}
                onChange={(e) => setCustomCode(e.target.value)}
                placeholder={r.customCodePlaceholder}
                className="min-h-[44px] flex-1 rounded-lg border border-black/10 bg-white/90 px-3 text-sm text-foreground placeholder:text-muted focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/15"
              />
              <Button onClick={updateCode} variant="secondary" size="sm">
                {r.update}
              </Button>
            </div>
          </GlassCard>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {[
              { label: r.referrals, value: data.stats?.referralCount ?? 0, tone: "text-foreground" },
              { label: r.totalEarned, value: `$${((data.stats?.totalEarnedCents ?? 0) / 100).toFixed(2)}`, tone: "text-foreground" },
              { label: r.available, value: `$${(availableCents / 100).toFixed(2)}`, tone: "text-emerald-700" },
            ].map((stat) => (
              <div key={stat.label} className="rounded-xl border border-black/10 bg-white/75 p-4 text-center shadow-sm">
                <div className={`text-2xl font-bold ${stat.tone}`}>{stat.value}</div>
                <div className="mt-1 text-xs text-secondary">{stat.label}</div>
              </div>
            ))}
          </div>

          <GlassCard className="bg-white/80">
            <h3 className="mb-3 text-sm font-semibold text-foreground">{r.payoutAddress}</h3>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                type="text"
                value={payoutAddress}
                onChange={(e) => setPayoutAddress(e.target.value)}
                placeholder={r.payoutAddressPlaceholder}
                className="min-h-[44px] flex-1 rounded-lg border border-black/10 bg-white/90 px-3 font-mono text-sm text-foreground placeholder:text-muted focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/15"
              />
              <Button onClick={savePayoutAddress} variant="secondary" size="sm">
                {r.save}
              </Button>
            </div>
          </GlassCard>

          <button
            onClick={claimPayout}
            disabled={claimDisabled}
            className={`min-h-[52px] w-full rounded-xl border px-4 py-3 font-semibold transition ${
              claimDisabled
                ? "cursor-not-allowed border-slate-200 bg-slate-200 text-slate-500"
                : "cursor-pointer border-emerald-600 bg-emerald-600 text-white shadow-sm hover:bg-emerald-500"
            }`}
          >
            {claiming ? r.processing : claimLabel}
          </button>
          <p className="text-center text-xs text-muted">{r.minimumClaim}</p>

          {payouts.length > 0 && (
            <div className="overflow-hidden rounded-xl border border-black/10 bg-white/80">
              <h3 className="border-b border-black/10 px-6 py-3 text-sm font-semibold text-foreground">{r.payoutHistory}</h3>
              <div className="divide-y divide-black/5">
                {payouts.map((p) => (
                  <div key={p.id} className="px-6 py-3 flex items-center justify-between text-sm">
                    <div>
                      <span className="font-medium text-foreground">${(p.amount_cents / 100).toFixed(2)}</span>
                      <span className="ml-2 text-muted">{new Date(p.claimed_at).toLocaleDateString()}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        p.status === "completed" ? "border border-emerald-200 bg-emerald-50 text-emerald-700" :
                        p.status === "failed" ? "border border-red-200 bg-red-50 text-red-700" :
                        "border border-amber-200 bg-amber-50 text-amber-700"
                      }`}>
                        {p.status}
                      </span>
                      {p.tx_hash && (
                        <a
                          href={`https://basescan.org/tx/${p.tx_hash}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs font-medium text-primary-light hover:text-primary"
                        >
                          tx
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
