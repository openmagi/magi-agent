import { z } from "zod";
import { VALID_MODELS, VALID_KEY_MODES, VALID_ROUTER_TYPES } from "@/lib/constants";

const telegramBotTokenSchema = z
  .string()
  .regex(/^\d{5,20}:[A-Za-z0-9_-]{30,100}$/, "Invalid bot token");

/* ─── Bot schemas ─── */

export const createBotSchema = z.object({
  modelSelection: z.enum(VALID_MODELS),
  telegramBotToken: telegramBotTokenSchema.optional(),
  telegramBotUsername: z.string().optional(),
  telegramUserHandle: z.string().optional(),
  apiKeyMode: z.enum(VALID_KEY_MODES),
  anthropicApiKey: z.string().nullable().optional(),
  fireworksApiKey: z.string().nullable().optional(),
  openaiApiKey: z.string().nullable().optional(),
  geminiApiKey: z.string().nullable().optional(),
  codexAccessToken: z.string().nullable().optional(),
  codexRefreshToken: z.string().nullable().optional(),
  customBaseUrl: z.string().url().max(500).nullable().optional(),
  pricingTier: z.enum(["pro", "pro_plus", "max", "flex"]).optional(),
  billingInterval: z.enum(["monthly", "yearly"]).optional(),
  personalityPreset: z.string().nullable().optional(),
  customStyle: z.string().max(1000, "Style reference too long (max 1000 characters)").nullable().optional(),
  language: z.string().optional(),
  routerType: z.enum(VALID_ROUTER_TYPES).optional(),
  referralCode: z.string().max(30).nullable().optional(),
  disabledSkills: z.array(z.string().max(100)).max(200).optional(),
  purposeCategory: z.string().max(50).nullable().optional(),
});

export type CreateBotInput = z.infer<typeof createBotSchema>;

export const updateBotSettingsSchema = z.object({
  name: z.string().min(1).max(100).optional(),
  model_selection: z.enum(VALID_MODELS).optional(),
  api_key_mode: z.enum(VALID_KEY_MODES).optional(),
  anthropic_api_key: z.string().optional(),
  fireworks_api_key: z.string().optional(),
  openai_api_key: z.string().optional(),
  codex_access_token: z.string().optional(),
  codex_refresh_token: z.string().optional(),
  brave_api_key: z.string().optional(),
  elevenlabs_api_key: z.string().optional(),
  groq_api_key: z.string().optional(),
  deepl_api_key: z.string().optional(),
  alpha_vantage_api_key: z.string().optional(),
  finnhub_api_key: z.string().optional(),
  fmp_api_key: z.string().optional(),
  fred_api_key: z.string().optional(),
  dart_api_key: z.string().optional(),
  firecrawl_api_key: z.string().optional(),
  semantic_scholar_api_key: z.string().optional(),
  serper_api_key: z.string().optional(),
  github_token: z.string().optional(),
  google_api_key: z.string().optional(),
  google_ads_developer_token: z.string().optional(),
  gemini_api_key: z.string().optional(),
  zapier_mcp_url: z.string().url().max(500).or(z.literal("")).optional(),
  custom_base_url: z.string().url().max(500).or(z.literal("")).optional(),
  bot_purpose: z.string().max(1000).optional(),
  purpose_preset: z.string().optional(),
  language: z.string().optional(),
  router_type: z.enum(VALID_ROUTER_TYPES).optional(),
  disabled_skills: z.array(z.string()).optional(),
  agent_rules: z.string().max(5000).nullable().optional(),
  agent_config: z.object({
    builtin_presets: z.record(
      z.string(),
      z.object({
        enabled: z.boolean(),
        mode: z.enum(["hybrid", "deterministic", "llm"]),
      }),
    ).optional(),
  }).optional(),
}).refine((data) => Object.keys(data).length > 0, {
  message: "No valid fields to update",
});

/* ─── Billing schemas ─── */

export const creditPurchaseSchema = z.object({
  amountCents: z.number().int().min(100, "Amount must be at least $1.00").max(100000, "Amount must be at most $1,000.00"),
});

export const switchPlanSchema = z.object({
  targetPlan: z.enum(["byok", "pro", "pro_plus", "max", "flex"]),
  billingInterval: z.enum(["monthly", "yearly"]).optional(),
  anthropicApiKey: z.string().optional(),
  fireworksApiKey: z.string().optional(),
  openaiApiKey: z.string().optional(),
  codexAccessToken: z.string().optional(),
  codexRefreshToken: z.string().optional(),
  customBaseUrl: z.string().url().max(500).optional(),
});

/* ─── USDC schema ─── */

export const usdcPaymentSchema = z.object({
  txHash: z.string().regex(/^0x[a-fA-F0-9]{64}$/, "Invalid transaction hash"),
});

/* ─── Onramp schema ─── */

export const onrampSessionTokenSchema = z.object({
  walletAddress: z.string().regex(/^0x[a-fA-F0-9]{40}$/, "Invalid wallet address"),
});

/* ─── Referral schema ─── */

export const payoutAddressSchema = z.object({
  address: z.string().regex(/^0x[a-fA-F0-9]{40}$/, "Invalid Ethereum address").nullable(),
});

/* ─── Onboarding schema ─── */

export const validateTokenSchema = z.object({
  token: telegramBotTokenSchema,
});

/* ─── SIWA schemas ─── */

export const siwaSignSchema = z.object({
  domain: z.string().min(1, "Domain is required").max(253),
  uri: z.string().url("URI must be a valid URL"),
  nonce: z.string().min(1, "Nonce is required").max(256),
  statement: z.string().max(1000).optional(),
  chainId: z.number().int().positive().optional(),
});

export const siwaVerifySchema = z.object({
  message: z.string().min(1, "Message is required"),
  signature: z.string().regex(/^0x[a-fA-F0-9]+$/, "Invalid signature format"),
});

/* ─── x402 schema ─── */

export const x402PaySchema = z.object({
  paymentRequiredHeader: z.string().min(1, "Payment required header is required"),
  targetUrl: z.string().url("Target URL must be a valid URL"),
});

/* ─── Admin schema ─── */

export const adminCreditsSchema = z.object({
  amountCents: z.number().int(),
  description: z.string().min(1, "Description is required"),
});

export const adminTossplaceMerchantSchema = z.object({
  merchantId: z.string().trim().min(1, "merchantId is required").max(100),
  merchantName: z.string().trim().max(200).optional().or(z.literal("")),
  note: z.string().trim().max(500).optional().or(z.literal("")),
});

/* ─── Connect Telegram schema ─── */

export const connectTelegramSchema = z.object({
  telegramBotToken: telegramBotTokenSchema,
  telegramBotUsername: z.string().optional(),
  telegramUserHandle: z.string().optional(),
});

/* ─── Connect Discord schema ─── */

export const connectDiscordSchema = z.object({
  discordBotToken: z.string().min(1, "Discord bot token is required").max(200),
});

/* ─── Organization schemas ─── */

export const createOrgSchema = z.object({
  name: z.string().min(1, "Name is required").max(100),
  slug: z.string().min(2).max(50).regex(/^[a-z0-9-]+$/, "Slug must be lowercase alphanumeric with hyphens"),
});

export const updateOrgSchema = z.object({
  name: z.string().min(1).max(100).optional(),
  bot_template: z.record(z.string(), z.unknown()).optional(),
}).refine((data) => Object.keys(data).length > 0, {
  message: "No valid fields to update",
});

export const orgInviteSchema = z.object({
  email: z.string().email("Valid email required"),
});

export const orgCreditTopupSchema = z.object({
  amountCents: z.number().int().min(100).max(100000),
});
