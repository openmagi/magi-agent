import { describe, it, expect } from "vitest";
import { calculateCostCents } from "./pricing";

// VAT_MULTIPLIER in pricing.ts = 1.1. No platform markup is applied.
// Math.ceil() applies at the boundary.
describe("pricing", () => {
  it("calculates haiku cost with VAT only", () => {
    // 1M input at 100 ¢/M + 1M output at 500 ¢/M = 600 cents
    // With 1.1x VAT = 660
    const cost = calculateCostCents("claude-haiku-4-5", 1_000_000, 1_000_000);
    expect(cost).toBe(660);
  });

  it("calculates sonnet 4.6 cost", () => {
    const cost = calculateCostCents("claude-sonnet-4-6", 1_000_000, 500_000);
    // (300 + 750) * 1.1 = 1155
    expect(cost).toBe(1155);
  });

  it("charges Mac Studio local models at the Sonnet 4.6 rate", () => {
    const sonnet = calculateCostCents("claude-sonnet-4-6", 1_000_000, 500_000);
    expect(calculateCostCents("local/gemma-fast", 1_000_000, 500_000)).toBe(sonnet);
    expect(calculateCostCents("local/gemma-max", 1_000_000, 500_000)).toBe(sonnet);
    expect(calculateCostCents("local/qwen-uncensored", 1_000_000, 500_000)).toBe(sonnet);
  });

  it("returns 0 for unknown model", () => {
    const cost = calculateCostCents("unknown-model", 1000, 1000);
    expect(cost).toBe(0);
  });

  it("handles partial model names", () => {
    const cost = calculateCostCents("anthropic/claude-haiku-4-5-20251001", 1_000_000, 0);
    // 100 * 1.1 = 110; floating-point noise must not add a cent.
    expect(cost).toBe(110);
  });

  it("calculates GPT-5.5 cost with current OpenAI pricing", () => {
    const cost = calculateCostCents("openai/gpt-5.5", 1_000_000, 1_000_000);
    // ($5 input + $30 output) * 1.1 = $38.50
    expect(cost).toBe(3850);
  });

  it("calculates GPT-5.5 Pro cost with current OpenAI pricing", () => {
    const cost = calculateCostCents("openai/gpt-5.5-pro", 1_000_000, 1_000_000);
    // ($30 input + $180 output) * 1.1 = $231.00
    expect(cost).toBe(23100);
  });

  it("charges GPT-5.5 Pro cache-read tokens at full input price", () => {
    const cost = calculateCostCents("openai/gpt-5.5-pro", 0, 0, 0, 1_000_000);
    // GPT-5.5 Pro does not support cached input discounts.
    expect(cost).toBe(3300);
  });

  it("calculates GPT-5.4 nano cost with current OpenAI pricing", () => {
    const cost = calculateCostCents("openai/gpt-5.4-nano", 1_000_000, 1_000_000);
    // ($0.20 input + $1.25 output) * 1.1 = $1.595
    expect(cost).toBe(160);
  });

  it("calculates GPT-5.4 mini cost with current OpenAI pricing", () => {
    const cost = calculateCostCents("openai/gpt-5.4-mini", 1_000_000, 1_000_000);
    // ($0.75 input + $4.50 output) * 1.1 = $5.775
    expect(cost).toBe(578);
  });

  it("calculates Kimi K2.6 Fireworks cost", () => {
    const cost = calculateCostCents("fireworks/kimi-k2p6", 1_000_000, 1_000_000);
    // ($0.95 input + $4.00 output) * 1.1 = $5.445
    expect(cost).toBe(545);
  });

  it("uses Fireworks Kimi K2.6 cached input pricing", () => {
    const cost = calculateCostCents("fireworks/kimi-k2p6", 0, 0, 0, 1_000_000);
    // $0.16 * 1.1 = $0.176
    expect(cost).toBe(18);
  });

  it("includes cache creation tokens at 125% of input price", () => {
    // Opus: input = 500 ¢/M
    // 1M cache creation at 500 * 1.25 = 625 ¢/M
    // With 1.1x VAT = 687.5 → ceil = 688
    const cost = calculateCostCents("claude-opus-4-6", 0, 0, 1_000_000, 0);
    expect(cost).toBe(688);
  });

  it("includes cache read tokens at 10% of input price", () => {
    // Haiku: input = 100 ¢/M
    // 1M cache read at 100 * 0.1 = 10 ¢/M
    // With 1.1x VAT = 11
    const cost = calculateCostCents("claude-haiku-4-5", 0, 0, 0, 1_000_000);
    expect(cost).toBe(11);
  });

  it("calculates full cost with all token types", () => {
    // Opus: input=500, output=2500 ¢/M
    // 100K input = 50 ¢, 200K output = 500 ¢
    // 400K cache creation = 400/1000 * 500 * 1.25 = 250 ¢
    // 500K cache read = 500/1000 * 500 * 0.1 = 25 ¢
    // Total = 825 ¢ * 1.1 = 907.5 → ceil = 908
    const cost = calculateCostCents("claude-opus-4-6", 100_000, 200_000, 400_000, 500_000);
    expect(cost).toBe(908);
  });

  it("defaults cache tokens to 0 for backward compatibility", () => {
    const withCache = calculateCostCents("claude-opus-4-6", 1_000_000, 1_000_000, 0, 0);
    const withoutCache = calculateCostCents("claude-opus-4-6", 1_000_000, 1_000_000);
    expect(withCache).toBe(withoutCache);
  });
});
