import { createAdminClient } from "@/lib/supabase/admin";
import { getStripe } from "@/lib/api/stripe";
import { encrypt } from "@/lib/crypto";
import { ensureProfile } from "@/lib/privy/ensure-profile";
import { createK8sClient } from "@/lib/provisioning/k8s-client";
import { triggerProvisioning } from "@/lib/provisioning/trigger";
import { getClusterBotCapacity } from "@/lib/services/cluster-capacity";
import { setOwnerWebhook } from "@/lib/telegram/webhook";
import { captureServerEvent } from "@/lib/posthog/server";
import { bindReferral, prepareReferralCheckoutDiscounts } from "@/lib/referral/checkout";
import { getPriceId, PLAN_MAX_BOTS } from "@/lib/billing/plans";
import { TRIAL_DURATION_DAYS } from "@/lib/constants";
import { getLocalLlmModelEntitlementError } from "@/lib/models/local-llm";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { CreateBotInput } from "@/lib/validation";

const ADJECTIVES = ["swift", "bright", "calm", "bold", "keen", "warm", "cool", "wild", "fair", "wise", "glad", "pure", "lucky", "witty", "zippy", "noble", "vivid", "smart", "agile", "rapid"];
const NOUNS = ["fox", "owl", "bear", "hawk", "lynx", "wolf", "hare", "wren", "pike", "dove", "elk", "crow", "seal", "frog", "moth", "yak", "ram", "bee", "ant", "ray"];

function randomAdjective(): string { return ADJECTIVES[Math.floor(Math.random() * ADJECTIVES.length)]; }
function randomNoun(): string { return NOUNS[Math.floor(Math.random() * NOUNS.length)]; }

const BOT_SELECT_FIELDS =
  "id, name, status, model_selection, api_key_mode, router_type, telegram_bot_username, telegram_owner_id, bot_purpose, purpose_preset, language, created_at, updated_at, deployed_version, error_message, provisioning_step" as const;

export const HARD_DELETE_REQUESTED_STEP = "hard_delete_requested";

export const BOT_HARD_DELETE_DATA_TABLES = [
  "chat_messages",
  "app_channel_messages",
  "push_messages",
  "chat_reset_counters",
  "chat_message_deletions",
  "chat_exports",
  "chat_attachments",
  "app_channels",
  "knowledge_documents",
  "knowledge_collections",
  "conversion_jobs",
  "consultation_artifacts",
  "consultation_jobs",
  "gateway_tokens",
  "bot_email_inboxes",
  "bot_x402_inboxes",
  "discord_bot_mappings",
  "bot_wallet_policies",
  "learned_skills",
  "skill_executions",
  "user_interactions",
  "sub_agents_cache",
] as const;

export const BOT_HARD_DELETE_PRESERVED_TABLES = [
  "usage_logs",
  "credit_transactions",
  "service_usage_logs",
  "wallet_usage_logs",
  "stripe_webhook_events",
] as const;

export function isMissingBotHardDeleteTableError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;

  const record = error as {
    code?: unknown;
    message?: unknown;
    details?: unknown;
    hint?: unknown;
  };
  const code = typeof record.code === "string" ? record.code : "";
  const text = [record.message, record.details, record.hint]
    .filter((value): value is string => typeof value === "string")
    .join(" ");

  return (
    code === "PGRST205" ||
    (/Could not find the table/i.test(text) && /schema cache/i.test(text))
  );
}

