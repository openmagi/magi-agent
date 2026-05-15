import { randomBytes } from "crypto";

const CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const CODE_LENGTH = 8;
const CUSTOM_CODE_RE = /^[a-zA-Z0-9-]{4,20}$/;
const EARNING_RATE = 0.10;
const MIN_ELIGIBLE_SPEND_CENTS = 799;
const MIN_CLAIM_CENTS = 1000;
const DAILY_PAYOUT_LIMIT_CENTS = 50000;

export { MIN_ELIGIBLE_SPEND_CENTS, MIN_CLAIM_CENTS, DAILY_PAYOUT_LIMIT_CENTS };

export function generateReferralCode(): string {
  const bytes = randomBytes(CODE_LENGTH);
  const chars = Array.from(bytes, (b) => CODE_CHARS[b % CODE_CHARS.length]).join("");
  return `REF-${chars}`;
}

export function isValidCustomCode(code: string): boolean {
  return CUSTOM_CODE_RE.test(code);
}

export function calculateEarningCents(sourceAmountCents: number): number {
  return Math.floor(sourceAmountCents * EARNING_RATE);
}

export function getSettlementPeriodMonth(paymentDate: Date): string {
  const year = paymentDate.getUTCFullYear();
  const month = paymentDate.getUTCMonth() + 1;
  if (month === 12) {
    return `${year + 1}-01`;
  }
  return `${year}-${String(month + 1).padStart(2, "0")}`;
}

export function centsToUsdcString(cents: number): string {
  return (cents / 100).toFixed(6);
}
