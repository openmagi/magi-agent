"use client";

import { useState, useEffect, useCallback } from "react";
import dynamic from "next/dynamic";
import { useFundWallet } from "@privy-io/react-auth";
import { base, mainnet } from "viem/chains";
import { GlassCard } from "@/components/ui/glass-card";
import { Button } from "@/components/ui/button";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { useCoinbaseOnramp } from "@/hooks/use-coinbase-onramp";
import { useMessages } from "@/lib/i18n";

const WalletPolicyModal = dynamic(
  () => import("./wallet-policy-modal").then((m) => m.WalletPolicyModal),
  { ssr: false }
);

interface AgentWalletSectionProps {
  botId: string;
}

interface TokenBalance {
  symbol: string;
  balance: string;
}

interface NetworkBalance {
  key: string;
  label: string;
  chainId: number;
  balances: TokenBalance[];
}

interface WalletData {
  id: string;
  address: string;
  chain: string | null;
  balances: TokenBalance[];
  networks?: NetworkBalance[];
}

interface PolicyData {
  id: string;
  name: string;
  policy_type: string;
  policy_json: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
}

function describePolicyBrief(json: Record<string, unknown>): string {
  // New format: { rules: [{ conditions: [...] }] }
  const rules = json.rules as Array<{ conditions?: Array<{ field: string; operator: string; value: string | string[] }> }> | undefined;
  // Legacy format: { conditions: [...] }
  const legacyConditions = json.conditions as Array<{ field: string; operator: string; value: string | string[] }> | undefined;
  const conditions = rules?.[0]?.conditions ?? legacyConditions;
  if (!conditions?.length) return "No conditions";
  return conditions.map((c) => {
    if (c.field === "value") return `Max ${Number(c.value) / 1e18} ETH per tx`;
    if (c.field === "chain_id") return `Chain: ${c.value}`;
    if (c.field === "to") return `Contract allowlist (${Array.isArray(c.value) ? c.value.length : 1})`;
    return `${c.field} ${c.operator} ${String(c.value)}`;
  }).join(" \u00B7 ");
}

function truncateAddress(address: string): string {
  return `${address.slice(0, 6)}...${address.slice(-4)}`;
}