function getSupabaseErrorMessage(error: unknown): string {
  if (error && typeof error === "object" && "message" in error) {
    const message = (error as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  return "Unknown error";
}

function logSkippedMissingBotCleanupTable(
  action: "read" | "delete",
  table: string,
  error: unknown,
): void {
  console.warn(
    `[bot-service] Skipping ${action} for missing bot cleanup table ${table}: ${getSupabaseErrorMessage(error)}`,
  );
}

interface StoragePathRow {
  storage_path?: string | null;
  source_storage_path?: string | null;
}

interface ConversionJobStorageRow {
  source_storage_path?: string | null;
  result_storage_path?: string | null;
}

interface KnowledgeStorageFile {
  name?: string | null;
}

export function buildBotDeletionStoragePaths({
  botId,
  chatAttachments = [],
  knowledgeOriginalFiles = [],
  conversionJobs = [],
  consultationJobs = [],
  consultationArtifacts = [],
}: {
  botId: string;
  chatAttachments?: StoragePathRow[];
  knowledgeOriginalFiles?: KnowledgeStorageFile[];
  conversionJobs?: ConversionJobStorageRow[];
  consultationJobs?: StoragePathRow[];
  consultationArtifacts?: StoragePathRow[];
}): string[] {
  const paths: string[] = [];
  const add = (value: string | null | undefined) => {
    const path = value?.trim();
    if (path) paths.push(path);
  };

  for (const row of chatAttachments) add(row.storage_path);
  for (const file of knowledgeOriginalFiles) {
    const name = file.name?.trim();
    if (!name) continue;
    paths.push(name.startsWith("knowledge/") ? name : `knowledge/${botId}/${name}`);
  }
  for (const row of conversionJobs) {
    add(row.source_storage_path);
    add(row.result_storage_path);
  }
  for (const row of consultationJobs) add(row.storage_path ?? row.source_storage_path);
  for (const row of consultationArtifacts) add(row.storage_path);

  return [...new Set(paths)];
}

export function buildBotTombstoneUpdate(
  now: string,
  options: {
    unresolvedPrivyWallet?: {
      id: string | null;
      address: string | null;
      chain: string | null;
    };
    errorMessage?: string | null;
  } = {},
): Record<string, unknown> {
  const unresolvedWallet = options.unresolvedPrivyWallet;
  return {
    status: "deleted",
    provisioning_step: HARD_DELETE_REQUESTED_STEP,
    error_message: options.errorMessage ?? null,
    health_status: "unknown",
    container_id: null,
    node_host_port: null,
    gateway_port: null,
    node_name: null,
    last_health_check: null,
    telegram_bot_token: null,
    telegram_bot_username: null,
    telegram_user_handle: null,
    telegram_owner_id: null,
    discord_bot_token: null,
    discord_bot_username: null,
    privy_wallet_id: unresolvedWallet?.id ?? null,
    privy_wallet_address: unresolvedWallet?.address ?? null,
    privy_wallet_chain: unresolvedWallet?.chain ?? null,
    registry_agent_id: null,
    registry_tx_hash: null,
    agent_skill_md: null,
    agent_endpoint_url: null,
    storage_used_bytes: 0,
    kb_storage_used_bytes: 0,
    updated_at: now,
  };
}

async function listStorageObjects(
  supabase: SupabaseClient,
  bucket: string,
  prefix: string,
): Promise<KnowledgeStorageFile[]> {
  const storage = supabase.storage.from(bucket);
  const files: KnowledgeStorageFile[] = [];
  const limit = 1000;

  async function walk(currentPrefix: string): Promise<void> {
    let offset = 0;
    while (true) {
      const { data, error } = await storage.list(currentPrefix, { limit, offset });
      if (error || !data || data.length === 0) break;

      for (const file of data) {
        const fullPath = `${currentPrefix.replace(/\/$/, "")}/${file.name}`;
        const maybeFolder = file as unknown as Record<string, unknown>;
        if (!maybeFolder.id && !maybeFolder.metadata) {
          await walk(fullPath);
        } else {
          files.push({ name: fullPath });
        }
      }

      if (data.length < limit) break;
      offset += limit;
    }
  }

  await walk(prefix);
  return files;
}

async function removeStoragePaths(
  supabase: SupabaseClient,
  bucket: string,
  paths: string[],
): Promise<void> {
  const uniquePaths = [...new Set(paths.filter(Boolean))];
  if (uniquePaths.length === 0) return;

  const storage = supabase.storage.from(bucket);
  for (let i = 0; i < uniquePaths.length; i += 100) {
    const batch = uniquePaths.slice(i, i + 100);
    const { error } = await storage.remove(batch);
    if (error) {
      throw new Error(`Failed to delete bot storage objects: ${error.message}`);
    }
  }
}

async function selectRowsByBotId<T>(
  supabase: SupabaseClient,
  table: string,
  select: string,
  botId: string,
): Promise<T[]> {
  // These cleanup tables are wider than the generated Supabase type surface.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const untyped = supabase as any;
  const { data, error } = await untyped
    .from(table)
    .select(select)
    .eq("bot_id", botId);

  if (error) {
    if (isMissingBotHardDeleteTableError(error)) {
      logSkippedMissingBotCleanupTable("read", table, error);
      return [];
    }
    throw new Error(`Failed to read ${table} for bot deletion: ${getSupabaseErrorMessage(error)}`);
  }

  return (data ?? []) as T[];
}

async function deleteRowsByBotId(
  supabase: SupabaseClient,
  table: string,
  botId: string,
): Promise<void> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const untyped = supabase as any;
  const { error } = await untyped
    .from(table)
    .delete()
    .eq("bot_id", botId);

  if (error) {
    if (isMissingBotHardDeleteTableError(error)) {
      logSkippedMissingBotCleanupTable("delete", table, error);
      return;
    }
    throw new Error(`Failed to delete ${table} for bot deletion: ${getSupabaseErrorMessage(error)}`);
  }
}

async function deletePersonalKnowledgeRows(
  supabase: SupabaseClient,
  botId: string,
): Promise<void> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const untyped = supabase as any;
  const { error: documentsError } = await untyped
    .from("knowledge_documents")
    .delete()
    .eq("bot_id", botId)
    .eq("scope", "personal");

  if (documentsError) {
    throw new Error(`Failed to delete bot knowledge_documents: ${documentsError.message}`);
  }

  const { error: collectionsError } = await untyped
    .from("knowledge_collections")
    .delete()
    .eq("bot_id", botId)
    .eq("scope", "personal");

  if (collectionsError) {
    throw new Error(`Failed to delete bot knowledge_collections: ${collectionsError.message}`);
  }
}

