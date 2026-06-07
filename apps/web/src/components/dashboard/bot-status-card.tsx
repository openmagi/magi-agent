"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { GlassCard } from "@/components/ui/glass-card";
import { StatusBadge } from "@/components/ui/badge";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";
import type { Messages } from "@/lib/i18n/locales/en";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { useBotStatus } from "@/hooks/use-bot-status";
import { trackBotRetry, trackEvent } from "@/lib/analytics";
import { normalizeTelegramPhoneInput } from "@/lib/telegram/phone";
import type { BotCardData } from "@/types/entities";
import { AgentWalletSection } from "./agent-wallet-section";
import { Modal } from "@/components/ui/modal";

interface BotStatusCardProps {
  bot: BotCardData;
  subscriptionPlan?: string;
}

/* ---------- Sub-components ---------- */

function PendingTelegramSection({
  bot,
  t,
  onTelegramOpen,
}: {
  bot: BotCardData;
  t: Messages;
  onTelegramOpen: () => void;
}) {
  return (
    <div className="mb-6 p-4 rounded-xl bg-primary/10 border border-primary/20">
      <div className="space-y-3">
        <div>
          <p className="text-sm font-medium text-primary-light">
            {t.onboarding.sendStartTitle}
          </p>
          <p className="text-xs text-secondary mt-1">
            {t.onboarding.sendStartDesc}
          </p>
        </div>
        {bot.telegram_bot_username && (
          <a
            href={`https://t.me/${bot.telegram_bot_username}?start=1`}
            target="_blank"
            rel="noopener noreferrer"
            onClick={onTelegramOpen}
          >
            <Button variant="primary" size="sm" className="w-full">
              {t.onboarding.sendStartButton}
            </Button>
          </a>
        )}
      </div>
    </div>
  );
}

