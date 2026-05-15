"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { usePrivy } from "@privy-io/react-auth";
import { getOnboardingState, setOnboardingState, clearOnboardingState } from "@/lib/onboarding/store";
import type { ApiKeyMode, ModelSelection } from "@/lib/supabase/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { GlassCard } from "@/components/ui/glass-card";
import { Input } from "@/components/ui/input";
import { useMessages } from "@/lib/i18n";
import { getStoredReferralCode, clearReferralCode } from "@/lib/referral/store-ref";
import {
  trackOnboardingDeploy,
  trackOnboardingDeploySuccess,
  trackOnboardingDeployError,
  trackOnboardingPlanSelect,
  trackOnboardingModelSelect,
} from "@/lib/analytics";
import { isFireworksModel, isOpenAIModel, isCodexModel, isGoogleModel } from "@/lib/billing/plans";
import {
  LOCAL_LLM_MODEL_OPTIONS,
  isLocalLlmEnabledPlan,
  isLocalLlmModel,
} from "@/lib/models/local-llm";
import {
  ROUTER_PICKER_OPTIONS,
  applyRouterPickerMode,
  getRouterDisplayName,
  getRouterPickerMode,
  type RouterPickerMode,
} from "@/lib/models/router-tier";
import type { ValidRouterType } from "@/lib/constants";
import { PURPOSE_DISABLED_SKILLS } from "@/lib/skills-catalog";
import type { PurposeCategory } from "@/lib/skills-catalog";
import { ProvisioningStatus } from "@/components/onboarding/provisioning-status";

interface StepReviewDeployProps {
  onNext: (newBotId?: string) => void;
  onBack: () => void;
  sessionId?: string | null;
  onDeployingChange?: (deploying: boolean) => void;
  mode?: "create" | "add";
  subscriptionPlan?: string | null;
}