async function clearBotOwnedData(
  supabase: SupabaseClient,
  botId: string,
  options: { preserveWalletPolicies?: boolean } = {},
): Promise<void> {
  const [
    chatAttachments,
    conversionJobs,
    consultationJobs,
    consultationArtifacts,
    knowledgeOriginalFiles,
  ] = await Promise.all([
    selectRowsByBotId<StoragePathRow>(supabase, "chat_attachments", "storage_path", botId),
    selectRowsByBotId<ConversionJobStorageRow>(
      supabase,
      "conversion_jobs",
      "source_storage_path,result_storage_path",
      botId,
    ),
    selectRowsByBotId<StoragePathRow>(
      supabase,
      "consultation_jobs",
      "source_storage_path",
      botId,
    ),
    selectRowsByBotId<StoragePathRow>(
      supabase,
      "consultation_artifacts",
      "storage_path",
      botId,
    ),
    listStorageObjects(supabase, "chat-attachments", `knowledge/${botId}`),
  ]);

  const storagePaths = buildBotDeletionStoragePaths({
    botId,
    chatAttachments,
    knowledgeOriginalFiles,
    conversionJobs,
    consultationJobs,
    consultationArtifacts,
  });
  await removeStoragePaths(supabase, "chat-attachments", storagePaths);

  const tablesToDelete = [
    "chat_messages",
    "app_channel_messages",
    "push_messages",
    "chat_reset_counters",
    "chat_message_deletions",
    "chat_exports",
    "chat_attachments",
    "app_channels",
    "conversion_jobs",
    "consultation_artifacts",
    "consultation_jobs",
    "gateway_tokens",
    "bot_email_inboxes",
    "bot_x402_inboxes",
    "discord_bot_mappings",
    "bot_wallet_policies",
    "learned_skills",
    "skill_executions",
    "user_interactions",
    "sub_agents_cache",
  ].filter((table) => !(options.preserveWalletPolicies && table === "bot_wallet_policies"));

  for (const table of tablesToDelete) {
    await deleteRowsByBotId(supabase, table, botId);
  }

  await deletePersonalKnowledgeRows(supabase, botId);
}

interface CreateBotResult {
  bot?: Record<string, unknown>;
  redirect?: string;
  checkoutUrl?: string;
  error?: string;
  code?: string;
  status?: number;
}

export class BotServiceError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
  ) {
    super(message);
    this.name = "BotServiceError";
  }
}

export function isBotServiceError(err: unknown): err is BotServiceError {
  return err instanceof BotServiceError;
}

export function shouldReprovisionForBotSettings({
  botRow,
  updates,
  body,
  externalKeyChanged = false,
}: {
  botRow: Record<string, unknown>;
  updates: Record<string, unknown>;
  body: Record<string, unknown>;
  externalKeyChanged?: boolean;
}): boolean {
  const modelChanged =
    "model_selection" in updates &&
    updates.model_selection !== botRow.model_selection;
  const effectiveApiKeyMode =
    typeof updates.api_key_mode === "string"
      ? updates.api_key_mode
      : botRow.api_key_mode;
  // Dynamic model switching: api-proxy reads model_selection + router_type
  // from DB (cached 30s). No pod recreate needed for platform_credits users.
  const dynamicPlatformModelChange =
    modelChanged && effectiveApiKeyMode === "platform_credits";
  const routerTypeChanged =
    "router_type" in updates && updates.router_type !== botRow.router_type;
  const dynamicPlatformRouterChange =
    routerTypeChanged && effectiveApiKeyMode === "platform_credits";

  return (
    (modelChanged && !dynamicPlatformModelChange) ||
    ("api_key_mode" in updates &&
      updates.api_key_mode !== botRow.api_key_mode) ||
    (routerTypeChanged && !dynamicPlatformRouterChange) ||
    ("language" in updates &&
      updates.language !== botRow.language) ||
    ("anthropic_api_key" in body && Boolean(body.anthropic_api_key)) ||
    ("fireworks_api_key" in body && Boolean(body.fireworks_api_key)) ||
    ("openai_api_key" in body && Boolean(body.openai_api_key)) ||
    ("codex_access_token" in body && Boolean(body.codex_access_token)) ||
    ("codex_refresh_token" in body && Boolean(body.codex_refresh_token)) ||
    ("custom_base_url" in body) ||
    ("disabled_skills" in updates) ||
    ("agent_rules" in updates &&
      updates.agent_rules !== botRow.agent_rules) ||
    ("agent_config" in updates) ||
    externalKeyChanged
  );
}

