"use client";

import { useState, useCallback, useRef } from "react";
import { generateOnRampURL } from "@coinbase/cbpay-js";
import { useAuthFetch } from "@/hooks/use-auth-fetch";

interface UseCoinbaseOnrampOptions {
  walletAddress: string | undefined;
  onSuccess?: () => void;
  onExit?: () => void;
}

interface UseCoinbaseOnrampReturn {
  openOnramp: (amount?: string) => Promise<void>;
  closeOnramp: () => void;
  isOpen: boolean;
  isLoading: boolean;
  error: string | null;
}

const POPUP_WIDTH = 460;
const POPUP_HEIGHT = 750;

export function useCoinbaseOnramp({
  walletAddress,
  onSuccess,
  onExit,
}: UseCoinbaseOnrampOptions): UseCoinbaseOnrampReturn {
  const authFetch = useAuthFetch();
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const popupRef = useRef<Window | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const cleanup = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    popupRef.current = null;
    setIsOpen(false);
  }, []);

  const closeOnramp = useCallback(() => {
    if (popupRef.current && !popupRef.current.closed) {
      popupRef.current.close();
    }
    cleanup();
  }, [cleanup]);

  const openOnramp = useCallback(async (amount?: string) => {
    if (!walletAddress) {
      setError("Wallet not available");
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      // Get session token from our API
      const res = await authFetch("/api/credits/onramp/session-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ walletAddress }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error((data as Record<string, string>).error || "Failed to get session token");
      }

      const { sessionToken } = await res.json() as { sessionToken: string };

      // Generate the onramp URL
      const onrampUrl = generateOnRampURL({
        sessionToken,
        destinationWallets: [
          {
            address: walletAddress,
            blockchains: ["base"],
            assets: ["USDC"],
            supportedNetworks: ["base"],
          },
        ],
        ...(amount ? { presetFiatAmount: parseFloat(amount) } : {}),
        defaultNetwork: "base",
        defaultExperience: "buy",
      });

      // Open popup centered
      const left = Math.round(window.screenX + (window.outerWidth - POPUP_WIDTH) / 2);
      const top = Math.round(window.screenY + (window.outerHeight - POPUP_HEIGHT) / 2);

      const popup = window.open(
        onrampUrl,
        "coinbase-onramp",
        `width=${POPUP_WIDTH},height=${POPUP_HEIGHT},left=${left},top=${top},toolbar=no,menubar=no,scrollbars=yes,resizable=yes`,
      );

      if (!popup) {
        throw new Error("Popup blocked. Please allow popups for this site.");
      }

      popupRef.current = popup;
      setIsOpen(true);

      // Poll for popup close
      pollRef.current = setInterval(() => {
        if (popup.closed) {
          cleanup();
          onSuccess?.();
        }
      }, 500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open Coinbase Onramp");
    } finally {
      setIsLoading(false);
    }
  }, [walletAddress, authFetch, cleanup, onSuccess]);

  // Call onExit when popup closes (isOpen transitions false)
  const prevIsOpen = useRef(isOpen);
  if (prevIsOpen.current && !isOpen) {
    onExit?.();
  }
  prevIsOpen.current = isOpen;

  return { openOnramp, closeOnramp, isOpen, isLoading, error };
}
