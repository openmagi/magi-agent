import modelsJson from "@/lib/provisioning/shared/models.json";
import type { ModelSelection } from "@/lib/supabase/types";

type ProvisioningModelSelection = ModelSelection | "gpt_5_4";

interface ConfigInput {
  modelSelection: ProvisioningModelSelection;
  apiKey: string;
  botToken: string;
  gatewayToken: string;
  baseUrl?: string;
  fireworksApiKey?: string;
  openaiApiKey?: string;
  // Codex OAuth tokens are written to auth-profiles.json; presence selects the openai-codex provider.
  codexAccessToken?: string;
  codexRefreshToken?: string;
  geminiApiKey?: string;
  braveApiKey?: string;
  braveApiKeyIsUserOwned?: boolean;
  elevenlabsApiKey?: string;
  groqApiKey?: string;
  deeplApiKey?: string;
  alphaVantageApiKey?: string;
  finnhubApiKey?: string;
  fmpApiKey?: string;
  fredApiKey?: string;
  dartApiKey?: string;
  firecrawlApiKey?: string;
  semanticScholarApiKey?: string;
  serperApiKey?: string;
  githubToken?: string;
  googleApiKey?: string;
  privyAppId?: string;
  privyAppSecret?: string;
  privyWalletId?: string;
  activeIntegrations?: string[];
  chatProxyUrl?: string;
  routerType?: string;
}

const ROUTER_PORT = "8402";
const OPENAI_ROUTER_PORT = "8404";
const CLAWY_SMART_ROUTER_PORT = "8406";
const BIG_DIC_ROUTER_PORT = "8408";
const API_PROXY_INTERNAL_BASE_URL = "http://api-proxy.clawy-system.svc.cluster.local:3001";

const MODEL_IDS: Record<string, string> = modelsJson.MODEL_IDS;
const MODEL_MAX_TOKENS: Record<string, number> = modelsJson.MODEL_MAX_TOKENS;
const FIREWORKS_MODELS = new Set(modelsJson.FIREWORKS_MODELS);
const OPENAI_MODELS = new Set(modelsJson.OPENAI_MODELS);
const LEGACY_OPENAI_MODELS = new Set(modelsJson.LEGACY_OPENAI_MODELS ?? []);
const CODEX_OAUTH_COMPATIBLE_MODELS = new Set(modelsJson.CODEX_OAUTH_COMPATIBLE_MODELS ?? []);
const GOOGLE_MODELS = new Set(modelsJson.GOOGLE_MODELS);
const LOCAL_LLM_MODELS = new Set(modelsJson.LOCAL_LLM_MODELS);
const GPT_54_NANO_MODEL_ID = "gpt-5.4-nano";
const GPT_54_MINI_MODEL_ID = "gpt-5.4-mini";
const GPT_55_MODEL_ID = "gpt-5.5";
const GPT_55_PRO_MODEL_ID = "gpt-5.5-pro";
const GPT_55_PROVIDER_MODEL_ID = `openai/${GPT_55_MODEL_ID}`;
const GPT_55_PRO_PROVIDER_MODEL_ID = `openai/${GPT_55_PRO_MODEL_ID}`;
const GPT_55_CODEX_PROVIDER_MODEL_ID = `openai-codex/${GPT_55_MODEL_ID}`;
const ANTHROPIC_TEXT_CONTEXT_WINDOW = 200000;
const ANTHROPIC_OPUS_47_CONTEXT_WINDOW = 262144;
const PREMIUM_ROUTER_CONTEXT_TOKENS = 195000;

