"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { usePrivy } from "@privy-io/react-auth";
import { useMessages } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { usePoll } from "@/hooks/use-poll";
import {
  trackOnboardingComplete,
  trackOnboardingProvisioningEnter,
} from "@/lib/analytics";
import type { BotDeployData } from "@/types/entities";

type Bot = BotDeployData;
type Phase = "provisioning" | "active" | "error";

const PROVISION_ESTIMATE_MS = 180_000; // 3 minutes

/** Map server-side step labels to user-friendly i18n keys */
function getStepMessage(
  step: string | null,
  t: ReturnType<typeof useMessages>,
): string {
  if (!step) return t.onboarding.settingUp;

  // Match provisioning controller STEP_LABELS to i18n messages
  if (step.includes("namespace") || step.includes("volume") || step.includes("secrets")) {
    return t.onboarding.provisioningStepCreatingResources;
  }
  if (step.includes("network") || step.includes("template") || step.includes("dynamic") || step.includes("config")) {
    return t.onboarding.provisioningStepConfiguringBot;
  }
  if (step.includes("skill") || step.includes("specialist") || step.includes("lifecycle") || step.includes("pod") || step.includes("Creating pod")) {
    return t.onboarding.provisioningStepStartingServices;
  }
  if (step.includes("waiting") || step.includes("container")) {
    return t.onboarding.provisioningStepConnecting;
  }
  if (step.includes("Cleaning")) {
    return t.onboarding.provisioningStepCreatingResources;
  }

  return t.onboarding.settingUp;
}

interface ProvisioningStatusProps {
  onComplete: () => void;
}

