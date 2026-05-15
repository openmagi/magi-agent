"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import dynamic from "next/dynamic";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { GlassCard } from "@/components/ui/glass-card";
import { Input, Textarea } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
// CustomizeTab moved to standalone /dashboard/[botId]/customize page
import { BotDeleteModal } from "@/components/dashboard/bot-delete-modal";
import { BotResetModal } from "@/components/dashboard/bot-reset-modal";
import { useMessages } from "@/lib/i18n";
import Link from "next/link";
import { trackSettingsSave } from "@/lib/analytics";
import { normalizeModelSelectionForSettings } from "@/lib/models/model-options";
import { LOCAL_LLM_MODEL_OPTIONS, isLocalLlmEnabledPlan } from "@/lib/models/local-llm";
import {
  ROUTER_PICKER_OPTIONS,
  applyRouterPickerMode,
  getRouterPickerMode,
  type RouterPickerMode,
} from "@/lib/models/router-tier";
import type { ModelSelection } from "@/lib/supabase/types";

const DeleteAccountModal = dynamic(
  () => import("@/components/dashboard/delete-account-modal").then((m) => m.DeleteAccountModal),
  { ssr: false }
);
import type { BotSettingsData } from "@/types/entities";

const BASE_MODEL_OPTIONS = [
  { value: "haiku", label: "Claude Haiku 4.5" },
  { value: "sonnet", label: "Claude Sonnet 4.5" },
  { value: "opus", label: "Claude Opus 4.6" },
  { value: "gpt_5_nano", label: "GPT-5.4 Nano" },
  { value: "gpt_5_mini", label: "GPT-5.4 Mini" },
  { value: "gpt_5_5", label: "GPT-5.5" },
  { value: "gpt_5_5_pro", label: "GPT-5.5 Pro" },
  { value: "codex", label: "Codex (OAuth Required)" },
  { value: "kimi_k2_5", label: "Kimi K2.6 (Fireworks AI)" },
  { value: "minimax_m2_7", label: "MiniMax M2.7 (Fireworks AI)" },
  { value: "gemini_3_1_flash_lite", label: "Gemini 3.1 Flash Lite (Google)" },
  { value: "gemini_3_1_pro", label: "Gemini 3.1 Pro (Google)" },
] as const;

const STATIC_LANGUAGE_OPTIONS = [
  { value: "en", label: "English" },
  { value: "ko", label: "한국어" },
  { value: "ja", label: "日本語" },
  { value: "zh", label: "中文" },
  { value: "es", label: "Español" },
];

/* ─── Custom Dropdown (replaces native <select>) ─── */

interface SettingsDropdownOption {
  value: string;
  label: string;
}