export function StepReviewDeploy({
  onNext,
  onBack,
  sessionId,
  onDeployingChange,
  mode = "create",
  subscriptionPlan,
}: StepReviewDeployProps) {
  const { getAccessToken, authenticated, login } = usePrivy();
  const t = useMessages();
  const state = getOnboardingState();
  const initialPricingTier = mode === "add" ? subscriptionPlan : state.pricingTier;

  const [selectedPlan, setSelectedPlan] = useState<ApiKeyMode | null>(mode === "add" ? "platform_credits" : (state.apiKeyMode ?? null));
  const [selectedTier, setSelectedTier] = useState<"pro" | "pro_plus" | "max" | "flex">(
    initialPricingTier === "flex"
      ? "flex"
      : initialPricingTier === "max"
        ? "max"
        : initialPricingTier === "pro_plus"
          ? "pro_plus"
          : "pro",
  );
  const [apiKey, setApiKey] = useState(state.anthropicApiKey ?? "");
  const [fireworksKey, setFireworksKey] = useState(state.fireworksApiKey ?? "");
  const [openaiKey, setOpenaiKey] = useState(state.openaiApiKey ?? "");
  const [geminiKey, setGeminiKey] = useState(state.geminiApiKey ?? "");
  const [codexAccessToken, setCodexAccessToken] = useState(state.codexAccessToken ?? "");
  const [codexRefreshToken, setCodexRefreshToken] = useState(state.codexRefreshToken ?? "");
  const [customBaseUrl, setCustomBaseUrl] = useState(state.customBaseUrl ?? "");
  const [showAdvanced, setShowAdvanced] = useState(!!state.customBaseUrl);
  const [selectedModel, setSelectedModel] = useState<ModelSelection>(state.modelSelection ?? "clawy_smart_routing");
  const [selectedRouterType, setSelectedRouterType] = useState<ValidRouterType>(state.routerType ?? "standard");
  const [modelPickerMode, setModelPickerMode] = useState<RouterPickerMode>(
    getRouterPickerMode(state.modelSelection, state.routerType),
  );
  const [showModelSettings, setShowModelSettings] = useState(false);
  const [claudeExpanded, setClaudeExpanded] = useState(false);
  const [gptExpanded, setGptExpanded] = useState(false);
  const [geminiExpanded, setGeminiExpanded] = useState(false);
  const modelIsFireworks = isFireworksModel(selectedModel);
  const modelIsOpenAI = isOpenAIModel(selectedModel);
  const modelIsGoogle = isGoogleModel(selectedModel);
  const modelIsLocalLlm = isLocalLlmModel(selectedModel);
  const localLlmPlanEnabled = selectedPlan === "platform_credits" && isLocalLlmEnabledPlan(selectedTier);
  const modelIsCodex = isCodexModel(selectedModel);
  const [deploying, setDeploying] = useState(false);
  const [showProvisioning, setShowProvisioning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seatsRemaining, setSeatsRemaining] = useState<number | null>(null);
  const [seatsFull, setSeatsFull] = useState(false);
  const skipDeployTrackRef = useRef(false);

  // Auto-select BYOK when Codex is selected (Codex is BYOK-only)
  useEffect(() => {
    if (modelIsCodex && selectedPlan !== "byok") {
      setSelectedPlan("byok");
    }
  }, [modelIsCodex, selectedPlan]);

  useEffect(() => {
    if (modelIsLocalLlm && !localLlmPlanEnabled) {
      setSelectedModel("clawy_smart_routing");
      setSelectedRouterType("standard");
      setModelPickerMode("standard_router");
    }
  }, [modelIsLocalLlm, localLlmPlanEnabled]);

  // Auto-expand model settings when BYOK plan is selected
  useEffect(() => {
    if (selectedPlan === "byok") {
      setShowModelSettings(true);
    }
  }, [selectedPlan]);

  // Fetch seat availability
  useEffect(() => {
    async function fetchSeats() {
      try {
        const res = await fetch("/api/seats");
        if (res.ok) {
          const data = await res.json();
          setSeatsRemaining(data.remaining);
          setSeatsFull(data.remaining <= 0);
        }
      } catch {
        // Silently fail — seats badge is non-critical
      }
    }
    fetchSeats();
  }, []);

  const CLAUDE_MODELS: ModelSelection[] = ["haiku", "sonnet", "opus"];
  const GPT_MODELS: ModelSelection[] = ["gpt_5_nano", "gpt_5_mini", "gpt_5_5", "gpt_5_5_pro"];
  const GEMINI_MODELS: ModelSelection[] = ["gemini_3_1_flash_lite", "gemini_3_1_pro"];

  const isClaudeSelected = CLAUDE_MODELS.includes(selectedModel);
  const isGptSelected = GPT_MODELS.includes(selectedModel);
  const isGeminiSelected = GEMINI_MODELS.includes(selectedModel);

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

  const modelName = getRouterDisplayName(selectedModel, selectedRouterType);

  function selectRouterMode(mode: RouterPickerMode) {
    const next = applyRouterPickerMode(mode, selectedModel !== "clawy_smart_routing" ? selectedModel : "opus");
    setModelPickerMode(mode);
    setSelectedModel(next.modelSelection);
    setSelectedRouterType(next.routerType);
    setClaudeExpanded(false);
    setGptExpanded(false);
    setGeminiExpanded(false);
  }

  function selectAdvancedModel(model: ModelSelection) {
    setModelPickerMode("advanced");
    setSelectedModel(model);
    setSelectedRouterType("standard");
  }

  const personalityDisplay = state.personalityPreset
    ? state.personalityPreset.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
    : state.customStyle
      ? t.onboarding.customStyle
      : t.onboarding.notSpecified;

  // Clear onboarding state when returning from checkout
  useEffect(() => {
    if (sessionId) {
      clearOnboardingState();
    }
  }, [sessionId]);

  // Clear stale pendingDeploy on mount to prevent auto-deploy loops
  useEffect(() => {
    const s = getOnboardingState();
    if (s.pendingDeploy && !authenticated) {
      setOnboardingState({ pendingDeploy: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleDeploy = useCallback(async () => {
    if (!selectedPlan) return;

    // If not authenticated, trigger login first and deploy after auth completes
    if (!authenticated) {
      await trackOnboardingDeploy(selectedPlan, selectedModel);
      setOnboardingState({
        pendingDeploy: true,
        apiKeyMode: selectedPlan,
        modelSelection: selectedModel,
        routerType: selectedRouterType,
        anthropicApiKey: selectedPlan === "byok" ? apiKey || null : null,
        fireworksApiKey: selectedPlan === "byok" ? fireworksKey || null : null,
        openaiApiKey: selectedPlan === "byok" && modelIsOpenAI ? openaiKey || null : null,
        geminiApiKey: selectedPlan === "byok" && modelIsGoogle ? geminiKey || null : null,
        codexAccessToken: modelIsCodex ? codexAccessToken || null : null,
        codexRefreshToken: modelIsCodex ? codexRefreshToken || null : null,
        customBaseUrl: selectedPlan === "byok" ? customBaseUrl || null : null,
        pricingTier: selectedPlan === "platform_credits" ? selectedTier : undefined,
      });
      login();
      return;
    }

    setDeploying(true);
    onDeployingChange?.(true);
    setError(null);

    setOnboardingState({
      apiKeyMode: selectedPlan,
      modelSelection: selectedModel,
      routerType: selectedRouterType,
      anthropicApiKey: selectedPlan === "byok" ? apiKey || null : null,
      fireworksApiKey: selectedPlan === "byok" ? fireworksKey || null : null,
      openaiApiKey: selectedPlan === "byok" && modelIsOpenAI ? openaiKey || null : null,
      geminiApiKey: selectedPlan === "byok" && modelIsGoogle ? geminiKey || null : null,
      codexAccessToken: modelIsCodex ? codexAccessToken || null : null,
      codexRefreshToken: modelIsCodex ? codexRefreshToken || null : null,
      customBaseUrl: selectedPlan === "byok" ? customBaseUrl || null : null,
      pricingTier: selectedPlan === "platform_credits" ? selectedTier : undefined,
    });

    // Skip tracking if this is an auto-deploy after OAuth (already tracked pre-redirect)
    if (skipDeployTrackRef.current) {
      skipDeployTrackRef.current = false;
    } else {
      trackOnboardingModelSelect(selectedModel);
      // Fire-and-forget — don't block the deploy API call
      trackOnboardingDeploy(selectedPlan, selectedModel);
    }

    try {
      const token = await getAccessToken();
      const res = await fetch("/api/bots", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          modelSelection: selectedModel,
          routerType: selectedRouterType,
          apiKeyMode: selectedPlan,
          anthropicApiKey: selectedPlan === "byok" ? apiKey || null : null,
          fireworksApiKey: selectedPlan === "byok" ? fireworksKey || null : null,
          openaiApiKey: selectedPlan === "byok" && modelIsOpenAI ? openaiKey || null : null,
          geminiApiKey: selectedPlan === "byok" && modelIsGoogle ? geminiKey || null : null,
          codexAccessToken: modelIsCodex ? codexAccessToken || null : null,
          codexRefreshToken: modelIsCodex ? codexRefreshToken || null : null,
          customBaseUrl: selectedPlan === "byok" ? customBaseUrl || null : null,
          pricingTier: selectedPlan === "platform_credits" ? selectedTier : undefined,
          personalityPreset: state.personalityPreset,
          customStyle: state.customStyle,
          language: state.language !== "auto" ? state.language : undefined,
          referralCode: getStoredReferralCode(),
          disabledSkills: state.purposeCategory
            ? PURPOSE_DISABLED_SKILLS[state.purposeCategory as PurposeCategory] ?? []
            : [],
          purposeCategory: state.purposeCategory ?? null,
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        const errMsg = data.code === "seats_full" ? "seats_full" : (data.error ?? "unknown");
        trackOnboardingDeployError(errMsg);
        if (data.code === "seats_full") {
          setSeatsFull(true);
          setSeatsRemaining(0);
          setError(t.onboarding.seatsFull);
        } else {
          setError(data.error ?? t.errors.unexpected);
        }
        return;
      }

      // Stripe Checkout redirect (new users without subscription)
      if (data.checkoutUrl) {
        clearOnboardingState();
        clearReferralCode();
        // Keep deploying=true — don't reset so modal stays visible until page navigates
        window.location.href = data.checkoutUrl;
        return;
      }

      // Add-bot mode: user already has subscription, bot created directly
      if (mode === "add" && data.bot?.id) {
        trackOnboardingDeploySuccess();
        clearOnboardingState();
        clearReferralCode();
        setDeploying(false);
        onDeployingChange?.(false);
        onNext(data.bot.id);
        return;
      }

      // Bot created successfully (user has subscription) — show provisioning
      if (data.bot?.id) {
        trackOnboardingDeploySuccess();
        clearOnboardingState();
        clearReferralCode();
        setShowProvisioning(true);
        setDeploying(false);
        onDeployingChange?.(false);
      } else {
        // No bot and no checkoutUrl — unexpected, show error
        setError("Unexpected response from server");
        setDeploying(false);
        onDeployingChange?.(false);
      }
    } catch (err) {
      trackOnboardingDeployError(err instanceof Error ? err.message : "network_error");
      setError(err instanceof Error ? err.message : t.onboarding.networkError);
      setDeploying(false);
      onDeployingChange?.(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    authenticated,
    selectedPlan,
    selectedTier,
    selectedModel,
    selectedRouterType,
    apiKey,
    fireworksKey,
    openaiKey,
    geminiKey,
    modelIsOpenAI,
    modelIsGoogle,
    modelIsCodex,
    codexAccessToken,
    codexRefreshToken,
    customBaseUrl,
    login,
    getAccessToken,
  ]);

  // Auto-deploy after login completes (deferred login flow — survives OAuth redirect)
  // Skip duplicate GA tracking since the user already clicked Deploy before OAuth.
  useEffect(() => {
    if (authenticated && getOnboardingState().pendingDeploy) {
      setOnboardingState({ pendingDeploy: false });
      // Set flag so handleDeploy skips trackOnboardingDeploy (already fired pre-OAuth)
      skipDeployTrackRef.current = true;
      handleDeploy();
    }
  }, [authenticated, handleDeploy]);

  /* --- Post-deploy: telegram setup + provisioning flow --- */
  if (showProvisioning) {
    return (
      <div className="text-center py-4">
        <ProvisioningStatus onComplete={onNext} />
      </div>
    );
  }

  /* --- Post-checkout provisioning view --- */
  if (sessionId) {
    return (
      <div className="text-center py-4">
        <div className="text-3xl mb-3 text-gradient">&#10003;</div>
        <h1 className="text-xl font-bold mb-1 text-gradient">{t.onboarding.paymentComplete}</h1>
        <p className="text-secondary text-sm mb-3">{t.onboarding.provisioning}</p>
        <ProvisioningStatus onComplete={onNext} />
      </div>
    );
  }

  /* --- Plan selection + review + deploy --- */
  const byokKeyReady = modelIsCodex
    ? !!codexAccessToken
    : modelIsOpenAI
      ? !!openaiKey
      : modelIsFireworks
        ? !!fireworksKey
        : modelIsGoogle
          ? !!geminiKey
          : modelIsLocalLlm
            ? selectedPlan === "platform_credits"
            : !!apiKey;
  const canDeploy = selectedPlan && (selectedPlan !== "byok" || byokKeyReady) && !seatsFull;

  const reviewItems = [
    { label: t.onboarding.reviewPersonality, value: personalityDisplay },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-xl font-bold text-gradient">
          {deploying ? t.onboarding.deploying : t.onboarding.deployTitle}
        </h1>
        {seatsRemaining !== null && !deploying && (
          <Badge variant={seatsFull ? "default" : "gradient"}>
            {seatsFull
              ? `0 ${t.onboarding.seatsLeft}`
              : `${seatsRemaining} ${t.onboarding.seatsLeft}`}
          </Badge>
        )}
      </div>
      <p className="text-secondary text-sm mb-5">
        {deploying ? t.onboarding.deployingRedirect : t.onboarding.deploySubtitle}
      </p>

      {seatsFull && (
        <div className="mb-5 py-3 px-4 rounded-xl bg-red-500/10 border border-red-500/20">
          <p className="text-sm text-red-400 text-center">
            {t.onboarding.seatsFull}
          </p>
        </div>
      )}

      {/* Plan selection — hidden in add mode (uses existing subscription) */}
      {mode !== "add" && (<>
      <h2 className="text-xs font-semibold text-secondary uppercase tracking-wider mb-2">
        {t.onboarding.choosePlan}
      </h2>
      <div className="space-y-2 mb-5">
        {/* Pro Plan */}
        <button
          onClick={() => { if (!modelIsCodex) { setSelectedPlan("platform_credits"); setSelectedTier("pro"); selectRouterMode("standard_router"); setShowModelSettings(false); trackOnboardingPlanSelect("pro"); } }}
          disabled={modelIsCodex}
          className={`w-full text-left ${modelIsCodex ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
        >
          <GlassCard
            hover={!modelIsCodex}
            className={`!p-3.5 !rounded-xl transition-all duration-200 ${
              selectedPlan === "platform_credits" && selectedTier === "pro" ? "gradient-border glow-sm" : ""
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-sm text-foreground">{t.onboarding.proPlanLabel}</span>
                <Badge variant="gradient">{t.onboarding.proPlanBadge}</Badge>
              </div>
              <span className="text-primary-light text-sm font-bold">{t.onboarding.proPlanPrice}</span>
            </div>
            <p className="text-xs text-secondary mt-0.5">{t.onboarding.proPlanDesc}</p>
            {modelIsCodex && <p className="text-[10px] text-red-400/70 mt-0.5">{t.onboarding.notAvailableForCodex}</p>}
          </GlassCard>
        </button>

        {/* Pro+ Plan */}
        <button
          onClick={() => { if (!modelIsCodex) { setSelectedPlan("platform_credits"); setSelectedTier("pro_plus"); selectRouterMode("standard_router"); setShowModelSettings(false); trackOnboardingPlanSelect("pro_plus"); } }}
          disabled={modelIsCodex}
          className={`w-full text-left ${modelIsCodex ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
        >
          <GlassCard
            hover={!modelIsCodex}
            className={`!p-3.5 !rounded-xl transition-all duration-200 ${
              selectedPlan === "platform_credits" && selectedTier === "pro_plus" ? "gradient-border glow-sm" : ""
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-sm text-foreground">{t.onboarding.proPlusPlanLabel}</span>
                <Badge variant="gradient">{t.onboarding.proPlusPlanBadge}</Badge>
              </div>
              <span className="text-primary-light text-sm font-bold">{t.onboarding.proPlusPlanPrice}</span>
            </div>
            <p className="text-xs text-secondary mt-0.5">{t.onboarding.proPlusPlanDesc}</p>
            {modelIsCodex && <p className="text-[10px] text-red-400/70 mt-0.5">{t.onboarding.notAvailableForCodex}</p>}
          </GlassCard>
        </button>

        {/* MAX Plan */}
        <button
          onClick={() => { if (!modelIsCodex) { setSelectedPlan("platform_credits"); setSelectedTier("max"); selectRouterMode("standard_router"); setShowModelSettings(false); trackOnboardingPlanSelect("max"); } }}
          disabled={modelIsCodex}
          className={`w-full text-left ${modelIsCodex ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
        >
          <GlassCard
            hover={!modelIsCodex}
            className={`!p-3.5 !rounded-xl transition-all duration-200 ${
              selectedPlan === "platform_credits" && selectedTier === "max" ? "gradient-border glow-sm" : ""
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-sm text-foreground">{t.onboarding.maxPlanLabel}</span>
                <Badge variant="gradient">{t.onboarding.maxPlanBadge}</Badge>
              </div>
              <span className="text-primary-light text-sm font-bold">{t.onboarding.maxPlanPrice}</span>
            </div>
            <p className="text-xs text-secondary mt-0.5">{t.onboarding.maxPlanDesc}</p>
            {modelIsCodex && <p className="text-[10px] text-red-400/70 mt-0.5">{t.onboarding.notAvailableForCodex}</p>}
          </GlassCard>
        </button>

        {/* FLEX Plan */}
        <button
          onClick={() => { if (!modelIsCodex) { setSelectedPlan("platform_credits"); setSelectedTier("flex"); selectRouterMode("standard_router"); setShowModelSettings(false); trackOnboardingPlanSelect("flex"); } }}
          disabled={modelIsCodex}
          className={`w-full text-left ${modelIsCodex ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
        >
          <GlassCard
            hover={!modelIsCodex}
            className={`!p-3.5 !rounded-xl transition-all duration-200 ${
              selectedPlan === "platform_credits" && selectedTier === "flex" ? "gradient-border glow-sm" : ""
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-sm text-foreground">{t.onboarding.flexPlanLabel}</span>
                <Badge variant="gradient">{t.onboarding.flexPlanBadge}</Badge>
              </div>
              <span className="text-primary-light text-sm font-bold">{t.onboarding.flexPlanPrice}</span>
            </div>
            <p className="text-xs text-secondary mt-0.5">{t.onboarding.flexPlanDesc}</p>
            {modelIsCodex && <p className="text-[10px] text-red-400/70 mt-0.5">{t.onboarding.notAvailableForCodex}</p>}
          </GlassCard>
        </button>
      </div>
      </>)}

      {/* Advanced Settings — model selection + API keys */}
      <div className="mb-5">
        <button
          type="button"
          onClick={() => setShowModelSettings(!showModelSettings)}
          className="flex items-center gap-1.5 text-xs text-secondary hover:text-foreground transition-colors cursor-pointer w-full"
        >
          <svg
            className={`w-3 h-3 transition-transform duration-200 ${showModelSettings ? "rotate-90" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          <span>{t.onboarding.advancedSettings}</span>
          <span className="text-[10px] text-muted ml-auto">{modelName}</span>
        </button>

        {showModelSettings && (
          <div className="mt-2 space-y-1">
            {ROUTER_PICKER_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => selectRouterMode(option.value)}
                className="w-full text-left cursor-pointer"
              >
                <GlassCard
                  hover
                  className={`!p-2 !rounded-lg transition-all duration-200 ${
                    modelPickerMode === option.value ? "gradient-border glow-sm" : ""
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

            {modelPickerMode === "advanced" && (
              <div className="mt-1.5 space-y-1 border-l border-black/[0.06] pl-2">
            {/* Claude (expandable) */}
            <div>
              <button
                onClick={() => {
                  if (!isClaudeSelected) {
                    selectAdvancedModel("opus");
                    setGptExpanded(false);
                    setGeminiExpanded(false);
                  }
                }}
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
                          {CLAUDE_SUB_OPTIONS.find((o) => o.id === selectedModel)?.name}
                        </span>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setClaudeExpanded(!claudeExpanded);
                        if (!claudeExpanded) {
                          setGptExpanded(false);
                          setGeminiExpanded(false);
                        }
                      }}
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
                          selectedModel === sub.id
                            ? "bg-primary/10 border border-primary/30"
                            : "hover:bg-black/[0.03] border border-transparent"
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <span className={`text-[11px] font-medium ${selectedModel === sub.id ? "text-foreground" : "text-secondary"}`}>
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
                onClick={() => {
                  if (!isGptSelected) {
                    selectAdvancedModel("gpt_5_5");
                    setClaudeExpanded(false);
                    setGeminiExpanded(false);
                  }
                }}
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
                          {GPT_SUB_OPTIONS.find((o) => o.id === selectedModel)?.name}
                        </span>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setGptExpanded(!gptExpanded);
                        if (!gptExpanded) {
                          setClaudeExpanded(false);
                          setGeminiExpanded(false);
                        }
                      }}
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
                          selectedModel === sub.id
                            ? "bg-primary/10 border border-primary/30"
                            : "hover:bg-black/[0.03] border border-transparent"
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <span className={`text-[11px] font-medium ${selectedModel === sub.id ? "text-foreground" : "text-secondary"}`}>
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
                onClick={() => {
                  if (!isGeminiSelected) {
                    selectAdvancedModel("gemini_3_1_pro");
                    setClaudeExpanded(false);
                    setGptExpanded(false);
                  }
                }}
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
                          {GEMINI_SUB_OPTIONS.find((o) => o.id === selectedModel)?.name}
                        </span>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setGeminiExpanded(!geminiExpanded);
                        if (!geminiExpanded) {
                          setClaudeExpanded(false);
                          setGptExpanded(false);
                        }
                      }}
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
                          selectedModel === sub.id
                            ? "bg-primary/10 border border-primary/30"
                            : "hover:bg-black/[0.03] border border-transparent"
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <span className={`text-[11px] font-medium ${selectedModel === sub.id ? "text-foreground" : "text-secondary"}`}>
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
                  selectedModel === "codex" ? "gradient-border glow-sm" : ""
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
                  selectedModel === "kimi_k2_5" ? "gradient-border glow-sm" : ""
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-xs text-foreground">{t.onboarding.kimiK2_5}</span>
                  <Badge variant="success" className="!px-1.5 !py-0 !text-[10px]">{t.onboarding.budgetPick}</Badge>
                </div>
                <p className="text-[10px] mt-0.5 leading-tight text-secondary truncate">{t.onboarding.kimiK2_5Desc}</p>
              </GlassCard>
            </button>

            {/* MiniMax M2.5 */}
            <button
              onClick={() => { selectAdvancedModel("minimax_m2_7"); setClaudeExpanded(false); setGptExpanded(false); setGeminiExpanded(false); }}
              className="w-full text-left cursor-pointer"
            >
              <GlassCard
                hover
                className={`!p-2 !rounded-lg transition-all duration-200 ${
                  selectedModel === "minimax_m2_7" ? "gradient-border glow-sm" : ""
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-xs text-foreground">{t.onboarding.minimaxM2_7}</span>
                  <Badge variant="warning" className="!px-1.5 !py-0 !text-[10px]">{t.onboarding.cheapestPick}</Badge>
                </div>
                <p className="text-[10px] mt-0.5 leading-tight text-secondary truncate">{t.onboarding.minimaxM2_7Desc}</p>
              </GlassCard>
            </button>

            {localLlmPlanEnabled && LOCAL_LLM_MODEL_OPTIONS.map((localModel) => (
              <button
                key={localModel.value}
                onClick={() => { selectAdvancedModel(localModel.value); setClaudeExpanded(false); setGptExpanded(false); setGeminiExpanded(false); }}
                className="w-full text-left cursor-pointer"
              >
                <GlassCard
                  hover
                  className={`!p-2 !rounded-lg transition-all duration-200 ${
                    selectedModel === localModel.value ? "gradient-border glow-sm" : ""
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-xs text-foreground">{localModel.label}</span>
                    <Badge variant="gradient" className="!px-1.5 !py-0 !text-[10px]">MAX</Badge>
                  </div>
                  <p className="text-[10px] mt-0.5 leading-tight text-secondary truncate">{localModel.description}</p>
                </GlassCard>
              </button>
            ))}
              </div>
            )}

            {/* API key inputs — only shown when BYOK plan selected */}
            {selectedPlan === "byok" && (
              <div className="mt-2 pt-2 border-t border-black/[0.06] space-y-3">
                {modelIsCodex ? (
                  <div className="space-y-3">
                    <Input
                      label={t.onboarding.codexTokensLabel}
                      type="password"
                      placeholder={t.onboarding.codexAccessTokenPlaceholder}
                      value={codexAccessToken}
                      onChange={(e) => setCodexAccessToken(e.target.value)}
                      className="font-mono text-sm"
                    />
                    <Input
                      label={t.settingsPage.codexRefreshTokenLabel}
                      type="password"
                      placeholder={t.onboarding.codexRefreshTokenPlaceholder}
                      value={codexRefreshToken}
                      onChange={(e) => setCodexRefreshToken(e.target.value)}
                      className="font-mono text-sm"
                    />
                    <div className="py-2 px-3 rounded-lg bg-black/[0.03] border border-black/5">
                      <p className="text-[10px] text-secondary leading-relaxed">
                        {t.settingsPage.codexGuideTitle}: {t.settingsPage.codexGuideStep1} → {t.settingsPage.codexGuideStep2} → {t.settingsPage.codexGuideStep3} → {t.settingsPage.codexGuideStep4}
                      </p>
                    </div>
                  </div>
                ) : modelIsOpenAI ? (
                  <Input
                    label={t.onboarding.openaiApiKeyLabel}
                    type="password"
                    placeholder={t.onboarding.openaiApiKeyPlaceholder}
                    value={openaiKey}
                    onChange={(e) => setOpenaiKey(e.target.value)}
                    className="font-mono text-sm"
                  />
                ) : modelIsGoogle ? (
                  <Input
                    label={t.onboarding.geminiApiKeyLabel}
                    type="password"
                    placeholder="AIza..."
                    value={geminiKey}
                    onChange={(e) => setGeminiKey(e.target.value)}
                    className="font-mono text-sm"
                  />
                ) : modelIsFireworks ? (
                  <Input
                    label={t.onboarding.fireworksApiKeyLabel}
                    type="password"
                    placeholder={t.onboarding.fireworksApiKeyPlaceholder}
                    value={fireworksKey}
                    onChange={(e) => setFireworksKey(e.target.value)}
                    className="font-mono text-sm"
                  />
                ) : (
                  <Input
                    label={t.onboarding.apiKeyInputLabel}
                    type="password"
                    placeholder={t.onboarding.apiKeyInputPlaceholder}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    className="font-mono text-sm"
                  />
                )}

                {/* Extra keys + custom base URL — not shown for Codex */}
                {!modelIsCodex && (
                  <>
                    <button
                      type="button"
                      onClick={() => setShowAdvanced(!showAdvanced)}
                      className="flex items-center gap-1.5 text-xs text-secondary hover:text-foreground transition-colors cursor-pointer"
                    >
                      <svg
                        className={`w-3 h-3 transition-transform duration-200 ${showAdvanced ? "rotate-90" : ""}`}
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                      {t.onboarding.advanced}
                    </button>
                    {showAdvanced && (
                      <div className="space-y-3">
                        {!modelIsFireworks && !modelIsOpenAI && (
                          <>
                            <div>
                              <Input label={t.onboarding.openaiApiKeyLabel} type="password" placeholder={t.onboarding.openaiApiKeyPlaceholder} value={openaiKey} onChange={(e) => setOpenaiKey(e.target.value)} className="font-mono text-sm" />
                              <p className="text-xs text-muted mt-1.5">{t.onboarding.openaiApiKeyHint}</p>
                            </div>
                            <div>
                              <Input label={t.onboarding.fireworksApiKeyLabel} type="password" placeholder={t.onboarding.fireworksApiKeyPlaceholder} value={fireworksKey} onChange={(e) => setFireworksKey(e.target.value)} className="font-mono text-sm" />
                              <p className="text-xs text-muted mt-1.5">{t.onboarding.fireworksApiKeyHint}</p>
                            </div>
                          </>
                        )}
                        {modelIsOpenAI && (
                          <>
                            <div>
                              <Input label={t.onboarding.apiKeyInputLabel} type="password" placeholder={t.onboarding.apiKeyInputPlaceholder} value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="font-mono text-sm" />
                              <p className="text-xs text-muted mt-1.5">{t.onboarding.anthropicApiKeyHint}</p>
                            </div>
                            <div>
                              <Input label={t.onboarding.fireworksApiKeyLabel} type="password" placeholder={t.onboarding.fireworksApiKeyPlaceholder} value={fireworksKey} onChange={(e) => setFireworksKey(e.target.value)} className="font-mono text-sm" />
                              <p className="text-xs text-muted mt-1.5">{t.onboarding.fireworksApiKeyHint}</p>
                            </div>
                          </>
                        )}
                        {modelIsFireworks && (
                          <>
                            <div>
                              <Input label={t.onboarding.apiKeyInputLabel} type="password" placeholder={t.onboarding.apiKeyInputPlaceholder} value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="font-mono text-sm" />
                              <p className="text-xs text-muted mt-1.5">{t.onboarding.anthropicApiKeyHint}</p>
                            </div>
                            <div>
                              <Input label={t.onboarding.openaiApiKeyLabel} type="password" placeholder={t.onboarding.openaiApiKeyPlaceholder} value={openaiKey} onChange={(e) => setOpenaiKey(e.target.value)} className="font-mono text-sm" />
                              <p className="text-xs text-muted mt-1.5">{t.onboarding.openaiApiKeyHint}</p>
                            </div>
                          </>
                        )}
                        <div>
                          <Input label={t.onboarding.customBaseUrlLabel} type="url" placeholder={t.onboarding.customBaseUrlPlaceholder} value={customBaseUrl} onChange={(e) => setCustomBaseUrl(e.target.value)} className="font-mono text-sm" />
                          <p className="text-xs text-muted mt-1.5">{t.onboarding.customBaseUrlHint}</p>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Review summary */}
      <h2 className="text-xs font-semibold text-secondary uppercase tracking-wider mb-2">
        {t.onboarding.reviewSummary}
      </h2>
      <div className="space-y-1.5 mb-4">
        {reviewItems.map((item) => (
          <div key={item.label} className="flex items-center justify-between px-3 py-2 rounded-lg bg-black/[0.03] border border-black/5">
            <span className="text-xs text-secondary">{item.label}</span>
            <span className="text-xs font-medium text-foreground">{item.value}</span>
          </div>
        ))}
      </div>

      {/* Trial note — hidden in add mode */}
      {mode !== "add" && (
        <div className="mb-4 py-2.5 px-4 rounded-xl bg-emerald-500/5 border border-emerald-500/10">
          <p className="text-xs text-emerald-600/90 text-center">
            {t.onboarding.deployTrialNote}
          </p>
        </div>
      )}

      {error && (
        <p className="text-red-400 text-xs mb-3">{error}</p>
      )}

      {/* Actions */}
      <div className="flex flex-col gap-2">
        <Button
          onClick={handleDeploy}
          disabled={!canDeploy || deploying}
          size="md"
          className="w-full"
        >
          {deploying ? t.onboarding.deploying : (mode === "add" ? t.onboarding.deployTitle : t.onboarding.deployButton)}
        </Button>
        {seatsRemaining !== null && seatsRemaining > 0 && !deploying && (
          <p className="text-xs text-secondary text-center">
            Only <span className="text-cta font-semibold">{seatsRemaining}</span>{" "}
            {t.onboarding.seatsLeft}
          </p>
        )}
        <Button
          variant="secondary"
          onClick={onBack}
          size="md"
          className="w-full"
        >
          {t.onboarding.back}
        </Button>
      </div>
    </div>
  );
}
