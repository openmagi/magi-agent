import { describe, it, expect } from "vitest";
import {
  generateReferralCode,
  isValidCustomCode,
  calculateEarningCents,
  getSettlementPeriodMonth,
  centsToUsdcString,
} from "./utils";

describe("referral utils", () => {
  describe("generateReferralCode", () => {
    it("generates code with REF- prefix and 8 alphanumeric chars", () => {
      const code = generateReferralCode();
      expect(code).toMatch(/^REF-[A-Z0-9]{8}$/);
    });

    it("generates unique codes", () => {
      const codes = new Set(Array.from({ length: 100 }, () => generateReferralCode()));
      expect(codes.size).toBe(100);
    });
  });

  describe("isValidCustomCode", () => {
    it("accepts valid alphanumeric codes with hyphens", () => {
      expect(isValidCustomCode("KEVIN2026")).toBe(true);
      expect(isValidCustomCode("my-code")).toBe(true);
      expect(isValidCustomCode("ABCD")).toBe(true);
    });

    it("rejects codes shorter than 4 chars", () => {
      expect(isValidCustomCode("AB")).toBe(false);
    });

    it("rejects codes longer than 20 chars", () => {
      expect(isValidCustomCode("A".repeat(21))).toBe(false);
    });

    it("rejects codes with special characters", () => {
      expect(isValidCustomCode("code!@#")).toBe(false);
      expect(isValidCustomCode("code space")).toBe(false);
    });
  });

  describe("calculateEarningCents", () => {
    it("returns 10% of source amount", () => {
      expect(calculateEarningCents(1500)).toBe(150);
      expect(calculateEarningCents(3900)).toBe(390);
    });

    it("rounds down fractional cents", () => {
      expect(calculateEarningCents(1550)).toBe(155);
      expect(calculateEarningCents(333)).toBe(33);
    });
  });

  describe("getSettlementPeriodMonth", () => {
    it("returns next month for a given date", () => {
      expect(getSettlementPeriodMonth(new Date("2026-02-15"))).toBe("2026-03");
      expect(getSettlementPeriodMonth(new Date("2026-12-01"))).toBe("2027-01");
    });
  });

  describe("centsToUsdcString", () => {
    it("converts cents to USDC string with 6 decimals", () => {
      expect(centsToUsdcString(1000)).toBe("10.000000");
      expect(centsToUsdcString(150)).toBe("1.500000");
    });
  });
});