function openaiProxyModelDefs() {
  return [
    {
      id: GPT_54_NANO_MODEL_ID,
      name: "GPT-5.4 Nano",
      reasoning: false,
      input: ["text", "image"],
      cost: { input: 0.2, output: 1.25, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 128000,
      maxTokens: 4096,
    },
    {
      id: GPT_54_MINI_MODEL_ID,
      name: "GPT-5.4 Mini",
      reasoning: true,
      input: ["text", "image"],
      cost: { input: 0.75, output: 4.5, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 128000,
      maxTokens: 8192,
    },
    {
      id: GPT_55_MODEL_ID,
      name: "GPT-5.5",
      reasoning: true,
      input: ["text", "image"],
      cost: { input: 5, output: 30, cacheRead: 0.5, cacheWrite: 0 },
      contextWindow: 1000000,
      maxTokens: 128000,
    },
    {
      id: GPT_55_PRO_MODEL_ID,
      name: "GPT-5.5 Pro",
      reasoning: true,
      input: ["text", "image"],
      cost: { input: 30, output: 180, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 1050000,
      maxTokens: 128000,
    },
  ];
}

function googleProxyModelDefs() {
  return [
    {
      id: "gemini-3.1-flash-lite-preview",
      name: "Gemini 3.1 Flash Lite",
      reasoning: true,
      input: ["text", "image"],
      cost: { input: 0.25, output: 1.50, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 1048576,
      maxTokens: 65536,
    },
    {
      id: "gemini-3.1-pro-preview",
      name: "Gemini 3.1 Pro",
      reasoning: true,
      input: ["text", "image"],
      cost: { input: 2.00, output: 12.00, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 1048576,
      maxTokens: 65536,
    },
  ];
}

function isPlatformProxyBaseUrl(url: string): boolean {
  return (
    url.includes("api-proxy") ||
    url.includes("chat.openmagi.ai") ||
    url.includes("chat.clawy.pro")
  );
}

export function buildOpenclawConfig(input: ConfigInput) {
  const {
    modelSelection, apiKey, botToken, gatewayToken, baseUrl, fireworksApiKey,
    openaiApiKey, codexAccessToken, codexRefreshToken, geminiApiKey,
    braveApiKey,
    braveApiKeyIsUserOwned,
    elevenlabsApiKey, groqApiKey, deeplApiKey, alphaVantageApiKey,
    finnhubApiKey, fmpApiKey, fredApiKey, dartApiKey, firecrawlApiKey,
    semanticScholarApiKey, serperApiKey, githubToken,
    googleApiKey,
    privyAppId, privyAppSecret, privyWalletId,
    activeIntegrations,
    chatProxyUrl,
  } = input;

  const useSmartRouting = modelSelection === "smart_routing";
  const useGptSmartRouting = modelSelection === "gpt_smart_routing";
  const useOpenMagiRouter = modelSelection === "clawy_smart_routing";
  const useFireworks = FIREWORKS_MODELS.has(modelSelection);
  const useOpenAI = OPENAI_MODELS.has(modelSelection) || LEGACY_OPENAI_MODELS.has(modelSelection);
  const useGoogle = GOOGLE_MODELS.has(modelSelection);
  const useLocalLlm = LOCAL_LLM_MODELS.has(modelSelection);
  const useCodex = modelSelection === "codex";
  const hasCodexOAuth = !!(codexAccessToken || codexRefreshToken);
  const useCodexOAuthForModel = !useCodex && hasCodexOAuth && CODEX_OAUTH_COMPATIBLE_MODELS.has(modelSelection);

  // Providers: always include anthropic; add routers and third-party providers as needed
  const providers: Record<string, Record<string, unknown>> = {
    anthropic: {
      baseUrl: baseUrl ?? "https://api.anthropic.com",
      apiKey,
      models: [
        {
          id: "claude-haiku-4-5",
          name: "Claude Haiku 4.5",
          reasoning: false,
          input: ["text", "image"],
          cost: { input: 1, output: 5, cacheRead: 0.1, cacheWrite: 1.25 },
          contextWindow: ANTHROPIC_TEXT_CONTEXT_WINDOW,
          maxTokens: 4096,
        },
        {
          id: "claude-sonnet-4-6",
          name: "Claude Sonnet 4.6",
          reasoning: true,
          input: ["text", "image"],
          cost: { input: 3, output: 15, cacheRead: 0.3, cacheWrite: 3.75 },
          contextWindow: ANTHROPIC_TEXT_CONTEXT_WINDOW,
          maxTokens: 8192,
        },
        {
          id: "claude-opus-4-6",
          name: "Claude Opus 4.6",
          reasoning: true,
          input: ["text", "image"],
          cost: { input: 5, output: 25, cacheRead: 0.5, cacheWrite: 6.25 },
          contextWindow: ANTHROPIC_OPUS_47_CONTEXT_WINDOW,
          maxTokens: 16384,
        },
      ],
    },
  };

  if (useSmartRouting) {
    // iblai-router sidecar runs on localhost:8402, speaks Anthropic Messages API
    providers["iblai-router"] = {
      baseUrl: `http://127.0.0.1:${ROUTER_PORT}`,
      apiKey: "passthrough",
      api: "anthropic-messages",
      models: [
        {
          id: "auto",
          name: "iblai-router (auto)",
          reasoning: true,
          input: ["text", "image"],
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: ANTHROPIC_TEXT_CONTEXT_WINDOW,
          maxTokens: 8192,
        },
      ],
    };
  }

  if (useGptSmartRouting) {
    // openai-router sidecar runs on localhost:8404, speaks OpenAI completions API
    providers["openai-router"] = {
      baseUrl: `http://127.0.0.1:${OPENAI_ROUTER_PORT}`,
      apiKey: "passthrough",
      api: "openai-completions",
      models: [
        {
          id: "auto",
          name: "openai-router (auto)",
          reasoning: true,
          input: ["text", "image"],
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: 128000,
          maxTokens: 8192,
        },
      ],
    };
  }

  if (useOpenMagiRouter) {
    const isBigDic = input.routerType === "big_dic";
    const isClaudeSupremacy = input.routerType === "claude_supremacy";
    const routerPort = isBigDic ? BIG_DIC_ROUTER_PORT : CLAWY_SMART_ROUTER_PORT;
    const routerName = isBigDic ? "big-dic-router" : "clawy-smart-router";
    const routerLabel = isBigDic ? "Big Dic Router (FLEX)"
      : input.routerType === "only_claude" ? "Only Claude Router"
      : input.routerType === "claude_supremacy" ? "Claude Supremacy Router"
      : "Open Magi Router (auto)";

    providers[routerName] = {
      baseUrl: `http://127.0.0.1:${routerPort}`,
      apiKey: "passthrough",
      api: "openai-completions",
      models: [
        {
          id: "auto",
          name: routerLabel,
          reasoning: true,
          input: ["text", "image"],
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: (isBigDic || isClaudeSupremacy) ? ANTHROPIC_OPUS_47_CONTEXT_WINDOW : ANTHROPIC_TEXT_CONTEXT_WINDOW,
          maxTokens: 16384,
        },
      ],
    };
  }

  // Big Dic bots (any model_selection): register openai/google providers via api-proxy
  // so subagents can specify models directly (e.g., openai/gpt-5.5, google/gemini-3.1-pro-preview).
  if (input.routerType === "big_dic") {
    const proxyUrl = (input.baseUrl ?? "https://api.anthropic.com").replace(/\/v1\/?$/, "");
    const proxyKey = input.apiKey;
    if (!providers["openai"]) {
      providers["openai"] = {
        baseUrl: proxyUrl, apiKey: proxyKey, api: "openai-completions",
        models: openaiProxyModelDefs(),
      };
    }
    if (!providers["google"]) {
      providers["google"] = {
        baseUrl: proxyUrl, apiKey: proxyKey, api: "openai-completions",
        models: googleProxyModelDefs(),
      };
    }
  }

  if (useFireworks) {
    const fireworksModelDefs = [
      {
        id: fireworksApiKey ? "accounts/fireworks/models/kimi-k2p6" : "kimi-k2p6",
        name: "Kimi K2.6",
        reasoning: true,
        input: ["text", "image"],
        cost: { input: 0.95, output: 4.0, cacheRead: 0.16, cacheWrite: 0 },
        contextWindow: 262144,
        maxTokens: 32768,
      },
      {
        id: fireworksApiKey ? "accounts/fireworks/models/minimax-m2p7" : "minimax-m2p7",
        name: "MiniMax M2.7",
        reasoning: true,
        input: ["text"],
        cost: { input: 0.3, output: 1.2, cacheRead: 0.03, cacheWrite: 0 },
        contextWindow: 196608,
        maxTokens: 8192,
      },
    ];
    providers["fireworks"] = {
      baseUrl: fireworksApiKey ? "https://api.fireworks.ai/inference/v1" : (baseUrl ? baseUrl + "/v1" : "https://api.anthropic.com"),
      apiKey: fireworksApiKey || apiKey,
      api: "openai-completions",
      models: fireworksModelDefs,
    };
  }

  if (useOpenAI || useGptSmartRouting) {
    providers["openai"] = {
      baseUrl: openaiApiKey ? "https://api.openai.com/v1" : (baseUrl ? baseUrl + "/v1" : "https://api.anthropic.com"),
      apiKey: openaiApiKey || apiKey,
      api: "openai-completions",
      models: openaiProxyModelDefs(),
    };
  }

  if (useCodex || useCodexOAuthForModel) {
    // Codex: OAuth provider — credentials loaded from auth-profiles.json (not apiKey)
    // OpenClaw reads access/refresh tokens from auth-profiles.json and auto-refreshes
    providers["openai-codex"] = {
      baseUrl: "https://api.openai.com/v1",
      api: "openai-completions",
      models: [
        {
          id: GPT_55_MODEL_ID,
          name: "Codex (GPT-5.5)",
          reasoning: true,
          input: ["text", "image"],
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: 1000000,
          maxTokens: 128000,
        },
      ],
    };
  }

  if (useGoogle) {
    providers["google"] = {
      baseUrl: geminiApiKey ? "https://generativelanguage.googleapis.com/v1beta/openai" : (baseUrl ? baseUrl + "/v1" : "https://api.anthropic.com"),
      apiKey: geminiApiKey || apiKey,
      api: "openai-completions",
      models: googleProxyModelDefs(),
    };
  }

  if (useLocalLlm) {
    const localModelDefs = [
      {
        id: "gemma-fast",
        name: "Gemma 4 Fast (beta)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 131072,
        maxTokens: 8192,
      },
      {
        id: "gemma-max",
        name: "Gemma 4 Max (beta)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 131072,
        maxTokens: 8192,
      },
      {
        id: "qwen-uncensored",
        name: "Qwen 3.5 Uncensored (beta)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 131072,
        maxTokens: 8192,
      },
    ];
    providers.local = {
      baseUrl: `${(baseUrl ?? API_PROXY_INTERNAL_BASE_URL).replace(/\/$/, "")}/v1`,
      apiKey,
      api: "openai-completions",
      models: localModelDefs,
    };
  }

  if (baseUrl && isPlatformProxyBaseUrl(baseUrl)) {
    const proxyUrl = baseUrl.replace(/\/v1\/?$/, "");
    if (!providers["openai"]) {
      providers["openai"] = {
        baseUrl: proxyUrl,
        apiKey,
        api: "openai-completions",
        models: openaiProxyModelDefs(),
      };
    }
    if (!providers["google"]) {
      providers["google"] = {
        baseUrl: proxyUrl,
        apiKey,
        api: "openai-completions",
        models: googleProxyModelDefs(),
      };
    }
  }

  // Primary model resolution chain
  const modelShortId = MODEL_IDS[modelSelection]?.split("/")[1]; // e.g. "kimi-k2p6"
  let primaryModelId: string;

  if (useSmartRouting) {
    primaryModelId = "iblai-router/auto";
  } else if (useGptSmartRouting) {
    primaryModelId = "openai-router/auto";
  } else if (useOpenMagiRouter) {
    const rName = input.routerType === "big_dic" ? "big-dic-router" : "clawy-smart-router";
    primaryModelId = `${rName}/auto`;
  } else if (useCodexOAuthForModel) {
    primaryModelId = GPT_55_CODEX_PROVIDER_MODEL_ID;
  } else if (useCodex) {
    primaryModelId = GPT_55_CODEX_PROVIDER_MODEL_ID;
  } else if (useFireworks && fireworksApiKey) {
    primaryModelId = `fireworks/accounts/fireworks/models/${modelShortId}`;
  } else {
    // MODEL_IDS now has correct provider prefixes (openai/, fireworks/, anthropic/)
    primaryModelId = MODEL_IDS[modelSelection];
  }

  // Build per-model params (only for non-router models)
  const models: Record<string, { params: { maxTokens: number; cacheControlTtl: string; reasoningEffort?: string } }> = {};

  if (!useSmartRouting && !useGptSmartRouting && !useOpenMagiRouter) {
    // Look up max tokens from canonical MODEL_IDS key (works for both BYOK and platform paths)
    const maxTokens = MODEL_MAX_TOKENS[primaryModelId] ?? MODEL_MAX_TOKENS[MODEL_IDS[modelSelection]] ?? 8192;
    const isGpt55 = primaryModelId === GPT_55_PROVIDER_MODEL_ID
      || primaryModelId === GPT_55_PRO_PROVIDER_MODEL_ID
      || primaryModelId === GPT_55_CODEX_PROVIDER_MODEL_ID;
    models[primaryModelId] = {
      params: {
        maxTokens,
        cacheControlTtl: "1h",
        ...(isGpt55 ? { reasoningEffort: "xhigh" } : {}),
      },
    };
  }

  // Model-specific context token budgets
  const isBigDic = input.routerType === "big_dic";
  const isClaudeSupremacy = input.routerType === "claude_supremacy";
  const contextTokens = useCodex ? 195000
    : useSmartRouting ? 180000
    : useGptSmartRouting ? 120000
    : useOpenMagiRouter ? ((isBigDic || isClaudeSupremacy) ? PREMIUM_ROUTER_CONTEXT_TOKENS : 195000)
    : (["gpt_5_nano", "gpt_5_mini", "gpt_5_1"] as string[]).includes(modelSelection) ? 80000
    : (modelSelection === "gpt_5_5" || modelSelection === "gpt_5_5_pro" || modelSelection === "gpt_5_4") ? 250000
    : modelSelection === "kimi_k2_5" ? 250000
    : modelSelection === "minimax_m2_7" ? 180000
    : modelSelection === "gemini_3_1_pro" ? 250000
    : modelSelection === "gemini_3_1_flash_lite" ? 250000
    : modelSelection === "local_gemma_fast" ? 120000
    : modelSelection === "local_gemma_max" ? 120000
    : modelSelection === "local_qwen_uncensored" ? 120000
    : modelSelection === "haiku" ? 200000
    : 250000;

  return {
    models: {
      providers,
    },
    agents: {
      defaults: {
        model: {
          primary: primaryModelId,
          fallbacks: useSmartRouting ? [MODEL_IDS["sonnet"]]
            : useGptSmartRouting ? [GPT_55_PROVIDER_MODEL_ID]
            : useOpenMagiRouter ? ["anthropic/claude-sonnet-4-6"]
            : [],
        },
        thinkingDefault: "off",
        maxConcurrent: 2,
        contextTokens,
        bootstrapMaxChars: 50000,
        bootstrapTotalMaxChars: 100000,
        contextPruning: {
          mode: "cache-ttl",
          ttl: "5m",
          keepLastAssistants: 3,
          softTrimRatio: 0.4,
          hardClearRatio: 0.6,
          minPrunableToolChars: 10000,
        },
        compaction: {
          mode: "safeguard",
          reserveTokensFloor: 40000,
          memoryFlush: {
            enabled: true,
            softThresholdTokens: 40000,
            prompt: [
              "Pre-compaction memory flush. The session transcript is about to be compacted — older messages will be removed.",
              "Save important context NOW using these steps:",
              "",
              "Step 1 — WORKING.md: Update with current task list and their status (keep under 100 lines).",
              "Step 2 — SCRATCHPAD.md: Update active working state — pending decisions, in-progress notes, cross-task lessons (keep under 150 lines).",
              "Step 3 — MEMORY.md: APPEND ONLY new user preferences, key decisions, or important facts learned this session. Do NOT rewrite existing entries. Keep total under 50 lines.",
              "Step 4 — memory/YYYY-MM-DD.md (MOST CRITICAL — DO NOT SKIP): APPEND a detailed structured log of this session. For each topic discussed, include: what the user requested, what analysis/actions you performed, specific decisions made with rationale, user feedback/reactions, concrete values and data points, files created or modified, and references to knowledge/ files. Use ## headings per topic. This is the compaction tree's source material — include enough detail that the daily compaction node can extract keywords, decisions, and patterns. Create memory/ directory if needed.",
              "Step 5 — knowledge/*.md: If the user shared documents (resumes, reports, configs, etc.), ensure a reference entry exists in knowledge/ with the workspace file path and a brief description.",
              "Step 6 — AGENT-REGISTRY.md: If specialists exist, verify the registry is accurate — all active agents listed with correct session IDs, purposes, and lastUsed dates. Do NOT skip this step.",
              "",
              "Rules:",
              "- Layer 1 files (MEMORY.md, SCRATCHPAD.md, WORKING.md) are in the system prompt — keep them concise.",
              "- Raw details and conversation logs go to memory/ and knowledge/ (Layer 2).",
              "- Do NOT duplicate information across layers.",
              "- Step 4 (daily log) is NON-NEGOTIABLE. If you only do one thing, do Step 4. Without it, your memory is permanently lost.",
              "- If nothing meaningful to save, reply with __SILENT__.",
            ].join("\n"),
          },
          model: "anthropic/claude-sonnet-4-6",
        },
        ...(Object.keys(models).length > 0 ? { models } : {}),
        heartbeat: {
          every: "55m",
          ...(useSmartRouting ? { model: "anthropic/claude-sonnet-4-6" } :
              useGptSmartRouting ? { model: GPT_55_PROVIDER_MODEL_ID } :
              useOpenMagiRouter ? { model: `${input.routerType === "big_dic" ? "big-dic-router" : "clawy-smart-router"}/auto` } : {}),
        },
        subagents: {
          maxSpawnDepth: 2,
          maxChildrenPerAgent: 3,
        },
      },
    },
    tools: {
      profile: "full" as const,
      deny: [
        ...(!braveApiKeyIsUserOwned ? ["web_search"] : []),
      ],
      ...(braveApiKey && braveApiKeyIsUserOwned ? {
        web: {
          search: {
            provider: "brave",
            apiKey: braveApiKey,
          },
        },
      } : {}),
    },
    plugins: {
      allow: ["clawy-agent-os"],
      load: {
        paths: ["/home/ocuser/.openclaw/extensions"],
      },
      entries: {
        ...(botToken ? { telegram: { enabled: true } } : {}),
        "clawy-agent-os": {
          enabled: true,
          hooks: {
            allowPromptInjection: true,
          },
        },
      },
    },
    commands: {
      native: "auto",
      nativeSkills: false,
    },
    channels: {
      ...(botToken ? {
        telegram: {
          dmPolicy: "pairing",
          botToken,
          groupPolicy: "allowlist",
          streaming: "partial",
          apiRoot: "https://telegram-gate.clawy-system.svc:3443",
        },
      } : {}),
    },
    browser: {
      headless: true,
      noSandbox: true,
      defaultProfile: "openclaw",
    },
    gateway: {
      mode: "local",
      auth: {
        mode: "token",
        token: gatewayToken,
      },
      controlUi: {
        enabled: false,
      },
    },
    session: {
      reset: {
        mode: "idle",
        idleMinutes: 1440,
      },
    },
    _envKeys: Object.fromEntries(
      Object.entries({
        XI_API_KEY: elevenlabsApiKey,
        GROQ_API_KEY: groqApiKey,
        DEEPL_API_KEY: deeplApiKey,
        ALPHA_VANTAGE_API_KEY: alphaVantageApiKey,
        FINNHUB_API_TOKEN: finnhubApiKey,
        FMP_API_KEY: fmpApiKey,
        FRED_API_KEY: fredApiKey,
        DART_API_KEY: dartApiKey,
        FIRECRAWL_API_KEY: firecrawlApiKey,
        SEMANTIC_SCHOLAR_API_KEY: semanticScholarApiKey,
        SERPER_API_KEY: serperApiKey,
        GH_TOKEN: githubToken,
        GOOGLE_API_KEY: googleApiKey,
        PRIVY_APP_ID: privyAppId,
        PRIVY_APP_SECRET: privyAppSecret,
        PRIVY_WALLET_ID: privyWalletId,
        GATEWAY_TOKEN: gatewayToken,
        CHAT_PROXY_URL: chatProxyUrl,
        // Integration awareness — bot knows which integrations are available
        ...(activeIntegrations && activeIntegrations.length > 0
          ? { ACTIVE_INTEGRATIONS: activeIntegrations.join(",") }
          : {}),
      }).filter(([, v]) => v !== undefined)
    ),
  };
}
