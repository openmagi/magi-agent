// Raw provider cost in cents per 1M tokens (before VAT)
// Anthropic (2026): Haiku $1/$5, Sonnet $3/$15, Opus $5/$25
// OpenAI (2026 GPT-5.5): 5.4 nano $0.20/$1.25, 5.4 mini $0.75/$4.50, 5.5 $5/$30, 5.5 Pro $30/$180
// Fireworks: Kimi K2.6 $0.95/$4.00, MiniMax M2.7 $0.30/$1.20
// Google: Gemini 3.1 Flash Lite $0.25/$1.50, Pro $2.00/$12.00
export const PRICING_CENTS_PER_MILLION: Record<
  string,
  { input: number; output: number; cacheRead?: number; cacheReadMultiplier?: number }
> = {
  "claude-haiku-4-5": { input: 100, output: 500 },
  "claude-sonnet-4-6": { input: 300, output: 1500 },
  "claude-sonnet-4-5": { input: 300, output: 1500 },
  "claude-opus-4-6": { input: 500, output: 2500 },
  "kimi-k2p6": { input: 95, output: 400, cacheRead: 16 },
  "minimax-m2p7": { input: 30, output: 120, cacheRead: 3 },
  "gpt-5.4-nano": { input: 20, output: 125 },
  "gpt-5.4-mini": { input: 75, output: 450 },
  "gpt-5.5-pro": { input: 3000, output: 18000, cacheReadMultiplier: 1 },
  "gpt-5.5": { input: 500, output: 3000 },
  "gemini-3.1-flash-lite-preview": { input: 25, output: 150 },
  "gemini-2.5-pro": { input: 125, output: 1000 },
  "gemini-3.1-pro-preview": { input: 200, output: 1200 },
  "local/gemma-fast": { input: 300, output: 1500 },
  "local/gemma-max": { input: 300, output: 1500 },
  "local/qwen-uncensored": { input: 300, output: 1500 },
  "gemma-fast": { input: 300, output: 1500 },
  "gemma-max": { input: 300, output: 1500 },
  "qwen-uncensored": { input: 300, output: 1500 },
};

// 0% platform markup. LLM calls pass through provider cost plus 10% VAT.
const VAT_MULTIPLIER = 1.1;

/** Display pricing per million tokens (dollars). Pre-computed service rates. */
export const DISPLAY_PRICING_PER_MILLION: {
  model: string;
  input: number;
  cacheCreation: number | null;
  cacheRead: number;
  output: number;
}[] = [
  { model: "Claude Haiku 4.5", input: 1.10, cacheCreation: 1.375, cacheRead: 0.11, output: 5.50 },
  { model: "Claude Sonnet 4.6", input: 3.30, cacheCreation: 4.125, cacheRead: 0.33, output: 16.50 },
  { model: "Claude Opus 4.6", input: 5.50, cacheCreation: 6.875, cacheRead: 0.55, output: 27.50 },
  { model: "Kimi K2.6", input: 1.045, cacheCreation: null, cacheRead: 0.176, output: 4.40 },
  { model: "MiniMax M2.7", input: 0.33, cacheCreation: null, cacheRead: 0.033, output: 1.32 },
  { model: "GPT-5.4 Nano", input: 0.22, cacheCreation: null, cacheRead: 0.022, output: 1.375 },
  { model: "GPT-5.4 Mini", input: 0.825, cacheCreation: null, cacheRead: 0.0825, output: 4.95 },
  { model: "GPT-5.5", input: 5.50, cacheCreation: null, cacheRead: 0.55, output: 33.00 },
  { model: "GPT-5.5 Pro", input: 33.00, cacheCreation: null, cacheRead: 33.00, output: 198.00 },
  { model: "Gemini 3.1 Flash Lite", input: 0.275, cacheCreation: null, cacheRead: 0.0275, output: 1.65 },
  { model: "Gemini 3.1 Pro", input: 2.20, cacheCreation: null, cacheRead: 0.22, output: 13.20 },
];

export function calculateCostCents(
  model: string,
  inputTokens: number,
  outputTokens: number,
  cacheCreationTokens = 0,
  cacheReadTokens = 0,
): number {
  const normalizedModel = Object.keys(PRICING_CENTS_PER_MILLION)
    .sort((a, b) => b.length - a.length)
    .find((k) => model.includes(k));
  if (!normalizedModel) return 0;

  const pricing = PRICING_CENTS_PER_MILLION[normalizedModel];
  const inputCost = (inputTokens / 1_000_000) * pricing.input;
  const outputCost = (outputTokens / 1_000_000) * pricing.output;
  const cacheReadRate = pricing.cacheRead ?? pricing.input * (pricing.cacheReadMultiplier ?? 0.1);
  // Cache tokens are passed separately by callers. Some providers expose exact
  // cached-input pricing; otherwise cache reads default to the model multiplier.
  const cacheCreationCost = (cacheCreationTokens / 1_000_000) * pricing.input * 1.25;
  const cacheReadCost = (cacheReadTokens / 1_000_000) * cacheReadRate;
  const totalCost = (inputCost + outputCost + cacheCreationCost + cacheReadCost) * VAT_MULTIPLIER;
  return Math.ceil(Number(totalCost.toFixed(10)));
}
