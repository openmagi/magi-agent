"use client";

import { useState, useEffect, useSyncExternalStore } from "react";
import posthog from "posthog-js";
import Link from "next/link";
import { getCookieConsent, setCookieConsent, syncConsentToServer } from "@/lib/consent/store";
import { POLICY_VERSION } from "@/lib/consent/constants";
import { useMessages } from "@/lib/i18n";

function subscribeNoop(cb: () => void) {
  // localStorage doesn't fire events in the same tab, so no subscription needed.
  // The value is read once via getSnapshot.
  void cb;
  return () => {};
}

function isEuRegion(): boolean {
  if (typeof document === "undefined") return true; // SSR: assume EU (safe)
  return document.cookie.includes("clawy_geo=eu");
}

function getConsentSnapshot(): boolean {
  // Non-EU users don't need consent
  if (!isEuRegion()) return false;
  return getCookieConsent() === null;
}

function getConsentServerSnapshot(): boolean {
  // SSR: always hidden to prevent hydration mismatch
  return false;
}

export function CookieConsentBanner() {
  const needsConsent = useSyncExternalStore(subscribeNoop, getConsentSnapshot, getConsentServerSnapshot);
  const [dismissed, setDismissed] = useState(false);
  const t = useMessages();

  // Auto-accept for non-EU users (keep localStorage + PostHog in sync)
  useEffect(() => {
    if (!isEuRegion() && getCookieConsent() === null) {
      setCookieConsent("accepted");
      posthog.opt_in_capturing();
    }
  }, []);

  function handleAccept() {
    setCookieConsent("accepted");
    posthog.opt_in_capturing();
    window.gtag?.("consent", "update", {
      analytics_storage: "granted",
      ad_storage: "granted",
      ad_user_data: "granted",
      ad_personalization: "granted",
    });
    syncConsentToServer("accepted", POLICY_VERSION);
    setDismissed(true);
  }

  function handleDecline() {
    setCookieConsent("declined");
    posthog.opt_out_capturing();
    syncConsentToServer("declined", POLICY_VERSION);
    setDismissed(true);
  }

  if (!needsConsent || dismissed) return null;

  return (
    <div className="fixed bottom-0 left-0 right-0 z-[60] p-4">
      <div className="max-w-2xl mx-auto bg-[#1a1a2e]/95 backdrop-blur-md border border-white/15 rounded-2xl p-4 shadow-2xl">
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-foreground">{t.consent.cookieTitle}</p>
            <p className="text-xs text-secondary mt-1">
              {t.consent.cookieDescription}{" "}
              <Link href="/privacy" className="text-primary hover:text-primary-light transition-colors">
                {t.consent.cookiePrivacyLink}
              </Link>
            </p>
          </div>
          <div className="flex gap-2 shrink-0">
            <button
              onClick={handleDecline}
              className="px-3 py-1.5 rounded-lg text-xs font-medium text-secondary hover:text-foreground transition-colors cursor-pointer"
            >
              {t.consent.cookieDecline}
            </button>
            <button
              onClick={handleAccept}
              className="px-3 py-1.5 rounded-lg text-xs font-medium bg-primary/20 text-primary-light border border-primary/30 hover:bg-primary/30 transition-colors cursor-pointer"
            >
              {t.consent.cookieAccept}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
