import { createK8sClient } from "./k8s-client";
import type { K8sClient, PodSpec, ContainerSpec } from "./k8s-client";
import { buildOpenclawConfig } from "./config-builder";
import { buildNetworkPolicy } from "./manifests/network-policy";
import {
  generateIdentityMd,
  generateUserMd,
  generateInterestsMd,
  generateHeartbeatMd,
  generateRoutingMd,
  generateUserRulesMd,
} from "./template-engine";
import { createAdminClient } from "@/lib/supabase/admin";
import { customSkillPathKey } from "@/lib/custom-skills";
import { createAgentWallet, createWalletPolicy, attachPolicyToWallet, buildDefaultPolicy } from "@/lib/privy/wallet-service";
import { randomUUID, randomBytes } from "crypto";
import { readFileSync, readdirSync, statSync } from "fs";
import { join, relative } from "path";

// Container image defaults (overridable via env vars — .trim() guards against \n from Vercel env)
const GATEWAY_IMAGE = (process.env.GATEWAY_IMAGE ?? "docker.io/library/clawy-gateway:latest").trim();
const NODE_HOST_IMAGE = (process.env.NODE_HOST_IMAGE ?? "docker.io/library/clawy-node-host:latest").trim();
const ROUTER_IMAGE = (process.env.ROUTER_IMAGE ?? "docker.io/library/clawy-router:latest").trim();
const OPENAI_ROUTER_IMAGE = (process.env.OPENAI_ROUTER_IMAGE ?? "docker.io/library/clawy-openai-router:latest").trim();
const CLAWY_SMART_ROUTER_IMAGE = (process.env.CLAWY_SMART_ROUTER_IMAGE ?? "docker.io/library/clawy-smart-router:latest").trim();
const BIG_DIC_ROUTER_IMAGE = (process.env.BIG_DIC_ROUTER_IMAGE ?? "node:22-alpine").trim();

// Static template file names that are copied to every bot workspace
const STATIC_TEMPLATES = [
  "AGENTS.md", "TOOLS.md", "LEARNING.md",
  "SCRATCHPAD.md", "MEMORY.md", "WORKING.md", "SOUL.md", "CLAUDE.md",
  "TASK-QUEUE.md", "CURRENT-PLAN.md",
];

// Skills directory — each skill is a folder with SKILL.md + optional resources
const SKILLS_DIR = join(process.cwd(), "src", "lib", "templates", "skills");

// Specialist templates + lifecycle scripts directories
const SPECIALIST_TEMPLATES_DIR = join(process.cwd(), "src", "lib", "templates", "specialist");
const LIFECYCLE_SCRIPTS_DIR = join(process.cwd(), "src", "lib", "templates", "scripts");
const KNOWLEDGE_DIR = join(process.cwd(), "src", "lib", "templates", "static", "knowledge");
const PLUGIN_DIR = join(process.cwd(), "infra", "docker", "clawy-agent-os-plugin");

/** Recursively collect all files under a directory, returning relative paths */
function collectFiles(dir: string, base?: string): string[] {
  const root = base ?? dir;
  const entries = readdirSync(dir);
  const result: string[] = [];
  for (const entry of entries) {
    if (entry.startsWith("._") || entry === ".DS_Store") continue;
    const fullPath = join(dir, entry);
    if (statSync(fullPath).isDirectory()) {
      result.push(...collectFiles(fullPath, root));
    } else {
      result.push(relative(root, fullPath));
    }
  }
  return result;
}

export interface ProvisioningInput {
  botId: string;
  userId: string;
  botName: string;
  telegramBotToken: string;
  telegramUserHandle?: string;
  telegramOwnerId?: number;
  modelSelection: string;
  apiKeyMode: string;
  anthropicApiKey?: string;
  proxyBaseUrl?: string;
  fireworksApiKey?: string;
  openaiApiKey?: string;
  codexAccessToken?: string;
  codexRefreshToken?: string;
  personalityPreset?: string;
  customStyle?: string;
  routerType?: string;
  pricingTier?: string;
  purposeCategory?: string;
  agentRules?: string;
  displayName: string;
}

export interface ProvisioningResult {
  success: boolean;
  namespace: string;
  gatewayToken: string;
  error?: string;
  completedSteps: number;
}

type StepName =
  | "create_namespace"
  | "create_pvc"
  | "create_secrets"
  | "create_wallet"
  | "create_default_policy"
  | "apply_network_policy"
  | "copy_static_templates"
  | "generate_dynamic_files"
  | "generate_config"
  | "copy_skills"
  | "copy_specialist_templates"
  | "copy_lifecycle_scripts"
  | "copy_plugin"
  | "create_pod";

const STEP_LABELS: Record<StepName, string> = {
  create_namespace: "Creating namespace",
  create_pvc: "Creating persistent volume",
  create_secrets: "Creating secrets",
  create_wallet: "Creating agent wallet",
  create_default_policy: "Setting up wallet policy",
  apply_network_policy: "Applying network policy",
  copy_static_templates: "Copying static templates",
  generate_dynamic_files: "Generating dynamic files",
  generate_config: "Generating configuration",
  copy_skills: "Copying skills",
  copy_specialist_templates: "Copying specialist templates",
  copy_lifecycle_scripts: "Copying lifecycle scripts",
  copy_plugin: "Copying Agent OS plugin",
  create_pod: "Creating pod",
};

const STEPS: StepName[] = [
  "create_namespace",
  "create_pvc",
  "create_secrets",
  "create_wallet",
  "create_default_policy",
  "apply_network_policy",
  "copy_static_templates",
  "generate_dynamic_files",
  "generate_config",
  "copy_skills",
  "copy_specialist_templates",
  "copy_lifecycle_scripts",
  "copy_plugin",
  "create_pod",
];

async function updateBotStatus(
  // Cast to any to avoid strict type constraints from incomplete generated Database types
  // (missing Relationships/Views fields required by @supabase/postgrest-js)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  supabase: any,
  botId: string,
  status: string,
  errorMessage?: string | null
) {
  await supabase
    .from("bots")
    .update({
      status,
      error_message: errorMessage ?? null,
      updated_at: new Date().toISOString(),
    })
    .eq("id", botId);
}

