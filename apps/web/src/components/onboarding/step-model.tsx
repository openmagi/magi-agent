"use client";

import { useState } from "react";
import { setOnboardingState, getOnboardingState } from "@/lib/onboarding/store";
import type { ModelSelection } from "@/lib/supabase/types";
import { GlassCard } from "@/components/ui/glass-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";
import { trackOnboardingModelSelect } from "@/lib/analytics";
import {
  ROUTER_PICKER_OPTIONS,
  applyRouterPickerMode,
  getRouterPickerMode,
  type RouterPickerMode,
} from "@/lib/models/router-tier";
import type { ValidRouterType } from "@/lib/constants";

interface StepModelProps {
  onNext: () => void;
}

const LANGUAGE_OPTIONS = [
  { value: "auto", label: "Auto Detect", flag: "🌐" },
  { value: "en", label: "English", flag: "🇺🇸" },
  { value: "ko", label: "한국어", flag: "🇰🇷" },
  { value: "ja", label: "日本語", flag: "🇯🇵" },
  { value: "zh", label: "中文", flag: "🇨🇳" },
  { value: "es", label: "Español", flag: "🇪🇸" },
];

const CLAUDE_MODELS: ModelSelection[] = ["haiku", "sonnet", "opus"];
const GPT_MODELS: ModelSelection[] = ["gpt_5_nano", "gpt_5_mini", "gpt_5_5", "gpt_5_5_pro"];
const GEMINI_MODELS: ModelSelection[] = ["gemini_3_1_flash_lite", "gemini_3_1_pro"];

