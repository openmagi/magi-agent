/** Normalize raw model strings to short display keys */
export function normalizeModelName(raw: string): string {
  const lower = raw.toLowerCase();
  if (lower.startsWith("firecrawl:")) return lower; // preserve firecrawl:scrape etc.
  if (lower.includes("opus")) return "opus";
  if (lower.includes("sonnet")) return "sonnet";
  if (lower.includes("haiku")) return "haiku";
  if (lower.includes("kimi") || /k2p[56]|k2[-_][56]/.test(lower)) return "kimi";
  if (lower.includes("minimax") || lower.includes("m2p5") || lower.includes("m2-5") || lower.includes("m2_5")) return "minimax";
  if (lower.includes("gpt-5.4-nano")) return "gpt_5_nano";
  if (lower.includes("gpt-5.4-mini") || lower.includes("gpt_5_mini")) return "gpt_5_mini";
  if (lower.includes("gpt-5.1") || lower.includes("gpt_5_1")) return "gpt_5_mini";
  if (lower.includes("gpt-5.5-pro") || lower.includes("gpt_5_5_pro")) return "gpt_5_5_pro";
  if (lower.includes("gpt-5.5") || lower.includes("gpt-5.4")) return "gpt_5_5";
  if (lower.includes("codex")) return "codex";
  if (lower.includes("gpt-5")) return "gpt_5_5";
  if (lower.includes("gemini-3.1-flash-lite") || lower.includes("gemini_3_1_flash_lite")) return "gemini_3_1_flash";
  if (lower.includes("gemini-3.1-flash") || lower.includes("gemini_3_1_flash")) return "gemini_3_1_flash";
  if (lower.includes("gemini-3.1-pro") || lower.includes("gemini_3_1_pro")) return "gemini_3_1_pro";
  if (lower.includes("gemini-2.5-flash") || lower.includes("gemini_2_5_flash")) return "gemini_2_5_flash";
  if (lower.includes("gemini-2.5-pro") || lower.includes("gemini_2_5_pro")) return "gemini_2_5_pro";
  if (lower.includes("gemini")) return "gemini_2_5_pro";
  return raw || "unknown";
}

/** Check if a model key represents a firecrawl operation */
export function isFirecrawlModel(model: string): boolean {
  return model.toLowerCase().startsWith("firecrawl:");
}