function SettingsDropdown({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: SettingsDropdownOption[];
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const selected = options.find((o) => o.value === value);

  return (
    <div ref={ref} className="relative block">
      {label && (
        <span className="block text-sm font-medium text-secondary mb-1.5">{label}</span>
      )}
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="w-full bg-white border border-black/10 rounded-xl px-4 py-3 text-left text-foreground focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors duration-200 flex items-center justify-between"
      >
        <span className="truncate">{selected?.label ?? value}</span>
        <svg
          className={`w-4 h-4 text-gray-400 shrink-0 ml-2 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="absolute z-50 mt-1.5 w-full bg-white/95 backdrop-blur-xl rounded-xl shadow-lg border border-black/[0.08] py-1 max-h-60 overflow-y-auto">
          {options.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
              }}
              className={`w-full text-left px-4 py-2.5 text-sm transition-colors duration-150 flex items-center gap-2.5 ${
                opt.value === value
                  ? "text-foreground font-medium bg-black/[0.03]"
                  : "text-secondary hover:bg-black/[0.04] hover:text-foreground"
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  opt.value === value ? "bg-primary" : "bg-transparent"
                }`}
              />
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

interface SettingsFormProps {
  bot: BotSettingsData | null;
  subscriptionPlan?: string | null;
}

const REPROVISION_ESTIMATE_MS = 120_000;

/* ─── Collapsible Section ─── */

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      className={`w-4 h-4 text-secondary transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
    >
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
    </svg>
  );
}

/* ─── API Key Categories ─── */

type ApiKeysMessages = ReturnType<typeof useMessages>["apiKeys"];

interface ApiKeyEntry {
  field: string;
  label: string;
  descriptionKey: keyof ApiKeysMessages;
  freeKey: keyof ApiKeysMessages;
  guideKey: keyof ApiKeysMessages;
  signupUrl: string;
  platformProvided?: boolean;
}

interface ApiKeyCategory {
  nameKey: keyof ApiKeysMessages;
  keys: ApiKeyEntry[];
}

const API_KEY_CATEGORIES: ApiKeyCategory[] = [
  {
    nameKey: "categoryWebSearch",
    keys: [
      { field: "brave_api_key", label: "Brave Search", descriptionKey: "braveDescription", freeKey: "braveFree", guideKey: "braveGuide", signupUrl: "https://brave.com/search/api", platformProvided: true },
    ],
  },
  {
    nameKey: "categoryVoice",
    keys: [
      { field: "elevenlabs_api_key", label: "ElevenLabs TTS", descriptionKey: "elevenlabsDescription", freeKey: "elevenlabsFree", guideKey: "elevenlabsGuide", signupUrl: "https://elevenlabs.io" },
      { field: "groq_api_key", label: "Groq STT (Whisper)", descriptionKey: "groqDescription", freeKey: "groqFree", guideKey: "groqGuide", signupUrl: "https://console.groq.com" },
    ],
  },
  {
    nameKey: "categoryTranslation",
    keys: [
      { field: "deepl_api_key", label: "DeepL", descriptionKey: "deeplDescription", freeKey: "deeplFree", guideKey: "deeplGuide", signupUrl: "https://www.deepl.com/pro-api" },
    ],
  },
  {
    nameKey: "categoryFinance",
    keys: [
      { field: "alpha_vantage_api_key", label: "Alpha Vantage", descriptionKey: "alphaVantageDescription", freeKey: "alphaVantageFree", guideKey: "alphaVantageGuide", signupUrl: "https://www.alphavantage.co" },
      { field: "finnhub_api_key", label: "Finnhub", descriptionKey: "finnhubDescription", freeKey: "finnhubFree", guideKey: "finnhubGuide", signupUrl: "https://finnhub.io" },
      { field: "fmp_api_key", label: "FMP", descriptionKey: "fmpDescription", freeKey: "fmpFree", guideKey: "fmpGuide", signupUrl: "https://financialmodelingprep.com" },
      { field: "fred_api_key", label: "FRED", descriptionKey: "fredDescription", freeKey: "fredFree", guideKey: "fredGuide", signupUrl: "https://fred.stlouisfed.org" },
      { field: "dart_api_key", label: "DART", descriptionKey: "dartDescription", freeKey: "dartFree", guideKey: "dartGuide", signupUrl: "https://opendart.fss.or.kr" },
    ],
  },
  {
    nameKey: "categoryWebCrawling",
    keys: [
      { field: "firecrawl_api_key", label: "Firecrawl", descriptionKey: "firecrawlDescription", freeKey: "firecrawlFree", guideKey: "firecrawlGuide", signupUrl: "https://www.firecrawl.dev" },
    ],
  },
  {
    nameKey: "categoryAcademicResearch",
    keys: [
      { field: "semantic_scholar_api_key", label: "Semantic Scholar", descriptionKey: "semanticScholarDescription", freeKey: "semanticScholarFree", guideKey: "semanticScholarGuide", signupUrl: "https://www.semanticscholar.org/product/api" },
      { field: "serper_api_key", label: "Serper (Google Scholar)", descriptionKey: "serperDescription", freeKey: "serperFree", guideKey: "serperGuide", signupUrl: "https://serper.dev" },
    ],
  },
  {
    nameKey: "categoryGoogle",
    keys: [
      { field: "google_api_key", label: "Google API Key", descriptionKey: "googleDescription", freeKey: "googleFree", guideKey: "googleGuide", signupUrl: "https://console.cloud.google.com/apis/credentials" },
      { field: "google_ads_developer_token", label: "Google Ads Developer Token", descriptionKey: "googleAdsDescription", freeKey: "googleAdsFree", guideKey: "googleAdsDeveloperTokenGuide", signupUrl: "https://ads.google.com/aw/apicenter" },
    ],
  },
  {
    nameKey: "categoryDeveloper",
    keys: [
      { field: "github_token", label: "GitHub", descriptionKey: "githubDescription", freeKey: "githubFree", guideKey: "githubGuide", signupUrl: "https://github.com/settings/tokens" },
    ],
  },
];

export function SettingsForm({ bot, subscriptionPlan }: SettingsFormProps) {
  if (!bot) return <SettingsAccountOnly />;
  return <BotSettingsForm bot={bot} subscriptionPlan={subscriptionPlan ?? null} />;
}

function SettingsAccountOnly() {
  const t = useMessages();
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);

  return (
    <div className="space-y-6 max-w-2xl">
      <h1 className="text-xl font-bold text-foreground">{t.settingsPage.title}</h1>
      <GlassCard className="!border-red-500/20">
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-red-400">{t.accountDeletion.dangerZone}</h3>
          <p className="text-xs text-secondary">
            {t.accountDeletion.modalDescription}
          </p>
          <button
            onClick={() => setDeleteModalOpen(true)}
            className="px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 hover:border-red-500/40 cursor-pointer"
          >
            {t.accountDeletion.deleteAccount}
          </button>
        </div>
      </GlassCard>
      <DeleteAccountModal open={deleteModalOpen} onClose={() => setDeleteModalOpen(false)} />
    </div>
  );
}

function BotSettingsForm({
  bot,
  subscriptionPlan,
}: {
  bot: BotSettingsData;
  subscriptionPlan: string | null;
}) {
  const authFetch = useAuthFetch();
  const t = useMessages();
  const isDeleted = bot.status === "deleted";

  const initialModelPickerMode = getRouterPickerMode(bot.model_selection, bot.router_type);
  const initialRouterSelection = applyRouterPickerMode(
    initialModelPickerMode,
    normalizeModelSelectionForSettings(bot.model_selection) as ModelSelection,
  );
  const [modelSelection, setModelSelection] = useState(initialRouterSelection.modelSelection);
  const [routerType, setRouterType] = useState(initialRouterSelection.routerType);
  const [modelPickerMode, setModelPickerMode] = useState<RouterPickerMode>(initialModelPickerMode);
  const [language, setLanguage] = useState(bot.language ?? "auto");
  const [apiKey, setApiKey] = useState("");
  const [fireworksApiKey, setFireworksApiKey] = useState("");
  const [customBaseUrl, setCustomBaseUrl] = useState("");
  const [openaiApiKey, setOpenaiApiKey] = useState("");
  const [codexAccessToken, setCodexAccessToken] = useState("");
  const [codexRefreshToken, setCodexRefreshToken] = useState("");
  const [geminiApiKey, setGeminiApiKey] = useState("");
  const [showAdvancedByok, setShowAdvancedByok] = useState(false);
  const isFireworks = modelSelection === "kimi_k2_5" || modelSelection === "minimax_m2_7";
  const isOpenAI = modelSelection === "gpt_5_nano" || modelSelection === "gpt_5_mini" || modelSelection === "gpt_5_5" || modelSelection === "gpt_5_5_pro" || modelSelection === "gpt_smart_routing";
  const isGoogle = modelSelection === "gemini_3_1_flash_lite" || modelSelection === "gemini_2_5_flash" || modelSelection === "gemini_3_1_pro";
  const isCodex = modelSelection === "codex";
  const canUseCodexOAuth = isCodex || modelSelection === "gpt_5_5";
  const gpt55HasCodexOAuth = modelSelection === "gpt_5_5" && (bot.has_codex_token || !!codexAccessToken.trim());
  const modelOptions = isLocalLlmEnabledPlan(subscriptionPlan)
    ? [
        ...BASE_MODEL_OPTIONS,
        ...LOCAL_LLM_MODEL_OPTIONS.map((model) => ({
          value: model.value,
          label: model.label,
        })),
      ]
    : BASE_MODEL_OPTIONS;
  const modelPickerDescription = ROUTER_PICKER_OPTIONS.find((opt) => opt.value === modelPickerMode)?.description ?? "";
  const [saving, setSaving] = useState(false);
  const [reprovisioning, setReprovisioning] = useState(false);
  const [progressPct, setProgressPct] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [accountDeleteModalOpen, setAccountDeleteModalOpen] = useState(false);
  const [botDeleteModalOpen, setBotDeleteModalOpen] = useState(false);
  const [resetModalOpen, setResetModalOpen] = useState(false);
  const provisionStartRef = useRef<number | null>(null);
  const progressRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Personality
  const initialPersonalityMode = bot.bot_purpose && !bot.purpose_preset ? "custom" : "preset";
  const [personalityMode, setPersonalityMode] = useState<"preset" | "custom">(initialPersonalityMode);
  const [selectedPreset, setSelectedPreset] = useState<string | null>(bot.purpose_preset);
  const [customStyle, setCustomStyle] = useState(
    initialPersonalityMode === "custom" ? (bot.bot_purpose ?? "") : ""
  );

  // Agent Registry
  const [registryPreviewOpen, setRegistryPreviewOpen] = useState(false);
  const [registryOpen, setRegistryOpen] = useState(false);
  const [registryEnabled, setRegistryEnabled] = useState(!!bot.agent_skill_md);
  const [registryLoading, setRegistryLoading] = useState(false);
  const [registryCopied, setRegistryCopied] = useState(false);
  const [registryAgentId, setRegistryAgentId] = useState<string | null>(bot.registry_agent_id);
  const [registryRegistering, setRegistryRegistering] = useState(false);
  const [skillMdUrl, setSkillMdUrl] = useState<string | null>(null);

  // Tab state (customize moved to standalone page)
  const activeTab = "settings" as const;

  // Collapsible sections
  const [personalityOpen, setPersonalityOpen] = useState(false);
  const [apiKeysOpen, setApiKeysOpen] = useState(false);
  const [expandedApiCategories, setExpandedApiCategories] = useState<Set<string>>(new Set());

  // External API keys
  const [extKeyValues, setExtKeyValues] = useState<Record<string, string>>({});
  const [expandedGuides, setExpandedGuides] = useState<Set<string>>(new Set());
  const setExtKeyValue = (field: string, value: string) => {
    setExtKeyValues((prev) => ({ ...prev, [field]: value }));
  };

  const toggleApiCategory = (name: string) => {
    setExpandedApiCategories((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const PERSONALITY_PRESETS: { id: string; emoji: string; name: string }[] = [
    { id: "professional", emoji: "💼", name: "Professional" },
    { id: "friendly", emoji: "😊", name: "Friendly" },
    { id: "casual", emoji: "🤙", name: "Casual" },
    { id: "teacher", emoji: "📚", name: "Teacher" },
    { id: "analytical", emoji: "🔬", name: "Analytical" },
  ];

  async function handleRegistryToggle() {
    if (isDeleted) return;
    setRegistryLoading(true);
    setError(null);
    try {
      if (registryEnabled) {
        const res = await authFetch(`/api/bots/${bot.id}/registry/skill`, { method: "DELETE" });
        if (!res.ok) throw new Error((await res.json()).error || "Failed to disable");
        setRegistryEnabled(false);
        setSkillMdUrl(null);
        setRegistryAgentId(null);
      } else {
        const res = await authFetch(`/api/bots/${bot.id}/registry/skill`, { method: "POST" });
        if (!res.ok) throw new Error((await res.json()).error || "Failed to enable");
        const data = await res.json();
        setRegistryEnabled(true);
        setSkillMdUrl(data.skillMdUrl);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t.errors.unexpected);
    } finally {
      setRegistryLoading(false);
    }
  }

  async function handleRegistryRegister() {
    if (isDeleted) return;
    setRegistryRegistering(true);
    setError(null);
    try {
      const res = await authFetch(`/api/bots/${bot.id}/registry`, { method: "POST" });
      if (!res.ok) throw new Error((await res.json()).error || "Failed to register");
      const data = await res.json();
      setRegistryAgentId(data.agentId);
    } catch (err) {
      setError(err instanceof Error ? err.message : t.errors.unexpected);
    } finally {
      setRegistryRegistering(false);
    }
  }

  // Poll status during reprovisioning
  const pollStatus = useCallback(async () => {
    try {
      const res = await authFetch(`/api/bots/${bot.id}/status`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.status === "active") {
        setReprovisioning(false);
        setProgressPct(100);
        setSuccess(t.settingsPage.reprovisioningComplete);
      }
    } catch {
      // retry on next poll
    }
  }, [authFetch, bot.id, t.settingsPage.reprovisioningComplete]);

  useEffect(() => {
    if (!reprovisioning) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      if (progressRef.current) { clearInterval(progressRef.current); progressRef.current = null; }
      return;
    }
    if (!provisionStartRef.current) provisionStartRef.current = Date.now();
    const tick = () => {
      const start = provisionStartRef.current;
      if (!start) return;
      setProgressPct(Math.min(((Date.now() - start) / REPROVISION_ESTIMATE_MS) * 100, 99));
    };
    tick();
    progressRef.current = setInterval(tick, 1_000);
    pollRef.current = setInterval(pollStatus, 5_000);
    return () => {
      if (progressRef.current) { clearInterval(progressRef.current); progressRef.current = null; }
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [reprovisioning, pollStatus]);

  function handleResetQueued() {
    setError(null);
    setSuccess(t.settingsPage.resetBotQueued);
    setReprovisioning(true);
    setProgressPct(0);
    provisionStartRef.current = Date.now();
  }

  async function handleSave() {
    if (isDeleted) {
      setError(t.settingsPage.deletedBotReadOnlyDescription);
      return;
    }

    trackSettingsSave(modelSelection);
    setSaving(true);
    setError(null);
    setSuccess(null);
    setReprovisioning(false);

    try {
      const body: Record<string, string | null | undefined> = {
        model_selection: modelSelection,
        router_type: routerType,
        language,
      };

      if (personalityMode === "preset" && selectedPreset) {
        body.purpose_preset = selectedPreset;
        body.bot_purpose = "";
      } else {
        body.purpose_preset = "";
        body.bot_purpose = customStyle;
      }

      if (bot.api_key_mode === "byok" && apiKey.trim()) {
        body.anthropic_api_key = apiKey.trim();
      }
      if (bot.api_key_mode === "byok" && fireworksApiKey.trim()) {
        body.fireworks_api_key = fireworksApiKey.trim();
      }
      if (bot.api_key_mode === "byok" && openaiApiKey.trim()) {
        body.openai_api_key = openaiApiKey.trim();
      }
      if (bot.api_key_mode === "byok" && geminiApiKey.trim()) {
        body.gemini_api_key = geminiApiKey.trim();
      }
      if (codexAccessToken.trim()) {
        body.codex_access_token = codexAccessToken.trim();
      }
      if (codexRefreshToken.trim()) {
        body.codex_refresh_token = codexRefreshToken.trim();
      }
      if (bot.api_key_mode === "byok") {
        body.custom_base_url = customBaseUrl.trim();
      }

      // External service API keys
      for (const [field, value] of Object.entries(extKeyValues)) {
        if (value.trim()) body[field] = value.trim();
      }

      const res = await authFetch(`/api/bots/${bot.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || "Failed to save settings");
      }

      const data = await res.json();

      if (data.reprovisioning) {
        setReprovisioning(true);
        setProgressPct(0);
        provisionStartRef.current = Date.now();
        setSuccess(t.settingsPage.reprovisioningStarted);
      } else {
        setSuccess(t.settingsPage.saveSuccess);
      }

      setApiKey("");
      setFireworksApiKey("");
      setOpenaiApiKey("");
      setCodexAccessToken("");
      setCodexRefreshToken("");
      setCustomBaseUrl("");
      setExtKeyValues({});
    } catch (err) {
      setError(err instanceof Error ? err.message : t.errors.unexpected);
    } finally {
      setSaving(false);
    }
  }

  function handleModelPickerModeChange(mode: RouterPickerMode) {
    const next = applyRouterPickerMode(mode, modelSelection as ModelSelection);
    setModelPickerMode(mode);
    setModelSelection(next.modelSelection);
    setRouterType(next.routerType);
  }

  function handleAdvancedModelChange(nextModel: ModelSelection) {
    setModelPickerMode("advanced");
    setModelSelection(nextModel);
    setRouterType("standard");
  }

  function handleBotDeleted() {
    setSuccess(t.settingsPage.deleteBotDeleted);
    window.location.assign(`/dashboard/${bot.id}/usage`);
  }

  const showCustom = personalityMode === "custom";

  return (
    <div className="space-y-4 max-w-2xl">
      {error && (
        <div className="glass border border-red-500/20 text-red-400 px-4 py-3 rounded-xl text-sm">
          {error}
        </div>
      )}
      {success && (
        <div className="glass border border-emerald-500/20 text-emerald-600 px-4 py-3 rounded-xl text-sm">
          {success}
        </div>
      )}

      {reprovisioning && (
        <GlassCard>
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <div className="h-4 w-4 rounded-full border-2 border-amber-400 border-t-transparent animate-spin shrink-0" />
              <div>
                <p className="font-medium text-amber-300">{t.settingsPage.reprovisioning}</p>
                <p className="text-sm text-secondary">{t.settingsPage.reprovisioningHint}</p>
              </div>
            </div>
            <div className="w-full h-1.5 bg-black/5 rounded-full overflow-hidden">
              <div
                className="h-full bg-amber-400/60 rounded-full transition-all duration-1000 ease-linear"
                style={{ width: `${Math.round(progressPct)}%` }}
              />
            </div>
          </div>
        </GlassCard>
      )}

      {isDeleted && (
        <GlassCard className="!border-amber-500/20">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="space-y-1">
              <p className="text-sm font-semibold text-amber-500">{t.settingsPage.deletedBotReadOnly}</p>
              <p className="text-xs text-secondary">{t.settingsPage.deletedBotReadOnlyDescription}</p>
            </div>
            <Link href={`/dashboard/${bot.id}/usage`}>
              <Button variant="ghost" size="sm">
                {t.settingsPage.viewDeletedBotUsage}
              </Button>
            </Link>
          </div>
        </GlassCard>
      )}

      {/* ─── Tab Navigation ─── */}
      <>

      {/* ─── Model + API Key Mode (always visible, compact) ─── */}
      <GlassCard className={isDeleted ? "pointer-events-none opacity-60" : undefined}>
        <div className="space-y-4">
          <SettingsDropdown
            label={t.settingsPage.modelLabel}
            value={modelPickerMode}
            onChange={(v) => handleModelPickerModeChange(v as RouterPickerMode)}
            options={ROUTER_PICKER_OPTIONS.map((opt) => ({ value: opt.value, label: opt.label }))}
          />
          <p className="text-xs text-muted mt-1">{modelPickerDescription || t.settingsPage.modelHint}</p>

          {modelPickerMode === "advanced" && (
            <div className="mt-2">
              <SettingsDropdown
                label="Custom model"
                value={modelSelection}
                onChange={(v) => handleAdvancedModelChange(v as ModelSelection)}
                options={[
                  ...(!modelOptions.some((opt) => opt.value === modelSelection)
                    ? [{ value: modelSelection, label: modelSelection }]
                    : []),
                  ...modelOptions.map((opt) => ({ value: opt.value, label: opt.label })),
                ]}
              />
              <p className="text-xs text-muted mt-1">Manual model selection for specialized setups.</p>
            </div>
          )}

          <SettingsDropdown
            label={t.settingsPage.languageLabel}
            value={language}
            onChange={setLanguage}
            options={[
              { value: "auto", label: t.settingsPage.languageAuto },
              ...STATIC_LANGUAGE_OPTIONS,
            ]}
          />

          <div className="border-t border-gray-200 pt-4">
            <p className="block text-sm font-medium text-secondary mb-2">{t.settingsPage.apiKeyModeLabel}</p>
            <div className="flex items-center justify-between">
              <p className="font-medium text-foreground text-sm">
                {bot.api_key_mode === "byok" ? t.settingsPage.byokLabel : t.settingsPage.creditsLabel}
              </p>
              <Link href="/dashboard/billing">
                <Button variant="ghost" size="sm">
                  {t.settingsPage.changePlan}
                </Button>
              </Link>
            </div>

            {bot.api_key_mode === "byok" && (
              <div className="mt-3 space-y-3">
                {/* Warn if switching to a model type without a stored key */}
                {((isFireworks && !bot.has_fireworks_key && !fireworksApiKey.trim()) ||
                  (isOpenAI && !gpt55HasCodexOAuth && !bot.has_openai_key && !openaiApiKey.trim()) ||
                  (isGoogle && !bot.has_gemini_key && !geminiApiKey.trim()) ||
                  (!isFireworks && !isOpenAI && !isGoogle && !isCodex && modelSelection !== "smart_routing" && !bot.has_anthropic_key && !apiKey.trim())) && (
                  <div className="flex items-center gap-2 p-2.5 rounded-lg border border-amber-500/20 bg-amber-500/5">
                    <svg className="w-4 h-4 text-amber-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.999L13.732 4.001c-.77-1.333-2.694-1.333-3.464 0L3.34 16.001c-.77 1.332.192 2.999 1.732 2.999z" />
                    </svg>
                    <p className="text-xs text-amber-300">{t.settingsPage.apiKeyRequiredHint}</p>
                  </div>
                )}
                {/* Primary key — required for current model */}
                {isFireworks ? (
                  <div>
                    <Input
                      label={t.settingsPage.fireworksApiKeyLabel}
                      type="password"
                      value={fireworksApiKey}
                      onChange={(e) => setFireworksApiKey(e.target.value)}
                      placeholder="fw-..."
                    />
                    <p className="text-xs text-muted mt-1.5">{t.settingsPage.fireworksApiKeyHint}</p>
                  </div>
                ) : isOpenAI ? (
                  <div>
                    <Input
                      label={t.settingsPage.openaiApiKeyLabel}
                      type="password"
                      value={openaiApiKey}
                      onChange={(e) => setOpenaiApiKey(e.target.value)}
                      placeholder="sk-..."
                    />
                    <p className="text-xs text-muted mt-1.5">{t.settingsPage.openaiApiKeyHint}</p>
                  </div>
                ) : isGoogle ? (
                  <div>
                    <Input
                      label={t.settingsPage.geminiApiKeyLabel}
                      type="password"
                      value={geminiApiKey}
                      onChange={(e) => setGeminiApiKey(e.target.value)}
                      placeholder="AIza..."
                    />
                    <p className="text-xs text-muted mt-1.5">{t.settingsPage.geminiApiKeyHint}</p>
                  </div>
                ) : (
                  <div>
                    <Input
                      label={t.settingsPage.apiKeyLabel}
                      type="password"
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      placeholder="sk-ant-..."
                    />
                    <p className="text-xs text-muted mt-1.5">{t.settingsPage.apiKeyHint}</p>
                  </div>
                )}

                {/* Advanced: optional secondary key + custom base URL */}
                <button
                  type="button"
                  onClick={() => setShowAdvancedByok(!showAdvancedByok)}
                  className="flex items-center gap-1.5 text-xs text-secondary hover:text-foreground transition-colors cursor-pointer"
                >
                  <svg
                    className={`w-3 h-3 transition-transform duration-200 ${showAdvancedByok ? "rotate-90" : ""}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                  {t.onboarding.advanced}
                </button>
                {showAdvancedByok && (
                  <div className="space-y-3">
                    {isFireworks ? (
                      <div>
                        <Input
                          label={t.settingsPage.apiKeyLabel}
                          type="password"
                          value={apiKey}
                          onChange={(e) => setApiKey(e.target.value)}
                          placeholder="sk-ant-..."
                        />
                        <p className="text-xs text-muted mt-1.5">{t.settingsPage.apiKeyHint}</p>
                      </div>
                    ) : (
                      <div>
                        <Input
                          label={t.settingsPage.fireworksApiKeyLabel}
                          type="password"
                          value={fireworksApiKey}
                          onChange={(e) => setFireworksApiKey(e.target.value)}
                          placeholder="fw-..."
                        />
                        <p className="text-xs text-muted mt-1.5">{t.settingsPage.fireworksApiKeyHint}</p>
                      </div>
                    )}
                    <div>
                      <Input
                        label={t.settingsPage.customBaseUrlLabel}
                        type="url"
                        value={customBaseUrl}
                        onChange={(e) => setCustomBaseUrl(e.target.value)}
                        placeholder="https://openrouter.ai/api/v1"
                      />
                      <p className="text-xs text-muted mt-1.5">{t.settingsPage.customBaseUrlHint}</p>
                    </div>
                  </div>
                )}
              </div>
            )}
          {canUseCodexOAuth && (
              <div className="mt-3 space-y-3">
                {/* Saved token indicator */}
                {bot.has_codex_token && (
                  <div className="flex items-center gap-2 p-2.5 rounded-lg border border-emerald-500/20 bg-emerald-500/5">
                    <svg className="w-4 h-4 text-emerald-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                    </svg>
                    <p className="text-xs text-emerald-300">{t.settingsPage.codexTokenSaved}</p>
                  </div>
                )}
                {/* Step-by-step guide (only show when no token saved) */}
                {!bot.has_codex_token && (
                  <div className="p-3 rounded-lg border border-blue-500/20 bg-blue-500/5 space-y-2">
                    <p className="text-xs font-medium text-blue-300">{t.settingsPage.codexGuideTitle}</p>
                    <ol className="text-[11px] text-blue-300/80 space-y-1.5 list-decimal list-inside">
                      <li>{t.settingsPage.codexGuideStep1}</li>
                      <li>
                        {t.settingsPage.codexGuideStep2}
                        <code className="ml-1 px-1.5 py-0.5 rounded bg-gray-100 text-blue-200 text-[10px] font-mono select-all break-all">
                          openclaw models auth login --provider openai-codex
                        </code>
                      </li>
                      <li>{t.settingsPage.codexGuideStep3}</li>
                      <li>{t.settingsPage.codexGuideStep4}</li>
                    </ol>
                    <p className="text-[10px] text-blue-300/60">{t.settingsPage.codexGuideNote}</p>
                  </div>
                )}
                <div>
                  <Input
                    label={t.settingsPage.codexAccessTokenLabel}
                    type="password"
                    value={codexAccessToken}
                    onChange={(e) => setCodexAccessToken(e.target.value)}
                    placeholder={bot.has_codex_token ? t.settingsPage.codexTokenKeepExisting : "eyJ..."}
                  />
                </div>
                <div>
                  <Input
                    label={t.settingsPage.codexRefreshTokenLabel}
                    type="password"
                    value={codexRefreshToken}
                    onChange={(e) => setCodexRefreshToken(e.target.value)}
                    placeholder={bot.has_codex_token ? t.settingsPage.codexTokenKeepExisting : "rt_..."}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </GlassCard>

      {/* ─── Personality (collapsible) ─── */}
      <GlassCard className={`!p-0 overflow-hidden ${isDeleted ? "pointer-events-none opacity-60" : ""}`}>
        <button
          onClick={() => setPersonalityOpen(!personalityOpen)}
          className="w-full flex items-center justify-between p-5 cursor-pointer hover:bg-gray-50 transition-colors"
        >
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">Personality</span>
            {!personalityOpen && (selectedPreset || customStyle) && (
              <span className="text-xs text-secondary truncate max-w-[200px]">
                {selectedPreset
                  ? PERSONALITY_PRESETS.find((p) => p.id === selectedPreset)?.name
                  : customStyle.slice(0, 40) + (customStyle.length > 40 ? "..." : "")}
              </span>
            )}
          </div>
          <ChevronIcon expanded={personalityOpen} />
        </button>

        {personalityOpen && (
          <div className="border-t border-gray-200 px-5 pb-5">
            <div className="flex flex-wrap gap-2 pt-4">
              {PERSONALITY_PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  onClick={() => { setPersonalityMode("preset"); setSelectedPreset(preset.id); }}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full border text-sm transition-all duration-200 cursor-pointer ${
                    personalityMode === "preset" && selectedPreset === preset.id
                      ? "border-primary/40 bg-primary/5 text-foreground"
                      : showCustom
                        ? "border-gray-100 bg-gray-50 text-secondary opacity-50"
                        : "border-gray-100 bg-gray-50 text-secondary hover:border-gray-300"
                  }`}
                >
                  <span>{preset.emoji}</span>
                  <span>{preset.name}</span>
                </button>
              ))}
            </div>

            <div className="flex items-center gap-3 my-3">
              <div className="flex-1 border-t border-gray-200" />
              <span className="text-xs text-secondary/60 uppercase tracking-wider">{t.onboarding.orCustom}</span>
              <div className="flex-1 border-t border-gray-200" />
            </div>

            {showCustom ? (
              <Textarea
                placeholder="Describe the speaking style for your agent..."
                value={customStyle}
                onChange={(e) => setCustomStyle(e.target.value)}
                rows={3}
              />
            ) : (
              <button
                onClick={() => { setPersonalityMode("custom"); setSelectedPreset(null); }}
                className="w-full cursor-pointer"
              >
                <div className="p-3 rounded-xl border border-gray-100 bg-gray-50 hover:border-gray-300 transition-all">
                  <span className="text-sm text-secondary">Describe the speaking style for your agent...</span>
                </div>
              </button>
            )}
          </div>
        )}
      </GlassCard>

      {/* ─── API Keys (collapsible) ─── */}
      <GlassCard className={`!p-0 overflow-hidden ${isDeleted ? "pointer-events-none opacity-60" : ""}`}>
        <button
          onClick={() => setApiKeysOpen(!apiKeysOpen)}
          className="w-full flex items-center justify-between p-5 cursor-pointer hover:bg-gray-50 transition-colors"
        >
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">{t.apiKeys.title}</span>
            <span className="text-xs text-secondary">{t.apiKeys.subtitle}</span>
          </div>
          <ChevronIcon expanded={apiKeysOpen} />
        </button>

        {apiKeysOpen && (
          <div className="border-t border-gray-200">
            {API_KEY_CATEGORIES.map((category, catIdx) => {
              const categoryName = t.apiKeys[category.nameKey];
              const isExpanded = expandedApiCategories.has(category.nameKey);
              const isLast = catIdx === API_KEY_CATEGORIES.length - 1;

              return (
                <div key={category.nameKey} className={!isLast ? "border-b border-gray-100" : ""}>
                  <button
                    onClick={() => toggleApiCategory(category.nameKey)}
                    className="w-full flex items-center justify-between px-5 py-3.5 cursor-pointer hover:bg-gray-50 transition-colors"
                  >
                    <span className="text-sm text-foreground">{categoryName}</span>
                    <ChevronIcon expanded={isExpanded} />
                  </button>

                  {isExpanded && (
                    <div className="px-5 pb-4 space-y-4">
                      {category.keys.map((entry) => {
                        const hasUserKey = !!(extKeyValues[entry.field]?.trim());
                        return (
                        <div key={entry.field}>
                          <div className="flex items-center gap-2 mb-1 flex-wrap">
                            <span className="font-medium text-sm text-foreground">{entry.label}</span>
                            {entry.platformProvided && !hasUserKey && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20">
                                {t.apiKeys.platformProvided}
                              </span>
                            )}
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                              {t.apiKeys.freeTier}: {t.apiKeys[entry.freeKey]}
                            </span>
                          </div>
                          <p className="text-xs text-secondary mb-1.5">{t.apiKeys[entry.descriptionKey]}</p>
                          {entry.platformProvided && !hasUserKey && (
                            <p className="text-xs text-blue-400/80 mb-1.5">{t.apiKeys.platformProvidedHint}</p>
                          )}
                          {entry.field === "brave_api_key" && bot.api_key_mode === "byok" && !hasUserKey && (
                            <p className="text-xs text-emerald-600/80 mb-1.5">{t.apiKeys.braveByokQuota}</p>
                          )}
                          <button
                            onClick={() => setExpandedGuides((prev) => {
                              const next = new Set(prev);
                              if (next.has(entry.field)) next.delete(entry.field);
                              else next.add(entry.field);
                              return next;
                            })}
                            className="text-xs text-primary hover:text-primary-light transition-colors inline-flex items-center gap-1 mb-1 cursor-pointer"
                          >
                            {t.apiKeys.howToGet}
                            <svg className={`w-3 h-3 transition-transform ${expandedGuides.has(entry.field) ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
                          </button>
                          {expandedGuides.has(entry.field) && (
                            <div className="text-xs text-gray-500 mb-2 pl-3 border-l-2 border-gray-200 space-y-1 whitespace-pre-line">
                              {t.apiKeys[entry.guideKey]}
                              <a
                                href={entry.signupUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-primary hover:text-primary-light transition-colors block mt-1.5"
                              >
                                {entry.signupUrl} &rarr;
                              </a>
                            </div>
                          )}
                          <Input
                            type="password"
                            value={extKeyValues[entry.field] ?? ""}
                            onChange={(e) => setExtKeyValue(entry.field, e.target.value)}
                            placeholder={entry.platformProvided ? t.apiKeys.platformKeyPlaceholder : t.apiKeys.placeholder}
                          />
                        </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </GlassCard>

      {/* ─── Agent Discovery (collapsible) ─── */}
      <GlassCard className={`!p-0 overflow-hidden ${isDeleted ? "pointer-events-none opacity-60" : ""}`}>
        <button
          onClick={() => setRegistryOpen(!registryOpen)}
          className="w-full flex items-center justify-between p-5 cursor-pointer hover:bg-gray-50 transition-colors"
        >
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">{t.agentRegistry.title}</span>
            {registryEnabled && !registryOpen && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                {t.agentRegistry.liveLabel}
              </span>
            )}
          </div>
          <ChevronIcon expanded={registryOpen} />
        </button>

        {registryOpen && (
          <div className="border-t border-gray-200 px-5 pb-5 pt-4 space-y-4">
            {/* Explanation */}
            <div className="space-y-2.5">
              <p className="text-xs text-secondary leading-relaxed">{t.agentRegistry.description}</p>

              {/* Preview toggle */}
              <div className="rounded-lg border border-gray-100 overflow-hidden">
                <button
                  onClick={() => setRegistryPreviewOpen(!registryPreviewOpen)}
                  className="w-full flex items-center justify-between px-3 py-2 bg-gray-50 hover:bg-gray-100 transition-colors cursor-pointer"
                >
                  <span className="text-[11px] text-secondary">{t.agentRegistry.previewLabel}</span>
                  <ChevronIcon expanded={registryPreviewOpen} />
                </button>
                {registryPreviewOpen && (
                  <div className="px-3 py-2.5 bg-white border-t border-gray-100 space-y-3">
                    <pre className="text-[11px] text-gray-700 leading-relaxed whitespace-pre-wrap font-mono">{
`# ${bot.name}
${bot.bot_purpose || t.agentRegistry.previewDefaultPurpose}

## Endpoints
### POST /v1/chat/${bot.id}/completions
- Auth: Bearer token or SIWA
- Format: OpenAI chat completion compatible
- Streaming: supported

## Identity
- Wallet: ${bot.privy_wallet_address || t.agentRegistry.previewNoWallet}
- Chain: Base (8453)

## Platform
openmagi.ai`
                    }</pre>
                    <div className="pt-2 border-t border-gray-100 space-y-1.5">
                      <p className="text-[10px] text-secondary/60">{t.agentRegistry.previewSecurityNote}</p>
                    </div>
                  </div>
                )}
              </div>

              <a
                href="https://www.erc8004.org"
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-primary hover:text-primary-light transition-colors inline-flex items-center gap-1"
              >
                {t.agentRegistry.learnMore} &rarr;
              </a>
            </div>

            {!registryEnabled ? (
              <div
                onClick={registryLoading ? undefined : handleRegistryToggle}
                className={`p-3.5 rounded-xl border border-gray-200 bg-gray-50 transition-all duration-200 ${
                  registryLoading ? "opacity-50 pointer-events-none" : "hover:border-primary/30 hover:bg-primary/[0.03] cursor-pointer"
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-lg bg-black/5 flex items-center justify-center shrink-0">
                      <svg className="w-4 h-4 text-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5a17.92 17.92 0 01-8.716-2.247m0 0A9 9 0 013 12c0-1.605.42-3.113 1.157-4.418" />
                      </svg>
                    </div>
                    <div>
                      <p className="text-sm font-medium text-foreground">{t.agentRegistry.enableLabel}</p>
                      <p className="text-[11px] text-secondary">{t.agentRegistry.enableHint}</p>
                    </div>
                  </div>
                  {registryLoading ? (
                    <div className="h-4 w-4 rounded-full border-2 border-primary border-t-transparent animate-spin shrink-0" />
                  ) : (
                    <svg className="w-4 h-4 text-secondary shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                  )}
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                {/* Discovery endpoint */}
                <div>
                  <p className="text-xs text-secondary mb-1">{t.agentRegistry.endpointLabel}</p>
                  <div className="flex items-center gap-2">
                    <code className="text-sm text-foreground bg-black/5 px-3 py-1.5 rounded-lg flex-1 truncate">
                      {skillMdUrl || `https://chat.openmagi.ai/.well-known/agents/${bot.id}/SKILL.md`}
                    </code>
                    <button
                      onClick={() => {
                        const url = skillMdUrl || `https://chat.openmagi.ai/.well-known/agents/${bot.id}/SKILL.md`;
                        navigator.clipboard.writeText(url);
                        setRegistryCopied(true);
                        setTimeout(() => setRegistryCopied(false), 2000);
                      }}
                      className="text-xs text-primary hover:text-primary-light transition-colors px-2 py-1.5 cursor-pointer"
                    >
                      {registryCopied ? t.agentRegistry.copied : t.agentRegistry.copy}
                    </button>
                  </div>
                </div>

                {/* On-chain registration */}
                <div className="p-3 rounded-xl border border-gray-100 bg-gray-50">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-xs text-secondary">{t.agentRegistry.onChainLabel}</p>
                      {registryAgentId ? (
                        <div className="flex items-center gap-1.5 mt-1">
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                            {t.agentRegistry.registered}
                          </span>
                          <code className="text-[11px] text-secondary">#{registryAgentId}</code>
                        </div>
                      ) : (
                        <p className="text-[11px] text-secondary/60 mt-0.5">{t.agentRegistry.notRegistered}</p>
                      )}
                    </div>
                    {!registryAgentId && (
                      <button
                        onClick={handleRegistryRegister}
                        disabled={registryRegistering}
                        className="text-xs text-primary hover:text-primary-light transition-colors px-3 py-1.5 rounded-lg border border-primary/20 hover:border-primary/40 cursor-pointer disabled:opacity-40 disabled:pointer-events-none"
                      >
                        {registryRegistering ? t.agentRegistry.registering : t.agentRegistry.registerLabel}
                      </button>
                    )}
                  </div>
                  {!registryAgentId && (
                    <p className="text-[10px] text-secondary/50 mt-1.5">{t.agentRegistry.gasNote}</p>
                  )}
                </div>

                <button
                  onClick={handleRegistryToggle}
                  disabled={registryLoading}
                  className="text-xs text-red-400 hover:text-red-300 transition-colors cursor-pointer"
                >
                  {registryLoading ? t.agentRegistry.disabling : t.agentRegistry.disableLabel}
                </button>
              </div>
            )}
          </div>
        )}
      </GlassCard>

      <Button
        variant="cta"
        size="md"
        onClick={handleSave}
        disabled={saving || isDeleted}
      >
        {saving ? t.settingsPage.saving : t.settingsPage.save}
      </Button>

      {/* ─── Danger Zone ─── */}
      <GlassCard className="!border-red-500/20">
        <div className="space-y-5">
          <h3 className="text-sm font-semibold text-red-400">{t.accountDeletion.dangerZone}</h3>
          {!isDeleted && (
            <>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="space-y-1">
                  <p className="text-sm font-medium text-foreground">{t.settingsPage.resetBot}</p>
                  <p className="text-xs text-secondary">{t.settingsPage.resetBotDescription}</p>
                </div>
                <button
                  type="button"
                  onClick={() => setResetModalOpen(true)}
                  className="shrink-0 px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 hover:border-red-500/40 cursor-pointer"
                >
                  {t.settingsPage.resetBot}
                </button>
              </div>
              <div className="border-t border-red-500/10 pt-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="space-y-1">
                  <p className="text-sm font-medium text-foreground">{t.settingsPage.deleteBot}</p>
                  <p className="text-xs text-secondary">{t.settingsPage.deleteBotDescription}</p>
                </div>
                <button
                  type="button"
                  onClick={() => setBotDeleteModalOpen(true)}
                  className="shrink-0 px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 hover:border-red-500/40 cursor-pointer"
                >
                  {t.settingsPage.deleteBot}
                </button>
              </div>
            </>
          )}
          <div className="border-t border-red-500/10 pt-5 space-y-3">
            <p className="text-xs text-secondary">
              {t.accountDeletion.modalDescription}
            </p>
            <button
              type="button"
              onClick={() => setAccountDeleteModalOpen(true)}
              className="px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 hover:border-red-500/40 cursor-pointer"
            >
              {t.accountDeletion.deleteAccount}
            </button>
          </div>
        </div>
      </GlassCard>

      <BotResetModal
        botId={bot.id}
        open={resetModalOpen}
        onClose={() => setResetModalOpen(false)}
        onQueued={handleResetQueued}
      />
      <BotDeleteModal
        botId={bot.id}
        open={botDeleteModalOpen}
        onClose={() => setBotDeleteModalOpen(false)}
        onDeleted={handleBotDeleted}
      />
      <DeleteAccountModal open={accountDeleteModalOpen} onClose={() => setAccountDeleteModalOpen(false)} />
      </>
    </div>
  );
}