/**
 * Provisions a bot through the 10-step pipeline:
 * 1. Create namespace (clawy-{botId})
 * 2. Create PVC for workspace data
 * 3. Create secrets (API keys, bot token, gateway token)
 * 4. Copy static template files (AGENTS.md, TOOLS.md, etc.) as Secret
 * 5. Generate dynamic files (IDENTITY.md, USER.md, HEARTBEAT.md, INTERESTS.md) as Secret
 * 6. Generate openclaw.json config as Secret
 * 7. Copy skills to workspace as Secret
 * 8. Copy specialist templates (AGENTS.md, HEARTBEAT.md, TOOLS.md + knowledge) as Secret
 * 9. Copy lifecycle scripts (agent-create.sh, agent-archive.sh, etc.) as Secret
 * 10. Create Pod (gateway + node-host containers, optional iblai-router sidecar)
 *
 * After pod creation, bot stays in "provisioning" status.
 * The status API polls K8s health and transitions to "active" when ready.
 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function provisionBot(
  input: ProvisioningInput
): Promise<ProvisioningResult> {
  // Validate botId format to prevent namespace injection
  if (!UUID_RE.test(input.botId)) {
    return { success: false, namespace: "", gatewayToken: "", error: "Invalid botId format", completedSteps: 0 };
  }
  const namespace = `clawy-${input.botId}`;
  // Platform-credits bots need gw_ prefixed tokens for api-proxy auth
  const isPlatformCredits = !input.anthropicApiKey;
  const gatewayToken = isPlatformCredits
    ? `gw_${randomBytes(32).toString("hex")}`
    : randomUUID();
  const podName = `bot-${input.botId}`;
  const pvcName = `workspace-${input.botId}`;
  const secretName = `bot-secrets-${input.botId}`;

  const k8s = createK8sClient();
  const supabase = createAdminClient();

  let completedSteps = 0;

  // Pre-step: If reprovisioning, delete ONLY the pod (preserve PVC + namespace + secrets).
  // Workspace data (MEMORY.md, SCRATCHPAD.md, USER.md) must never be wiped by an
  // automatic code path. See CLAUDE.md "NEVER delete PVCs".
  try {
    const nsExists = await k8s.namespaceExists(namespace);
    if (nsExists) {
      await updateBotStatus(supabase, input.botId, "provisioning", "Cleaning up previous pod");
      try {
        await k8s.deletePod(namespace, `bot-${input.botId}`);
      } catch (err) {
        if ((err as { statusCode?: number }).statusCode !== 404) {
          console.warn("[provisioning] pre-cleanup pod delete non-fatal error:", err);
        }
      }
    }
  } catch (error) {
    const errorMessage = `Cleanup failed: ${error instanceof Error ? error.message : String(error)}`;
    await updateBotStatus(supabase, input.botId, "error", errorMessage);
    return {
      success: false,
      namespace,
      gatewayToken,
      error: errorMessage,
      completedSteps,
    };
  }

  for (const step of STEPS) {
    try {
      await updateBotStatus(
        supabase,
        input.botId,
        "provisioning",
        STEP_LABELS[step]
      );

      await executeStep(step, {
        input,
        k8s,
        namespace,
        gatewayToken,
        podName,
        pvcName,
        secretName,
      });

      completedSteps++;
    } catch (error) {
      const errorMessage = `Step "${step}" failed: ${error instanceof Error ? error.message : String(error)}`;
      await updateBotStatus(supabase, input.botId, "error", errorMessage);

      return {
        success: false,
        namespace,
        gatewayToken,
        error: errorMessage,
        completedSteps,
      };
    }
  }

  // All K8s resources created — bot stays in "provisioning" status.
  // The status API will poll K8s health and transition to "active" when ready.
  await updateBotStatus(supabase, input.botId, "provisioning", "Pod created, waiting for containers to start");

  return {
    success: true,
    namespace,
    gatewayToken,
    completedSteps,
  };
}

interface StepContext {
  input: ProvisioningInput;
  k8s: K8sClient;
  namespace: string;
  gatewayToken: string;
  podName: string;
  pvcName: string;
  secretName: string;
  activeIntegrations?: string[];
}

async function executeStep(
  step: StepName,
  ctx: StepContext
): Promise<void> {
  switch (step) {
    case "create_namespace":
      return stepCreateNamespace(ctx);
    case "create_pvc":
      return stepCreatePVC(ctx);
    case "create_secrets":
      return stepCreateSecrets(ctx);
    case "create_wallet":
      return stepCreateWallet(ctx);
    case "create_default_policy":
      return stepCreateDefaultPolicy(ctx);
    case "apply_network_policy":
      return stepApplyNetworkPolicy(ctx);
    case "copy_static_templates":
      return stepCopyStaticTemplates(ctx);
    case "generate_dynamic_files":
      return stepGenerateDynamicFiles(ctx);
    case "generate_config":
      return stepGenerateConfig(ctx);
    case "copy_skills":
      return stepCopySkills(ctx);
    case "copy_specialist_templates":
      return stepCopySpecialistTemplates(ctx);
    case "copy_lifecycle_scripts":
      return stepCopyLifecycleScripts(ctx);
    case "copy_plugin":
      return stepCopyPlugin(ctx);
    case "create_pod":
      return stepCreatePod(ctx);
  }
}

// Step 1: Create namespace + GHCR pull secret
async function stepCreateNamespace(ctx: StepContext): Promise<void> {
  // Label allows NetworkPolicy to restrict api-proxy access to bot namespaces only
  await ctx.k8s.createNamespace(ctx.namespace, { "clawy-bot": "true" });

  // Create image pull secret if GHCR credentials are configured
  const ghcrUser = process.env.GHCR_USERNAME;
  const ghcrToken = process.env.GHCR_TOKEN;
  if (ghcrUser && ghcrToken) {
    const dockerConfigJson = JSON.stringify({
      auths: {
        "ghcr.io": {
          auth: Buffer.from(`${ghcrUser}:${ghcrToken}`).toString("base64"),
        },
      },
    });
    await ctx.k8s.createSecret(ctx.namespace, "ghcr-secret", {
      ".dockerconfigjson": dockerConfigJson,
    });
  }

  // Copy internal CA cert for telegram-gate TLS (from clawy-system namespace)
  try {
    const caSecret = await ctx.k8s.getSecret("clawy-system", "clawy-internal-ca");
    if (caSecret?.["ca.pem"]) {
      await ctx.k8s.createSecret(ctx.namespace, "clawy-internal-ca", {
        "ca.pem": caSecret["ca.pem"],
      });
    }
  } catch {
    // Non-fatal — telegram-gate TLS is optional
  }
}

// Step 2: Create PVC for workspace data (16Gi for max/flex, 512Mi default)
// KB files stored on S3 object storage — PVC only holds workspace config/sessions
async function stepCreatePVC(ctx: StepContext): Promise<void> {
  const sizeMb = (ctx.input.pricingTier === "max" || ctx.input.pricingTier === "flex") ? 16384 : 512;
  await ctx.k8s.createPVC(ctx.namespace, ctx.pvcName, sizeMb);
}

// Step 3: Create secrets
async function stepCreateSecrets(ctx: StepContext): Promise<void> {
  const secretData: Record<string, string> = {
    TELEGRAM_BOT_TOKEN: ctx.input.telegramBotToken,
    GATEWAY_TOKEN: ctx.gatewayToken,
    // Platform-credits: use gatewayToken (gw_ prefixed) for api-proxy auth
    // BYOK: use user's actual API key
    ANTHROPIC_API_KEY: ctx.input.anthropicApiKey || ctx.gatewayToken,
    OPENAI_API_KEY: ctx.input.openaiApiKey ?? "",
    FIREWORKS_API_KEY: ctx.input.fireworksApiKey ?? "",
    CODEX_ACCESS_TOKEN: ctx.input.codexAccessToken ?? "",
    CODEX_REFRESH_TOKEN: ctx.input.codexRefreshToken ?? "",
  };

  await ctx.k8s.createSecret(ctx.namespace, ctx.secretName, secretData);
}

// Step 3.5: Create Privy agent wallet
async function stepCreateWallet(ctx: StepContext): Promise<void> {
  // Skip if Privy not configured
  if (!process.env.PRIVY_AUTHORIZATION_KEY_ID) return;

  // Skip if wallet already exists (reprovisioning)
  const supabase = createAdminClient();
  const { data: bot } = await supabase
    .from("bots")
    .select("privy_wallet_id")
    .eq("id", ctx.input.botId)
    .single();

  if (bot?.privy_wallet_id) return;

  const wallet = await createAgentWallet("ethereum");

  await supabase
    .from("bots")
    .update({
      privy_wallet_id: wallet.id,
      privy_wallet_address: wallet.address,
      privy_wallet_chain: wallet.chainType,
    })
    .eq("id", ctx.input.botId);
}

// Step 3.6: Create and attach default wallet policy
async function stepCreateDefaultPolicy(ctx: StepContext): Promise<void> {
  const supabase = createAdminClient();
  const { data: bot } = await supabase
    .from("bots")
    .select("privy_wallet_id")
    .eq("id", ctx.input.botId)
    .single();

  if (!bot?.privy_wallet_id) return;

  // Check if default policy already exists
  const { data: existing } = await supabase
    .from("bot_wallet_policies")
    .select("id")
    .eq("bot_id", ctx.input.botId)
    .eq("policy_type", "default")
    .limit(1);

  if (existing && existing.length > 0) return;

  const policyInput = buildDefaultPolicy(ctx.input.botId);
  const policy = await createWalletPolicy(policyInput);
  await attachPolicyToWallet(bot.privy_wallet_id, policy.id);

  await supabase.from("bot_wallet_policies").insert({
    bot_id: ctx.input.botId,
    privy_policy_id: policy.id,
    name: policyInput.name,
    policy_type: "default",
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    policy_json: JSON.parse(JSON.stringify(policyInput)) as any,
    is_active: true,
  });
}

// Step 4: Apply NetworkPolicy (deny-all ingress, allow DNS + HTTPS egress)
async function stepApplyNetworkPolicy(ctx: StepContext): Promise<void> {
  const manifest = buildNetworkPolicy(ctx.namespace);
  await ctx.k8s.applyNetworkPolicy(ctx.namespace, manifest);
}

// Step 4: Copy static template files as ConfigMap
async function stepCopyStaticTemplates(ctx: StepContext): Promise<void> {
  const templatesDir = join(process.cwd(), "src", "lib", "templates", "static");
  const configMapData: Record<string, string> = {};
  for (const file of STATIC_TEMPLATES) {
    configMapData[file] = readFileSync(join(templatesDir, file), "utf-8");
  }

  await ctx.k8s.createSecret(
    ctx.namespace,
    `static-templates-${ctx.input.botId}`,
    configMapData
  );
}

// Step 5: Generate dynamic files as ConfigMap
async function stepGenerateDynamicFiles(ctx: StepContext): Promise<void> {
  const identityMd = generateIdentityMd({
    botName: ctx.input.botName,
    personalityPreset: ctx.input.personalityPreset ?? null,
    customStyle: ctx.input.customStyle ?? null,
    purposeCategory: ctx.input.purposeCategory ?? null,
  });

  const userMd = generateUserMd(ctx.input.displayName);

  const interestsMd = generateInterestsMd(
    ctx.input.personalityPreset ?? null
  );

  const heartbeatMd = generateHeartbeatMd();

  const routerType = ctx.input.modelSelection === "clawy_smart_routing"
    ? (ctx.input.routerType ?? "standard")
    : "standard";
  const routingMd = generateRoutingMd(routerType);

  const userRulesMd = generateUserRulesMd(ctx.input.agentRules ?? null);

  const files: Record<string, string> = {
    "IDENTITY.md": identityMd,
    "USER.md": userMd,
    "HEARTBEAT.md": heartbeatMd,
    "INTERESTS.md": interestsMd,
    "ROUTING.md": routingMd,
  };
  if (userRulesMd) {
    files["USER-RULES.md"] = userRulesMd;
  }

  await ctx.k8s.createSecret(
    ctx.namespace,
    `dynamic-files-${ctx.input.botId}`,
    files
  );
}

async function getWalletIdForBot(botId: string): Promise<string | undefined> {
  const supabase = createAdminClient();
  const { data } = await supabase
    .from("bots")
    .select("privy_wallet_id")
    .eq("id", botId)
    .single();
  return data?.privy_wallet_id ?? undefined;
}

// Step 6: Generate openclaw.json config
async function stepGenerateConfig(ctx: StepContext): Promise<void> {
  // Query active integrations for this user
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const supabase = createAdminClient() as any;
  const { data: integrationRows } = await supabase
    .from("user_integrations")
    .select("provider")
    .eq("user_id", ctx.input.userId)
    .eq("status", "active");
  const activeIntegrations = ((integrationRows as Array<{ provider: string }>) ?? []).map(
    (r: { provider: string }) => r.provider
  );
  ctx.activeIntegrations = activeIntegrations;

  const config = buildOpenclawConfig({
    modelSelection: ctx.input.modelSelection as
      | "smart_routing"
      | "haiku"
      | "sonnet"
      | "opus"
      | "kimi_k2_5"
      | "minimax_m2_5"
      | "minimax_m2_7"
      | "gpt_5_nano"
      | "gpt_5_mini"
      | "gpt_5_1"
      | "gpt_5_5"
      | "gpt_5_5_pro"
      | "gpt_5_4"
      | "gpt_smart_routing"
      | "clawy_smart_routing"
      | "codex"
      | "gemini_2_5_flash"
      | "gemini_2_5_pro"
      | "gemini_3_1_flash_lite"
      | "gemini_3_1_pro"
      | "local_gemma_fast"
      | "local_gemma_max"
      | "local_qwen_uncensored",
    apiKey: ctx.input.anthropicApiKey || ctx.gatewayToken,
    botToken: ctx.input.telegramBotToken,
    gatewayToken: ctx.gatewayToken,
    baseUrl: ctx.input.proxyBaseUrl,
    fireworksApiKey: ctx.input.fireworksApiKey,
    openaiApiKey: ctx.input.openaiApiKey,
    codexAccessToken: ctx.input.codexAccessToken,
    codexRefreshToken: ctx.input.codexRefreshToken,
    privyAppId: process.env.NEXT_PUBLIC_PRIVY_APP_ID,
    privyAppSecret: process.env.PRIVY_APP_SECRET,
    privyWalletId: await getWalletIdForBot(ctx.input.botId),
    activeIntegrations,
    chatProxyUrl: process.env.CHAT_PROXY_URL ?? "https://chat.openmagi.ai",
    routerType: ctx.input.routerType,
  });

  await ctx.k8s.createSecret(
    ctx.namespace,
    `config-${ctx.input.botId}`,
    {
      "openclaw.json": JSON.stringify(config, null, 2),
    }
  );

  // Create router config secret (overrides baked-in config.json in Docker image)
  if (ctx.input.modelSelection === "smart_routing") {
    const routerConfigPath = join(process.cwd(), "infra", "docker", "router", "config.json");
    const routerConfig = JSON.parse(readFileSync(routerConfigPath, "utf-8"));

    // Router reads apiBaseUrl from config.json — override it to point through
    // the api-proxy when using platform credits, so gw_ tokens get swapped.
    if (ctx.input.proxyBaseUrl) {
      routerConfig.apiBaseUrl = ctx.input.proxyBaseUrl;
    }

    await ctx.k8s.createSecret(
      ctx.namespace,
      `router-config-${ctx.input.botId}`,
      { "config.json": JSON.stringify(routerConfig, null, 2) }
    );
  }

  // Create OpenAI router config secret for GPT smart routing
  if (ctx.input.modelSelection === "gpt_smart_routing") {
    const openaiRouterConfigPath = join(process.cwd(), "infra", "docker", "openai-router", "config.json");
    const openaiRouterConfig = JSON.parse(readFileSync(openaiRouterConfigPath, "utf-8"));

    if (ctx.input.proxyBaseUrl) {
      openaiRouterConfig.apiBaseUrl = ctx.input.proxyBaseUrl;
    }

    await ctx.k8s.createSecret(
      ctx.namespace,
      `openai-router-config-${ctx.input.botId}`,
      { "config.json": JSON.stringify(openaiRouterConfig, null, 2) }
    );
  }

  // Create Open Magi smart router config secret
  if (ctx.input.modelSelection === "clawy_smart_routing") {
    // Pick the right config file based on router type
    const configFileName = ctx.input.routerType === "only_claude" ? "config-only-claude.json"
      : ctx.input.routerType === "claude_supremacy" ? "config-claude-supremacy.json"
      : "config.json";
    const clawyRouterConfigPath = join(process.cwd(), "infra", "docker", "clawy-smart-router", configFileName);
    const clawyRouterConfig = JSON.parse(readFileSync(clawyRouterConfigPath, "utf-8"));

    // Platform credits mode: override all provider base URLs to route through api-proxy
    if (ctx.input.proxyBaseUrl) {
      for (const provider of Object.keys(clawyRouterConfig.providers)) {
        clawyRouterConfig.providers[provider].baseUrl = ctx.input.proxyBaseUrl;
      }
      if (clawyRouterConfig.classifier.baseUrl) {
        clawyRouterConfig.classifier.baseUrl = ctx.input.proxyBaseUrl;
      }
    }

    await ctx.k8s.createSecret(
      ctx.namespace,
      `clawy-smart-router-config-${ctx.input.botId}`,
      { "config.json": JSON.stringify(clawyRouterConfig, null, 2) }
    );
  }

  // Create Big Dic Router config secret (for clawy_smart_routing + big_dic router type)
  const useBigDicRouter = ctx.input.modelSelection === "clawy_smart_routing" && ctx.input.routerType === "big_dic";
  if (useBigDicRouter) {
    const bigDicConfigPath = join(process.cwd(), "infra", "docker", "big-dic-router", "config.json");
    const bigDicConfig = JSON.parse(readFileSync(bigDicConfigPath, "utf-8"));

    if (ctx.input.proxyBaseUrl) {
      for (const provider of Object.keys(bigDicConfig.providers)) {
        bigDicConfig.providers[provider].baseUrl = ctx.input.proxyBaseUrl;
      }
      // Classifier goes through api-proxy too (Anthropic Messages format)
    }

    await ctx.k8s.createSecret(
      ctx.namespace,
      `big-dic-router-config-${ctx.input.botId}`,
      { "config.json": JSON.stringify(bigDicConfig, null, 2) }
    );
  }
}

// Integration skill directories → required provider mapping
// Skills mapped to "__external__" are excluded from all internal bots
// (x402 gateway skills are for external API marketplace only)
const INTEGRATION_SKILL_PROVIDERS: Record<string, string> = {
  "google-calendar": "google",
  "google-gmail": "google",
  "google-drive": "google",
  "google-sheets": "google",
  "google-docs": "google",
  "google-ads": "google",
  "notion-integration": "notion",
  "notion-kb": "notion",
  "slack-integration": "slack",
  "spotify-integration": "spotify",
  "twitter": "twitter",
  "meta-social": "meta",
  "meta-ads": "meta",
  "meta-insights": "meta",
  // x402 gateway — external API marketplace only, not for internal bots
  "clawy-services": "__external__",
  // device-* skills are always included (no provider requirement)
};

/**
 * Build a shell script that writes all skill files to the 3 skill directories
 * inside the running pod via base64-encoded echo commands.
 */
