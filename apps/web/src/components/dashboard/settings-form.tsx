"use client";

import { useState, useEffect, useRef } from "react";
import { GlassCard } from "@/components/ui/glass-card";
import { Input, Textarea } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";
import { agentFetch } from "@/lib/local-api";

/* ─── Custom Dropdown ─── */

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

/* ─── Model Options ─── */

const MODEL_OPTIONS = [
  { value: "haiku", label: "Claude Haiku 4.5" },
  { value: "sonnet", label: "Claude Sonnet 4.5" },
  { value: "opus", label: "Claude Opus 4.6" },
  { value: "gpt_5_nano", label: "GPT-5.4 Nano" },
  { value: "gpt_5_mini", label: "GPT-5.4 Mini" },
  { value: "gpt_5_5", label: "GPT-5.5" },
  { value: "gemini_3_1_flash_lite", label: "Gemini 3.1 Flash Lite" },
  { value: "gemini_3_1_pro", label: "Gemini 3.1 Pro" },
] as const;

const LANGUAGE_OPTIONS = [
  { value: "auto", label: "Auto-detect" },
  { value: "en", label: "English" },
  { value: "ko", label: "Korean" },
  { value: "ja", label: "Japanese" },
  { value: "zh", label: "Chinese" },
  { value: "es", label: "Spanish" },
];

const PERSONALITY_PRESETS = [
  { id: "professional", emoji: "B", name: "Professional" },
  { id: "friendly", emoji: "F", name: "Friendly" },
  { id: "casual", emoji: "C", name: "Casual" },
  { id: "teacher", emoji: "T", name: "Teacher" },
  { id: "analytical", emoji: "A", name: "Analytical" },
];

export function SettingsForm() {
  const t = useMessages();

  const [modelSelection, setModelSelection] = useState("sonnet");
  const [language, setLanguage] = useState("auto");
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Personality
  const [personalityMode, setPersonalityMode] = useState<"preset" | "custom">("preset");
  const [selectedPreset, setSelectedPreset] = useState<string | null>("professional");
  const [customStyle, setCustomStyle] = useState("");
  const [personalityOpen, setPersonalityOpen] = useState(false);
  const [apiKeysOpen, setApiKeysOpen] = useState(false);

  const showCustom = personalityMode === "custom";

  async function handleSave(): Promise<void> {
    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      const body: Record<string, string | null | undefined> = {
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
        body.anthropic_api_key = apiKey.trim();
      }

      const res = await agentFetch("/v1/settings", {
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
    } catch (err) {
      setError(err instanceof Error ? err.message : t.errors.unexpected);
    } finally {
      setSaving(false);
    }
  }

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

      {/* Model + Language */}
      <GlassCard>
        <div className="space-y-4">
          <SettingsDropdown
            label={t.settingsPage.modelLabel}
            value={modelSelection}
            onChange={setModelSelection}
            options={MODEL_OPTIONS.map((opt) => ({ value: opt.value, label: opt.label }))}
          />
          <p className="text-xs text-muted mt-1">{t.settingsPage.modelHint}</p>

          <SettingsDropdown
            label={t.settingsPage.languageLabel}
            value={language}
            onChange={setLanguage}
            options={LANGUAGE_OPTIONS}
          />

          {/* API Key */}
          <div className="border-t border-gray-200 pt-4">
            <Input
              label="API Key (Anthropic)"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-ant-..."
            />
            <p className="text-xs text-muted mt-1.5">{t.settingsPage.apiKeyHint}</p>
          </div>
        </div>
      </GlassCard>

      {/* Personality (collapsible) */}
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
                  <span>{preset.name}</span>
                </button>
              ))}
            </div>

            <div className="flex items-center gap-3 my-3">
              <div className="flex-1 border-t border-gray-200" />
              <span className="text-xs text-secondary/60 uppercase tracking-wider">or custom</span>
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

      {/* Additional API Keys (collapsible) */}
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
          <div className="border-t border-gray-200 px-5 pb-5 pt-4 space-y-3">
            <Input
              label="OpenAI API Key"
              type="password"
              placeholder="sk-..."
            />
            <Input
              label="Fireworks API Key"
              type="password"
              placeholder="fw-..."
            />
            <Input
              label="Google Gemini API Key"
              type="password"
              placeholder="AIza..."
            />
            <Input
              label="Brave Search API Key"
              type="password"
              placeholder="BSA..."
            />
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
