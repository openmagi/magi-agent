"use client";

import { useState, useEffect, useCallback } from "react";
import { useSendTransaction, useWallets, useFundWallet } from "@privy-io/react-auth";
import {
  createPublicClient,
  http,
  encodeFunctionData,
  parseAbi,
  parseUnits,
  formatUnits,
} from "viem";
import { base } from "viem/chains";
import { QRCodeSVG } from "qrcode.react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { useCoinbaseOnramp } from "@/hooks/use-coinbase-onramp";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";
import { USDC_CONTRACT, RECEIVING_WALLET, BASE_CHAIN_ID } from "@/lib/constants";

const ERC20_ABI = parseAbi([
  "function transfer(address to, uint256 amount) returns (bool)",
  "function balanceOf(address account) view returns (uint256)",
]);

const publicClient = createPublicClient({
  chain: base,
  transport: http("https://mainnet.base.org"),
});

const PRESET_AMOUNTS = [
  { label: "$5", dollars: 5 },
  { label: "$10", dollars: 10 },
  { label: "$25", dollars: 25 },
];

type PaymentStep = "idle" | "confirming" | "verifying" | "success" | "error";

interface UsdcPaymentProps {
  onCredited: (amountCents: number) => void;
}

export function UsdcPayment({ onCredited }: UsdcPaymentProps) {
  const authFetch = useAuthFetch();
  const t = useMessages();
  const { sendTransaction } = useSendTransaction();
  const { wallets, ready: walletsReady } = useWallets();
  const { fundWallet } = useFundWallet();

  const [step, setStep] = useState<PaymentStep>("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [creditedAmount, setCreditedAmount] = useState(0);
  const [usdcBalance, setUsdcBalance] = useState<string | null>(null);
  const [customAmount, setCustomAmount] = useState("");
  const [manualTxHash, setManualTxHash] = useState("");
  const [showFundWallet, setShowFundWallet] = useState(false);
  const [copied, setCopied] = useState(false);
  const [platformCopied, setPlatformCopied] = useState(false);

  const embeddedWallet = wallets.find((w) => w.walletClientType === "privy");

  const fetchBalance = useCallback(async () => {
    if (!embeddedWallet?.address) return;
    try {
      const bal = await publicClient.readContract({
        address: USDC_CONTRACT,
        abi: ERC20_ABI,
        functionName: "balanceOf",
        args: [embeddedWallet.address as `0x${string}`],
      });
      setUsdcBalance(formatUnits(bal, 6));
    } catch {
      setUsdcBalance(null);
    }
  }, [embeddedWallet?.address]);

  useEffect(() => {
    if (walletsReady && embeddedWallet) {
      fetchBalance();
    }
  }, [walletsReady, embeddedWallet, fetchBalance]);

  async function handlePayUsdc(dollars: number) {
    if (!embeddedWallet) return;

    setStep("confirming");
    setErrorMessage("");

    try {
      const transferData = encodeFunctionData({
        abi: ERC20_ABI,
        functionName: "transfer",
        args: [
          RECEIVING_WALLET as `0x${string}`,
          parseUnits(dollars.toString(), 6),
        ],
      });

      const receipt = await sendTransaction({
        to: USDC_CONTRACT,
        chainId: BASE_CHAIN_ID,
        data: transferData,
      });

      const txHash =
        (receipt as Record<string, string>).transactionHash ??
        (receipt as Record<string, string>).hash;

      if (!txHash) {
        throw new Error("No transaction hash returned");
      }

      await claimCredits(txHash);
    } catch (err) {
      setStep("error");
      setErrorMessage(
        err instanceof Error ? err.message : "Payment failed",
      );
    }
  }

  async function claimCredits(txHash: string) {
    setStep("verifying");

    try {
      const res = await authFetch("/api/credits/usdc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ txHash }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || "Verification failed");
      }

      setCreditedAmount(data.amountCents);
      setStep("success");
      onCredited(data.amountCents);
      fetchBalance();
    } catch (err) {
      setStep("error");
      setErrorMessage(
        err instanceof Error ? err.message : "Verification failed",
      );
    }
  }

  async function handleManualClaim() {
    if (!/^0x[a-fA-F0-9]{64}$/.test(manualTxHash)) {
      setStep("error");
      setErrorMessage(t.billingPage.usdcInvalidHash);
      return;
    }
    await claimCredits(manualTxHash);
  }

  function reset() {
    setStep("idle");
    setErrorMessage("");
    setCreditedAmount(0);
    setManualTxHash("");
  }

  async function copyAddress() {
    if (!embeddedWallet?.address) return;
    await navigator.clipboard.writeText(embeddedWallet.address);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  async function copyPlatformAddress() {
    await navigator.clipboard.writeText(RECEIVING_WALLET);
    setPlatformCopied(true);
    setTimeout(() => setPlatformCopied(false), 2000);
  }

  async function handleFundWithMoonPay() {
    if (!embeddedWallet?.address) return;
    try {
      await fundWallet({
        address: embeddedWallet.address,
        options: {
          chain: base,
          asset: "USDC",
          amount: "10",
          defaultFundingMethod: "card",
        },
      });
    } catch {
      // User may have cancelled — ignore
    } finally {
      // Refresh balance after funding flow closes
      setTimeout(() => fetchBalance(), 3000);
    }
  }

  const { openOnramp, isLoading: coinbaseLoading, error: coinbaseError } = useCoinbaseOnramp({
    walletAddress: embeddedWallet?.address,
    onSuccess: () => setTimeout(() => fetchBalance(), 3000),
  });

  // Success state
  if (step === "success") {
    return (
      <div className="text-center py-6">
        <div className="text-3xl mb-2">&#x2713;</div>
        <p className="text-emerald-400 font-semibold text-lg mb-1">
          {t.billingPage.usdcSuccess}
        </p>
        <p className="text-secondary text-sm">
          +${(creditedAmount / 100).toFixed(2)} {t.billingPage.usdcCredited}
        </p>
        <Button variant="secondary" size="sm" className="mt-4" onClick={reset}>
          {t.billingPage.buyCredits}
        </Button>
      </div>
    );
  }

  // Confirming / Verifying state
  if (step === "confirming" || step === "verifying") {
    return (
      <div className="text-center py-6">
        <div className="animate-spin inline-block w-6 h-6 border-2 border-primary border-t-transparent rounded-full mb-3" />
        <p className="text-secondary text-sm">
          {step === "confirming"
            ? t.billingPage.usdcConfirming
            : t.billingPage.usdcVerifying}
        </p>
      </div>
    );
  }

  const balanceNum = usdcBalance ? parseFloat(usdcBalance) : 0;

  return (
    <div>
      {/* Wallet balance + fund */}
      {walletsReady && embeddedWallet && usdcBalance !== null && (
        <div className="mb-4">
          <div className="flex items-center justify-between px-3 py-2 rounded-lg bg-black/[0.04] border border-black/10">
            <span className="text-sm text-secondary">
              {t.billingPage.usdcBalance}
            </span>
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-foreground">
                {parseFloat(usdcBalance).toFixed(2)} USDC
              </span>
              <button
                type="button"
                onClick={() => setShowFundWallet(!showFundWallet)}
                className="text-xs text-primary hover:text-primary/80 font-medium transition-colors"
              >
                {t.billingPage.usdcAddFunds}
              </button>
            </div>
          </div>

          {/* Fund wallet panel */}
          {showFundWallet && (
            <div className="mt-3 p-4 rounded-lg bg-black/[0.04] border border-black/10">
              <div className="flex flex-col sm:flex-row gap-4">
                {/* QR Code */}
                <div className="flex-shrink-0 flex justify-center">
                  <div className="p-3 bg-white rounded-xl">
                    <QRCodeSVG
                      value={embeddedWallet.address}
                      size={120}
                      level="M"
                    />
                  </div>
                </div>

                <div className="flex-1 min-w-0">
                  <p className="text-xs text-secondary mb-2">
                    {t.billingPage.usdcFundDesc}
                  </p>

                  {/* Wallet address + copy */}
                  <div className="flex items-center gap-2 mb-3">
                    <code
                      className="flex-1 min-w-0 truncate text-xs font-mono text-foreground bg-black/[0.04] border border-black/10 rounded-md px-2.5 py-1.5 cursor-pointer hover:border-primary/30 transition-colors"
                      onClick={copyAddress}
                      title={embeddedWallet.address}
                    >
                      {embeddedWallet.address}
                    </code>
                    <button
                      type="button"
                      onClick={copyAddress}
                      className="flex-shrink-0 text-xs px-2.5 py-1.5 rounded-md border border-black/10 text-secondary hover:text-foreground hover:border-black/[0.12] transition-colors"
                    >
                      {copied ? t.billingPage.usdcCopied : t.billingPage.usdcCopy}
                    </button>
                  </div>

                  {/* Buy with card */}
                  <div>
                    <p className="text-xs text-secondary mb-1.5">{t.billingPage.usdcBuyWithCard}</p>
                    <div className="flex gap-2 flex-wrap">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={handleFundWithMoonPay}
                      >
                        MoonPay
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => openOnramp()}
                        disabled={coinbaseLoading}
                      >
                        {coinbaseLoading ? "..." : "Coinbase"}
                        <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400">
                          {t.billingPage.usdcCoinbaseUsOnly}
                        </span>
                      </Button>
                    </div>
                    {coinbaseError && (
                      <p className="text-xs text-red-400 mt-1.5">{coinbaseError}</p>
                    )}
                  </div>
                </div>
              </div>

              <p className="text-xs text-muted mt-3">
                {t.billingPage.usdcFundNetwork}
              </p>
            </div>
          )}
        </div>
      )}

      {!walletsReady || !embeddedWallet ? (
        <p className="text-sm text-secondary py-4">
          {t.billingPage.usdcNoWallet}
        </p>
      ) : (
        <>
          {/* Error banner */}
          {step === "error" && errorMessage && (
            <div className="mb-4 px-3 py-2 rounded-lg border border-red-500/20 text-red-400 text-sm">
              {errorMessage}
            </div>
          )}

          {/* ── Pay from Wallet ── */}
          <div className="flex items-center gap-3 mb-3">
            <div className="flex-1 h-px bg-black/[0.06]" />
            <span className="text-xs font-medium text-secondary uppercase tracking-wide">
              {t.billingPage.usdcPayFromWallet}
            </span>
            <div className="flex-1 h-px bg-black/[0.06]" />
          </div>

          {/* Preset amounts */}
          <div className="flex flex-wrap gap-3">
            {PRESET_AMOUNTS.map((preset) => (
              <Button
                key={preset.dollars}
                variant="cta"
                size="md"
                onClick={() => handlePayUsdc(preset.dollars)}
                disabled={balanceNum < preset.dollars}
              >
                {preset.label}
              </Button>
            ))}
          </div>

          {/* Custom amount */}
          <div className="mt-4 pt-4 border-t border-black/[0.08]">
            <p className="text-sm text-secondary mb-2">
              {t.billingPage.customAmount}
            </p>
            <div className="flex gap-3 items-center">
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-secondary">
                  $
                </span>
                <input
                  type="number"
                  min="1"
                  max="500"
                  step="1"
                  placeholder="50"
                  value={customAmount}
                  onChange={(e) => setCustomAmount(e.target.value)}
                  className="w-28 pl-7 pr-3 py-2 rounded-lg bg-black/[0.04] border border-black/10 text-foreground text-sm focus:outline-none focus:border-primary/50 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                />
              </div>
              <Button
                variant="cta"
                size="md"
                onClick={() => {
                  const d = parseFloat(customAmount);
                  if (d >= 1 && d <= 500) handlePayUsdc(d);
                }}
                disabled={
                  !customAmount ||
                  parseFloat(customAmount) < 1 ||
                  parseFloat(customAmount) > 500 ||
                  balanceNum < parseFloat(customAmount || "0")
                }
              >
                {t.billingPage.buyCustom}
              </Button>
            </div>
          </div>

          {/* ── Direct Transfer ── */}
          <div className="flex items-center gap-3 mt-6 mb-3">
            <div className="flex-1 h-px bg-black/[0.06]" />
            <span className="text-xs font-medium text-secondary uppercase tracking-wide">
              {t.billingPage.usdcDirectTransfer}
            </span>
            <div className="flex-1 h-px bg-black/[0.06]" />
          </div>

          <div className="p-4 rounded-lg bg-black/[0.04] border border-black/10">
            <p className="text-sm text-secondary mb-4">
              {t.billingPage.usdcDirectTransferDesc}
            </p>

            {/* Platform wallet address + QR */}
            <div className="flex flex-col sm:flex-row gap-4 mb-4">
              <div className="flex-shrink-0 flex justify-center">
                <div className="p-3 bg-white rounded-xl">
                  <QRCodeSVG value={RECEIVING_WALLET} size={100} level="M" />
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-muted mb-1.5">
                  {t.billingPage.usdcPlatformWallet}
                </p>
                <div className="flex items-center gap-2">
                  <code
                    className="flex-1 min-w-0 truncate text-xs font-mono text-foreground bg-black/[0.04] border border-black/10 rounded-md px-2.5 py-1.5 cursor-pointer hover:border-primary/30 transition-colors"
                    onClick={copyPlatformAddress}
                    title={RECEIVING_WALLET}
                  >
                    {RECEIVING_WALLET}
                  </code>
                  <button
                    type="button"
                    onClick={copyPlatformAddress}
                    className="flex-shrink-0 text-xs px-2.5 py-1.5 rounded-md border border-black/10 text-secondary hover:text-foreground hover:border-black/[0.12] transition-colors"
                  >
                    {platformCopied ? t.billingPage.usdcCopied : t.billingPage.usdcCopy}
                  </button>
                </div>
              </div>
            </div>

            {/* Network warning */}
            <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/20 mb-4">
              <span className="text-amber-400 text-sm mt-0.5">&#x26A0;</span>
              <p className="text-xs text-amber-400">
                {t.billingPage.usdcDirectNetwork}
              </p>
            </div>

            {/* TX Hash input + Claim */}
            <div className="flex gap-2">
              <input
                type="text"
                placeholder={t.billingPage.usdcManualPlaceholder}
                value={manualTxHash}
                onChange={(e) => setManualTxHash(e.target.value)}
                className="flex-1 px-3 py-2 rounded-lg bg-black/[0.04] border border-black/10 text-foreground text-xs font-mono focus:outline-none focus:border-primary/50"
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={handleManualClaim}
                disabled={!manualTxHash}
              >
                {t.billingPage.usdcManualClaim}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