/** Check whether all platform seats are taken. Returns true if seats are full. */
export async function areSeatsFull(supabase: SupabaseClient): Promise<boolean> {
  const [clusterCapacity, settingsResult, countResult] = await Promise.all([
    getClusterBotCapacity(),
    supabase
      .from("platform_settings")
      .select("value")
      .eq("key", "max_seats")
      .single(),
    supabase
      .from("bots")
      .select("id", { count: "exact", head: true })
      .in("status", ["active", "provisioning"]),
  ]);

  const adminCap = settingsResult.data
    ? parseInt(settingsResult.data.value, 10)
    : Infinity;
  const maxSeats = Math.min(clusterCapacity, adminCap);

  return (countResult.count ?? 0) >= maxSeats;
}

/** Count user's non-deleted bots. Used for multi-bot limit enforcement. */
export async function countUserBots(
  supabase: SupabaseClient,
  userId: string
): Promise<number> {
  const { count } = await supabase
    .from("bots")
    .select("id", { count: "exact", head: true })
    .eq("user_id", userId)
    .in("status", ["active", "provisioning", "error"]);

  return count ?? 0;
}

/** Find telegram_owner_id from a previous (deleted) bot by the same user. */
async function getExistingOwnerId(
  supabase: SupabaseClient,
  userId: string
): Promise<number | null> {
  const { data: prevBot } = await supabase
    .from("bots")
    .select("telegram_owner_id")
    .eq("user_id", userId)
    .not("telegram_owner_id", "is", null)
    .order("created_at", { ascending: false })
    .limit(1)
    .single();
  return prevBot?.telegram_owner_id ?? null;
}

/** Map api_key_mode + pricing tier to subscription plan. */
function toPlan(apiKeyMode: string, pricingTier?: string): string {
  if (apiKeyMode === "byok") return "byok";
  if (pricingTier === "flex") return "flex";
  if (pricingTier === "max") return "max";
  if (pricingTier === "pro_plus") return "pro_plus";
  return "pro";
}

async function getActiveSubscriptionPlan(
  supabase: SupabaseClient,
  userId: string,
): Promise<string | null> {
  const { data } = await supabase
    .from("subscriptions")
    .select("plan")
    .eq("user_id", userId)
    .in("status", ["active", "trialing"])
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  return typeof data?.plan === "string" ? data.plan : null;
}

function assertLocalModelAllowed(
  modelSelection: string | null | undefined,
  apiKeyMode: string | null | undefined,
  plan: string | null | undefined,
): { error?: string; code?: string; status?: number } | null {
  const violation = getLocalLlmModelEntitlementError(modelSelection, apiKeyMode, plan);
  if (!violation) return null;
  return {
    error: violation.message,
    code: violation.code,
    status: violation.status,
  };
}

async function checkOrCreateSubscription(
  supabase: SupabaseClient,
  userId: string
): Promise<boolean> {
  const { data: existingSub } = await supabase
    .from("subscriptions")
    .select("id, status")
    .eq("user_id", userId)
    .in("status", ["active", "trialing"])
    .single();

  return !!existingSub;
}