export function StepModel({ onNext }: StepModelProps) {
  const state = getOnboardingState();
  const [selected, setSelected] = useState<ModelSelection | null>(state.modelSelection);
  const [routerType, setRouterType] = useState<ValidRouterType>(state.routerType ?? "standard");
  const [language, setLanguage] = useState(state.language ?? "auto");
  const [advancedOpen, setAdvancedOpen] = useState(
    getRouterPickerMode(state.modelSelection, state.routerType) === "advanced",
  );
  const [claudeExpanded, setClaudeExpanded] = useState(false);
  const [gptExpanded, setGptExpanded] = useState(false);
  const [geminiExpanded, setGeminiExpanded] = useState(false);
  const t = useMessages();

  const isClaudeSelected = selected !== null && CLAUDE_MODELS.includes(selected);
  const isGptSelected = selected !== null && GPT_MODELS.includes(selected);
  const isGeminiSelected = selected !== null && GEMINI_MODELS.includes(selected);
  const selectedMode = getRouterPickerMode(selected, routerType);

  const CLAUDE_SUB_OPTIONS: { id: ModelSelection; name: string; description: string; badge?: string }[] = [
    { id: "haiku", name: t.onboarding.haiku, description: t.onboarding.haikuDesc },
    { id: "sonnet", name: t.onboarding.sonnet, description: t.onboarding.sonnetDesc },
    { id: "opus", name: t.onboarding.opus, description: t.onboarding.opusDesc },
  ];

  const GPT_SUB_OPTIONS: { id: ModelSelection; name: string; description: string; badge?: string }[] = [
    { id: "gpt_5_nano", name: t.onboarding.gpt5Nano, description: t.onboarding.gpt5NanoDesc },
    { id: "gpt_5_mini", name: t.onboarding.gpt51, description: t.onboarding.gpt51Desc },
    { id: "gpt_5_5", name: t.onboarding.gpt54, description: t.onboarding.gpt54Desc },
    { id: "gpt_5_5_pro", name: t.onboarding.gpt55Pro, description: t.onboarding.gpt55ProDesc },
  ];

  const GEMINI_SUB_OPTIONS: { id: ModelSelection; name: string; description: string; badge?: string }[] = [
    { id: "gemini_3_1_flash_lite", name: t.onboarding.gemini31FlashLite, description: t.onboarding.gemini31FlashLiteDesc, badge: t.onboarding.budgetPick },
    { id: "gemini_3_1_pro", name: t.onboarding.gemini31Pro, description: t.onboarding.gemini31ProDesc },
  ];

  function selectRouterMode(mode: RouterPickerMode) {
    const next = applyRouterPickerMode(mode, selected && selected !== "clawy_smart_routing" ? selected : "opus");
    setSelected(next.modelSelection);
    setRouterType(next.routerType);
    setAdvancedOpen(mode === "advanced");
    setClaudeExpanded(false);
    setGptExpanded(false);
    setGeminiExpanded(false);
  }

  function selectAdvancedModel(model: ModelSelection) {
    setSelected(model);
    setRouterType("standard");
    setAdvancedOpen(true);
  }

  function handleGptClick() {
    if (!isGptSelected) {
      selectAdvancedModel("gpt_5_5");
      setClaudeExpanded(false);
      setGeminiExpanded(false);
    }
  }

  function toggleGptExpand(e: React.MouseEvent) {
    e.stopPropagation();
    setGptExpanded(!gptExpanded);
    if (!gptExpanded) { setClaudeExpanded(false); setGeminiExpanded(false); }
  }

  function handleClaudeClick() {
    if (!isClaudeSelected) {
      selectAdvancedModel("opus");
      setGptExpanded(false);
      setGeminiExpanded(false);
    }
  }

  function toggleClaudeExpand(e: React.MouseEvent) {
    e.stopPropagation();
    setClaudeExpanded(!claudeExpanded);
    if (!claudeExpanded) { setGptExpanded(false); setGeminiExpanded(false); }
  }

  function handleGeminiClick() {
    if (!isGeminiSelected) {
      selectAdvancedModel("gemini_3_1_pro");
      setClaudeExpanded(false);
      setGptExpanded(false);
    }
  }

  function toggleGeminiExpand(e: React.MouseEvent) {
    e.stopPropagation();
    setGeminiExpanded(!geminiExpanded);
    if (!geminiExpanded) { setClaudeExpanded(false); setGptExpanded(false); }
  }

  function handleNext() {
    if (!selected) return;
    trackOnboardingModelSelect(selected);
    setOnboardingState({ modelSelection: selected, routerType, language, step: 1 });
    onNext();
  }

  return (
    <div>
      <h1 className="text-base font-bold mb-0.5 text-gradient">{t.onboarding.modelTitle}</h1>
      <p className="text-secondary text-[11px] mb-2">{t.onboarding.modelSubtitle}</p>

      <div className="space-y-1">
        {ROUTER_PICKER_OPTIONS.map((option) => (
          <button
            key={option.value}
            onClick={() => selectRouterMode(option.value)}
            className="w-full text-left cursor-pointer"
          >
            <GlassCard
              hover
              className={`!p-2 !rounded-lg transition-all duration-200 ${
                selectedMode === option.value ? "gradient-border glow-sm" : ""
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="font-semibold text-xs text-foreground">{option.label}</span>
                {option.value === "standard_router" && (
                  <Badge variant="gradient" className="!px-1.5 !py-0 !text-[10px]">{t.onboarding.recommended}</Badge>
                )}
              </div>
              <p className="text-[10px] mt-0.5 leading-tight text-secondary truncate">{option.description}</p>
            </GlassCard>
          </button>
        ))}

        {advancedOpen && (
          <div className="mt-1.5 space-y-1 border-l border-black/[0.06] pl-2">
        {/* Claude (expandable) */}
        <div>
          <button
            onClick={handleClaudeClick}
            className="w-full text-left cursor-pointer"
          >
            <GlassCard
              hover
              className={`!p-2 !rounded-lg transition-all duration-200 ${
                isClaudeSelected ? "gradient-border glow-sm" : ""
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-semibold text-xs text-foreground">{t.onboarding.claudeGroup}</span>
                  <Badge variant="gradient" className="!px-1.5 !py-0 !text-[10px]">{t.onboarding.claudeGroupBadge}</Badge>
                  {isClaudeSelected && (
                    <span className="text-[10px] text-primary-light truncate">
                      {CLAUDE_SUB_OPTIONS.find((o) => o.id === selected)?.name}
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={toggleClaudeExpand}
                  className="p-1 -m-1 cursor-pointer"
                >
                  <svg
                    className={`w-3.5 h-3.5 text-secondary shrink-0 transition-transform duration-200 ${claudeExpanded ? "rotate-180" : ""}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
              </div>
              <p className="text-[10px] mt-0.5 leading-tight text-secondary">{t.onboarding.claudeGroupDesc}</p>
            </GlassCard>
          </button>

          {/* Claude sub-options */}
          {claudeExpanded && (
            <div className="ml-3 mt-0.5 space-y-0.5 border-l border-black/[0.06] pl-2">
              {CLAUDE_SUB_OPTIONS.map((sub) => (
                <button
                      key={sub.id}
                      onClick={() => selectAdvancedModel(sub.id)}
                  className="w-full text-left cursor-pointer"
                >
                  <div
                    className={`px-2 py-1.5 rounded-md transition-all duration-200 ${
                      selected === sub.id
                        ? "bg-primary/10 border border-primary/30"
                        : "hover:bg-black/[0.03] border border-transparent"
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className={`text-[11px] font-medium ${selected === sub.id ? "text-foreground" : "text-secondary"}`}>
                        {sub.name}
                      </span>
                      {sub.badge && <Badge variant="default" className="!px-1.5 !py-0 !text-[10px]">{sub.badge}</Badge>}
                    </div>
                    <p className="text-[10px] leading-tight text-muted mt-0.5 truncate">{sub.description}</p>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* GPT-5 (expandable) */}
        <div>
          <button
            onClick={handleGptClick}
            className="w-full text-left cursor-pointer"
          >
            <GlassCard
              hover
              className={`!p-2 !rounded-lg transition-all duration-200 ${
                isGptSelected ? "gradient-border glow-sm" : ""
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-semibold text-xs text-foreground">{t.onboarding.gptGroup}</span>
                  <Badge variant="default" className="!px-1.5 !py-0 !text-[10px]">{t.onboarding.gptGroupBadge}</Badge>
                  {isGptSelected && (
                    <span className="text-[10px] text-primary-light truncate">
                      {GPT_SUB_OPTIONS.find((o) => o.id === selected)?.name}
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={toggleGptExpand}
                  className="p-1 -m-1 cursor-pointer"
                >
                  <svg
                    className={`w-3.5 h-3.5 text-secondary shrink-0 transition-transform duration-200 ${gptExpanded ? "rotate-180" : ""}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
              </div>
              <p className="text-[10px] mt-0.5 leading-tight text-secondary">{t.onboarding.gptGroupDesc}</p>
            </GlassCard>
          </button>

          {/* GPT sub-options */}
          {gptExpanded && (
            <div className="ml-3 mt-0.5 space-y-0.5 border-l border-black/[0.06] pl-2">
              {GPT_SUB_OPTIONS.map((sub) => (
                <button
                  key={sub.id}
                  onClick={() => selectAdvancedModel(sub.id)}
                  className="w-full text-left cursor-pointer"
                >
                  <div
                    className={`px-2 py-1.5 rounded-md transition-all duration-200 ${
                      selected === sub.id
                        ? "bg-primary/10 border border-primary/30"
                        : "hover:bg-black/[0.03] border border-transparent"
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className={`text-[11px] font-medium ${selected === sub.id ? "text-foreground" : "text-secondary"}`}>
                        {sub.name}
                      </span>
                      {sub.badge && <Badge variant="default" className="!px-1.5 !py-0 !text-[10px]">{sub.badge}</Badge>}
                    </div>
                    <p className="text-[10px] leading-tight text-muted mt-0.5 truncate">{sub.description}</p>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Gemini (expandable) */}
        <div>
          <button
            onClick={handleGeminiClick}
            className="w-full text-left cursor-pointer"
          >
            <GlassCard
              hover
              className={`!p-2 !rounded-lg transition-all duration-200 ${
                isGeminiSelected ? "gradient-border glow-sm" : ""
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-semibold text-xs text-foreground">{t.onboarding.geminiGroup}</span>
                  <Badge variant="default" className="!px-1.5 !py-0 !text-[10px]">{t.onboarding.geminiGroupBadge}</Badge>
                  {isGeminiSelected && (
                    <span className="text-[10px] text-primary-light truncate">
                      {GEMINI_SUB_OPTIONS.find((o) => o.id === selected)?.name}
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={toggleGeminiExpand}
                  className="p-1 -m-1 cursor-pointer"
                >
                  <svg
                    className={`w-3.5 h-3.5 text-secondary shrink-0 transition-transform duration-200 ${geminiExpanded ? "rotate-180" : ""}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
              </div>
              <p className="text-[10px] mt-0.5 leading-tight text-secondary">{t.onboarding.geminiGroupDesc}</p>
            </GlassCard>
          </button>

          {/* Gemini sub-options */}
          {geminiExpanded && (
            <div className="ml-3 mt-0.5 space-y-0.5 border-l border-black/[0.06] pl-2">
              {GEMINI_SUB_OPTIONS.map((sub) => (
                <button
                  key={sub.id}
                  onClick={() => selectAdvancedModel(sub.id)}
                  className="w-full text-left cursor-pointer"
                >
                  <div
                    className={`px-2 py-1.5 rounded-md transition-all duration-200 ${
                      selected === sub.id
                        ? "bg-primary/10 border border-primary/30"
                        : "hover:bg-black/[0.03] border border-transparent"
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className={`text-[11px] font-medium ${selected === sub.id ? "text-foreground" : "text-secondary"}`}>
                        {sub.name}
                      </span>
                      {sub.badge && <Badge variant="default" className="!px-1.5 !py-0 !text-[10px]">{sub.badge}</Badge>}
                    </div>
                    <p className="text-[10px] leading-tight text-muted mt-0.5 truncate">{sub.description}</p>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Codex */}
        <button
          onClick={() => { selectAdvancedModel("codex"); setClaudeExpanded(false); setGptExpanded(false); setGeminiExpanded(false); }}
          className="w-full text-left cursor-pointer"
        >
          <GlassCard
            hover
            className={`!p-2 !rounded-lg transition-all duration-200 ${
              selected === "codex" ? "gradient-border glow-sm" : ""
            }`}
          >
            <div className="flex items-center gap-2">
              <span className="font-semibold text-xs text-foreground">{t.onboarding.codex}</span>
              <Badge variant="warning" className="!px-1.5 !py-0 !text-[10px]">{t.onboarding.codexBadge}</Badge>
            </div>
            <p className="text-[10px] mt-0.5 leading-tight text-secondary truncate">{t.onboarding.codexDesc}</p>
          </GlassCard>
        </button>

        {/* Kimi K2.6 */}
        <button
          onClick={() => { selectAdvancedModel("kimi_k2_5"); setClaudeExpanded(false); setGptExpanded(false); setGeminiExpanded(false); }}
          className="w-full text-left cursor-pointer"
        >
          <GlassCard
            hover
            className={`!p-2 !rounded-lg transition-all duration-200 ${
              selected === "kimi_k2_5" ? "gradient-border glow-sm" : ""
            }`}
          >
            <div className="flex items-center gap-2">
              <span className="font-semibold text-xs text-foreground">{t.onboarding.kimiK2_5}</span>
              <Badge variant="success" className="!px-1.5 !py-0 !text-[10px]">{t.onboarding.budgetPick}</Badge>
            </div>
            <p className="text-[10px] mt-0.5 leading-tight text-secondary truncate">{t.onboarding.kimiK2_5Desc}</p>
          </GlassCard>
        </button>

        {/* MiniMax M2.7 */}
        <button
          onClick={() => { selectAdvancedModel("minimax_m2_7"); setClaudeExpanded(false); setGptExpanded(false); setGeminiExpanded(false); }}
          className="w-full text-left cursor-pointer"
        >
          <GlassCard
            hover
            className={`!p-2 !rounded-lg transition-all duration-200 ${
              selected === "minimax_m2_7" ? "gradient-border glow-sm" : ""
            }`}
          >
            <div className="flex items-center gap-2">
              <span className="font-semibold text-xs text-foreground">{t.onboarding.minimaxM2_7}</span>
              <Badge variant="warning" className="!px-1.5 !py-0 !text-[10px]">{t.onboarding.cheapestPick}</Badge>
            </div>
            <p className="text-[10px] mt-0.5 leading-tight text-secondary truncate">{t.onboarding.minimaxM2_7Desc}</p>
          </GlassCard>
        </button>
          </div>
        )}
      </div>

      {/* Language selector */}
      <div className="mt-2.5">
        <p className="text-[11px] font-medium text-secondary mb-1">{t.settingsPage.languageLabel}</p>
        <div className="flex flex-wrap gap-1">
          {LANGUAGE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setLanguage(opt.value)}
              className={`px-2 py-0.5 rounded-md text-[11px] transition-all duration-200 cursor-pointer ${
                language === opt.value
                  ? "bg-primary/15 border border-primary/40 text-foreground"
                  : "bg-black/[0.03] border border-black/[0.06] text-secondary hover:border-black/[0.12]"
              }`}
            >
              <span className="mr-1">{opt.flag}</span>
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <Button
        onClick={handleNext}
        disabled={!selected}
        size="md"
        className="w-full mt-3"
      >
        {t.onboarding.continue}
      </Button>
    </div>
  );
}
