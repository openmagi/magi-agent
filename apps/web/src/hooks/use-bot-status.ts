"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { usePoll } from "@/hooks/use-poll";
import { trackOnboardingComplete } from "@/lib/analytics";

const PROVISION_ESTIMATE_MS = 180_000; // 3 minutes
const PROVISION_START_KEY = "clawy_provision_start_";

function getStoredProvisionStart(botId: string): number | null {
  try {
    const v = localStorage.getItem(PROVISION_START_KEY + botId);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

function setStoredProvisionStart(botId: string, ts: number): void {
  try {
    localStorage.setItem(PROVISION_START_KEY + botId, String(ts));
  } catch {
    /* ignore */
  }
}

function clearStoredProvisionStart(botId: string): void {
  try {
    localStorage.removeItem(PROVISION_START_KEY + botId);
  } catch {
    /* ignore */
  }
}

export interface UseBotStatusResult {
  currentStatus: string;
  healthStatus: string;
  needsTelegramSetup: boolean;
  restarting: boolean;
  progressPct: number;
  provisionTimedOut: boolean;
  telegramOpened: boolean;
  /** Mark Telegram as opened (webhook handles provisioning). */
  handleTelegramOpen: () => void;
  /** Start or restart provisioning (from error or retry). */
  startProvisioning: () => void;
}

export function useBotStatus(
  botId: string,
  initialStatus: string,
): UseBotStatusResult {
  const [currentStatus, setCurrentStatus] = useState(initialStatus);
  const [healthStatus, setHealthStatus] = useState("unknown");
  const [needsTelegramSetup, setNeedsTelegramSetup] = useState(
    initialStatus === "pending_telegram"
  );
  const [restarting, setRestarting] = useState(false);
  const [telegramOpened, setTelegramOpened] = useState(false);
  const [progressPct, setProgressPct] = useState(0);
  const [provisionTimedOut, setProvisionTimedOut] = useState(false);

  const provisionStartRef = useRef<number | null>(
    getStoredProvisionStart(botId)
  );
  const progressRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const authFetch = useAuthFetch();

  const isProvisioning = currentStatus === "provisioning";
  const isPendingTelegram = currentStatus === "pending_telegram";
  const isDegraded = currentStatus === "active" && healthStatus !== "healthy" && healthStatus !== "unknown";
  const shouldPoll = isProvisioning || isPendingTelegram || isDegraded;

  // Poll bot status
  const pollStatus = useCallback(async () => {
    try {
      const res = await authFetch(`/api/bots/${botId}/status`);
      if (!res.ok) return;

      const data = await res.json();
      setCurrentStatus(data.status);
      if (data.healthStatus) setHealthStatus(data.healthStatus);

      if (data.status === "active") {
        if (
          provisionStartRef.current &&
          !localStorage.getItem(`clawy_onboarding_done_${botId}`)
        ) {
          trackOnboardingComplete();
          try {
            localStorage.setItem(`clawy_onboarding_done_${botId}`, "1");
          } catch {
            /* ignore */
          }
        }
        clearStoredProvisionStart(botId);
        setNeedsTelegramSetup(false);
        setRestarting(false);
      }

      // Transition from pending_telegram → provisioning (webhook triggered it)
      if (data.status === "provisioning" && isPendingTelegram) {
        setNeedsTelegramSetup(false);
        const now = Date.now();
        provisionStartRef.current = now;
        setStoredProvisionStart(botId, now);
        setProgressPct(0);
        setProvisionTimedOut(false);
      }
    } catch {
      // Network error — retry on next poll
    }
  }, [authFetch, botId, isPendingTelegram]);

  usePoll(pollStatus, 5_000, shouldPoll);

  // Progress bar timer
  useEffect(() => {
    if (!isProvisioning && !restarting) {
      if (progressRef.current) {
        clearInterval(progressRef.current);
        progressRef.current = null;
      }
      return;
    }

    if (!provisionStartRef.current) {
      const now = Date.now();
      provisionStartRef.current = now;
      setStoredProvisionStart(botId, now);
    }

    const tick = () => {
      const startedAt = provisionStartRef.current;
      if (!startedAt) return;
      const elapsed = Date.now() - startedAt;
      const pct = Math.min((elapsed / PROVISION_ESTIMATE_MS) * 100, 100);
      setProgressPct(pct);
      if (elapsed >= PROVISION_ESTIMATE_MS) {
        setProvisionTimedOut(true);
      }
    };

    tick();
    progressRef.current = setInterval(tick, 1_000);

    return () => {
      if (progressRef.current) {
        clearInterval(progressRef.current);
        progressRef.current = null;
      }
    };
  }, [isProvisioning, restarting, botId]);

  // Webhook handles provisioning — just mark Telegram as opened
  const handleTelegramOpen = useCallback(() => {
    setTelegramOpened(true);
  }, []);

  const startProvisioning = useCallback(() => {
    setCurrentStatus("provisioning");
    const now = Date.now();
    provisionStartRef.current = now;
    setStoredProvisionStart(botId, now);
    setProgressPct(0);
    setProvisionTimedOut(false);
  }, [botId]);

  return {
    currentStatus,
    healthStatus,
    needsTelegramSetup,
    restarting,
    progressPct,
    provisionTimedOut,
    telegramOpened,
    handleTelegramOpen,
    startProvisioning,
  };
}