/** Insert a bot row and store BYOK key if provided. */
async function insertBot(
  supabase: SupabaseClient,
  userId: string,
  input: CreateBotInput,
  existingOwnerId: number | null,
  encryptToken: boolean
): Promise<{ bot: Record<string, unknown> | null; error: string | null }> {
  const token = input.telegramBotToken
    ? (encryptToken ? encrypt(input.telegramBotToken) : input.telegramBotToken)
    : null;

  const initialStatus = "provisioning";

  const { data: bot, error: botError } = await supabase
    .from("bots")
    .insert({
      user_id: userId,
      name: input.telegramBotUsername || `${randomAdjective()}_${randomNoun()}_bot`,
      telegram_bot_token: token,
      telegram_bot_username: input.telegramBotUsername,
      telegram_user_handle: input.telegramUserHandle || null,
      telegram_owner_id: existingOwnerId,
      model_selection: input.modelSelection,
      api_key_mode: input.apiKeyMode,
      router_type: input.routerType || "standard",
      bot_purpose: input.customStyle || null,
      purpose_preset: input.personalityPreset || null,
      language: input.language || "auto",
      disabled_skills: input.disabledSkills ?? [],
      purpose_category: input.purposeCategory || null,
      status: initialStatus,
    })
    .select(BOT_SELECT_FIELDS)
    .single();

  if (botError) {
    console.error("[bot-service] Insert error:", botError);
    return { bot: null, error: "Failed to create bot" };
  }

  // Best-effort Telegram webhook setup (non-blocking, owner can be set later)
  if (input.telegramBotToken) {
    try {
      await setOwnerWebhook(input.telegramBotToken, bot!.id as string);
    } catch (err) {
      console.error("[bot-service] webhook setup failed:", err);
    }
  }

  if (input.apiKeyMode === "byok" && input.anthropicApiKey) {
    await supabase
      .from("profiles")
      .update({ anthropic_api_key: encrypt(input.anthropicApiKey) })
      .eq("id", userId);
  }

  if (input.apiKeyMode === "byok" && input.fireworksApiKey) {
    await supabase
      .from("profiles")
      .update({ fireworks_api_key: encrypt(input.fireworksApiKey) })
      .eq("id", userId);
  }

  if (input.apiKeyMode === "byok" && input.openaiApiKey) {
    await supabase
      .from("profiles")
      .update({ openai_api_key: encrypt(input.openaiApiKey) } as Record<string, unknown>)
      .eq("id", userId);
  }

  if (input.apiKeyMode === "byok" && input.geminiApiKey) {
    await supabase
      .from("profiles")
      .update({ gemini_api_key: encrypt(input.geminiApiKey) } as Record<string, unknown>)
      .eq("id", userId);
  }

  if (input.codexAccessToken) {
    await supabase
      .from("profiles")
      .update({ codex_access_token: encrypt(input.codexAccessToken) } as Record<string, unknown>)
      .eq("id", userId);
  }

  if (input.codexRefreshToken) {
    await supabase
      .from("profiles")
      .update({ codex_refresh_token: encrypt(input.codexRefreshToken) } as Record<string, unknown>)
      .eq("id", userId);
  }

  if (input.apiKeyMode === "byok" && input.customBaseUrl) {
    await supabase
      .from("profiles")
      .update({ custom_base_url: input.customBaseUrl })
      .eq("id", userId);
  }

  await supabase
    .from("profiles")
    .update({ onboarding_completed: true })
    .eq("id", userId);

  return { bot, error: null };
}

/**
 * Full bot creation flow: validate, check seats, check duplicates,
 * resolve subscription, insert bot.
 */