function ProvisioningSection({
  restarting,
  progressPct,
  provisionTimedOut,
  isActive,
  bot,
  t,
}: {
  restarting: boolean;
  progressPct: number;
  provisionTimedOut: boolean;
  isActive: boolean;
  bot: BotCardData;
  t: Messages;
}) {
  return (
    <div className="mb-6 p-4 rounded-xl bg-amber-500/10 border border-amber-500/20">
      <div className="space-y-3">
        <div className="flex items-center gap-3">
          <div className="h-4 w-4 rounded-full border-2 border-amber-400 border-t-transparent animate-spin shrink-0" />
          <div>
            <p className="text-sm font-medium text-amber-300">
              {restarting ? t.botCard.telegramSetupUpdating : t.botCard.provisioning}
            </p>
            {!restarting && (
              <p className="text-xs text-amber-300/70 mt-0.5">
                {t.botCard.provisioningDesc}
              </p>
            )}
          </div>
        </div>
        <div className="w-full h-1.5 bg-black/5 rounded-full overflow-hidden">
          <div
            className="h-full bg-amber-400/60 rounded-full transition-all duration-1000 ease-linear"
            style={{ width: `${Math.round(progressPct)}%` }}
          />
        </div>
        {provisionTimedOut && !isActive && (
          <div className="pt-1 space-y-2">
            <p className="text-xs text-amber-300/70">
              {t.botCard.provisioningRetry}
            </p>
            {bot.telegram_bot_username && !bot.telegram_owner_id && (
              <a
                href={`https://t.me/${bot.telegram_bot_username}?start=1`}
                target="_blank"
                rel="noopener noreferrer"
              >
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-full text-amber-300 border-amber-500/30"
                >
                  {t.onboarding.sendStartButton}
                </Button>
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ErrorSection({
  t,
  onRetry,
}: {
  t: Messages;
  onRetry: () => void;
}) {
  return (
    <div className="mb-6 p-4 rounded-xl bg-red-500/10 border border-red-500/20">
      <div className="space-y-3">
        <div>
          <p className="text-sm font-medium text-red-400">
            {t.botCard.errorTitle}
          </p>
          <p className="text-xs text-secondary mt-1">
            {t.botCard.errorDesc}
          </p>
        </div>
        <Button variant="primary" size="sm" className="w-full" onClick={onRetry}>
          {t.botCard.retryButton}
        </Button>
      </div>
    </div>
  );
}

function DegradedSection({
  t,
  healthStatus,
  onRetry,
  retrying,
}: {
  t: Messages;
  healthStatus: string;
  onRetry: () => void;
  retrying: boolean;
}) {
  const isRecovering = healthStatus === "recovering";
  return (
    <div className="mb-6 p-4 rounded-xl bg-amber-500/10 border border-amber-500/20">
      <div className="space-y-3">
        <div>
          <p className="text-sm font-medium text-amber-300">
            {t.botCard.degradedTitle}
          </p>
          <p className="text-xs text-secondary mt-1">
            {isRecovering ? t.botCard.degradedRecovering : t.botCard.degradedDesc}
          </p>
        </div>
        {!isRecovering && (
          <Button variant="ghost" size="sm" className="w-full text-amber-300 border-amber-500/30" onClick={onRetry} disabled={retrying}>
            {retrying ? t.botCard.provisioning : t.botCard.retryButton}
          </Button>
        )}
      </div>
    </div>
  );
}

function StoppedSection({
  t,
  subscribing,
  onSubscribe,
  error,
}: {
  t: Messages;
  subscribing: boolean;
  onSubscribe: () => void;
  error: string | null;
}) {
  return (
    <div className="mb-6 p-4 rounded-xl bg-amber-500/10 border border-amber-500/20">
      <div className="space-y-3">
        <div>
          <p className="text-sm font-medium text-amber-300">
            {t.botCard.stoppedTitle}
          </p>
          <p className="text-xs text-secondary mt-1">
            {t.botCard.stoppedDesc}
          </p>
        </div>
        <Button
          variant="cta"
          size="sm"
          className="w-full"
          onClick={onSubscribe}
          disabled={subscribing}
        >
          {subscribing ? t.botCard.subscribing : t.botCard.subscribeButton}
        </Button>
        {error && (
          <p className="text-xs text-red-400">{error}</p>
        )}
      </div>
    </div>
  );
}

function BotDetailsSection({
  bot,
  t,
}: {
  bot: BotCardData;
  t: Messages;
}) {
  return (
    <div className="mt-6 pt-5 border-t border-gray-200">
      <div className="flex flex-col sm:flex-row gap-3">
        <Link href={`/dashboard/${bot.id}/cli`}>
          <Button variant="ghost" size="sm">
            {t.dashboard.cli}
          </Button>
        </Link>
        <Link href={`/dashboard/${bot.id}/settings`}>
          <Button variant="ghost" size="sm">
            {t.dashboard.settings}
          </Button>
        </Link>
        <Link href={`/dashboard/${bot.id}/usage`}>
          <Button variant="ghost" size="sm">
            {t.dashboard.usage}
          </Button>
        </Link>
        <Link href="/dashboard/billing">
          <Button variant="ghost" size="sm">
            {t.dashboard.billing}
          </Button>
        </Link>
      </div>
    </div>
  );
}

/* ---------- Main component ---------- */

export function BotStatusCard({ bot, subscriptionPlan }: BotStatusCardProps) {
  const authFetch = useAuthFetch();
  const t = useMessages();

  const {
    currentStatus,
    healthStatus,
    needsTelegramSetup,
    restarting,
    progressPct,
    provisionTimedOut,
    telegramOpened,
    handleTelegramOpen,
    startProvisioning,
  } = useBotStatus(
    bot.id,
    bot.status,
  );

  const [retrying, setRetrying] = useState(false);
  const [subscribing, setSubscribing] = useState(false);
  const [subscribeError, setSubscribeError] = useState<string | null>(null);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState(bot.name);
  const [savingName, setSavingName] = useState(false);

  const [showTelegramConnect, setShowTelegramConnect] = useState(false);
  const [telegramPhase, setTelegramPhase] = useState<"token" | "start">("token");
  const [telegramToken, setTelegramToken] = useState("");
  const [telegramBotUsername, setTelegramBotUsername] = useState<string | null>(null);
  const [telegramValidating, setTelegramValidating] = useState(false);
  const [telegramError, setTelegramError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  // Easy mode state
  const [connectMode, setConnectMode] = useState<"easy" | "advanced">("easy");
  const [easyStep, setEasyStep] = useState<"phone" | "code" | "2fa" | "creating">("phone");
  const [countryCode, setCountryCode] = useState("+82");
  const [phoneNumber, setPhoneNumber] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [verifyCode, setVerifyCode] = useState("");
  const [twoFaPassword, setTwoFaPassword] = useState("");
  const [easyLoading, setEasyLoading] = useState(false);
  const [easyError, setEasyError] = useState<string | null>(null);
  const [resendTimer, setResendTimer] = useState(0);
  const codeInputRef = useRef<HTMLInputElement>(null);

  // Discord state
  const [showDiscordGuide, setShowDiscordGuide] = useState(false);
  const [discordToken, setDiscordToken] = useState("");
  const [discordConnecting, setDiscordConnecting] = useState(false);
  const [discordInviteUrl, setDiscordInviteUrl] = useState<string | null>(null);
  const [discordError, setDiscordError] = useState<string | null>(null);

  const handleDiscordSubmitToken = useCallback(async () => {
    if (!discordToken.trim()) return;
    setDiscordConnecting(true);
    setDiscordError(null);
    try {
      const res = await authFetch(`/api/bots/${bot.id}/connect-discord`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discordBotToken: discordToken.trim() }),
      });
      const data = await res.json();
      if (res.ok) {
        setDiscordInviteUrl(data.inviteUrl);
      } else {
        setDiscordError(data.error || "Failed to connect");
      }
    } catch {
      setDiscordError("Connection failed");
    }
    setDiscordConnecting(false);
  }, [discordToken, authFetch, bot.id]);

  const handleDiscordDisconnect = useCallback(async () => {
    try {
      await authFetch(`/api/bots/${bot.id}/disconnect-discord`, { method: "POST" });
      window.location.reload();
    } catch { /* ignore */ }
  }, [authFetch, bot.id]);

  const handleSaveName = useCallback(async () => {
    const trimmed = nameInput.trim();
    if (!trimmed || trimmed === bot.name) {
      setEditingName(false);
      setNameInput(bot.name);
      return;
    }
    setSavingName(true);
    try {
      const res = await authFetch(`/api/bots/${bot.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmed }),
      });
      if (res.ok) {
        window.location.reload();
      }
    } catch {
      /* ignore */
    } finally {
      setSavingName(false);
      setEditingName(false);
    }
  }, [nameInput, bot.name, bot.id, authFetch]);

  // Phase 1: Validate token with Telegram API
  const handleValidateToken = useCallback(async () => {
    if (!telegramToken.trim()) return;
    setTelegramValidating(true);
    setTelegramError(null);
    try {
      const res = await fetch(`https://api.telegram.org/bot${telegramToken.trim()}/getMe`);
      const data = await res.json() as { ok: boolean; result?: { username?: string } };
      if (!data.ok) {
        setTelegramError("Invalid bot token");
        return;
      }
      setTelegramBotUsername(data.result?.username ?? null);
      setTelegramPhase("start");
    } catch {
      setTelegramError("Failed to validate token");
    } finally {
      setTelegramValidating(false);
    }
  }, [telegramToken]);

  // Phase 2: Connect telegram to bot
  const handleConnectTelegram = useCallback(async () => {
    setTelegramValidating(true);
    setTelegramError(null);
    try {
      const connectRes = await authFetch(`/api/bots/${bot.id}/connect-telegram`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          telegramBotToken: telegramToken,
          telegramBotUsername: telegramBotUsername,
        }),
      });
      if (!connectRes.ok) {
        const err = await connectRes.json().catch(() => ({}));
        setTelegramError((err as Record<string, string>).error || "Failed to connect");
        return;
      }
      setShowTelegramConnect(false);
      setTelegramToken("");
      setTelegramPhase("token");
      setTelegramBotUsername(null);
      window.location.reload();
    } catch {
      setTelegramError("Network error");
    } finally {
      setTelegramValidating(false);
    }
  }, [telegramToken, telegramBotUsername, authFetch, bot.id]);

  const handleCopyNewbot = useCallback(async () => {
    await navigator.clipboard.writeText("/newbot");
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, []);

  const handleCloseTelegramConnect = useCallback(() => {
    setShowTelegramConnect(false);
    setTelegramToken("");
    setTelegramPhase("token");
    setTelegramBotUsername(null);
    setTelegramError(null);
    setCopied(false);
    setEasyStep("phone");
    setPhoneNumber("");
    setVerifyCode("");
    setTwoFaPassword("");
    setSessionId("");
    setEasyError(null);
  }, []);

  // Resend countdown
  useEffect(() => {
    if (resendTimer <= 0) return;
    const timer = setTimeout(() => setResendTimer(r => r - 1), 1000);
    return () => clearTimeout(timer);
  }, [resendTimer]);

  // Auto-focus code input
  useEffect(() => {
    if (easyStep === "code") codeInputRef.current?.focus();
  }, [easyStep]);

  const callTelegramAuth = useCallback(async (action: string, body: Record<string, string>) => {
    const res = await fetch("/api/onboarding/telegram-auto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...body }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Request failed");
    return data;
  }, []);

  const handleEasySendCode = useCallback(async () => {
    setEasyLoading(true);
    setEasyError(null);
    try {
      const fullPhone = normalizeTelegramPhoneInput(countryCode, phoneNumber);
      const data = await callTelegramAuth("send-code", { phone: fullPhone });
      setSessionId(data.sessionId);
      setEasyStep("code");
      setResendTimer(60);
    } catch (err) {
      setEasyError(err instanceof Error ? err.message : "Failed to send code");
    } finally {
      setEasyLoading(false);
    }
  }, [countryCode, phoneNumber, callTelegramAuth]);

  const handleEasyCreateBot = useCallback(async (sid: string) => {
    setEasyStep("creating");
    setEasyError(null);
    try {
      const data = await callTelegramAuth("create-bot", { sessionId: sid });
      // Auto-connect the created bot
      const connectRes = await authFetch(`/api/bots/${bot.id}/connect-telegram`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          telegramBotToken: data.token,
          telegramBotUsername: data.username,
        }),
      });
      if (!connectRes.ok) {
        const err = await connectRes.json().catch(() => ({}));
        throw new Error((err as Record<string, string>).error || "Failed to connect bot");
      }
      setShowTelegramConnect(false);
      window.location.reload();
    } catch (err) {
      setEasyError(err instanceof Error ? err.message : "Bot creation failed. Try Advanced mode.");
      setEasyStep("phone");
    }
  }, [callTelegramAuth, authFetch, bot.id]);

  const handleEasyVerifyCode = useCallback(async () => {
    setEasyLoading(true);
    setEasyError(null);
    try {
      const data = await callTelegramAuth("verify-code", { sessionId, code: verifyCode });
      if (data.needs2FA) {
        setEasyStep("2fa");
      } else {
        await handleEasyCreateBot(sessionId);
      }
    } catch (err) {
      setEasyError(err instanceof Error ? err.message : "Verification failed");
    } finally {
      setEasyLoading(false);
    }
  }, [sessionId, verifyCode, callTelegramAuth, handleEasyCreateBot]);

  const handleEasyVerify2FA = useCallback(async () => {
    setEasyLoading(true);
    setEasyError(null);
    try {
      await callTelegramAuth("verify-2fa", { sessionId, password: twoFaPassword });
      await handleEasyCreateBot(sessionId);
    } catch (err) {
      setEasyError(err instanceof Error ? err.message : "2FA verification failed");
    } finally {
      setEasyLoading(false);
    }
  }, [sessionId, twoFaPassword, callTelegramAuth, handleEasyCreateBot]);

  const handleEasyResend = useCallback(async () => {
    setEasyLoading(true);
    setEasyError(null);
    try {
      const fullPhone = normalizeTelegramPhoneInput(countryCode, phoneNumber);
      const data = await callTelegramAuth("send-code", { phone: fullPhone });
      setSessionId(data.sessionId);
      setResendTimer(60);
      setVerifyCode("");
    } catch (err) {
      setEasyError(err instanceof Error ? err.message : "Failed to resend");
    } finally {
      setEasyLoading(false);
    }
  }, [countryCode, phoneNumber, callTelegramAuth]);

  const isActive = currentStatus === "active";
  const isProvisioning = currentStatus === "provisioning";
  const isPendingTelegram = currentStatus === "pending_telegram";
  const isError = currentStatus === "error";
  const isStopped = currentStatus === "stopped";
  const isDegraded = isActive && healthStatus !== "healthy" && healthStatus !== "unknown";

  const handleRetry = useCallback(async () => {
    trackBotRetry();
    setRetrying(true);
    try {
      // Active bots use restart (not provision) to avoid recreating everything
      const endpoint = isActive
        ? `/api/bots/${bot.id}/restart`
        : `/api/bots/${bot.id}/provision`;
      const res = await authFetch(endpoint, { method: "POST" });
      if (res.ok) startProvisioning();
    } catch {
      /* ignore */
    } finally {
      setRetrying(false);
    }
  }, [authFetch, bot.id, isActive, startProvisioning]);

  const handleSubscribe = useCallback(async () => {
    trackEvent("bot_subscribe_click");
    setSubscribing(true);
    setSubscribeError(null);
    try {
      const res = await authFetch("/api/billing/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ botId: bot.id }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.checkoutUrl) window.location.href = data.checkoutUrl;
        else if (data.redirect) window.location.href = data.redirect;
      } else {
        const data = await res.json().catch(() => ({}));
        setSubscribeError(data.error || "Failed to start subscription");
      }
    } catch {
      setSubscribeError("Network error. Please try again.");
    } finally {
      setSubscribing(false);
    }
  }, [authFetch, bot.id]);

  return (
    <GlassCard glow={isActive && !needsTelegramSetup}>
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          {isActive && !restarting && !isDegraded && (
            <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 pulse-glow" />
          )}
          {isActive && !restarting && isDegraded && (
            <div className="w-2.5 h-2.5 rounded-full bg-amber-400 animate-pulse" />
          )}
          {(isProvisioning || restarting || retrying) && (
            <div className="w-2.5 h-2.5 rounded-full bg-amber-400 animate-pulse" />
          )}
          {isError && !retrying && (
            <div className="w-2.5 h-2.5 rounded-full bg-red-400" />
          )}
          {isStopped && (
            <div className="w-2.5 h-2.5 rounded-full bg-amber-400" />
          )}
          {editingName ? (
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSaveName();
                  if (e.key === "Escape") { setEditingName(false); setNameInput(bot.name); }
                }}
                className="text-lg font-semibold text-foreground bg-white border border-gray-300 rounded-lg px-2 py-0.5 focus:outline-none focus:border-primary/50 w-48"
                autoFocus
                disabled={savingName}
              />
              <button
                onClick={handleSaveName}
                disabled={savingName}
                className="text-xs text-primary-light hover:text-primary transition-colors cursor-pointer disabled:opacity-50"
              >
                {savingName ? "..." : "Save"}
              </button>
              <button
                onClick={() => { setEditingName(false); setNameInput(bot.name); }}
                className="text-xs text-secondary hover:text-foreground transition-colors cursor-pointer"
              >
                Cancel
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2 group">
              <h2 className="text-lg font-semibold text-foreground">{bot.name}</h2>
              <button
                onClick={() => setEditingName(true)}
                className="text-secondary hover:text-foreground transition-colors cursor-pointer"
                title="Rename bot"
              >
                <svg viewBox="0 0 20 20" fill="currentColor" className="w-3.5 h-3.5">
                  <path d="M2.695 14.763l-1.262 3.154a.5.5 0 00.65.65l3.155-1.262a4 4 0 001.343-.885L17.5 5.5a2.121 2.121 0 00-3-3L3.58 13.42a4 4 0 00-.885 1.343z" />
                </svg>
              </button>
            </div>
          )}
        </div>
        <StatusBadge
          status={retrying || restarting ? "provisioning" : currentStatus}
        />
      </div>

      {/* State-specific sections */}
      {isPendingTelegram && !telegramOpened && !bot.telegram_owner_id && (
        <PendingTelegramSection bot={bot} t={t} onTelegramOpen={handleTelegramOpen} />
      )}

      {(isProvisioning || restarting) && (
        <ProvisioningSection
          restarting={restarting}
          progressPct={progressPct}
          provisionTimedOut={provisionTimedOut}
          isActive={isActive}
          bot={bot}
          t={t}
        />
      )}

      {isError && !retrying && <ErrorSection t={t} onRetry={handleRetry} />}

      {isDegraded && !restarting && (
        <DegradedSection t={t} healthStatus={healthStatus} onRetry={handleRetry} retrying={retrying} />
      )}

      {isStopped && (
        <StoppedSection t={t} subscribing={subscribing} onSubscribe={handleSubscribe} error={subscribeError} />
      )}

      {needsTelegramSetup && !restarting && isActive && (
        <div className="mb-6 p-4 rounded-xl bg-[#2AABEE]/10 border border-[#2AABEE]/20">
          <div className="flex items-start gap-3">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" className="shrink-0 mt-0.5">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8l-1.6 7.52c-.12.54-.44.67-.9.42l-2.48-1.83-1.2 1.15c-.13.13-.24.24-.5.24l.18-2.52 4.56-4.12c.2-.18-.04-.27-.3-.1L8.5 13.37l-2.42-.76c-.52-.16-.53-.52.12-.77l9.46-3.64c.44-.16.82.1.68.6z" fill="#2AABEE"/>
            </svg>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-foreground">
                {t.botCard.telegramSetup}
              </p>
              <p className="text-xs text-secondary mt-1">
                {t.botCard.telegramDashboardDesc}
              </p>
              <Button
                variant="primary"
                size="sm"
                className="mt-3 bg-[#2AABEE] hover:bg-[#2AABEE]/80"
                onClick={() => setShowTelegramConnect(true)}
              >
                {t.botCard.telegramConnectButton}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Bot info grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-4 text-sm">
        <div>
          <p className="text-secondary text-xs uppercase tracking-wider mb-1">
            {t.botCard.telegram}
          </p>
          {bot.telegram_bot_username ? (
            <p className="font-medium text-foreground">@{bot.telegram_bot_username}</p>
          ) : (
            <button
              onClick={() => setShowTelegramConnect(true)}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-[#2AABEE] hover:text-[#2AABEE]/80 transition-colors cursor-pointer"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8l-1.6 7.52c-.12.54-.44.67-.9.42l-2.48-1.83-1.2 1.15c-.13.13-.24.24-.5.24l.18-2.52 4.56-4.12c.2-.18-.04-.27-.3-.1L8.5 13.37l-2.42-.76c-.52-.16-.53-.52.12-.77l9.46-3.64c.44-.16.82.1.68.6z" fill="currentColor"/>
              </svg>
              Connect Telegram
            </button>
          )}
        </div>
        <div>
          <p className="text-secondary text-xs uppercase tracking-wider mb-1">DISCORD</p>
          {bot.discord_bot_username ? (
            <div className="flex items-center gap-2">
              <p className="font-medium text-foreground">@{bot.discord_bot_username}</p>
              <button
                onClick={handleDiscordDisconnect}
                className="text-[10px] text-red-400 hover:text-red-300 transition-colors cursor-pointer"
              >
                {t.settingsPage.discordDisconnect}
              </button>
            </div>
          ) : (
            <button
              onClick={() => setShowDiscordGuide(true)}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-[#5865F2] hover:text-[#4752C4] transition-colors cursor-pointer"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03z"/>
              </svg>
              {t.settingsPage.discordConnect}
            </button>
          )}
        </div>
        <div>
          <p className="text-secondary text-xs uppercase tracking-wider mb-1">
            {t.botCard.plan}
          </p>
          <p className="font-medium text-foreground">
            {(() => {
              const plan = subscriptionPlan ?? (bot.api_key_mode === "byok" ? "byok" : "pro");
              switch (plan) {
                case "max":
                  return <>{t.botCard.maxPlan}<span className="text-xs text-muted ml-1.5">({t.botCard.maxPrice})</span></>;
                case "pro_plus":
                  return <>{t.botCard.proPlusPlan}<span className="text-xs text-muted ml-1.5">({t.botCard.proPlusPrice})</span></>;
                case "flex":
                  return <>{t.botCard.flexPlan}<span className="text-xs text-muted ml-1.5">({t.botCard.flexPrice})</span></>;
                case "pro":
                  return <>{t.botCard.proPlan}<span className="text-xs text-muted ml-1.5">({t.botCard.proPrice})</span></>;
                default:
                  return <>BYOK<span className="text-xs text-muted ml-1.5">({t.botCard.byokPrice})</span></>;
              }
            })()}
          </p>
        </div>
        <div>
          <p className="text-secondary text-xs uppercase tracking-wider mb-1">
            {t.botCard.created}
          </p>
          <p className="font-medium text-foreground">
            {new Date(bot.created_at).toLocaleDateString()}
          </p>
        </div>
      </div>

      {/* Active bot details */}
      {isActive && !needsTelegramSetup && (
        <BotDetailsSection
          bot={bot}
          t={t}
        />
      )}

      {/* Agent Wallet section — show when bot is active or has a wallet */}
      {(isActive || bot.privy_wallet_address) && (
        <AgentWalletSection botId={bot.id} />
      )}

      {/* Telegram Connect Modal */}
      {showTelegramConnect && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={handleCloseTelegramConnect}>
          <div className="bg-white border border-gray-200 rounded-2xl w-full max-w-md mx-4 max-h-[85vh] overflow-y-auto shadow-lg" onClick={(e) => e.stopPropagation()}>
            <div className="p-5">
              <div className="flex items-center justify-between mb-1">
                <h3 className="text-lg font-bold text-foreground">Connect Telegram</h3>
                <button onClick={handleCloseTelegramConnect} className="text-secondary hover:text-foreground transition-colors cursor-pointer p-1">
                  <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                    <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
                  </svg>
                </button>
              </div>
              <p className="text-sm text-secondary mb-4">{t.onboarding.telegramSubtitle}</p>

              {/* Mode toggle */}
              <div className="flex gap-1 p-1 bg-gray-100 rounded-xl mb-4">
                <button
                  type="button"
                  onClick={() => setConnectMode("easy")}
                  className={`flex-1 py-2 px-3 rounded-lg text-sm font-medium transition-all ${
                    connectMode === "easy"
                      ? "bg-white text-foreground shadow-sm"
                      : "text-secondary hover:text-foreground"
                  }`}
                >
                  {t.onboarding.telegramEasyTab}
                </button>
                <button
                  type="button"
                  onClick={() => setConnectMode("advanced")}
                  className={`flex-1 py-2 px-3 rounded-lg text-sm font-medium transition-all ${
                    connectMode === "advanced"
                      ? "bg-white text-foreground shadow-sm"
                      : "text-secondary hover:text-foreground"
                  }`}
                >
                  {t.onboarding.telegramAdvancedTab}
                </button>
              </div>

              {/* ─── Easy Mode ─── */}
              {connectMode === "easy" && (
                <>
                  {easyStep === "creating" && (
                    <div className="text-center py-8">
                      <div className="animate-spin w-8 h-8 border-2 border-[#2AABEE] border-t-transparent rounded-full mx-auto mb-4" />
                      <p className="text-sm font-medium text-foreground">{t.onboarding.telegramCreatingBot}</p>
                      <p className="text-xs text-secondary mt-1">{t.onboarding.telegramCreatingDesc}</p>
                    </div>
                  )}

                  {easyStep === "phone" && (
                    <>
                      <p className="text-xs text-secondary mb-4">{t.onboarding.telegramAutoConsent}</p>
                      <div className="flex justify-center mb-4">
                        <div className="w-14 h-14 rounded-full bg-[#2AABEE] flex items-center justify-center">
                          <svg viewBox="0 0 24 24" className="w-7 h-7 text-white fill-current">
                            <path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z" />
                          </svg>
                        </div>
                      </div>
                      <h4 className="text-base font-semibold text-center mb-1">{t.onboarding.telegramPhoneTitle}</h4>
                      <p className="text-xs text-secondary text-center mb-4">{t.onboarding.telegramPhoneDesc}</p>

                      <label className="text-xs text-secondary font-medium mb-1.5 block">{t.onboarding.telegramPhoneLabel}</label>
                      <div className="flex gap-2 mb-3">
                        <select
                          value={countryCode}
                          onChange={(e) => setCountryCode(e.target.value)}
                          className="w-[90px] bg-white border border-gray-300 rounded-xl px-2 py-3 text-sm focus:outline-none focus:border-primary/50 cursor-pointer"
                        >
                          {[
                            { code: "+82", flag: "\u{1F1F0}\u{1F1F7}" }, { code: "+1", flag: "\u{1F1FA}\u{1F1F8}" },
                            { code: "+81", flag: "\u{1F1EF}\u{1F1F5}" }, { code: "+86", flag: "\u{1F1E8}\u{1F1F3}" },
                            { code: "+44", flag: "\u{1F1EC}\u{1F1E7}" }, { code: "+49", flag: "\u{1F1E9}\u{1F1EA}" },
                            { code: "+33", flag: "\u{1F1EB}\u{1F1F7}" }, { code: "+65", flag: "\u{1F1F8}\u{1F1EC}" },
                            { code: "+91", flag: "\u{1F1EE}\u{1F1F3}" }, { code: "+61", flag: "\u{1F1E6}\u{1F1FA}" },
                            { code: "+55", flag: "\u{1F1E7}\u{1F1F7}" }, { code: "+34", flag: "\u{1F1EA}\u{1F1F8}" },
                            { code: "+852", flag: "\u{1F1ED}\u{1F1F0}" }, { code: "+886", flag: "\u{1F1F9}\u{1F1FC}" },
                            { code: "+66", flag: "\u{1F1F9}\u{1F1ED}" }, { code: "+84", flag: "\u{1F1FB}\u{1F1F3}" },
                          ].map((c) => (
                            <option key={c.code} value={c.code}>{c.flag} {c.code}</option>
                          ))}
                        </select>
                        <input
                          type="tel"
                          inputMode="numeric"
                          placeholder="10 1234 5678"
                          value={phoneNumber}
                          onChange={(e) => setPhoneNumber(e.target.value)}
                          className="flex-1 bg-white border border-gray-300 rounded-xl px-4 py-3 text-sm text-foreground placeholder-gray-400 focus:outline-none focus:border-[#2AABEE]/50 focus:ring-1 focus:ring-[#2AABEE]/20 transition-colors"
                        />
                      </div>
                      {easyError && <p className="text-red-500 text-[13px] mb-2">{easyError}</p>}
                      <button
                        onClick={handleEasySendCode}
                        disabled={!phoneNumber.trim() || easyLoading}
                        className="w-full bg-[#2AABEE] rounded-xl py-3 text-sm font-semibold text-white hover:bg-[#229ED9] transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {easyLoading ? t.onboarding.telegramSending : t.onboarding.telegramSendCode}
                      </button>
                    </>
                  )}

                  {easyStep === "code" && (
                    <>
                      <h4 className="text-base font-semibold text-center mb-1">{t.onboarding.telegramCodeTitle}</h4>
                      <p className="text-xs text-secondary text-center mb-4">
                        {t.onboarding.telegramCodeDesc}<strong>{countryCode}{phoneNumber}</strong>
                      </p>
                      <label className="text-xs text-secondary font-medium mb-1.5 block">{t.onboarding.telegramCodeLabel}</label>
                      <input
                        ref={codeInputRef}
                        type="text"
                        inputMode="numeric"
                        placeholder="12345"
                        value={verifyCode}
                        onChange={(e) => setVerifyCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                        className="w-full bg-white border border-gray-300 rounded-xl px-4 py-3 text-foreground placeholder-gray-400 focus:outline-none focus:border-primary/50 text-center text-lg tracking-[0.3em] font-mono mb-2"
                        autoComplete="one-time-code"
                      />
                      {easyError && <p className="text-red-500 text-[13px] mb-2">{easyError}</p>}
                      <button
                        onClick={handleEasyVerifyCode}
                        disabled={verifyCode.length < 3 || easyLoading}
                        className="w-full bg-[#2AABEE] rounded-xl py-3 text-sm font-semibold text-white hover:bg-[#229ED9] transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {easyLoading ? t.onboarding.telegramVerifying : t.onboarding.telegramVerifyCode}
                      </button>
                      <div className="flex justify-between items-center mt-3">
                        <button onClick={() => { setEasyStep("phone"); setEasyError(null); }} className="text-xs text-secondary hover:text-foreground transition-colors cursor-pointer">
                          {t.onboarding.telegramBack}
                        </button>
                        <button
                          onClick={handleEasyResend}
                          disabled={resendTimer > 0 || easyLoading}
                          className={`text-xs cursor-pointer transition-colors ${resendTimer > 0 ? "text-gray-400" : "text-[#2AABEE] hover:text-[#229ED9]"}`}
                        >
                          {resendTimer > 0 ? `${t.onboarding.telegramResend} (${resendTimer}s)` : t.onboarding.telegramResend}
                        </button>
                      </div>
                    </>
                  )}

                  {easyStep === "2fa" && (
                    <>
                      <h4 className="text-base font-semibold text-center mb-1">{t.onboarding.telegram2faTitle}</h4>
                      <p className="text-xs text-secondary text-center mb-4">{t.onboarding.telegram2faDesc}</p>
                      <label className="text-xs text-secondary font-medium mb-1.5 block">{t.onboarding.telegram2faLabel}</label>
                      <input
                        type="password"
                        placeholder={t.onboarding.telegram2faPlaceholder}
                        value={twoFaPassword}
                        onChange={(e) => setTwoFaPassword(e.target.value)}
                        className="w-full bg-white border border-gray-300 rounded-xl px-4 py-3 text-sm text-foreground placeholder-gray-400 focus:outline-none focus:border-primary/50 mb-2"
                        autoComplete="current-password"
                      />
                      {easyError && <p className="text-red-500 text-[13px] mb-2">{easyError}</p>}
                      <button
                        onClick={handleEasyVerify2FA}
                        disabled={!twoFaPassword || easyLoading}
                        className="w-full bg-[#2AABEE] rounded-xl py-3 text-sm font-semibold text-white hover:bg-[#229ED9] transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {easyLoading ? t.onboarding.telegramVerifying : t.onboarding.telegramVerify2fa}
                      </button>
                      <button onClick={() => { setEasyStep("phone"); setEasyError(null); }} className="block mx-auto mt-3 text-xs text-secondary hover:text-foreground transition-colors cursor-pointer">
                        {t.onboarding.telegramBack}
                      </button>
                    </>
                  )}
                </>
              )}

              {/* ─── Advanced Mode ─── */}
              {connectMode === "advanced" && (
                <>
                  {telegramPhase === "token" && (
                    <>
                      <a
                        href="https://t.me/BotFather?start"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-3 bg-[#2AABEE]/10 border border-[#2AABEE]/20 rounded-xl p-3 mb-4 hover:bg-[#2AABEE]/15 transition-colors"
                      >
                        <span className="text-2xl">{"\u{1F916}"}</span>
                        <div className="flex-1">
                          <p className="text-sm font-semibold text-[#2AABEE]">{t.onboarding.telegramOpenBotFather}</p>
                          <p className="text-xs text-secondary mt-0.5">{t.onboarding.telegramOpenBotFatherDesc}</p>
                        </div>
                        <span className="text-secondary">{"\u2197"}</span>
                      </a>

                      <div className="space-y-2.5 mb-4">
                        <div className="flex items-start gap-2.5">
                          <span className="shrink-0 w-[18px] h-[18px] rounded-full bg-gray-100 text-[11px] font-semibold text-gray-600 flex items-center justify-center">1</span>
                          <div className="flex-1 space-y-1.5">
                            <p className="text-[13px] text-secondary leading-[18px]">
                              {t.onboarding.telegramStep2Send} <code className="text-violet-400 font-mono font-semibold">/newbot</code> {t.onboarding.telegramStep2Follow}
                            </p>
                            <button onClick={handleCopyNewbot} className="inline-flex items-center gap-1.5 bg-gray-100 border border-gray-200 rounded-md px-2 py-1 hover:bg-black/10 transition-colors cursor-pointer">
                              <code className="text-violet-400 font-mono text-[13px] font-semibold">/newbot</code>
                              <span className="text-[10px] text-secondary">{copied ? t.onboarding.copied : t.onboarding.tapToCopy}</span>
                            </button>
                          </div>
                        </div>
                        <div className="flex items-start gap-2.5">
                          <span className="shrink-0 w-[18px] h-[18px] rounded-full bg-gray-100 text-[11px] font-semibold text-gray-600 flex items-center justify-center">2</span>
                          <p className="text-[13px] text-secondary leading-[18px]">{t.onboarding.telegramStep3}</p>
                        </div>
                        <div className="flex items-start gap-2.5">
                          <span className="shrink-0 w-[18px] h-[18px] rounded-full bg-gray-100 text-[11px] font-semibold text-gray-600 flex items-center justify-center">3</span>
                          <p className="text-[13px] text-secondary leading-[18px]">{t.onboarding.telegramStep4}</p>
                        </div>
                      </div>

                      <label className="text-xs text-secondary font-medium mb-1.5 block">{t.onboarding.telegramTokenLabel}</label>
                      <input
                        type="text"
                        value={telegramToken}
                        onChange={(e) => { setTelegramToken(e.target.value); setTelegramError(null); }}
                        placeholder={t.onboarding.telegramTokenPlaceholder}
                        className="w-full bg-white border border-gray-300 rounded-xl px-4 py-3 text-sm text-foreground placeholder-gray-400 font-mono focus:outline-none focus:border-primary/50 mb-2"
                      />
                      {telegramError && <p className="text-red-400 text-[13px] mb-2">{telegramError}</p>}
                      <button
                        onClick={handleValidateToken}
                        disabled={telegramValidating || !telegramToken.trim()}
                        className="w-full bg-primary rounded-xl py-3 text-sm font-semibold text-white hover:bg-primary/90 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed mt-2"
                      >
                        {telegramValidating ? t.onboarding.telegramValidating : t.onboarding.telegramValidateBtn}
                      </button>
                    </>
                  )}

                  {telegramPhase === "start" && (
                    <>
                      <div className="bg-[#2AABEE]/8 border border-[#2AABEE]/15 rounded-xl p-3.5 mb-4">
                        <p className="text-base font-semibold text-foreground mb-1">Send /start to your bot</p>
                        <p className="text-[13px] text-secondary leading-[18px] mb-3">
                          Open your bot in Telegram and send /start so we can link your account
                        </p>
                        <a
                          href={`https://t.me/${telegramBotUsername}?start=1`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center justify-center gap-1.5 bg-[#2AABEE]/15 rounded-lg py-3 hover:bg-[#2AABEE]/25 transition-colors"
                        >
                          <span className="text-sm font-semibold text-[#2AABEE]">Open @{telegramBotUsername} in Telegram</span>
                          <span className="text-[#2AABEE]">{"\u2197"}</span>
                        </a>
                      </div>
                      {telegramError && <p className="text-red-400 text-[13px] mb-2">{telegramError}</p>}
                      <div className="flex gap-3 mt-3">
                        <button
                          onClick={() => { setTelegramPhase("token"); setTelegramError(null); }}
                          className="px-5 py-3 border border-gray-300 rounded-xl text-gray-600 text-sm hover:bg-gray-100 transition-colors cursor-pointer"
                        >
                          Back
                        </button>
                        <button
                          onClick={handleConnectTelegram}
                          disabled={telegramValidating}
                          className="flex-1 bg-primary rounded-xl py-3 text-sm font-semibold text-white hover:bg-primary/90 transition-colors cursor-pointer disabled:opacity-50"
                        >
                          {telegramValidating ? "Connecting..." : "Connect"}
                        </button>
                      </div>
                    </>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}
      {/* Discord Guide Modal */}
      <Modal open={showDiscordGuide} onClose={() => { setShowDiscordGuide(false); setDiscordToken(""); setDiscordInviteUrl(null); setDiscordError(null); }}>
        <div className="p-5">
          {!discordInviteUrl ? (
            <>
              <h3 className="text-lg font-bold text-foreground mb-4">{t.settingsPage.discordGuideTitle}</h3>
              <ol className="space-y-3 mb-5 text-sm text-foreground">
                <li className="flex items-start gap-2.5">
                  <span className="shrink-0 w-[18px] h-[18px] rounded-full bg-gray-100 text-[11px] font-semibold text-gray-600 flex items-center justify-center">1</span>
                  <span>
                    {t.settingsPage.discordGuideStep1}
                    <a href="https://discord.com/developers/applications" target="_blank" rel="noopener noreferrer" className="ml-1 text-[#5865F2] hover:underline">
                      discord.com/developers/applications &rarr;
                    </a>
                  </span>
                </li>
                <li className="flex items-start gap-2.5">
                  <span className="shrink-0 w-[18px] h-[18px] rounded-full bg-gray-100 text-[11px] font-semibold text-gray-600 flex items-center justify-center">2</span>
                  <span>{t.settingsPage.discordGuideStep2}</span>
                </li>
                <li className="flex items-start gap-2.5">
                  <span className="shrink-0 w-[18px] h-[18px] rounded-full bg-gray-100 text-[11px] font-semibold text-gray-600 flex items-center justify-center">3</span>
                  <span>{t.settingsPage.discordGuideStep3}</span>
                </li>
                <li className="flex items-start gap-2.5">
                  <span className="shrink-0 w-[18px] h-[18px] rounded-full bg-gray-100 text-[11px] font-semibold text-gray-600 flex items-center justify-center">4</span>
                  <span>{t.settingsPage.discordGuideStep4}</span>
                </li>
                <li className="flex items-start gap-2.5">
                  <span className="shrink-0 w-[18px] h-[18px] rounded-full bg-gray-100 text-[11px] font-semibold text-gray-600 flex items-center justify-center">5</span>
                  <span>{t.settingsPage.discordGuideStep5}</span>
                </li>
              </ol>
              <input
                type="text"
                value={discordToken}
                onChange={(e) => setDiscordToken(e.target.value)}
                placeholder={t.settingsPage.discordGuideTokenPlaceholder}
                className="w-full bg-white border border-gray-300 rounded-xl px-4 py-3 text-sm text-foreground placeholder-gray-400 focus:outline-none focus:border-[#5865F2]/50 focus:ring-1 focus:ring-[#5865F2]/20 transition-colors mb-3"
              />
              {discordError && <p className="text-red-500 text-[13px] mb-2">{discordError}</p>}
              <button
                onClick={handleDiscordSubmitToken}
                disabled={!discordToken.trim() || discordConnecting}
                className="w-full bg-[#5865F2] rounded-xl py-3 text-sm font-semibold text-white hover:bg-[#4752C4] transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {discordConnecting ? "..." : t.settingsPage.discordGuideSubmit}
              </button>
            </>
          ) : (
            <>
              <div className="text-center mb-5">
                <div className="w-12 h-12 rounded-full bg-emerald-500/10 flex items-center justify-center mx-auto mb-3">
                  <svg viewBox="0 0 20 20" fill="currentColor" className="w-6 h-6 text-emerald-500">
                    <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clipRule="evenodd" />
                  </svg>
                </div>
                <h3 className="text-lg font-bold text-foreground">{t.settingsPage.discordGuideConnected}</h3>
              </div>
              <p className="text-sm text-secondary text-center mb-4">{t.settingsPage.discordGuideInvite}</p>
              <a
                href={discordInviteUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="block w-full bg-[#5865F2] rounded-xl py-3 text-sm font-semibold text-white text-center hover:bg-[#4752C4] transition-colors mb-3"
              >
                {t.settingsPage.discordGuideInviteButton} &rarr;
              </a>
              <p className="text-xs text-secondary text-center mb-4">
                {t.settingsPage.discordGuideInviteHint.replace("{username}", bot.discord_bot_username ?? "")}
              </p>
              <button
                onClick={() => { setShowDiscordGuide(false); setDiscordToken(""); setDiscordInviteUrl(null); setDiscordError(null); window.location.reload(); }}
                className="w-full bg-gray-100 rounded-xl py-3 text-sm font-semibold text-foreground hover:bg-gray-200 transition-colors cursor-pointer"
              >
                {t.settingsPage.discordGuideDone}
              </button>
            </>
          )}
        </div>
      </Modal>
    </GlassCard>
  );
}