export function AgentWalletSection({ botId }: AgentWalletSectionProps) {
  const authFetch = useAuthFetch();
  const t = useMessages();
  const { fundWallet } = useFundWallet();
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [wallet, setWallet] = useState<WalletData | null>(null);
  const [policies, setPolicies] = useState<PolicyData[]>([]);
  const [fetched, setFetched] = useState(false);
  const [copied, setCopied] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [refreshingBalance, setRefreshingBalance] = useState(false);
  const [topUpOpen, setTopUpOpen] = useState(false);

  const fetchWallet = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`/api/bots/${botId}/wallet`);
      if (res.ok) {
        const data = await res.json();
        setWallet(data.wallet ?? null);
        setPolicies(data.policies ?? []);
      }
    } catch {
      // Silently fail — the section will show "no wallet" state
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, [authFetch, botId]);

  // Fetch wallet data when section is first expanded
  useEffect(() => {
    if (expanded && !fetched) {
      void fetchWallet();
    }
  }, [expanded, fetched, fetchWallet]);

  const handleCopyAddress = useCallback(async () => {
    if (!wallet?.address) return;
    try {
      await navigator.clipboard.writeText(wallet.address);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API not available
    }
  }, [wallet?.address]);

  const handleTopUp = useCallback(async (chain: typeof base | typeof mainnet = base) => {
    if (!wallet?.address) return;
    try {
      await fundWallet({
        address: wallet.address,
        options: {
          chain,
          asset: "USDC",
          amount: "10",
          defaultFundingMethod: "card",
        },
      });
    } catch {
      // User may have cancelled — ignore
    } finally {
      setTimeout(() => void fetchWallet(), 3000);
    }
  }, [fundWallet, wallet?.address, fetchWallet]);

  const { openOnramp: openCoinbaseTopUp, isLoading: coinbaseLoading } = useCoinbaseOnramp({
    walletAddress: wallet?.address,
    onSuccess: () => setTimeout(() => void fetchWallet(), 3000),
  });

  const handleTogglePolicy = useCallback(async (policyId: string, currentActive: boolean) => {
    setTogglingId(policyId);
    try {
      const res = await authFetch(`/api/bots/${botId}/wallet/policies/${policyId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: !currentActive }),
      });
      if (res.ok) {
        const updated = await res.json() as PolicyData;
        setPolicies((prev) => prev.map((p) => (p.id === policyId ? updated : p)));
      }
    } catch {
      // Silently fail
    } finally {
      setTogglingId(null);
    }
  }, [authFetch, botId]);

  const handleDeletePolicy = useCallback(async (policyId: string) => {
    setDeletingId(policyId);
    try {
      const res = await authFetch(`/api/bots/${botId}/wallet/policies/${policyId}`, {
        method: "DELETE",
      });
      if (res.ok) {
        setPolicies((prev) => prev.filter((p) => p.id !== policyId));
      }
    } catch {
      // Silently fail
    } finally {
      setDeletingId(null);
    }
  }, [authFetch, botId]);

  const handleAddPolicy = useCallback(async (policy: {
    name: string;
    type: string;
    method: string;
    conditions: Array<{ field: string; operator: string; value: string | string[] }>;
    action: "ALLOW" | "DENY";
  }) => {
    const res = await authFetch(`/api/bots/${botId}/wallet/policies`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(policy),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data as Record<string, string>).error || "Failed to create policy");
    }
    const created = await res.json() as PolicyData;
    setPolicies((prev) => [...prev, created]);
    setModalOpen(false);
  }, [authFetch, botId]);

  const walletNetworks = wallet
    ? wallet.networks?.length
      ? wallet.networks
      : [
          {
            key: "base",
            label: wallet.chain ?? "Base",
            chainId: 8453,
            balances: wallet.balances ?? [],
          },
        ]
    : [];

  return (
    <>
      <GlassCard className="!p-0 overflow-hidden mt-4">
        {/* Collapsible header */}
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center justify-between p-5 cursor-pointer hover:bg-black/[0.02] transition-colors"
        >
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">Agent Wallet</span>
            {!expanded && wallet?.address && (
              <span className="text-xs text-secondary">
                {truncateAddress(wallet.address)}
              </span>
            )}
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
          <div className="border-t border-black/[0.06] px-5 pb-5 pt-4">
            {loading && !fetched ? (
              /* Loading skeleton */
              <div className="space-y-3 animate-pulse">
                <div className="h-4 w-48 bg-black/[0.06] rounded" />
                <div className="h-4 w-32 bg-black/[0.06] rounded" />
                <div className="h-12 w-full bg-black/[0.06] rounded-lg" />
              </div>
            ) : !wallet ? (
              /* No wallet state */
              <div className="text-center py-6">
                <div className="w-10 h-10 mx-auto mb-3 rounded-full bg-black/[0.04] flex items-center justify-center">
                  <svg className="w-5 h-5 text-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21 12a2.25 2.25 0 00-2.25-2.25H15a3 3 0 11-6 0H5.25A2.25 2.25 0 003 12m18 0v6a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 18v-6m18 0V9M3 12V9m18 0a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 9m18 0V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v3" />
                  </svg>
                </div>
                <p className="text-sm text-secondary">No wallet provisioned yet</p>
                <p className="text-xs text-muted mt-1">A wallet will be created during bot provisioning</p>
              </div>
            ) : (
              /* Wallet info + policies */
              <div className="space-y-4">
                {/* Wallet address */}
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs text-secondary uppercase tracking-wider mb-1">Wallet Address</p>
                    <button
                      onClick={handleCopyAddress}
                      className="flex items-center gap-2 group cursor-pointer"
                    >
                      <code className="text-sm text-foreground font-mono">
                        {truncateAddress(wallet.address)}
                      </code>
                      <svg
                        className="w-3.5 h-3.5 text-secondary group-hover:text-foreground transition-colors"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth={2}
                      >
                        {copied ? (
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        ) : (
                          <path strokeLinecap="round" strokeLinejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                        )}
                      </svg>
                      {copied && (
                        <span className="text-xs text-green-400">Copied!</span>
                      )}
                    </button>
                  </div>
                  <div className="text-right">
                    <p className="text-xs text-secondary uppercase tracking-wider mb-1">Networks</p>
                    <p className="text-sm text-foreground">{wallet.chain ?? "Base + Ethereum"}</p>
                  </div>
                </div>

                {/* Token balances */}
                <div className="space-y-3">
                  {walletNetworks.map((network) => (
                    <div key={network.key} className="space-y-2">
                      <div className="flex items-center justify-between">
                        <p className="text-xs font-medium text-secondary">{network.label}</p>
                        <p className="text-[11px] text-muted">Chain {network.chainId}</p>
                      </div>
                      <div className="grid grid-cols-3 gap-3">
                        {network.balances.map((token) => (
                          <div
                            key={`${network.key}-${token.symbol}`}
                            className="p-3 rounded-lg bg-black/[0.04] border border-black/10 text-center"
                          >
                            <p className="text-xs text-secondary mb-0.5">{token.symbol}</p>
                            <p className="text-sm text-foreground font-mono">{token.balance}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                  <div className="flex items-center justify-end gap-2">
                    <div className="relative">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setTopUpOpen(!topUpOpen)}
                        className="!min-h-0 !px-2.5 !py-1.5 text-xs"
                      >
                        {t.settingsPage.topUp}
                        <svg className={`w-3 h-3 ml-1 transition-transform ${topUpOpen ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                        </svg>
                      </Button>
                      {topUpOpen && (
                        <div className="absolute right-0 top-full mt-1 z-10 w-56 rounded-lg border border-black/10 bg-background/95 backdrop-blur-sm shadow-lg py-1">
                          <button
                            onClick={() => { setTopUpOpen(false); void handleTopUp(base); }}
                            className="w-full px-3 py-2 text-left text-sm text-foreground hover:bg-black/[0.04] transition-colors cursor-pointer"
                          >
                            {t.billingPage.usdcBuyWithMoonPay} - Base
                          </button>
                          <button
                            onClick={() => { setTopUpOpen(false); void handleTopUp(mainnet); }}
                            className="w-full px-3 py-2 text-left text-sm text-foreground hover:bg-black/[0.04] transition-colors cursor-pointer"
                          >
                            {t.billingPage.usdcBuyWithMoonPay} - Ethereum
                          </button>
                          <button
                            onClick={() => { setTopUpOpen(false); openCoinbaseTopUp(); }}
                            disabled={coinbaseLoading}
                            className="w-full px-3 py-2 text-left text-sm text-foreground hover:bg-black/[0.04] transition-colors cursor-pointer disabled:opacity-40 flex items-center justify-between"
                          >
                            <span>{t.billingPage.usdcBuyWithCoinbase}</span>
                            <span className="text-[10px] text-secondary">{t.billingPage.usdcCoinbaseUsOnly}</span>
                          </button>
                        </div>
                      )}
                    </div>
                    <button
                      onClick={async () => {
                        setRefreshingBalance(true);
                        await fetchWallet();
                        setRefreshingBalance(false);
                      }}
                      disabled={refreshingBalance}
                      className="p-2 rounded-lg hover:bg-black/[0.04] text-secondary hover:text-foreground transition-colors cursor-pointer disabled:opacity-40"
                      title="Refresh balances"
                    >
                      <svg
                        className={`w-4 h-4 ${refreshingBalance ? "animate-spin" : ""}`}
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth={2}
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                      </svg>
                    </button>
                  </div>
                </div>

                {/* Policies list */}
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <p className="text-xs text-secondary uppercase tracking-wider">Policies</p>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setModalOpen(true)}
                      className="!min-h-0 !px-2 !py-1 text-xs"
                    >
                      + Add Policy
                    </Button>
                  </div>

                  {policies.length === 0 ? (
                    <p className="text-xs text-muted py-2">No policies configured</p>
                  ) : (
                    <div className="space-y-2">
                      {policies.map((policy) => (
                        <div
                          key={policy.id}
                          className="flex items-center justify-between p-3 rounded-lg bg-black/[0.04] border border-black/10"
                        >
                          <div className="flex-1 min-w-0 mr-3">
                            <div className="flex items-center gap-2 mb-0.5">
                              <span className="text-sm text-foreground font-medium truncate">
                                {policy.name}
                              </span>
                              {policy.policy_type === "default" && (
                                <span className="text-xs px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 shrink-0">
                                  default
                                </span>
                              )}
                              <span
                                className={`text-xs px-1.5 py-0.5 rounded shrink-0 ${
                                  policy.is_active
                                    ? "bg-green-500/20 text-green-400"
                                    : "bg-black/[0.06] text-secondary"
                                }`}
                              >
                                {policy.is_active ? "active" : "inactive"}
                              </span>
                            </div>
                            <p className="text-xs text-muted truncate">
                              {describePolicyBrief(policy.policy_json)}
                            </p>
                          </div>

                          <div className="flex items-center gap-1.5 shrink-0">
                            {/* Toggle active/inactive */}
                            <button
                              onClick={() => handleTogglePolicy(policy.id, policy.is_active)}
                              disabled={togglingId === policy.id}
                              className="p-1.5 rounded-md hover:bg-black/[0.06] text-secondary hover:text-foreground transition-colors cursor-pointer disabled:opacity-40"
                              title={policy.is_active ? "Deactivate" : "Activate"}
                            >
                              {togglingId === policy.id ? (
                                <div className="w-4 h-4 rounded-full border-2 border-secondary border-t-transparent animate-spin" />
                              ) : (
                                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                  {policy.is_active ? (
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                                  ) : (
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                                  )}
                                </svg>
                              )}
                            </button>

                            {/* Delete */}
                            <button
                              onClick={() => handleDeletePolicy(policy.id)}
                              disabled={deletingId === policy.id}
                              className="p-1.5 rounded-md hover:bg-red-500/10 text-secondary hover:text-red-400 transition-colors cursor-pointer disabled:opacity-40"
                              title="Delete policy"
                            >
                              {deletingId === policy.id ? (
                                <div className="w-4 h-4 rounded-full border-2 border-secondary border-t-transparent animate-spin" />
                              ) : (
                                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                </svg>
                              )}
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </GlassCard>

      {/* Add Policy Modal */}
      <WalletPolicyModal
        botId={botId}
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSubmit={handleAddPolicy}
      />
    </>
  );
}
