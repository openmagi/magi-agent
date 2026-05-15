"use client";

import { useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { getDismissedPolicyVersion, dismissPolicyVersion } from "@/lib/consent/store";
import { POLICY_VERSION } from "@/lib/consent/constants";
import { useMessages } from "@/lib/i18n";

function subscribeNoop(cb: () => void) {
  void cb;
  return () => {};
}

function getPolicySnapshot(): boolean {
  return getDismissedPolicyVersion() !== POLICY_VERSION;
}

function getPolicyServerSnapshot(): boolean {
  return false;
}

export function PolicyChangeBanner() {
  const needsBanner = useSyncExternalStore(subscribeNoop, getPolicySnapshot, getPolicyServerSnapshot);
  const [dismissed, setDismissed] = useState(false);
  const t = useMessages();

  function handleDismiss() {
    dismissPolicyVersion(POLICY_VERSION);
    setDismissed(true);
  }

  if (!needsBanner || dismissed) return null;

  return (
    <div className="glass border border-blue-500/20 rounded-xl px-4 py-3 mb-4">
      <div className="flex items-start gap-3">
        <svg className="w-5 h-5 text-blue-400 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-blue-300">{t.consent.policyUpdatedTitle}</p>
          <p className="text-xs text-secondary mt-1">
            {t.consent.policyUpdatedDescription}{" "}
            <Link href="/terms" className="text-blue-400 hover:text-blue-300 transition-colors">
              {t.consent.policyTermsLink}
            </Link>
            {" · "}
            <Link href="/privacy" className="text-blue-400 hover:text-blue-300 transition-colors">
              {t.consent.policyPrivacyLink}
            </Link>
          </p>
        </div>
        <button
          onClick={handleDismiss}
          className="text-xs text-blue-400 hover:text-blue-300 transition-colors px-2 py-1 shrink-0 cursor-pointer"
        >
          {t.consent.policyDismiss}
        </button>
      </div>
    </div>
  );
}