export function ProvisioningStatus({ onComplete }: ProvisioningStatusProps) {
  const { getAccessToken } = usePrivy();
  const t = useMessages();
  const [bot, setBot] = useState<Bot | null>(null);
  const [phase, setPhase] = useState<Phase>("provisioning");
  const [progressPct, setProgressPct] = useState(0);
  const [stepMessage, setStepMessage] = useState(t.onboarding.settingUp);
  const [timedOut, setTimedOut] = useState(false);

  const provisionStartRef = useRef<number | null>(null);
  const progressTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const completionTrackedRef = useRef(false);

  // Poll bot status during provisioning
  const pollBotStatus = useCallback(async () => {
    if (!bot) return;
    try {
      const token = await getAccessToken();
      const res = await fetch(`/api/bots/${bot.id}/status`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setBot((prev) => ({ ...prev!, status: data.status }));

        // Update step message from server
        if (data.provisioningStep) {
          setStepMessage(getStepMessage(data.provisioningStep, t));
        }

        if (data.status === "active") {
          setPhase("active");
          setProgressPct(100);
          setStepMessage(t.onboarding.botLive);
          if (!completionTrackedRef.current) {
            completionTrackedRef.current = true;
            trackOnboardingComplete();
          }
        } else if (data.status === "error") {
          setPhase("error");
        }
      }
    } catch {
      // Silently retry
    }
  }, [bot, getAccessToken, t]);

  usePoll(pollBotStatus, 5_000, phase === "provisioning" && !!bot);

  // Progress timer: tick every second during provisioning
  useEffect(() => {
    if (phase !== "provisioning") {
      if (progressTimerRef.current) {
        clearInterval(progressTimerRef.current);
        progressTimerRef.current = null;
      }
      return;
    }

    trackOnboardingProvisioningEnter();

    if (!provisionStartRef.current) {
      provisionStartRef.current = Date.now();
    }

    const tick = () => {
      const started = provisionStartRef.current;
      if (!started) return;
      const elapsed = Date.now() - started;
      // Progress goes up to 95% on time estimate, last 5% reserved for actual completion
      const pct = Math.min((elapsed / PROVISION_ESTIMATE_MS) * 95, 95);
      setProgressPct(pct);

      // Update step message based on elapsed time (fallback if server doesn't report steps)
      if (elapsed < 30_000) {
        setStepMessage(t.onboarding.provisioningStepCreatingResources);
      } else if (elapsed < 60_000) {
        setStepMessage(t.onboarding.provisioningStepConfiguringBot);
      } else if (elapsed < 120_000) {
        setStepMessage(t.onboarding.provisioningStepStartingServices);
      } else if (elapsed < PROVISION_ESTIMATE_MS) {
        setStepMessage(t.onboarding.provisioningStepConnecting);
      } else {
        setStepMessage(t.onboarding.provisioningStepFinalizing);
        setTimedOut(true);
      }
    };

    tick();
    progressTimerRef.current = setInterval(tick, 1_000);

    return () => {
      if (progressTimerRef.current) {
        clearInterval(progressTimerRef.current);
        progressTimerRef.current = null;
      }
    };
  }, [phase, t]);

  // Initial fetch: find the bot and determine starting phase
  useEffect(() => {
    let cancelled = false;

    async function fetchBots() {
      try {
        const token = await getAccessToken();
        const res = await fetch("/api/bots", {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) return;
        const data = await res.json();
        const bots: Bot[] = data.bots ?? [];
        const provisioningBot = bots.find((b) => b.status === "provisioning");
        const pendingBot = bots.find((b) => b.status === "pending_telegram");
        const errorBot = bots.find((b) => b.status === "error");
        const activeBot = bots.find((b) => b.status === "active");
        const targetBot = provisioningBot ?? pendingBot ?? errorBot ?? activeBot ?? bots[0];

        if (cancelled || !targetBot) return;
        setBot(targetBot);

        if (targetBot.status === "active") {
          setPhase("active");
          setProgressPct(100);
          setStepMessage(t.onboarding.botLive);
        } else if (targetBot.status === "error") {
          setPhase("error");
        } else {
          // pending_telegram or provisioning — start provisioning
          setPhase("provisioning");
          provisionStartRef.current = Date.now();
          // Auto-trigger provisioning for stale pending_telegram bots
          if (targetBot.status === "pending_telegram") {
            try {
              const tok = await getAccessToken();
              await fetch(`/api/bots/${targetBot.id}/provision`, {
                method: "POST",
                headers: { Authorization: `Bearer ${tok}` },
              });
            } catch { /* best-effort */ }
          }
        }
      } catch {
        // Network error
      }
    }

    fetchBots();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, t]);

  const handleRetryDeploy = useCallback(async () => {
    if (!bot) return;
    setPhase("provisioning");
    provisionStartRef.current = Date.now();
    setProgressPct(0);
    setTimedOut(false);
    try {
      const token = await getAccessToken();
      await fetch(`/api/bots/${bot.id}/provision`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch {
      /* ignore */
    }
  }, [bot, getAccessToken]);

  // Phase: Error
  if (phase === "error") {
    return (
      <div className="mt-6 text-center">
        <div className="text-3xl mb-3">&#10060;</div>
        <h2 className="text-lg font-bold mb-1 text-foreground">
          {t.botCard.errorTitle}
        </h2>
        <p className="text-secondary text-sm mb-4">
          {t.botCard.errorDesc}
        </p>
        <Button onClick={handleRetryDeploy} size="md">
          {t.botCard.retryButton}
        </Button>
      </div>
    );
  }

  // Phase: Provisioning
  if (phase === "provisioning") {
    return (
      <div className="mt-6">
        <div className="text-center mb-4">
          <h2 className="text-lg font-bold text-foreground mb-1">
            {t.onboarding.provisioningProgress}
          </h2>
          <p className="text-secondary text-sm">{stepMessage}</p>
        </div>

        {/* Progress bar */}
        <div className="w-full h-2 rounded-full bg-black/[0.04] overflow-hidden mb-3">
          <div
            className="h-full rounded-full bg-gradient-to-r from-primary to-primary-light transition-all duration-1000 ease-out"
            style={{ width: `${Math.max(progressPct, 2)}%` }}
          />
        </div>

        <div className="flex items-center justify-between text-xs text-secondary">
          <span>{Math.round(progressPct)}%</span>
          {timedOut && (
            <span className="text-amber-400">
              {t.onboarding.provisioningTimedOut}
            </span>
          )}
        </div>
      </div>
    );
  }

  // Phase: Active
  return (
    <div className="mt-6">
      <div className="text-center mb-4">
        <div className="text-3xl mb-2">&#10003;</div>
        <h2 className="text-lg font-bold text-foreground mb-1">
          {t.onboarding.botLive}
        </h2>
      </div>

      {/* Completed progress bar */}
      <div className="w-full h-2 rounded-full bg-black/[0.04] overflow-hidden mb-4">
        <div className="h-full rounded-full bg-gradient-to-r from-primary to-emerald-400 w-full" />
      </div>

      <div className="flex justify-center">
        <Button onClick={onComplete} size="lg">
          {t.onboarding.goToDashboard}
        </Button>
      </div>
    </div>
  );
}