export async function createBot(
  userId: string,
  input: CreateBotInput,
  origin?: string
): Promise<CreateBotResult> {
  await ensureProfile(userId);
  const supabase = createAdminClient();
  const requestedPlan = toPlan(input.apiKeyMode, input.pricingTier);
  const activePlan = await getActiveSubscriptionPlan(supabase, userId);
  const modelEntitlement = assertLocalModelAllowed(
    input.modelSelection,
    input.apiKeyMode,
    activePlan ?? requestedPlan,
  );
  if (modelEntitlement) return modelEntitlement;

  // Seat limit
  if (await areSeatsFull(supabase)) {
    return {
      error: "All seats are currently taken. Please try again later.",
      code: "seats_full",
      status: 403,
    };
  }

  // Bot limit check (multi-bot: per-plan limit)
  const botCount = await countUserBots(supabase, userId);
  const { data: subForLimit } = await supabase
    .from("subscriptions")
    .select("plan")
    .eq("user_id", userId)
    .in("status", ["active", "trialing"])
    .single();
  const maxBots = PLAN_MAX_BOTS[subForLimit?.plan ?? "pro"] ?? 1;

  if (botCount >= maxBots) {
    return {
      error: "Bot limit reached for your plan. Upgrade to add more bots.",
      code: "bot_limit_reached",
      status: 403,
    };
  }

  const existingOwnerId = await getExistingOwnerId(supabase, userId);
  const hasSubscription = await checkOrCreateSubscription(supabase, userId);

  if (hasSubscription) {
    const { bot, error } = await insertBot(
      supabase,
      userId,
      input,
      existingOwnerId,
      true
    );
    if (error) return { error, status: 500 };
    if (bot) {
      await triggerProvisioning(bot.id as string);
    }
    // Best-effort referral binding
    if (input.referralCode) {
      try { await bindReferral(userId, input.referralCode); } catch { /* non-blocking */ }
    }
    captureServerEvent(userId, "bot_created", {
      model: input.modelSelection,
      api_key_mode: input.apiKeyMode,
      has_trial: false,
    });
    return { bot: bot as Record<string, unknown>, redirect: "/dashboard" };
  }

  // No subscription — redirect to Stripe Checkout with 7-day trial
  const stripe = getStripe();

  // Find or create Stripe customer
  const { data: existingProfile } = await supabase
    .from("profiles")
    .select("stripe_customer_id")
    .eq("id", userId)
    .single();

  let customerId = (existingProfile as unknown as Record<string, unknown>)
    ?.stripe_customer_id as string | undefined;

  if (!customerId) {
    const customer = await stripe.customers.create({
      metadata: { user_id: userId },
    });
    customerId = customer.id;
    await supabase
      .from("profiles")
      .update({ stripe_customer_id: customerId } as Record<string, unknown>)
      .eq("id", userId);
  }

  // Save BYOK keys to profiles before checkout (webhook reads them from profiles)
  if (input.apiKeyMode === "byok" && input.anthropicApiKey) {
    await supabase.from("profiles").update({ anthropic_api_key: encrypt(input.anthropicApiKey) }).eq("id", userId);
  }
  if (input.apiKeyMode === "byok" && input.fireworksApiKey) {
    await supabase.from("profiles").update({ fireworks_api_key: encrypt(input.fireworksApiKey) }).eq("id", userId);
  }
  if (input.apiKeyMode === "byok" && input.openaiApiKey) {
    await supabase.from("profiles").update({ openai_api_key: encrypt(input.openaiApiKey) } as Record<string, unknown>).eq("id", userId);
  }
  if (input.apiKeyMode === "byok" && input.geminiApiKey) {
    await supabase.from("profiles").update({ gemini_api_key: encrypt(input.geminiApiKey) } as Record<string, unknown>).eq("id", userId);
  }
  if (input.codexAccessToken) {
    await supabase.from("profiles").update({ codex_access_token: encrypt(input.codexAccessToken) } as Record<string, unknown>).eq("id", userId);
  }
  if (input.codexRefreshToken) {
    await supabase.from("profiles").update({ codex_refresh_token: encrypt(input.codexRefreshToken) } as Record<string, unknown>).eq("id", userId);
  }
  if (input.apiKeyMode === "byok" && input.customBaseUrl) {
    await supabase.from("profiles").update({ custom_base_url: input.customBaseUrl }).eq("id", userId);
  }

  // Mark onboarding complete before checkout redirect
  await supabase.from("profiles").update({ onboarding_completed: true }).eq("id", userId);

  const plan = requestedPlan;
  const priceId = getPriceId(plan);

  const discounts = await prepareReferralCheckoutDiscounts(
    stripe,
    userId,
    input.referralCode,
  );

  const checkoutOrigin = origin || process.env.NEXT_PUBLIC_SITE_URL || "https://openmagi.ai";

  try {
    // Build checkout params — trial_end as unix timestamp (Stripe requires this format with discounts)
    // Ceil to next UTC midnight so Stripe always displays the full TRIAL_DURATION_DAYS (not N-1)
    const nextMidnight = Math.ceil(Date.now() / 1000 / 86400) * 86400;
    const trialEnd = nextMidnight + TRIAL_DURATION_DAYS * 86400;

    const checkoutParams: Record<string, unknown> = {
      customer: customerId,
      mode: "subscription",
      line_items: [{ price: priceId, quantity: 1 }],
      payment_method_collection: "always" as const,
      subscription_data: {
        trial_end: trialEnd,
      },
      success_url: `${checkoutOrigin}/dashboard/overview?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${checkoutOrigin}/dashboard/overview?onboarding=true`,
      metadata: {
        user_id: userId,
        plan,
        api_key_mode: input.apiKeyMode,
        model_selection: input.modelSelection,
        router_type: input.routerType || "standard",
        bot_purpose: input.customStyle ?? "",
        purpose_preset: input.personalityPreset ?? "",
        language: input.language ?? "auto",
        disabled_skills: JSON.stringify(input.disabledSkills ?? []),
        purpose_category: input.purposeCategory ?? "",
      },
    };

    // Discounts cannot be combined with trial_period_days in some Stripe configurations
    // Use trial_end instead and add discounts separately
    if (discounts.length > 0) {
      checkoutParams.discounts = discounts;
    }

    const session = await stripe.checkout.sessions.create(
      checkoutParams as Parameters<typeof stripe.checkout.sessions.create>[0]
    );

    captureServerEvent(userId, "checkout_started", {
      model: input.modelSelection,
      api_key_mode: input.apiKeyMode,
      plan,
    });

    return { checkoutUrl: session.url! };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[bot-service] Stripe checkout creation failed:", msg, err);
    return { error: `Failed to create checkout session: ${msg}`, status: 500 };
  }
}

