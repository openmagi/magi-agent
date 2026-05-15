import { afterEach, describe, it, expect } from "vitest";
import {
  selectRequirement,
  validateX402Requirement,
} from "./payment-service";
import type { PaymentRequired, PaymentRequirements } from "@x402/core/types";

const ORIGINAL_ENV = { ...process.env };

function baseUsdcRequirement(
  overrides: Partial<PaymentRequirements> = {},
): PaymentRequirements {
  return {
    network: "eip155:8453",
    asset: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    amount: "1000000",
    payTo: "0x1111111111111111111111111111111111111111",
    resource: "https://api.vendor.test/pay",
    scheme: "exact",
    ...overrides,
  } as unknown as PaymentRequirements;
}

function setStrictX402Env(): void {
  process.env.X402_ALLOWED_PAY_TO = "0x1111111111111111111111111111111111111111";
  process.env.X402_ALLOWED_DOMAINS = "api.vendor.test";
}

describe("x402 payment-service", () => {
  afterEach(() => {
    process.env = { ...ORIGINAL_ENV };
  });

  describe("selectRequirement", () => {
    it("should prefer USDC on Base chain", () => {
      const paymentRequired = {
        x402Version: 2,
        accepts: [
          {
            network: "eip155:1",
            asset: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            amount: "1000000",
            scheme: "exact",
          },
          {
            network: "eip155:8453",
            asset: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            amount: "1000000",
            scheme: "exact",
          },
        ],
      } as unknown as PaymentRequired;

      const req = selectRequirement(paymentRequired);
      expect(req?.network).toBe("eip155:8453");
      expect(req?.asset?.toLowerCase()).toBe(
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
      );
    });

    it("returns null when the Base requirement is not USDC", () => {
      const paymentRequired = {
        x402Version: 2,
        accepts: [
          {
            network: "eip155:1",
            asset: "0xSomeToken",
            amount: "1000",
            scheme: "exact",
          },
          {
            network: "eip155:8453",
            asset: "0xOtherToken",
            amount: "500",
            scheme: "exact",
          },
        ],
      } as unknown as PaymentRequired;

      const req = selectRequirement(paymentRequired);
      expect(req).toBeNull();
    });

    it("returns null when no Base USDC requirement is available", () => {
      const paymentRequired = {
        x402Version: 2,
        accepts: [
          {
            network: "eip155:1",
            asset: "0xSomeToken",
            amount: "1000",
            scheme: "exact",
          },
        ],
      } as unknown as PaymentRequired;

      const req = selectRequirement(paymentRequired);
      expect(req).toBeNull();
    });

    it("should return null for empty requirements", () => {
      const paymentRequired = {
        x402Version: 2,
        accepts: [],
      } as unknown as PaymentRequired;

      const req = selectRequirement(paymentRequired);
      expect(req).toBeNull();
    });
  });

  describe("validateX402Requirement", () => {
    it("rejects payment validation when the payTo allowlist is not configured", () => {
      delete process.env.X402_ALLOWED_PAY_TO;
      process.env.X402_ALLOWED_DOMAINS = "api.vendor.test";

      expect(() =>
        validateX402Requirement(
          baseUsdcRequirement(),
          "https://api.vendor.test/pay",
          { botId: "bot-1" },
        ),
      ).toThrow(/X402_ALLOWED_PAY_TO/i);
    });

    it("rejects payment validation when the target domain allowlist is not configured", () => {
      process.env.X402_ALLOWED_PAY_TO = "0x1111111111111111111111111111111111111111";
      delete process.env.X402_ALLOWED_DOMAINS;

      expect(() =>
        validateX402Requirement(
          baseUsdcRequirement(),
          "https://api.vendor.test/pay",
          { botId: "bot-1" },
        ),
      ).toThrow(/X402_ALLOWED_DOMAINS/i);
    });

    it("rejects payment requirements above the server maximum before signing", () => {
      process.env.X402_MAX_AMOUNT_USDC = "1";

      expect(() =>
        validateX402Requirement(
          baseUsdcRequirement({ amount: "1000001" }),
          "https://api.vendor.test/pay",
          { botId: "bot-1" },
        ),
      ).toThrow(/exceeds/i);
    });

    it("rejects payment requirements outside the required payTo allowlist", () => {
      process.env.X402_ALLOWED_PAY_TO = "0x2222222222222222222222222222222222222222";
      process.env.X402_ALLOWED_DOMAINS = "api.vendor.test";

      expect(() =>
        validateX402Requirement(
          baseUsdcRequirement(),
          "https://api.vendor.test/pay",
          { botId: "bot-1" },
        ),
      ).toThrow(/payTo/i);
    });

    it("rejects payment requirements outside the required domain allowlist", () => {
      process.env.X402_ALLOWED_PAY_TO = "0x1111111111111111111111111111111111111111";
      process.env.X402_ALLOWED_DOMAINS = "api.allowed.test";

      expect(() =>
        validateX402Requirement(
          baseUsdcRequirement(),
          "https://api.vendor.test/pay",
          { botId: "bot-1" },
        ),
      ).toThrow(/domain/i);
    });

    it("rejects missing or mismatched target URL resource bindings", () => {
      setStrictX402Env();

      expect(() =>
        validateX402Requirement(
          baseUsdcRequirement({ resource: undefined }),
          "https://api.vendor.test/pay",
          { botId: "bot-1" },
        ),
      ).toThrow(/resource binding/i);

      expect(() =>
        validateX402Requirement(
          baseUsdcRequirement({ resource: "https://api.vendor.test/other" }),
          "https://api.vendor.test/pay",
          { botId: "bot-1" },
        ),
      ).toThrow(/target URL/i);
    });
  });
});
