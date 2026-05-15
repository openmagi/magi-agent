"use client";

import { useState } from "react";
import { setOnboardingState, getOnboardingState } from "@/lib/onboarding/store";
import { GlassCard } from "@/components/ui/glass-card";
import { Textarea } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";
import { trackOnboardingPurposeSelect, trackOnboardingPurposeSkip } from "@/lib/analytics";
import { usePrivy } from "@privy-io/react-auth";

const LANGUAGE_OPTIONS = [
  { value: "auto", label: "Auto Detect", flag: "\u{1F310}" },
  { value: "en", label: "English", flag: "\u{1F1FA}\u{1F1F8}" },
  { value: "ko", label: "\uD55C\uAD6D\uC5B4", flag: "\u{1F1F0}\u{1F1F7}" },
  { value: "ja", label: "\u65E5\u672C\u8A9E", flag: "\u{1F1EF}\u{1F1F5}" },
  { value: "zh", label: "\u4E2D\u6587", flag: "\u{1F1E8}\u{1F1F3}" },
  { value: "es", label: "Espa\u00F1ol", flag: "\u{1F1EA}\u{1F1F8}" },
];

interface PersonalityPresetData {
  id: string;
  name: string;
  emoji: string;
  description: string;
  preview: string;
  styleReference: string;
}

const PERSONALITY_PRESETS: PersonalityPresetData[] = [
  {
    id: "professional",
    name: "personalityProfessional",
    emoji: "\u{1F4BC}",
    description: "personalityProfessionalDesc",
    preview: "personalityProfessionalPreview",
    styleReference: "You communicate in a professional, business-like manner.\n- Use polite but efficient language \u2014 no filler, no fluff\n- Lead with the answer or action, then explain if needed\n- Use bullet points and structured formatting for clarity\n- When the user asks a question, answer it directly first\n- Avoid casual expressions, emojis, or humor unless the user initiates\n- Use formal address in Korean (\uC874\uB313\uB9D0/\uD569\uC1FC\uCCB4), formal register in other languages\n- When uncertain, state assumptions clearly rather than hedging\n- Summarize long information into actionable takeaways",
  },
  {
    id: "friendly",
    name: "personalityFriendly",
    emoji: "\u{1F60A}",
    description: "personalityFriendlyDesc",
    preview: "personalityFriendlyPreview",
    styleReference: "You communicate in a warm, friendly, and approachable manner.\n- Use a conversational tone \u2014 like a helpful friend who happens to be knowledgeable\n- Show genuine interest in what the user is working on\n- Use encouraging language (\"Great question!\", \"That makes sense\")\n- Okay to use light humor and occasional emojis when natural\n- In Korean, use comfortable \uC874\uB313\uB9D0 (\uD574\uC694\uCCB4), not overly formal\n- Ask follow-up questions to show engagement\n- Celebrate wins and progress with the user\n- When delivering bad news, be empathetic but honest",
  },
  {
    id: "casual",
    name: "personalityCasual",
    emoji: "\u{1F919}",
    description: "personalityCasualDesc",
    preview: "personalityCasualPreview",
    styleReference: "You communicate in a very casual, relaxed style \u2014 like texting a close friend.\n- Use short, punchy sentences\n- Contractions always (\"don't\", \"can't\", \"it's\")\n- Okay to use slang, abbreviations, and emojis freely\n- In Korean, use \uBC18\uB9D0 (casual speech) \u2014 \uD574\uCCB4 is fine\n- React naturally (\"oh nice\", \"wait really?\", \"lol that's rough\")\n- Skip formalities \u2014 no \"I'd be happy to help\" or \"Certainly!\"\n- Be direct and honest, even blunt when appropriate\n- Match the user's energy \u2014 if they're excited, be excited back",
  },
  {
    id: "teacher",
    name: "personalityTeacher",
    emoji: "\u{1F4DA}",
    description: "personalityTeacherDesc",
    preview: "personalityTeacherPreview",
    styleReference: "You communicate like a patient, skilled teacher.\n- Break down complex topics into digestible steps\n- Use analogies and real-world examples to explain concepts\n- Check understanding before moving on (\"Does that make sense so far?\")\n- When the user makes mistakes, guide them to the answer rather than just giving it\n- Use numbered steps for processes, bullet points for lists\n- Provide context for why something works, not just how\n- In Korean, use friendly \uC874\uB313\uB9D0 (\uD574\uC694\uCCB4) \u2014 approachable but respectful\n- Adjust explanation depth based on the user's demonstrated knowledge level\n- Encourage questions and curiosity",
  },
  {
    id: "analytical",
    name: "personalityAnalytical",
    emoji: "\u{1F52C}",
    description: "personalityAnalyticalDesc",
    preview: "personalityAnalyticalPreview",
    styleReference: "You communicate in a logical, structured, and evidence-based manner.\n- Present information with clear reasoning chains (premise \u2192 evidence \u2192 conclusion)\n- Use data, numbers, and specific references whenever possible\n- Structure responses with headers and sections for complex topics\n- Acknowledge trade-offs and edge cases proactively\n- Distinguish between facts, estimates, and opinions explicitly\n- In Korean, use \uD569\uC1FC\uCCB4 (formal) \u2014 precision matters in register too\n- When making recommendations, list pros/cons or comparison tables\n- Avoid emotional language \u2014 let the data speak\n- Flag assumptions and uncertainties rather than presenting guesses as facts",
  },
];