/** Update bot settings and trigger reprovisioning if needed. */
export async function updateBotSettings(
  botId: string,
  userId: string,
  botRow: Record<string, unknown>,
  body: Record<string, unknown>
): Promise<{
  data: Record<string, unknown>;
  reprovisioning?: boolean;
  reprovisionError?: string;
}> {
  const supabase = createAdminClient();
  const allowedFields = [
    "name",
    "model_selection",
    "api_key_mode",
    "anthropic_api_key",
    "fireworks_api_key",
    "openai_api_key",
    "codex_access_token",
    "codex_refresh_token",
    "brave_api_key",
    "elevenlabs_api_key",
    "groq_api_key",
    "deepl_api_key",
    "alpha_vantage_api_key",
    "finnhub_api_key",
    "fmp_api_key",
    "fred_api_key",
    "dart_api_key",
    "firecrawl_api_key",
    "semantic_scholar_api_key",
    "serper_api_key",
    "github_token",
    "google_api_key",
    "google_ads_developer_token",
    "gemini_api_key",
    "custom_base_url",
    "bot_purpose",
    "purpose_preset",
    "language",
    "router_type",
    "disabled_skills",
    "agent_rules",
    "agent_config",
  ];

  const updates: Record<string, unknown> = {};
  for (const field of allowedFields) {
    if (field in body) {
      updates[field] = body[field];
    }
  }

  const finalModelSelection = typeof updates.model_selection === "string"
    ? updates.model_selection
    : typeof botRow.model_selection === "string"
      ? botRow.model_selection
      : null;
  const finalApiKeyMode = typeof updates.api_key_mode === "string"
    ? updates.api_key_mode
    : typeof botRow.api_key_mode === "string"
      ? botRow.api_key_mode
      : null;

  if ("model_selection" in updates || "api_key_mode" in updates) {
    const activePlan = await getActiveSubscriptionPlan(supabase, userId);
    const violation = getLocalLlmModelEntitlementError(finalModelSelection, finalApiKeyMode, activePlan);
    if (violation) {
      throw new BotServiceError(violation.message, violation.status, violation.code);
    }
  }

  // Handle API keys — stored on profile, encrypted at rest
  if ("anthropic_api_key" in updates) {
    const rawKey = updates.anthropic_api_key as string;
    await supabase
      .from("profiles")
      .update({ anthropic_api_key: rawKey ? encrypt(rawKey) : null })
      .eq("id", userId);
    delete updates.anthropic_api_key;
  }

  if ("fireworks_api_key" in updates) {
    const rawKey = updates.fireworks_api_key as string;
    await supabase
      .from("profiles")
      .update({ fireworks_api_key: rawKey ? encrypt(rawKey) : null })
      .eq("id", userId);
    delete updates.fireworks_api_key;
  }

  // OpenAI / Codex keys — columns added in migration 036, cast to bypass generated types
  if ("openai_api_key" in updates) {
    const rawKey = updates.openai_api_key as string;
    await supabase
      .from("profiles")
      .update({ openai_api_key: rawKey ? encrypt(rawKey) : null } as Record<string, unknown>)
      .eq("id", userId);
    delete updates.openai_api_key;
  }

  if ("codex_access_token" in updates) {
    const rawToken = updates.codex_access_token as string;
    await supabase
      .from("profiles")
      .update({ codex_access_token: rawToken ? encrypt(rawToken) : null } as Record<string, unknown>)
      .eq("id", userId);
    delete updates.codex_access_token;
  }

  if ("codex_refresh_token" in updates) {
    const rawToken = updates.codex_refresh_token as string;
    await supabase
      .from("profiles")
      .update({ codex_refresh_token: rawToken ? encrypt(rawToken) : null } as Record<string, unknown>)
      .eq("id", userId);
    delete updates.codex_refresh_token;
  }

  // External service API keys — encrypted and stored on profiles
  const externalKeyFields = [
    "brave_api_key",
    "elevenlabs_api_key",
    "groq_api_key",
    "deepl_api_key",
    "alpha_vantage_api_key",
    "finnhub_api_key",
    "fmp_api_key",
    "fred_api_key",
    "dart_api_key",
    "firecrawl_api_key",
    "semantic_scholar_api_key",
    "serper_api_key",
    "github_token",
    "google_api_key",
    "google_ads_developer_token",
    "gemini_api_key",
    "zapier_mcp_url",
  ] as const;

  let externalKeyChanged = false;
  for (const field of externalKeyFields) {
    if (field in updates) {
      const rawKey = updates[field] as string;
      await supabase
        .from("profiles")
        .update({ [field]: rawKey ? encrypt(rawKey) : null })
        .eq("id", userId);
      delete updates[field];
      if (rawKey) externalKeyChanged = true;
    }
  }

  if ("custom_base_url" in updates) {
    const rawUrl = updates.custom_base_url as string;
    await supabase
      .from("profiles")
      .update({ custom_base_url: rawUrl || null })
      .eq("id", userId);
    delete updates.custom_base_url;
  }

  const needsReprovision = shouldReprovisionForBotSettings({
    botRow,
    updates,
    body,
    externalKeyChanged,
  });

  if (Object.keys(updates).length > 0) {
    const { data: updatedBot, error } = await supabase
      .from("bots")
      .update(updates)
      .eq("id", botId)
      .select(BOT_SELECT_FIELDS)
      .single();

    if (error) {
      throw new Error("Failed to update bot");
    }

    if (needsReprovision && botRow.status !== "deleted") {
      try {
        await supabase
          .from("bots")
          .update({ status: "provisioning" })
          .eq("id", botId);
        await triggerProvisioning(botId);
        return {
          data: { ...(updatedBot as unknown as Record<string, unknown>), status: "provisioning" },
          reprovisioning: true,
        };
      } catch (err) {
        console.error(
          `[bot-service] Reprovisioning failed for bot ${botId}:`,
          err
        );
        return {
          data: updatedBot as unknown as Record<string, unknown>,
          reprovisioning: false,
          reprovisionError: "Reprovisioning failed",
        };
      }
    }

    return { data: updatedBot as unknown as Record<string, unknown> };
  }

  // Only API keys were updated
  if (needsReprovision && botRow.status !== "deleted") {
    try {
      await supabase
        .from("bots")
        .update({ status: "provisioning" })
        .eq("id", botId);
      await triggerProvisioning(botId);
    } catch (err) {
      console.error(
        `[bot-service] Reprovisioning failed for bot ${botId}:`,
        err
      );
    }
  }

  const { data: refreshedBot } = await supabase
    .from("bots")
    .select(BOT_SELECT_FIELDS)
    .eq("id", botId)
    .single();

  return {
    data: refreshedBot as unknown as Record<string, unknown>,
    reprovisioning: !!needsReprovision,
  };
}

