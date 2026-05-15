import {
  STORAGE_KEY_COOKIE_CONSENT,
  STORAGE_KEY_POLICY_VERSION,
} from "./constants";

export type CookieConsent = "accepted" | "declined";

export function getCookieConsent(): CookieConsent | null {
  if (typeof window === "undefined") return null;
  const value = localStorage.getItem(STORAGE_KEY_COOKIE_CONSENT);
  if (value === "accepted" || value === "declined") return value;
  return null;
}

export function setCookieConsent(consent: CookieConsent): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY_COOKIE_CONSENT, consent);
}

export function getDismissedPolicyVersion(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(STORAGE_KEY_POLICY_VERSION);
}

export function dismissPolicyVersion(version: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY_POLICY_VERSION, version);
}

/**
 * Fire-and-forget: persist consent decision to the DB audit trail.
 * Only fires if the user is authenticated (privy-token cookie exists).
 * Fails silently — localStorage remains the UX source of truth.
 */
export function syncConsentToServer(
  status: CookieConsent,
  policyVersion: string
): void {
  if (typeof window === "undefined") return;
  if (!document.cookie.includes("privy-token")) return;

  fetch("/api/account/consent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      consentType: "analytics",
      status,
      policyVersion,
    }),
  }).catch(() => {
    // Silent failure — audit trail is best-effort
  });
}