export function buildSkillCopyScript(skillsData: Record<string, string>): string {
  // Collect unique top-level skill directory names from the server-managed skills
  const serverSkillDirs = new Set<string>();
  for (const safeKey of Object.keys(skillsData)) {
    const relPath = safeKey.replace(/__/g, "/");
    const topDir = relPath.split("/")[0];
    serverSkillDirs.add(topDir);
  }

  const lines: string[] = [
    "set -e",
    'SKILLS_GW="/home/ocuser/.openclaw/workspace/skills"',
    'SKILLS_AGENT="/home/ocuser/.openclaw/agents/main/workspace/skills"',
    'SKILLS_SHARED="/home/ocuser/.openclaw/agents-shared/templates/skills"',
    "mkdir -p \"$SKILLS_GW\" \"$SKILLS_AGENT\" \"$SKILLS_SHARED\"",
  ];

  // Only remove server-managed skill directories, preserving user-created ones
  for (const dir of serverSkillDirs) {
    for (const prefix of ["$SKILLS_GW", "$SKILLS_AGENT", "$SKILLS_SHARED"]) {
      lines.push(`rm -rf "${prefix}/${dir}"`);
    }
  }

  for (const [safeKey, content] of Object.entries(skillsData)) {
    const relPath = safeKey.replace(/__/g, "/");
    const b64 = Buffer.from(content).toString("base64");
    for (const prefix of ["$SKILLS_GW", "$SKILLS_AGENT", "$SKILLS_SHARED"]) {
      lines.push(`mkdir -p "$(dirname "${prefix}/${relPath}")"`);
      lines.push(`echo "${b64}" | base64 -d > "${prefix}/${relPath}"`);
    }
  }

  return lines.join("\n");
}

