"use client";

import { useState, useCallback } from "react";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { useAuthFetch } from "@/hooks/use-auth-fetch";

type PolicyPreset = "ai_describe" | "spending_limit" | "chain_restriction" | "contract_allowlist" | "custom";

interface WalletPolicyModalProps {
  botId: string;
  open: boolean;
  onClose: () => void;
  onSubmit: (policy: {
    name: string;
    type: string;
    method: string;
    conditions: Array<{ field: string; operator: string; value: string | string[] }>;
    action: "ALLOW" | "DENY";
  }) => Promise<void>;
}

const PRESET_OPTIONS: Array<{ id: PolicyPreset; label: string; description: string }> = [
  { id: "ai_describe", label: "Describe with AI", description: "Describe in plain English" },
  { id: "spending_limit", label: "Spending Limit", description: "Set maximum ETH per transaction" },
  { id: "chain_restriction", label: "Chain Restriction", description: "Restrict to specific chain" },
  { id: "contract_allowlist", label: "Contract Allowlist", description: "Only allow specific contracts" },
  { id: "custom", label: "Custom JSON", description: "Define custom policy conditions" },
];

const CHAIN_OPTIONS = [
  { id: "8453", label: "Base (Mainnet)" },
  { id: "84532", label: "Base Sepolia (Testnet)" },
  { id: "1", label: "Ethereum Mainnet" },
  { id: "11155111", label: "Ethereum Sepolia" },
];

