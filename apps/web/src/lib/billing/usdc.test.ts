import { describe, it, expect } from "vitest";
import {
  usdcRawToCreditCents,
  USDC_RAW_PER_CREDIT_CENT,
  USDC_DECIMALS,
  MIN_CONFIRMATIONS,
  BASE_CHAIN_ID,
  USDC_CONTRACT,
  RECEIVING_WALLET,
} from "./usdc";

describe("USDC constants", () => {
  it("uses Base chain ID 8453", () => {
    expect(BASE_CHAIN_ID).toBe(8453);
  });

  it("uses 6 decimals for USDC", () => {
    expect(USDC_DECIMALS).toBe(6);
  });

  it("requires minimum 5 confirmations", () => {
    expect(MIN_CONFIRMATIONS).toBe(5);
  });

  it("has valid USDC contract address", () => {
    expect(USDC_CONTRACT).toMatch(/^0x[a-fA-F0-9]{40}$/);
  });

  it("has valid receiving wallet address", () => {
    expect(RECEIVING_WALLET).toMatch(/^0x[a-fA-F0-9]{40}$/);
  });
});

describe("usdcRawToCreditCents", () => {
  it("converts 1 USDC to 100 credit cents", () => {
    const oneUsdc = BigInt(10 ** 6); // 1_000_000
    expect(usdcRawToCreditCents(oneUsdc)).toBe(100);
  });

  it("converts 25 USDC to 2500 credit cents", () => {
    const raw = BigInt(25 * 10 ** 6);
    expect(usdcRawToCreditCents(raw)).toBe(2500);
  });

  it("converts 0.50 USDC to 50 credit cents", () => {
    const raw = BigInt(500_000);
    expect(usdcRawToCreditCents(raw)).toBe(50);
  });

  it("truncates sub-cent amounts to zero", () => {
    // 0.009999 USDC = 9999 raw => 9999 / 10000 = 0
    expect(usdcRawToCreditCents(BigInt(9999))).toBe(0);
  });

  it("converts 100 USDC to 10000 credit cents", () => {
    const raw = BigInt(100 * 10 ** 6);
    expect(usdcRawToCreditCents(raw)).toBe(10000);
  });

  it("uses correct ratio constant", () => {
    expect(USDC_RAW_PER_CREDIT_CENT).toBe(BigInt(10_000));
  });
});