interface CustomSkillDataRow {
  skill_name: string;
  content: string;
}

/** Collect skill files, filtered by active integrations. Returns safeKey → content map. */
export function collectSkillData(
  activeIntegrations?: string[],
  customSkills?: CustomSkillDataRow[]
): Record<string, string> {
  const skillFiles = collectFiles(SKILLS_DIR);
  const activeSet = new Set(activeIntegrations ?? []);
  const result: Record<string, string> = {};
  for (const relPath of skillFiles) {
    const skillDir = relPath.split("/")[0];
    const requiredProvider = INTEGRATION_SKILL_PROVIDERS[skillDir];
    if (requiredProvider && !activeSet.has(requiredProvider)) {
      continue;
    }
    // K8s secret keys cannot contain '/' — replace with '__' (reversed on pod init)
    const safeKey = relPath.replace(/\//g, "__");
    result[safeKey] = readFileSync(join(SKILLS_DIR, relPath), "utf-8");
  }
  for (const skill of customSkills ?? []) {
    if (!skill.skill_name.startsWith("custom-")) continue;
    if (!skill.content.trim()) continue;
    result[customSkillPathKey(skill.skill_name)] = skill.content;
  }
  return result;
}

// Step 7: Skills — now served via HTTP from provisioning worker
// No Secret creation needed; init container downloads tar.gz at pod startup.
async function stepCopySkills(_ctx: StepContext): Promise<void> {
  // No-op: skills are pulled via HTTP by the init container
  // Endpoint: GET /skills/:botId on provisioning-worker:8080
}

// Step 8: Copy specialist templates (AGENTS.md, HEARTBEAT.md, TOOLS.md + knowledge files)
async function stepCopySpecialistTemplates(ctx: StepContext): Promise<void> {
  const templateData: Record<string, string> = {};

  // Specialist template files (AGENTS.md, HEARTBEAT.md, TOOLS.md)
  const specialistFiles = collectFiles(SPECIALIST_TEMPLATES_DIR);
  for (const relPath of specialistFiles) {
    const safeKey = relPath.replace(/\//g, "__");
    templateData[safeKey] = readFileSync(join(SPECIALIST_TEMPLATES_DIR, relPath), "utf-8");
  }

  // Knowledge files (useful-mcps.md etc.)
  try {
    const knowledgeFiles = collectFiles(KNOWLEDGE_DIR);
    for (const relPath of knowledgeFiles) {
      const safeKey = `knowledge__${relPath.replace(/\//g, "__")}`;
      templateData[safeKey] = readFileSync(join(KNOWLEDGE_DIR, relPath), "utf-8");
    }
  } catch {
    // Knowledge directory may not exist — skip gracefully
  }

  await ctx.k8s.createSecret(
    ctx.namespace,
    `specialist-templates-${ctx.input.botId}`,
    templateData
  );
}

// Step 9: Copy lifecycle scripts (agent-create.sh, agent-archive.sh, etc.)
async function stepCopyLifecycleScripts(ctx: StepContext): Promise<void> {
  const scriptsData: Record<string, string> = {};

  try {
    const scriptFiles = collectFiles(LIFECYCLE_SCRIPTS_DIR);
    for (const relPath of scriptFiles) {
      const safeKey = relPath.replace(/\//g, "__");
      scriptsData[safeKey] = readFileSync(join(LIFECYCLE_SCRIPTS_DIR, relPath), "utf-8");
    }
  } catch {
    // Scripts directory may not exist — skip gracefully
  }

  if (Object.keys(scriptsData).length > 0) {
    await ctx.k8s.createSecret(
      ctx.namespace,
      `lifecycle-scripts-${ctx.input.botId}`,
      scriptsData
    );
  }
}

// Step 10: Copy Agent OS plugin code as Secret
async function stepCopyPlugin(ctx: StepContext): Promise<void> {
  const pluginData: Record<string, string> = {};

  try {
    const pluginFiles = collectFiles(PLUGIN_DIR);
    for (const relPath of pluginFiles) {
      const safeKey = relPath.replace(/\//g, "__");
      pluginData[safeKey] = readFileSync(join(PLUGIN_DIR, relPath), "utf-8");
    }
  } catch {
    // Plugin directory may not exist — skip gracefully
  }

  if (Object.keys(pluginData).length > 0) {
    await ctx.k8s.createSecret(
      ctx.namespace,
      `agent-os-plugin-${ctx.input.botId}`,
      pluginData
    );
  }
}

// Step 11: Create Pod
//
// Architecture:
//   initContainer "setup" — runs openclaw onboard + writes config + copies workspace files
//   container "gateway"   — openclaw gateway run (foreground)
//   container "node-host" — openclaw node run (connects to gateway via localhost)
//   container "iblai-router" (optional) — smart routing sidecar
//
// Shared volumes:
//   "openclaw-home" (emptyDir) — mounted at /home/ocuser/.openclaw (config + state)
//   "workspace" (PVC) — persistent agent workspace
//
async function stepCreatePod(ctx: StepContext): Promise<void> {
  const GATEWAY_PORT = "8080";

  // Build the init script that creates the directory structure and copies files.
  // We skip `openclaw onboard` entirely — it's too memory-heavy for init containers.
  // Instead we create the directory structure manually and write the config directly.
  const initScript = `
set -e

CONFIG_DIR="/home/ocuser/.openclaw"

# 1. Create directory structure (matches what openclaw onboard would create)
mkdir -p "$CONFIG_DIR/workspace"
mkdir -p "$CONFIG_DIR/agents/main/workspace"
mkdir -p "$CONFIG_DIR/agents/main/sessions"
mkdir -p "$CONFIG_DIR/credentials"

# 2. Write our generated openclaw.json config
if [ -f /mnt/config/openclaw.json ]; then
  cp /mnt/config/openclaw.json "$CONFIG_DIR/openclaw.json"
  echo "[init] Config written"
fi

# 3. Copy static templates to both workspace paths (dual workspace pattern)
GATEWAY_WS="$CONFIG_DIR/workspace"
AGENT_WS="$CONFIG_DIR/agents/main/workspace"

# Create memory hierarchy directories
mkdir -p "$GATEWAY_WS/memory" "$GATEWAY_WS/plans" "$GATEWAY_WS/knowledge" "$GATEWAY_WS/knowledge/discoveries"
mkdir -p "$AGENT_WS/memory" "$AGENT_WS/plans" "$AGENT_WS/knowledge" "$AGENT_WS/knowledge/discoveries"
echo "[init] Memory hierarchy dirs created"

# Files that should NOT be overwritten if they already exist on PVC
# These accumulate user data over time — overwriting = data loss
USER_FILES="MEMORY.md SCRATCHPAD.md WORKING.md USER.md SOUL.md INTERESTS.md"

# Static templates from secret mount
for f in /mnt/static-templates/*; do
  [ -f "$f" ] || continue
  fname=$(basename "$f")
  IS_USER_FILE=false
  for uf in $USER_FILES; do
    [ "$fname" = "$uf" ] && IS_USER_FILE=true && break
  done
  if [ "$IS_USER_FILE" = "true" ] && [ -f "$GATEWAY_WS/$fname" ]; then
    echo "[init] Static: $fname (preserved existing)"
    continue
  fi
  cp "$f" "$GATEWAY_WS/$fname"
  cp "$f" "$AGENT_WS/$fname"
  echo "[init] Static: $fname"
done

# Move plans/ files into subdirectory
for pf in TASK-QUEUE.md CURRENT-PLAN.md; do
  if [ -f "$GATEWAY_WS/$pf" ]; then
    mv "$GATEWAY_WS/$pf" "$GATEWAY_WS/plans/$pf"
    mv "$AGENT_WS/$pf" "$AGENT_WS/plans/$pf"
    echo "[init] Moved $pf -> plans/$pf"
  fi
done

# Dynamic files from secret mount
# USER.md is preserved if it already exists (user data accumulates over time)
for f in /mnt/dynamic-files/*; do
  [ -f "$f" ] || continue
  fname=$(basename "$f")
  if ([ "$fname" = "USER.md" ] || [ "$fname" = "INTERESTS.md" ]) && [ -f "$GATEWAY_WS/$fname" ]; then
    echo "[init] Dynamic: $fname (preserved existing)"
    continue
  fi
  cp "$f" "$GATEWAY_WS/$fname"
  cp "$f" "$AGENT_WS/$fname"
  echo "[init] Dynamic: $fname"
done

# 4. Download skills via HTTP from provisioning worker (replaces Secret mount)
SKILLS_GW="$GATEWAY_WS/skills"
SKILLS_AGENT="$AGENT_WS/skills"
SKILLS_SHARED="$CONFIG_DIR/agents-shared/templates/skills"
mkdir -p "$SKILLS_GW" "$SKILLS_AGENT" "$SKILLS_SHARED"

SKILLS_URL="http://provisioning-worker.clawy-system.svc.cluster.local:8080/skills/${ctx.input.botId}"
SKILLS_OK=0
for i in 1 2 3; do
  if curl -sf -H "Authorization: Bearer $GATEWAY_TOKEN" "$SKILLS_URL" | tar xz -C "$SKILLS_GW" 2>/dev/null; then
    SKILLS_OK=1
    break
  fi
  echo "[init] Skills download retry $i..."
  sleep 2
done
if [ "$SKILLS_OK" = "1" ]; then
  cp -r "$SKILLS_GW"/* "$SKILLS_AGENT"/ 2>/dev/null || true
  cp -r "$SKILLS_GW"/* "$SKILLS_SHARED"/ 2>/dev/null || true
  echo "[init] Skills installed: $(ls "$SKILLS_GW" | wc -l) skills"
else
  echo "[init] WARN: Skills download failed, continuing without skills"
fi

# 5. Write Telegram auto-approval file (numeric ID only — OpenClaw rejects usernames)
if [ -n "$TELEGRAM_OWNER_ID" ]; then
  echo '{"version":1,"allowFrom":['"$TELEGRAM_OWNER_ID"']}' > "$CONFIG_DIR/credentials/telegram-default-allowFrom.json"
  echo "[init] Telegram auto-approval for owner ID $TELEGRAM_OWNER_ID"
else
  echo "[init] No numeric Telegram owner ID — skipping auto-approval (will resolve after first message)"
fi

# 6. Write auth-profiles.json for OAuth providers (Codex)
# OpenClaw reads from OPENCLAW_STATE_DIR/agents/main/agent/auth-profiles.json
# Our wrapper sets OPENCLAW_STATE_DIR=cli-state, so the path is:
#   $CONFIG_DIR/cli-state/agents/main/agent/auth-profiles.json
if [ -n "$CODEX_ACCESS_TOKEN" ]; then
  AUTH_DIR="$CONFIG_DIR/cli-state/agents/main/agent"
  mkdir -p "$AUTH_DIR"
  # Extract JWT exp claim for expires field (milliseconds)
  CODEX_EXPIRES_MS=$(node -e "try{const p=process.argv[1].split('.')[1];const d=JSON.parse(Buffer.from(p,'base64url'));console.log(d.exp*1000)}catch{console.log(Date.now()+864000000)}" "$CODEX_ACCESS_TOKEN" 2>/dev/null)
  [ -z "$CODEX_EXPIRES_MS" ] && CODEX_EXPIRES_MS="$(( $(date +%s) * 1000 + 864000000 ))"
  if [ -n "$CODEX_REFRESH_TOKEN" ]; then
    cat > "$AUTH_DIR/auth-profiles.json" << AUTHEOF
{
  "version": 1,
  "profiles": {
    "openai-codex:default": {
      "type": "oauth",
      "access": "$CODEX_ACCESS_TOKEN",
      "refreshToken": "$CODEX_REFRESH_TOKEN",
      "expires": $CODEX_EXPIRES_MS,
      "provider": "openai-codex"
    }
  }
}
AUTHEOF
  else
    cat > "$AUTH_DIR/auth-profiles.json" << AUTHEOF
{
  "version": 1,
  "profiles": {
    "openai-codex:default": {
      "type": "api_key",
      "key": "$CODEX_ACCESS_TOKEN",
      "provider": "openai-codex"
    }
  }
}
AUTHEOF
  fi
  chmod 600 "$AUTH_DIR/auth-profiles.json"
  chown ocuser:ocuser "$AUTH_DIR/auth-profiles.json"
  echo "[init] auth-profiles.json written (openai-codex OAuth, type=\${CODEX_REFRESH_TOKEN:+oauth}\${CODEX_REFRESH_TOKEN:-api_key})"
fi

# 7. Multi-agent infrastructure
TEMPLATES_DIR="$CONFIG_DIR/agents-shared/templates"
BIN_DIR="$CONFIG_DIR/bin"
mkdir -p "$TEMPLATES_DIR/skills" "$TEMPLATES_DIR/knowledge"
mkdir -p "$BIN_DIR"

# Copy specialist templates (AGENTS.md, HEARTBEAT.md, TOOLS.md + knowledge)
for f in /mnt/specialist-templates/*; do
  [ -f "$f" ] || continue
  filename=$(basename "$f")
  real_path=$(echo "$filename" | sed 's/__/\\//g')
  dir=$(dirname "$TEMPLATES_DIR/$real_path")
  mkdir -p "$dir"
  cp "$f" "$TEMPLATES_DIR/$real_path"
  echo "[init] Specialist template: $real_path"
done

# Copy main agent skills to specialist templates (specialists get same skills)
cp -r "$AGENT_WS/skills/"* "$TEMPLATES_DIR/skills/" 2>/dev/null || true

# Copy lifecycle scripts and make executable
for f in /mnt/lifecycle-scripts/*; do
  [ -f "$f" ] || continue
  cp "$f" "$BIN_DIR/$(basename "$f")"
  echo "[init] Script: $(basename "$f")"
done
chmod +x "$BIN_DIR"/*.sh 2>/dev/null || true

# 8. Install Agent OS plugin to extensions directory
PLUGIN_DEST="$CONFIG_DIR/extensions/clawy-agent-os"
mkdir -p "$PLUGIN_DEST/src"
for f in /mnt/agent-os-plugin/*; do
  [ -f "$f" ] || continue
  fname=$(basename "$f")
  case "$fname" in
    src__*)
      real=$(echo "$fname" | sed 's/^src__//')
      cp "$f" "$PLUGIN_DEST/src/$real"
      echo "[init] Plugin: src/$real"
      ;;
    *)
      cp "$f" "$PLUGIN_DEST/$fname"
      echo "[init] Plugin: $fname"
      ;;
  esac
done

# Initialize AGENT-REGISTRY.md in main workspace
cat > "$AGENT_WS/AGENT-REGISTRY.md" << 'REGEOF'
# Agent Registry

## Active Agents (0/8 slots)

(no specialists created yet)

## Archived Agents

(none)
REGEOF
cp "$AGENT_WS/AGENT-REGISTRY.md" "$GATEWAY_WS/AGENT-REGISTRY.md"

# Create specialists workspace directory
mkdir -p "$CONFIG_DIR/specialists"
mkdir -p "$CONFIG_DIR/archive"

# Inject apiRoot into main config (OpenClaw strips it during normalize, so we set it last)
if [ -f "$CONFIG_DIR/openclaw.json" ]; then
  node -e '
    const fs = require("fs");
    const f = process.argv[1];
    try {
      const c = JSON.parse(fs.readFileSync(f, "utf-8"));
      if (c.channels && c.channels.telegram && !c.channels.telegram.apiRoot) {
        c.channels.telegram.apiRoot = "https://telegram-gate.clawy-system.svc:3443";
        fs.writeFileSync(f, JSON.stringify(c, null, 2));
        console.log("[init] apiRoot injected into config");
      }
    } catch(e) { console.log("[init] apiRoot inject skipped:", e.message); }
  ' "$CONFIG_DIR/openclaw.json" 2>/dev/null || true
fi

echo "[init] Setup complete"
`;

  const commonEnv = [
    { name: "HOME", value: "/home/ocuser" },
    { name: "OPENCLAW_GATEWAY_TOKEN", valueFrom: { secretKeyRef: { name: ctx.secretName, key: "GATEWAY_TOKEN" } } },
    { name: "NODE_EXTRA_CA_CERTS", value: "/etc/ssl/clawy/ca.pem" },
  ];

  const initEnv = [
    ...commonEnv,
    { name: "GATEWAY_TOKEN", valueFrom: { secretKeyRef: { name: ctx.secretName, key: "GATEWAY_TOKEN" } } },
    { name: "TELEGRAM_OWNER_HANDLE", value: ctx.input.telegramUserHandle ?? "" },
    { name: "TELEGRAM_OWNER_ID", value: ctx.input.telegramOwnerId ? String(ctx.input.telegramOwnerId) : "" },
    { name: "CODEX_ACCESS_TOKEN", valueFrom: { secretKeyRef: { name: ctx.secretName, key: "CODEX_ACCESS_TOKEN" } } },
    { name: "CODEX_REFRESH_TOKEN", valueFrom: { secretKeyRef: { name: ctx.secretName, key: "CODEX_REFRESH_TOKEN" } } },
  ];

  const ocHomeMount = { name: "openclaw-home", mountPath: "/home/ocuser/.openclaw" };

  // Init container
  const initContainers = [
    {
      name: "setup",
      image: GATEWAY_IMAGE,  // Alpine-based, has sh/cp/mkdir
      command: ["sh", "-c", initScript],
      env: initEnv,
      volumeMounts: [
        ocHomeMount,
        { name: "workspace", mountPath: "/workspace" },
        { name: "config-vol", mountPath: "/mnt/config" },
        { name: "static-templates-vol", mountPath: "/mnt/static-templates" },
        { name: "dynamic-files-vol", mountPath: "/mnt/dynamic-files" },
        { name: "specialist-templates-vol", mountPath: "/mnt/specialist-templates" },
        { name: "lifecycle-scripts-vol", mountPath: "/mnt/lifecycle-scripts" },
        { name: "agent-os-plugin-vol", mountPath: "/mnt/agent-os-plugin" },
      ],
      resources: {
        requests: { cpu: "50m", memory: "32Mi" },
        limits: { cpu: "200m", memory: "64Mi" },
      },
    },
  ];

  // MAX/FLEX plan: dedicated node with higher resource limits
  const isFlex = ctx.input.pricingTier === "max" || ctx.input.pricingTier === "flex";

  // Flex/Max: boost init container for qmd vector embedding (~2GB model download + load)
  if (isFlex) {
    initContainers[0].resources = {
      requests: { cpu: "200m", memory: "512Mi" },
      limits: { cpu: "1000m", memory: "3Gi" },
    };
  }
  const gatewayMemory = isFlex ? "6Gi" : "5Gi";
  const nodeHostMemory = isFlex ? "6Gi" : "5Gi";
  const gatewayMaxOldSpace = isFlex ? "5120" : "4096";
  const nodeHostMaxOldSpace = isFlex ? "5120" : "4096";
  const gatewayCpuLimit = isFlex ? "2000m" : "500m";
  const nodeHostCpuLimit = isFlex ? "2000m" : "1000m";

  const containers: ContainerSpec[] = [
    // Gateway container
    {
      name: "gateway",
      image: GATEWAY_IMAGE,
      command: ["openclaw", "gateway", "run", "--port", GATEWAY_PORT, "--bind", "lan"],
      ports: [{ containerPort: Number(GATEWAY_PORT), name: "gateway" }],
      env: [
        ...commonEnv,
        { name: "BOT_ID", value: ctx.input.botId },
        { name: "NODE_OPTIONS", value: `--max-old-space-size=${gatewayMaxOldSpace}` },
      ],
      volumeMounts: [
        ocHomeMount,
        { name: "workspace", mountPath: "/workspace" },
        { name: "clawy-ca", mountPath: "/etc/ssl/clawy", readOnly: true },
      ],
      resources: {
        requests: { cpu: "80m", memory: "512Mi" },
        limits: { cpu: gatewayCpuLimit, memory: gatewayMemory },
      },
      // Graceful shutdown: give gateway time to flush cron jobs + session state to PVC
      lifecycle: { preStop: { exec: { command: ["sh", "-c", "sleep 5"] } } },
    },
    // Node host container
    {
      name: "node-host",
      image: NODE_HOST_IMAGE,
      command: ["openclaw", "node", "run", "--host", "127.0.0.1", "--port", GATEWAY_PORT],
      ports: [{ containerPort: 3000, name: "node-host" }],
      env: [
        ...commonEnv,
        { name: "BOT_ID", value: ctx.input.botId },
        { name: "NODE_OPTIONS", value: `--max-old-space-size=${nodeHostMaxOldSpace}` },
        // CLI must use separate identity from gateway — gateway rejects its own device ID
        { name: "OPENCLAW_STATE_DIR", value: "/home/ocuser/.openclaw/cli-state" },
      ],
      volumeMounts: [
        ocHomeMount,
        { name: "workspace", mountPath: "/workspace" },
        { name: "clawy-ca", mountPath: "/etc/ssl/clawy", readOnly: true },
      ],
      resources: {
        requests: { cpu: "150m", memory: "512Mi" },
        limits: { cpu: nodeHostCpuLimit, memory: nodeHostMemory },
      },
    },
  ];

  // iblai-router sidecar for smart routing
  const useSmartRouting = ctx.input.modelSelection === "smart_routing";
  if (useSmartRouting) {
    const routerEnv = [
      // Router reads API key from K8s secret (auth-profiles.json fallback not available in sidecar)
      // For platform_credits: this is a gw_ gateway token, not the real key
      { name: "ANTHROPIC_API_KEY", valueFrom: { secretKeyRef: { name: ctx.secretName, key: "ANTHROPIC_API_KEY" } } },
      { name: "ROUTER_PORT", value: "8402" },
      { name: "ROUTER_LOG", value: "1" },
      { name: "NODE_OPTIONS", value: "--max-old-space-size=256" },
    ];

    // For BYOK bots: enable usage logging in the router so we can track API usage
    if (ctx.input.apiKeyMode === "byok") {
      const appUrl = process.env.NEXT_PUBLIC_APP_URL || "https://openmagi.ai";
      routerEnv.push(
        { name: "BOT_ID", value: ctx.input.botId },
        { name: "USER_ID", value: ctx.input.userId },
        { name: "USAGE_LOG_URL", value: `${appUrl}/api/internal/usage` },
        { name: "INTERNAL_SERVICE_TOKEN", value: process.env.INTERNAL_SERVICE_TOKEN || "" },
      );
    }

    containers.push({
      name: "iblai-router",
      image: ROUTER_IMAGE,
      command: ["node", "/home/routeruser/router-proxy.js"],
      ports: [{ containerPort: 8402, name: "router" }],
      env: routerEnv,
      volumeMounts: [
        { name: "router-config-vol", mountPath: "/home/routeruser/config.json", subPath: "config.json" },
      ],
      resources: {
        requests: { cpu: "30m", memory: "48Mi" },
        limits: { cpu: "200m", memory: "256Mi" },
      },
    });
  }

  // openai-router sidecar for GPT smart routing
  const useGptSmartRouting = ctx.input.modelSelection === "gpt_smart_routing";
  if (useGptSmartRouting) {
    const openaiRouterEnv = [
      { name: "OPENAI_API_KEY", valueFrom: { secretKeyRef: { name: ctx.secretName, key: "OPENAI_API_KEY" } } },
      // For platform credits: use the anthropic key (gw_ token) as passthrough
      { name: "ANTHROPIC_API_KEY", valueFrom: { secretKeyRef: { name: ctx.secretName, key: "ANTHROPIC_API_KEY" } } },
      { name: "ROUTER_PORT", value: "8404" },
      { name: "ROUTER_LOG", value: "1" },
      { name: "NODE_OPTIONS", value: "--max-old-space-size=256" },
    ];

    if (ctx.input.apiKeyMode === "byok") {
      const appUrl = process.env.NEXT_PUBLIC_APP_URL || "https://openmagi.ai";
      openaiRouterEnv.push(
        { name: "BOT_ID", value: ctx.input.botId },
        { name: "USER_ID", value: ctx.input.userId },
        { name: "USAGE_LOG_URL", value: `${appUrl}/api/internal/usage` },
        { name: "INTERNAL_SERVICE_TOKEN", value: process.env.INTERNAL_SERVICE_TOKEN || "" },
      );
    }

    containers.push({
      name: "openai-router",
      image: OPENAI_ROUTER_IMAGE,
      command: ["node", "/home/routeruser/openai-router-proxy.js"],
      ports: [{ containerPort: 8404, name: "openai-router" }],
      env: openaiRouterEnv,
      volumeMounts: [
        { name: "openai-router-config-vol", mountPath: "/home/routeruser/config.json", subPath: "config.json" },
      ],
      resources: {
        requests: { cpu: "30m", memory: "48Mi" },
        limits: { cpu: "200m", memory: "256Mi" },
      },
    });
  }

  // Open Magi router sidecar for cross-provider smart routing.
  const useOpenMagiRouter = ctx.input.modelSelection === "clawy_smart_routing";
  if (useOpenMagiRouter) {
    const openMagiRouterEnv = [
      { name: "ANTHROPIC_API_KEY", valueFrom: { secretKeyRef: { name: ctx.secretName, key: "ANTHROPIC_API_KEY" } } },
      { name: "ROUTER_PORT", value: "8406" },
      { name: "ROUTER_LOG", value: "1" },
      { name: "NODE_OPTIONS", value: "--max-old-space-size=256" },
    ];

    // Platform credits mode: route through api-proxy
    if (ctx.input.proxyBaseUrl) {
      openMagiRouterEnv.push({ name: "ROUTER_API_BASE_URL", value: ctx.input.proxyBaseUrl });
    }

    // BYOK mode: provide all three provider keys + usage logging
    if (ctx.input.apiKeyMode === "byok") {
      // In BYOK, the user must provide keys for all providers they want to use
      // The router will fail gracefully if a provider key is missing
      if (ctx.input.openaiApiKey) {
        openMagiRouterEnv.push({ name: "OPENAI_API_KEY", valueFrom: { secretKeyRef: { name: ctx.secretName, key: "OPENAI_API_KEY" } } });
      }
      if (ctx.input.fireworksApiKey) {
        openMagiRouterEnv.push({ name: "FIREWORKS_API_KEY", valueFrom: { secretKeyRef: { name: ctx.secretName, key: "FIREWORKS_API_KEY" } } });
      }

      const appUrl = process.env.NEXT_PUBLIC_APP_URL || "https://openmagi.ai";
      openMagiRouterEnv.push(
        { name: "BOT_ID", value: ctx.input.botId },
        { name: "USER_ID", value: ctx.input.userId },
        { name: "USAGE_LOG_URL", value: `${appUrl}/api/internal/usage` },
        { name: "INTERNAL_SERVICE_TOKEN", value: process.env.INTERNAL_SERVICE_TOKEN || "" },
      );
    }

    // Big Dic Router overrides the standard Open Magi router.
    if (ctx.input.routerType === "big_dic") {
      containers.push({
        name: "big-dic-router",
        image: BIG_DIC_ROUTER_IMAGE,
        command: ["node", "/home/routeruser/big-dic-router-proxy.js"],
        ports: [{ containerPort: 8408, name: "bigdic-router" }],
        env: [
          ...openMagiRouterEnv.map((e) =>
            e.name === "ROUTER_PORT" ? { name: "ROUTER_PORT", value: "8408" } : e
          ),
          // Platform-level fallback key for rate limit retry
          ...(process.env.ANTHROPIC_API_KEY_FALLBACK ? [{
            name: "ANTHROPIC_API_KEY_FALLBACK",
            value: process.env.ANTHROPIC_API_KEY_FALLBACK,
          }] : []),
        ],
        volumeMounts: [
          { name: "bigdic-router-config-vol", mountPath: "/home/routeruser/config.json", subPath: "config.json" },
        ],
        resources: {
          requests: { cpu: "30m", memory: "48Mi" },
          limits: { cpu: "200m", memory: "256Mi" },
        },
      });
    } else {
      containers.push({
        name: "clawy-smart-router",
        image: CLAWY_SMART_ROUTER_IMAGE,
        command: ["node", "/home/routeruser/clawy-smart-router-proxy.js"],
        ports: [{ containerPort: 8406, name: "clawy-router" }],
        env: openMagiRouterEnv,
        volumeMounts: [
          { name: "clawy-router-config-vol", mountPath: "/home/routeruser/config.json", subPath: "config.json" },
        ],
        resources: {
          requests: { cpu: "30m", memory: "48Mi" },
          limits: { cpu: "200m", memory: "256Mi" },
        },
      });
    }
  }

  // Security context: hardened — drop all capabilities + seccomp default (conservative).
  // NOTE: runAsUser NOT set — preserves image default (gateway reads root-owned files
  // written by init container). readOnlyRootFilesystem deferred (path audit pending).
  const securityContext = {
    allowPrivilegeEscalation: false,
    capabilities: { drop: ["ALL"] },
    seccompProfile: { type: "RuntimeDefault" },
  };

  // Apply securityContext to main containers only — init container needs runAsUser:0 for setup
  for (const c of containers) {
    c.securityContext = securityContext;
  }

  const podSpec: PodSpec = {
    initContainers,
    containers,
    volumes: [
      {
        name: "workspace",
        persistentVolumeClaim: { claimName: ctx.pvcName },
      },
      {
        name: "openclaw-home",
        emptyDir: {},
      },
      {
        name: "config-vol",
        secret: { secretName: `config-${ctx.input.botId}` },
      },
      {
        name: "static-templates-vol",
        secret: { secretName: `static-templates-${ctx.input.botId}` },
      },
      {
        name: "dynamic-files-vol",
        secret: { secretName: `dynamic-files-${ctx.input.botId}` },
      },
      {
        name: "specialist-templates-vol",
        secret: { secretName: `specialist-templates-${ctx.input.botId}` },
      },
      {
        name: "lifecycle-scripts-vol",
        secret: { secretName: `lifecycle-scripts-${ctx.input.botId}` },
      },
      {
        name: "agent-os-plugin-vol",
        secret: { secretName: `agent-os-plugin-${ctx.input.botId}` },
      },
      {
        name: "clawy-ca",
        secret: { secretName: "clawy-internal-ca", optional: true },
      },
      ...(useSmartRouting ? [{
        name: "router-config-vol",
        secret: { secretName: `router-config-${ctx.input.botId}` },
      }] : []),
      ...(useGptSmartRouting ? [{
        name: "openai-router-config-vol",
        secret: { secretName: `openai-router-config-${ctx.input.botId}` },
      }] : []),
      ...(useOpenMagiRouter && ctx.input.routerType !== "big_dic" ? [{
        name: "clawy-router-config-vol",
        secret: { secretName: `clawy-smart-router-config-${ctx.input.botId}` },
      }] : []),
      ...(useOpenMagiRouter && ctx.input.routerType === "big_dic" ? [{
        name: "bigdic-router-config-vol",
        secret: { secretName: `big-dic-router-config-${ctx.input.botId}` },
      }] : []),
    ],
    imagePullSecrets: (process.env.GHCR_USERNAME && process.env.GHCR_TOKEN)
      ? [{ name: "ghcr-secret" }]
      : undefined,
    restartPolicy: "Always",
    // Graceful shutdown: gateway needs time to flush cron scheduler state + active sessions to PVC
    terminationGracePeriodSeconds: 15,
    // MAX/FLEX plan: pin to a dedicated node via label selector + taint toleration
    ...(isFlex ? {
      nodeSelector: { dedicated: "flex-bot" },
      tolerations: [{ key: "dedicated", operator: "Equal", value: "flex-bot", effect: "NoSchedule" }],
    } : {}),
  };

  await ctx.k8s.createPod(ctx.namespace, ctx.podName, podSpec);
}