/** Hard-delete bot-owned data while preserving the bot row as an audit tombstone. */
export async function deleteBotAndCleanup(botId: string): Promise<void> {
  const supabase = createAdminClient();

  const [{ data: botData }, { data: policies }] = await Promise.all([
    supabase
      .from("bots")
      .select("privy_wallet_id, privy_wallet_address, privy_wallet_chain, registry_agent_id")
      .eq("id", botId)
      .single(),
    supabase
      .from("bot_wallet_policies")
      .select("privy_policy_id")
      .eq("bot_id", botId),
  ]);
  const externalFailures: string[] = [];
  let privyWalletDeleteFailed = false;
  let privyPolicyDeleteFailed = false;

  // Best-effort K8s namespace cleanup
  try {
    const k8s = createK8sClient();
    const namespace = `clawy-${botId}`;
    if (await k8s.namespaceExists(namespace)) {
      await k8s.deleteNamespace(namespace);
    }
  } catch (err) {
    console.error(
      `[bot-service] K8s cleanup failed for bot ${botId}, worker will retry:`,
      err
    );
    externalFailures.push(`k8s namespace: ${err instanceof Error ? err.message : String(err)}`);
  }

  // Best-effort Privy wallet policy cleanup
  if (policies && policies.length > 0) {
    try {
      const { deletePolicy } = await import("@/lib/privy/wallet-service");
      for (const p of policies) {
        try {
          await deletePolicy(p.privy_policy_id);
        } catch (err) {
          privyPolicyDeleteFailed = true;
          externalFailures.push(
            `Privy policy ${p.privy_policy_id}: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }
    } catch (err) {
      console.error(
        `[bot-service] Wallet cleanup failed for bot ${botId}:`,
        err
      );
      privyPolicyDeleteFailed = true;
      externalFailures.push(`Privy policies: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // Best-effort ERC-8004 registry deregistration
  try {
    if (botData?.privy_wallet_id && botData?.registry_agent_id) {
      const { deregisterAgent } = await import("@/lib/registry/agent-registry");
      await deregisterAgent(botData.privy_wallet_id, botData.registry_agent_id);
    }
  } catch (err) {
    console.error(`[bot-service] Registry deregistration failed for bot ${botId}:`, err);
    externalFailures.push(`registry deregistration: ${err instanceof Error ? err.message : String(err)}`);
  }

  if (botData?.privy_wallet_id) {
    try {
      const { deleteWallet } = await import("@/lib/privy/wallet-service");
      await deleteWallet(botData.privy_wallet_id);
    } catch (err) {
      privyWalletDeleteFailed = true;
      console.error(`[bot-service] Privy wallet delete failed for bot ${botId}:`, err);
      externalFailures.push(`Privy wallet: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  await clearBotOwnedData(supabase, botId, {
    preserveWalletPolicies: privyPolicyDeleteFailed,
  });

  const errorMessage = externalFailures.length > 0
    ? `External cleanup failed: ${externalFailures.join("; ")}`
    : null;
  const unresolvedPrivyWallet =
    privyWalletDeleteFailed || privyPolicyDeleteFailed
      ? {
          id: botData?.privy_wallet_id ?? null,
          address: botData?.privy_wallet_address ?? null,
          chain: botData?.privy_wallet_chain ?? null,
        }
      : undefined;

  const { error } = await supabase
    .from("bots")
    .update(buildBotTombstoneUpdate(new Date().toISOString(), {
      unresolvedPrivyWallet,
      errorMessage,
    }))
    .eq("id", botId);

  if (error) {
    throw new Error("Failed to mark bot as deleted");
  }

  if (externalFailures.length > 0) {
    throw new Error(errorMessage ?? "External cleanup failed");
  }
}