export function WalletPolicyModal({ botId, open, onClose, onSubmit }: WalletPolicyModalProps) {
  const authFetch = useAuthFetch();
  const [preset, setPreset] = useState<PolicyPreset>("ai_describe");
  const [name, setName] = useState("");
  const [spendingLimit, setSpendingLimit] = useState("");
  const [chainId, setChainId] = useState("8453");
  const [contractAddresses, setContractAddresses] = useState("");
  const [customJson, setCustomJson] = useState(`[
  {
    "field_source": "ethereum_transaction",
    "field": "value",
    "operator": "lte",
    "value": "50000000000000000"
  }
]`);
  const [aiDescription, setAiDescription] = useState("");
  const [aiGenerated, setAiGenerated] = useState<{
    name: string;
    conditions: Array<{ field: string; operator: string; value: string | string[] }>;
    action?: string;
    note?: string;
  } | null>(null);
  const [generating, setGenerating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resetForm = useCallback(() => {
    setPreset("ai_describe");
    setName("");
    setSpendingLimit("");
    setChainId("8453");
    setContractAddresses("");
    setCustomJson("[]");
    setAiDescription("");
    setAiGenerated(null);
    setError(null);
  }, []);

  const handleClose = useCallback(() => {
    resetForm();
    onClose();
  }, [onClose, resetForm]);

  const handleGenerate = useCallback(async () => {
    if (!aiDescription.trim()) {
      setError("Describe what your policy should do");
      return;
    }
    setError(null);
    setGenerating(true);
    setAiGenerated(null);
    try {
      const res = await authFetch(`/api/bots/${botId}/wallet/policies/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: aiDescription }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error((data as Record<string, string>).error || "Failed to generate policy");
      }
      const data = await res.json() as { name: string; conditions: Array<{ field: string; operator: string; value: string | string[] }>; action?: string; note?: string };
      setAiGenerated(data);
      if (!name.trim()) {
        setName(data.name);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setGenerating(false);
    }
  }, [authFetch, botId, aiDescription, name]);

  const handleSubmit = useCallback(async () => {
    setError(null);

    const policyName = name.trim() || `${PRESET_OPTIONS.find((p) => p.id === preset)?.label ?? "Policy"}`;

    let conditions: Array<{ field: string; operator: string; value: string | string[] }> = [];

    if (preset === "ai_describe") {
      if (!aiGenerated) {
        setError("Generate conditions first by clicking the Generate button");
        return;
      }
      conditions = aiGenerated.conditions;
    } else if (preset === "spending_limit") {
      const limit = parseFloat(spendingLimit);
      if (isNaN(limit) || limit <= 0) {
        setError("Enter a valid spending limit greater than 0");
        return;
      }
      conditions = [{ field: "value", operator: "lte", value: String(limit * 1e18) }];
    } else if (preset === "chain_restriction") {
      conditions = [{ field: "chain_id", operator: "eq", value: chainId }];
    } else if (preset === "contract_allowlist") {
      const addresses = contractAddresses
        .split(/[\n,]/)
        .map((a) => a.trim())
        .filter((a) => a.length > 0);
      if (addresses.length === 0) {
        setError("Enter at least one contract address");
        return;
      }
      conditions = [{ field: "to", operator: "in", value: addresses }];
    } else {
      try {
        conditions = JSON.parse(customJson) as typeof conditions;
        if (!Array.isArray(conditions)) {
          setError("Custom JSON must be an array of conditions");
          return;
        }
      } catch {
        setError("Invalid JSON format");
        return;
      }
    }

    const action = (preset === "ai_describe" && aiGenerated?.action === "DENY") ? "DENY" : "ALLOW";

    setSubmitting(true);
    try {
      await onSubmit({
        name: policyName,
        type: preset === "ai_describe" ? "ai_generated" : preset,
        method: "eth_sendTransaction",
        conditions,
        action: action as "ALLOW" | "DENY",
      });
      resetForm();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create policy");
    } finally {
      setSubmitting(false);
    }
  }, [name, preset, spendingLimit, chainId, contractAddresses, customJson, aiGenerated, onSubmit, resetForm]);

  return (
    <Modal open={open} onClose={handleClose}>
      <div className="p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-lg font-semibold text-foreground">Add Policy</h3>
          <button
            onClick={handleClose}
            className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-black/[0.06] text-secondary hover:text-foreground transition-colors cursor-pointer"
            aria-label="Close"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Policy name */}
        <div className="mb-4">
          <label className="block text-sm text-secondary mb-1.5">Policy Name (optional)</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Max 0.1 ETH per tx"
            className="w-full px-3 py-2 rounded-lg bg-black/[0.04] border border-black/10 text-sm text-foreground placeholder:text-secondary focus:outline-none focus:border-primary"
          />
        </div>

        {/* Policy guide */}
        <div className="mb-4 p-3 rounded-lg bg-black/[0.03] border border-black/[0.06] text-xs text-secondary leading-relaxed">
          <p className="mb-1.5">Policies are enforced <span className="text-foreground font-medium">per transaction</span>. You can restrict:</p>
          <ul className="list-disc list-inside space-y-0.5 text-muted">
            <li>Max ETH value per transaction</li>
            <li>Allowed/blocked chains (Base, Ethereum, etc.)</li>
            <li>Allowed/blocked recipient addresses or contracts</li>
            <li>Time windows (e.g. only allow after a specific date)</li>
          </ul>
          <p className="mt-1.5 text-secondary/50">Daily, weekly, or cumulative spending limits are not supported.</p>
        </div>

        {/* Preset selection */}
        <div className="mb-4">
          <label className="block text-sm text-secondary mb-1.5">Policy Type</label>
          <div className="grid grid-cols-2 gap-2">
            {PRESET_OPTIONS.map((option) => (
              <button
                key={option.id}
                onClick={() => { setPreset(option.id); setAiGenerated(null); setError(null); }}
                className={`text-left p-3 rounded-lg border transition-all cursor-pointer ${
                  option.id === "ai_describe" ? "col-span-2" : ""
                } ${
                  preset === option.id
                    ? "border-primary/40 bg-primary/10"
                    : "border-black/10 bg-black/[0.04] hover:border-black/[0.12]"
                }`}
              >
                <span className="block text-sm font-medium text-foreground">
                  {option.id === "ai_describe" && (
                    <svg className="w-3.5 h-3.5 inline-block mr-1.5 -mt-0.5 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" />
                    </svg>
                  )}
                  {option.label}
                </span>
                <span className="block text-xs text-secondary mt-0.5">{option.description}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Preset-specific fields */}
        <div className="mb-5">
          {preset === "ai_describe" && (
            <div>
              <label className="block text-sm text-secondary mb-1.5">Describe your policy</label>
              {!aiGenerated ? (
                /* Input state — textarea with inline generate button */
                <div>
                  <div className="relative">
                    <textarea
                      value={aiDescription}
                      onChange={(e) => setAiDescription(e.target.value)}
                      placeholder={"e.g. Allow up to 0.5 ETH per transaction, only on Base chain"}
                      rows={3}
                      disabled={generating}
                      className="w-full px-3 py-2 pb-10 rounded-lg bg-black/[0.04] border border-black/10 text-sm text-foreground placeholder:text-secondary focus:outline-none focus:border-primary resize-none disabled:opacity-50"
                    />
                    <button
                      onClick={handleGenerate}
                      disabled={generating || !aiDescription.trim()}
                      className="absolute right-2 bottom-2 flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary/20 hover:bg-primary/30 text-primary text-xs font-medium transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {generating ? (
                        <>
                          <span className="w-3 h-3 rounded-full border-2 border-primary/40 border-t-primary animate-spin" />
                          Generating...
                        </>
                      ) : (
                        <>
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" />
                          </svg>
                          Generate
                        </>
                      )}
                    </button>
                  </div>
                </div>
              ) : (
                /* Result state — generated conditions preview */
                <div className="rounded-lg bg-black/[0.04] border border-green-500/30 overflow-hidden">
                  <div className="flex items-center justify-between px-3 py-2 border-b border-black/[0.06]">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-green-400 font-medium">Generated conditions</span>
                      {aiGenerated.action === "DENY" && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-red-500/20 text-red-400">DENY</span>
                      )}
                    </div>
                    <button
                      onClick={() => setAiGenerated(null)}
                      className="text-xs text-secondary hover:text-foreground transition-colors cursor-pointer"
                    >
                      Edit description
                    </button>
                  </div>
                  <pre className="px-3 py-2.5 text-xs text-foreground font-mono whitespace-pre-wrap overflow-x-auto max-h-40 overflow-y-auto">
                    {JSON.stringify(aiGenerated.conditions, null, 2)}
                  </pre>
                  {aiGenerated.note && (
                    <div className="px-3 py-2 border-t border-black/[0.06]">
                      <p className="text-xs text-amber-400/80">{aiGenerated.note}</p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {preset === "spending_limit" && (
            <div>
              <label className="block text-sm text-secondary mb-1.5">Max ETH per Transaction</label>
              <input
                type="number"
                step="0.001"
                min="0"
                value={spendingLimit}
                onChange={(e) => setSpendingLimit(e.target.value)}
                placeholder="0.1"
                className="w-full px-3 py-2 rounded-lg bg-black/[0.04] border border-black/10 text-sm text-foreground placeholder:text-secondary focus:outline-none focus:border-primary"
              />
            </div>
          )}

          {preset === "chain_restriction" && (
            <div>
              <label className="block text-sm text-secondary mb-1.5">Chain</label>
              <select
                value={chainId}
                onChange={(e) => setChainId(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-black/[0.04] border border-black/10 text-sm text-foreground focus:outline-none focus:border-primary"
              >
                {CHAIN_OPTIONS.map((chain) => (
                  <option key={chain.id} value={chain.id}>
                    {chain.label}
                  </option>
                ))}
              </select>
            </div>
          )}

          {preset === "contract_allowlist" && (
            <div>
              <label className="block text-sm text-secondary mb-1.5">
                Contract Addresses (one per line or comma-separated)
              </label>
              <textarea
                value={contractAddresses}
                onChange={(e) => setContractAddresses(e.target.value)}
                placeholder={"0x1234...abcd\n0x5678...efgh"}
                rows={4}
                className="w-full px-3 py-2 rounded-lg bg-black/[0.04] border border-black/10 text-sm text-foreground placeholder:text-secondary focus:outline-none focus:border-primary resize-none"
              />
            </div>
          )}

          {preset === "custom" && (
            <div>
              <label className="block text-sm text-secondary mb-1.5">
                Conditions JSON
              </label>
              <textarea
                value={customJson}
                onChange={(e) => setCustomJson(e.target.value)}
                rows={8}
                className="w-full px-3 py-2 rounded-lg bg-black/[0.04] border border-black/10 text-sm text-foreground placeholder:text-secondary focus:outline-none focus:border-primary font-mono text-xs resize-none"
              />
              <p className="text-xs text-muted mt-1.5">
                Each condition: <code className="text-secondary">field_source</code>, <code className="text-secondary">field</code> (value, to, chain_id), <code className="text-secondary">operator</code> (eq, lte, in), <code className="text-secondary">value</code>
              </p>
            </div>
          )}
        </div>

        {/* Error */}
        {error && (
          <div className="mb-4 text-xs px-3 py-2 rounded-lg bg-red-500/10 text-red-400 border border-red-500/20">
            {error}
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-3">
          <Button variant="ghost" size="sm" onClick={handleClose} className="flex-1">
            Cancel
          </Button>
          <Button variant="primary" size="sm" onClick={handleSubmit} disabled={submitting} className="flex-1">
            {submitting ? "Creating..." : "Create Policy"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
