"use client";

export function storeReferralCode(): void {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);
  const ref = params.get("ref");
  if (ref) {
    document.cookie = `ref_code=${encodeURIComponent(ref)};path=/;max-age=${30 * 24 * 60 * 60};samesite=lax`;
  }
}

export function getStoredReferralCode(): string | null {
  if (typeof window === "undefined") return null;
  const match = document.cookie.match(/ref_code=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export function clearReferralCode(): void {
  if (typeof window === "undefined") return;
  document.cookie = "ref_code=;path=/;max-age=0;samesite=lax";
}