interface StepPersonalityProps {
  onNext: () => void;
  onBack: () => void;
}

export function StepPersonality({ onNext, onBack }: StepPersonalityProps) {
  const state = getOnboardingState();
  const t = useMessages();
  const { getAccessToken } = usePrivy();

  const initialMode = state.customStyle && !state.personalityPreset ? "custom" : "preset";
  const [mode, setMode] = useState<"preset" | "custom">(initialMode);
  const [selectedPreset, setSelectedPreset] = useState<string | null>(state.personalityPreset ?? null);
  const [expandedPreset, setExpandedPreset] = useState<string | null>(null);
  const [customDescription, setCustomDescription] = useState("");
  const [generatedStyle, setGeneratedStyle] = useState(
    initialMode === "custom" ? (state.customStyle ?? "") : ""
  );
  const [generating, setGenerating] = useState(false);
  const [language, setLanguage] = useState(state.language ?? "auto");

  function handlePresetClick(presetId: string) {
    setMode("preset");
    if (selectedPreset === presetId) {
      // Toggle expand
      setExpandedPreset(expandedPreset === presetId ? null : presetId);
    } else {
      setSelectedPreset(presetId);
      setExpandedPreset(presetId);
    }
  }

  async function handleGenerate() {
    if (!customDescription.trim() || generating) return;
    setGenerating(true);
    try {
      const token = await getAccessToken();
      const res = await fetch("/api/bots/generate-style", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ description: customDescription, language }),
      });
      if (res.ok) {
        const data = await res.json();
        setGeneratedStyle(data.styleReference);
      }
    } catch {
      // silently fail
    } finally {
      setGenerating(false);
    }
  }

  function handleNext() {
    if (mode === "preset" && selectedPreset) {
      trackOnboardingPurposeSelect(selectedPreset);
      setOnboardingState({
        personalityPreset: selectedPreset,
        customStyle: null,
        language,
        step: 2,
      });
    } else {
      trackOnboardingPurposeSelect(null);
      setOnboardingState({
        personalityPreset: null,
        customStyle: generatedStyle || null,
        language,
        step: 2,
      });
    }
    onNext();
  }

  function handleSkip() {
    trackOnboardingPurposeSkip();
    setOnboardingState({ language, step: 2 });
    onNext();
  }

  const showCustom = mode === "custom";

  return (
    <div>
      <h1 className="text-xl font-bold mb-1 text-gradient">{t.onboarding.personalityTitle}</h1>
      <p className="text-secondary text-sm mb-5">{t.onboarding.personalitySubtitle}</p>

      {/* Language selector */}
      <div className="mb-4">
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

      {/* Personality preset cards */}
      <div className="space-y-2">
        {PERSONALITY_PRESETS.map((preset) => {
          const isSelected = mode === "preset" && selectedPreset === preset.id;
          const isExpanded = expandedPreset === preset.id && isSelected;
          const presetName = (t.onboarding as Record<string, string>)[preset.name] ?? preset.id;
          const presetDesc = (t.onboarding as Record<string, string>)[preset.description] ?? "";

          return (
            <div key={preset.id}>
              <button
                onClick={() => handlePresetClick(preset.id)}
                className="w-full text-left cursor-pointer"
              >
                <GlassCard
                  hover
                  className={`!p-3.5 !rounded-xl transition-all duration-200 ${
                    isSelected
                      ? "gradient-border glow-sm"
                      : showCustom ? "opacity-50" : ""
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-lg">{preset.emoji}</span>
                    <div className="flex-1">
                      <span className="font-semibold text-sm text-foreground">{presetName}</span>
                      <p className="text-xs text-secondary mt-0.5 leading-relaxed">{presetDesc}</p>
                    </div>
                    {isSelected && (
                      <svg
                        className={`w-4 h-4 text-secondary transition-transform duration-200 ${isExpanded ? "rotate-180" : ""}`}
                        fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                      </svg>
                    )}
                  </div>
                </GlassCard>
              </button>

              {/* Expandable style reference */}
              {isExpanded && (
                <div className="mt-1 ml-2 mr-2">
                  <GlassCard className="!p-3 !rounded-lg">
                    <p className="text-[10px] font-medium text-secondary uppercase tracking-wider mb-1.5">
                      {t.onboarding.stylePreviewLabel}
                    </p>
                    <pre className="text-xs text-foreground/80 whitespace-pre-wrap leading-relaxed font-sans">
                      {(t.onboarding as Record<string, string>)[preset.preview] ?? preset.styleReference}
                    </pre>
                  </GlassCard>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Divider */}
      <div className="flex items-center gap-3 my-4">
        <div className="flex-1 border-t border-black/[0.06]" />
        <span className="text-xs text-secondary/60 uppercase tracking-wider">{t.onboarding.orCustom}</span>
        <div className="flex-1 border-t border-black/[0.06]" />
      </div>

      {/* Custom style */}
      {showCustom ? (
        <div className="space-y-2">
          <Textarea
            label={t.onboarding.customStyleLabel}
            placeholder={t.onboarding.customStylePlaceholder}
            value={customDescription}
            onChange={(e) => setCustomDescription(e.target.value)}
            rows={2}
          />
          <Button
            variant="secondary"
            size="sm"
            onClick={handleGenerate}
            disabled={!customDescription.trim() || generating}
            className="w-full"
          >
            {generating ? t.onboarding.customStyleGenerating : t.onboarding.customStyleGenerate}
          </Button>
          {generatedStyle && (
            <GlassCard className="!p-3 !rounded-lg">
              <p className="text-[10px] font-medium text-secondary uppercase tracking-wider mb-1.5">
                {t.onboarding.stylePreviewLabel}
              </p>
              <Textarea
                value={generatedStyle}
                onChange={(e) => setGeneratedStyle(e.target.value)}
                rows={6}
                className="!text-xs !leading-relaxed"
              />
            </GlassCard>
          )}
        </div>
      ) : (
        <button
          onClick={() => { setMode("custom"); setSelectedPreset(null); setExpandedPreset(null); }}
          className="w-full cursor-pointer"
        >
          <GlassCard hover className="!p-3 !rounded-xl">
            <span className="text-sm text-secondary">{t.onboarding.customStylePlaceholder}</span>
          </GlassCard>
        </button>
      )}

      <div className="flex gap-3 mt-5">
        <Button
          variant="secondary"
          onClick={onBack}
          size="md"
          className="flex-1"
        >
          {t.onboarding.back}
        </Button>
        <Button
          onClick={handleNext}
          size="md"
          className="flex-1"
        >
          {t.onboarding.continue}
        </Button>
      </div>

      <button
        onClick={handleSkip}
        className="w-full mt-3 text-sm text-secondary hover:text-foreground transition-colors cursor-pointer"
      >
        {t.onboarding.skip}
      </button>
    </div>
  );
}
