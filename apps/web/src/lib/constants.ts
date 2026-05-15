import { LOCAL_LLM_MODEL_OPTIONS } from "@/lib/models/local-llm";

// ── Platform Limits ──────────────────────────────────────────────────────────
export const FALLBACK_MAX_SEATS = 50;
export const TRIAL_DURATION_DAYS = 14;

// ── Cluster Capacity Calculation ─────────────────────────────────────────────
/** Memory request per bot pod (gateway 256Mi + node-host 128Mi + router 64Mi) */
export const BOT_MEMORY_REQUEST_MI = 448;
/** Cluster-wide overhead for system workloads (provisioning worker, Longhorn, etc.) */
export const CLUSTER_SYSTEM_OVERHEAD_MI = 2560;
/** Cache TTL for cluster capacity queries */
export const CLUSTER_CAPACITY_CACHE_TTL_MS = 5 * 60 * 1000;

// ── Model Configuration ─────────────────────────────────────────────────────
export const VALID_MODELS = [
  "smart_routing",
  "haiku",
  "sonnet",
  "opus",
  "kimi_k2_5",
  "minimax_m2_7",
  "gpt_5_nano",
  "gpt_5_mini",
  "gpt_5_5",
  "gpt_5_5_pro",
  "gpt_smart_routing",
  "codex",
  "clawy_smart_routing",
  "local_gemma_fast",
  "local_gemma_max",
  "local_qwen_uncensored",
  "gemini_3_1_flash_lite",
  "gemini_3_1_pro",
] as const;

export type ValidModel = (typeof VALID_MODELS)[number];

export const VALID_KEY_MODES = ["byok", "platform_credits"] as const;

export type ValidKeyMode = (typeof VALID_KEY_MODES)[number];

export const MODEL_LABELS: Record<string, string> = {
  smart_routing: "Smart Routing",
  haiku: "Claude Haiku 4.5",
  sonnet: "Claude Sonnet 4.5",
  opus: "Claude Opus 4.6",
  kimi_k2_5: "Kimi K2.6",
  minimax_m2_7: "MiniMax M2.7",
  gpt_5_nano: "GPT-5.4 Nano",
  gpt_5_mini: "GPT-5.4 Mini",
  gpt_5_5: "GPT-5.5",
  gpt_5_5_pro: "GPT-5.5 Pro",
  gpt_smart_routing: "GPT Smart Routing",
  codex: "Codex",
  clawy_smart_routing: "Open Magi Router",
  ...Object.fromEntries(
    LOCAL_LLM_MODEL_OPTIONS.map((model) => [model.value, model.label]),
  ),
  gemini_3_1_flash_lite: "Gemini 3.1 Flash Lite",
  gemini_3_1_pro: "Gemini 3.1 Pro",
};

// ── Router Types ─────────────────────────────────────────────────────────────
/**
 * clawy_smart_routing sub-flavours. `standard` is the original router set;
 * `big_dic` (FLEX-only), `only_claude` and `claude_supremacy` are newer
 * specialised routers. Values map 1:1 to what the provisioning worker
 * templates into the router sidecar — keep in sync with
 * `src/lib/provisioning/config-builder.ts` and `template-engine.ts`.
 */
export const VALID_ROUTER_TYPES = ["standard", "big_dic", "only_claude", "claude_supremacy"] as const;

export type ValidRouterType = (typeof VALID_ROUTER_TYPES)[number];

export const ROUTER_TYPE_LABELS: Record<string, string> = {
  standard: "Standard",
  big_dic: "Big Dic (FLEX)",
  only_claude: "Only Claude",
  claude_supremacy: "Claude Supremacy",
};

// ── USDC / Base Chain ────────────────────────────────────────────────────────
export const USDC_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
export const RECEIVING_WALLET = "0x6a2f675f5f81909eecd1966a15c90877bc106858";
export const BASE_CHAIN_ID = 8453;
