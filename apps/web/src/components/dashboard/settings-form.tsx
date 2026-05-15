"use client";

import { useState, useEffect, useRef } from "react";
import { useAgentFetch } from "@/lib/local-api";
import { GlassCard } from "@/components/ui/glass-card";
import { Input, Textarea } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";

const PROVIDER_OPTIONS = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
  { value: "openai_compatible", label: "OpenAI-Compatible" },
] as const;

const BASE_MODEL_OPTIONS = [
  { value: "haiku", label: "Claude Haiku 4.5" },
  { value: "sonnet", label: "Claude Sonnet 4.5" },
  { value: "opus", label: "Claude Opus 4.6" },
  { value: "gpt_5_nano", label: "GPT-5.4 Nano" },
  { value: "gpt_5_mini", label: "GPT-5.4 Mini" },
  { value: "gpt_5_5", label: "GPT-5.5" },
  { value: "gpt_5_5_pro", label: "GPT-5.5 Pro" },
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
}

interface ApiKeyCategory {
  nameKey: keyof ApiKeysMessages;
  keys: ApiKeyEntry[];
}

const API_KEY_CATEGORIES: ApiKeyCategory[] = [
  {
    nameKey: "categoryWebSearch",
    keys: [
      { field: "brave_api_key", label: "Brave Search", descriptionKey: "braveDescription", freeKey: "braveFree", guideKey: "braveGuide", signupUrl: "https://brave.com/search/api" },
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

interface SettingsFormProps {
  bot?: null;
}

const PERSONALITY_PRESETS: { id: string; emoji: string; name: string }[] = [
  { id: "professional", emoji: "💼", name: "Professional" },
  { id: "friendly", emoji: "😊", name: "Friendly" },
  { id: "casual", emoji: "🤙", name: "Casual" },
  { id: "teacher", emoji: "📚", name: "Teacher" },
  { id: "analytical", emoji: "🔬", name: "Analytical" },
];

export function SettingsForm(_props: SettingsFormProps) {
  const agentFetch = useAgentFetch();
  const t = useMessages();

  const [provider, setProvider] = useState("anthropic");
  const [modelSelection, setModelSelection] = useState("sonnet");
  const [language, setLanguage] = useState("auto");
  const [apiKey, setApiKey] = useState("");
  const [customBaseUrl, setCustomBaseUrl] = useState("");
  const [workspacePath, setWorkspacePath] = useState("");

  // Personality
  const [personalityMode, setPersonalityMode] = useState<"preset" | "custom">("preset");
  const [selectedPreset, setSelectedPreset] = useState<string | null>("professional");
  const [customStyle, setCustomStyle] = useState("");

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

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      const body: Record<string, string | null | undefined> = {
        provider,
        model_selection: modelSelection,
        language,
      };

      if (personalityMode === "preset" && selectedPreset) {
        body.purpose_preset = selectedPreset;
        body.bot_purpose = "";
      } else {
        body.purpose_preset = "";
        body.bot_purpose = customStyle;
      }

      if (apiKey.trim()) {
        body.api_key = apiKey.trim();
      }
      if (customBaseUrl.trim()) {
        body.custom_base_url = customBaseUrl.trim();
      }
      if (workspacePath.trim()) {
        body.workspace_path = workspacePath.trim();
      }

      // External service API keys
      for (const [field, value] of Object.entries(extKeyValues)) {
        if (value.trim()) body[field] = value.trim();
      }

      const res = await agentFetch(`/v1/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || "Failed to save settings");
      }

      setSuccess(t.settingsPage.saveSuccess);
      setApiKey("");
      setExtKeyValues({});
    } catch (err) {
      setError(err instanceof Error ? err.message : t.errors.unexpected);
    } finally {
      setSaving(false);
    }
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

      {/* ─── Provider + Model + Language ─── */}
      <GlassCard>
        <div className="space-y-4">
          <SettingsDropdown
            label="Provider"
            value={provider}
            onChange={setProvider}
            options={PROVIDER_OPTIONS.map((opt) => ({ value: opt.value, label: opt.label }))}
          />

          <SettingsDropdown
            label={t.settingsPage.modelLabel}
            value={modelSelection}
            onChange={setModelSelection}
            options={BASE_MODEL_OPTIONS.map((opt) => ({ value: opt.value, label: opt.label }))}
          />

          <SettingsDropdown
            label={t.settingsPage.languageLabel}
            value={language}
            onChange={setLanguage}
            options={[
              { value: "auto", label: t.settingsPage.languageAuto },
              ...STATIC_LANGUAGE_OPTIONS,
            ]}
          />

          {/* Provider API Key */}
          <div className="border-t border-gray-200 pt-4 space-y-3">
            <Input
              label="API Key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={provider === "anthropic" ? "sk-ant-..." : provider === "openai" ? "sk-..." : provider === "google" ? "AIza..." : "API key"}
            />
            <p className="text-xs text-muted mt-1.5">
              {provider === "openai_compatible"
                ? "API key for your OpenAI-compatible endpoint."
                : `Your ${PROVIDER_OPTIONS.find((p) => p.value === provider)?.label ?? provider} API key.`}
            </p>

            {(provider === "openai_compatible") && (
              <div>
                <Input
                  label="Base URL"
                  type="url"
                  value={customBaseUrl}
                  onChange={(e) => setCustomBaseUrl(e.target.value)}
                  placeholder="https://openrouter.ai/api/v1"
                />
                <p className="text-xs text-muted mt-1.5">Custom endpoint for OpenAI-compatible providers.</p>
              </div>
            )}

            <div>
              <Input
                label="Workspace Path"
                type="text"
                value={workspacePath}
                onChange={(e) => setWorkspacePath(e.target.value)}
                placeholder="/home/user/projects"
              />
              <p className="text-xs text-muted mt-1.5">Default workspace directory for the agent.</p>
            </div>
          </div>
        </div>
      </GlassCard>

      {/* ─── Personality (collapsible) ─── */}
      <GlassCard className="!p-0 overflow-hidden">
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
              <span className="text-xs text-secondary/60 uppercase tracking-wider">{t.onboarding?.orCustom ?? "or custom"}</span>
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
      <GlassCard className="!p-0 overflow-hidden">
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
                      {category.keys.map((entry) => (
                        <div key={entry.field}>
                          <div className="flex items-center gap-2 mb-1 flex-wrap">
                            <span className="font-medium text-sm text-foreground">{entry.label}</span>
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                              {t.apiKeys.freeTier}: {t.apiKeys[entry.freeKey]}
                            </span>
                          </div>
                          <p className="text-xs text-secondary mb-1.5">{t.apiKeys[entry.descriptionKey]}</p>
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
                            placeholder={t.apiKeys.placeholder}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </GlassCard>

      <Button
        variant="cta"
        size="md"
        onClick={handleSave}
        disabled={saving}
      >
        {saving ? t.settingsPage.saving : t.settingsPage.save}
      </Button>
    </div>
  );
}
