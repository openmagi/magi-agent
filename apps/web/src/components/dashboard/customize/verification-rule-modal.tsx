"use client";

import { useState, useCallback } from "react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { useMessages } from "@/lib/i18n";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";

interface BuiltinPresetConfig {
  enabled: boolean;
  mode: "hybrid" | "deterministic" | "llm";
}

interface VerificationRuleModalProps {
  botId: string;
  initialRules: string | null;
  initialAgentConfig?: { builtin_presets?: Record<string, BuiltinPresetConfig> };
  open: boolean;
  onClose: () => void;
}

interface VerificationPreset {
  id: string;
  icon: string;
  title: string;
  description: string;
  defaultEnabled: boolean;
}

interface PresetCategory {
  id: string;
  title: string;
  icon: string;
  presets: VerificationPreset[];
}

// i18n key mappings — actual text resolved at render time via t.customize.*
const PRESET_CATEGORY_DEFS = [
  { id: "answer", icon: "✅", titleKey: "presetCatAnswer", presetIds: ["answer-quality", "completion-evidence", "pre-refusal", "output-purity", "deferral-blocker"] },
  { id: "fact", icon: "🔬", titleKey: "presetCatFact", presetIds: ["fact-grounding", "self-claim", "resource-existence", "claim-citation", "deterministic-evidence"] },
  { id: "coding", icon: "💻", titleKey: "presetCatCoding", presetIds: ["coding-verification", "coding-context", "coding-workspace-lock", "coding-child-review", "benchmark-verifier"] },
  { id: "task", icon: "📋", titleKey: "presetCatTask", presetIds: ["task-contract", "goal-progress", "task-board-completion"] },
  { id: "output", icon: "📦", titleKey: "presetCatOutput", presetIds: ["output-delivery", "artifact-delivery", "response-language"] },
  { id: "research", icon: "🔎", titleKey: "presetCatResearch", presetIds: ["parallel-research", "source-authority"] },
  { id: "memory", icon: "🧠", titleKey: "presetCatMemory", presetIds: ["memory-continuity"] },
] as const;

interface PresetDef { id: string; icon: string; titleKey: string; descKey: string; defaultEnabled: boolean }

const PRESET_DEFS: PresetDef[] = [
  { id: "answer-quality", icon: "✅", titleKey: "presetAnswerQuality", descKey: "presetAnswerQualityDesc", defaultEnabled: true },
  { id: "completion-evidence", icon: "📋", titleKey: "presetCompletionEvidence", descKey: "presetCompletionEvidenceDesc", defaultEnabled: true },
  { id: "pre-refusal", icon: "🚫", titleKey: "presetPreRefusal", descKey: "presetPreRefusalDesc", defaultEnabled: true },
  { id: "output-purity", icon: "🧹", titleKey: "presetOutputPurity", descKey: "presetOutputPurityDesc", defaultEnabled: true },
  { id: "deferral-blocker", icon: "⏱️", titleKey: "presetDeferralBlocker", descKey: "presetDeferralBlockerDesc", defaultEnabled: true },
  { id: "fact-grounding", icon: "🔬", titleKey: "presetFactGrounding", descKey: "presetFactGroundingDesc", defaultEnabled: false },
  { id: "self-claim", icon: "📂", titleKey: "presetSelfClaim", descKey: "presetSelfClaimDesc", defaultEnabled: true },
  { id: "resource-existence", icon: "🔍", titleKey: "presetResourceExistence", descKey: "presetResourceExistenceDesc", defaultEnabled: true },
  { id: "claim-citation", icon: "📎", titleKey: "presetClaimCitation", descKey: "presetClaimCitationDesc", defaultEnabled: true },
  { id: "deterministic-evidence", icon: "🔢", titleKey: "presetDeterministicEvidence", descKey: "presetDeterministicEvidenceDesc", defaultEnabled: true },
  { id: "coding-verification", icon: "🧪", titleKey: "presetCodingVerification", descKey: "presetCodingVerificationDesc", defaultEnabled: true },
  { id: "coding-context", icon: "🗺️", titleKey: "presetCodingContext", descKey: "presetCodingContextDesc", defaultEnabled: true },
  { id: "coding-workspace-lock", icon: "🔐", titleKey: "presetCodingWorkspaceLock", descKey: "presetCodingWorkspaceLockDesc", defaultEnabled: true },
  { id: "coding-child-review", icon: "👁️", titleKey: "presetCodingChildReview", descKey: "presetCodingChildReviewDesc", defaultEnabled: true },
  { id: "benchmark-verifier", icon: "📊", titleKey: "presetBenchmarkVerifier", descKey: "presetBenchmarkVerifierDesc", defaultEnabled: false },
  { id: "task-contract", icon: "📝", titleKey: "presetTaskContract", descKey: "presetTaskContractDesc", defaultEnabled: true },
  { id: "goal-progress", icon: "🎯", titleKey: "presetGoalProgress", descKey: "presetGoalProgressDesc", defaultEnabled: true },
  { id: "task-board-completion", icon: "✔️", titleKey: "presetTaskBoardCompletion", descKey: "presetTaskBoardCompletionDesc", defaultEnabled: true },
  { id: "output-delivery", icon: "📤", titleKey: "presetOutputDelivery", descKey: "presetOutputDeliveryDesc", defaultEnabled: true },
  { id: "artifact-delivery", icon: "🎁", titleKey: "presetArtifactDelivery", descKey: "presetArtifactDeliveryDesc", defaultEnabled: true },
  { id: "response-language", icon: "🌐", titleKey: "presetResponseLanguage", descKey: "presetResponseLanguageDesc", defaultEnabled: true },
  { id: "parallel-research", icon: "🔄", titleKey: "presetParallelResearch", descKey: "presetParallelResearchDesc", defaultEnabled: true },
  { id: "source-authority", icon: "📚", titleKey: "presetSourceAuthority", descKey: "presetSourceAuthorityDesc", defaultEnabled: true },
  { id: "memory-continuity", icon: "🔗", titleKey: "presetMemoryContinuity", descKey: "presetMemoryContinuityDesc", defaultEnabled: true },
];

const PRESET_MAP = new Map(PRESET_DEFS.map((p) => [p.id, p]));

const SECURITY_HOOK_KEYS = [
  { icon: "🛑", key: "secDangerousPatterns" },
  { icon: "🔒", key: "secPathEscape" },
  { icon: "🔑", key: "secSecretExposure" },
  { icon: "⚠️", key: "secGitSafety" },
  { icon: "📄", key: "secSealedFiles" },
  { icon: "🔐", key: "secArityPermission" },
] as const;

const MODE_KEYS = { hybrid: "presetModeHybrid", deterministic: "presetModeDeterministic", llm: "presetModeLlm" } as const;

function defaultPresetConfigs(initial?: Record<string, BuiltinPresetConfig>): Record<string, BuiltinPresetConfig> {
  const configs: Record<string, BuiltinPresetConfig> = {};
  for (const p of PRESET_DEFS) {
    configs[p.id] = initial?.[p.id] ?? { enabled: p.defaultEnabled, mode: "hybrid" };
  }
  return configs;
}

interface SavedCondition {
  id: string;
  label: string;
  technical: string;
  isPreset: boolean;
}

interface SavedCheck {
  id: string;
  label: string;
  technical: string;
}

const HOOK_POINTS = [
  { value: "beforeCommit", icon: "✅" },
  { value: "beforeToolUse", icon: "🔧" },
  { value: "afterToolUse", icon: "📋" },
  { value: "beforeLLMCall", icon: "🧠" },
  { value: "afterLLMCall", icon: "💬" },
  { value: "beforeTurnStart", icon: "▶️" },
  { value: "afterTurnEnd", icon: "⏹️" },
] as const;

const FAIL_BEHAVIORS = [
  { value: "blockAndRetry", icon: "🔄" },
  { value: "askUser", icon: "💬" },
  { value: "warnOnly", icon: "⚠️" },
  { value: "recordOnly", icon: "📝" },
] as const;

type Step = "list" | "hookPoint" | "condition" | "check" | "failBehavior" | "preview";
const BUILDER_STEPS: Step[] = ["hookPoint", "condition", "check", "failBehavior", "preview"];

function normalizeRule(rule: string): string {
  return rule.replace(/\s+/g, " ").replace(/[.。]+$/g, "").toLowerCase().trim();
}

function hasRule(rules: string, rule: string): boolean {
  const needle = normalizeRule(rule);
  if (!needle) return false;
  return rules.split("\n").some((line) => normalizeRule(line.replace(/^\s*[-*+]\s+/, "")) === needle);
}

function StepIndicator({ current, total }: { current: number; total: number }): React.ReactElement {
  return (
    <div className="flex items-center gap-1.5 mb-6">
      {Array.from({ length: total }, (_, i) => (
        <div key={i} className={`h-1 rounded-full transition-all duration-300 ${i <= current ? "bg-primary w-6" : "bg-black/10 w-4"}`} />
      ))}
    </div>
  );
}

function BackButton({ onClick, label }: { onClick: () => void; label: string }): React.ReactElement {
  return (
    <button type="button" onClick={onClick} className="flex items-center gap-1 text-xs text-secondary hover:text-foreground transition-colors">
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
      {label}
    </button>
  );
}

function OptionCard({ selected, onClick, children }: { selected?: boolean; onClick: () => void; children: React.ReactNode }): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full flex items-center gap-3 glass rounded-2xl px-4 py-3.5 text-left transition-all duration-200 hover:border-primary/20 hover:bg-glass-hover active:scale-[0.99] ${
        selected ? "!border-primary/30 !bg-primary/[0.04]" : ""
      }`}
    >
      {children}
    </button>
  );
}

export function VerificationRuleModal({ botId, initialRules, initialAgentConfig, open, onClose }: VerificationRuleModalProps): React.ReactElement | null {
  const authFetch = useAuthFetch();
  const t = useMessages();

  const [rules, setRules] = useState(initialRules ?? "");
  const [presetConfigs, setPresetConfigs] = useState<Record<string, BuiltinPresetConfig>>(
    () => defaultPresetConfigs(initialAgentConfig?.builtin_presets),
  );
  const [presetDirty, setPresetDirty] = useState(false);
  const [step, setStep] = useState<Step>("list");
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);

  const [selectedHookPoint, setSelectedHookPoint] = useState<string>("beforeCommit");
  const [selectedCondition, setSelectedCondition] = useState<string | null>(null);
  const [selectedCheck, setSelectedCheck] = useState<string | null>(null);
  const [customCheckInput, setCustomCheckInput] = useState("");
  const [failBehavior, setFailBehavior] = useState<string>("blockAndRetry");
  const [failOpen, setFailOpen] = useState(true);

  const [savedConditions, setSavedConditions] = useState<SavedCondition[]>([]);
  const [savedChecks, setSavedChecks] = useState<SavedCheck[]>([]);
  const [newConditionInput, setNewConditionInput] = useState("");

  const hookPointLabels: Record<string, string> = {
    beforeCommit: t.customize.hookPointBeforeCommit,
    beforeToolUse: t.customize.hookPointBeforeToolUse,
    afterToolUse: t.customize.hookPointAfterToolUse,
    beforeLLMCall: t.customize.hookPointBeforeLLMCall,
    afterLLMCall: t.customize.hookPointAfterLLMCall,
    beforeTurnStart: t.customize.hookPointBeforeTurnStart,
    afterTurnEnd: t.customize.hookPointAfterTurnEnd,
  };

  const presetConditions: SavedCondition[] = [
    { id: "research", label: t.customize.conditionResearch, technical: "research.sourceSensitive", isPreset: true },
    { id: "coding", label: t.customize.conditionCoding, technical: "coding.implementation", isPreset: true },
    { id: "fileCreate", label: t.customize.conditionFileCreate, technical: "output.fileCreated", isPreset: true },
    { id: "externalAction", label: t.customize.conditionExternalAction, technical: "action.external", isPreset: true },
    { id: "longTask", label: t.customize.conditionLongTask, technical: "task.longRunning", isPreset: true },
    { id: "always", label: t.customize.conditionAlways, technical: "always", isPreset: true },
  ];

  const failBehaviorLabels: Record<string, string> = {
    blockAndRetry: t.customize.failBlockAndRetry,
    askUser: t.customize.failAskUser,
    warnOnly: t.customize.failWarnOnly,
    recordOnly: t.customize.failRecordOnly,
  };

  const allConditions = [...presetConditions, ...savedConditions];
  const activeRules = rules.split("\n").filter((l) => l.trim().length > 0);
  const currentStepIdx = BUILDER_STEPS.indexOf(step);

  const resetBuilder = useCallback(() => {
    setSelectedHookPoint("beforeCommit");
    setSelectedCondition(null);
    setSelectedCheck(null);
    setCustomCheckInput("");
    setFailBehavior("blockAndRetry");
    setFailOpen(true);
    setNewConditionInput("");
  }, []);

  const closeModal = useCallback(() => { setStep("list"); onClose(); }, [onClose]);

  const buildRuleText = (): string => {
    const condTech = selectedCondition ? allConditions.find((c) => c.id === selectedCondition)?.technical ?? "" : "";
    const checkTech = selectedCheck ? savedChecks.find((c) => c.id === selectedCheck)?.technical ?? customCheckInput : customCheckInput;
    const parts = [`[${selectedHookPoint}]`];
    if (condTech && condTech !== "always") parts.push(`when ${condTech},`);
    parts.push(checkTech + ".");
    if (failBehavior === "blockAndRetry") parts.push("If the check fails, block and retry.");
    else if (failBehavior === "askUser") parts.push("If the check fails, ask the user.");
    else if (failBehavior === "warnOnly") parts.push("If the check fails, warn only.");
    else parts.push("Record result only.");
    parts.push(failOpen ? "If verification errors, pass through." : "If verification errors, block.");
    return parts.join(" ");
  };

  const addRule = (): void => {
    const rule = buildRuleText();
    if (!hasRule(rules, rule)) {
      const prefix = rules.trim().length > 0 ? `${rules.trimEnd()}\n` : "";
      setRules(`${prefix}- ${rule}`);
    }
    resetBuilder();
    setStep("list");
  };

  const removeRule = (idx: number): void => {
    let ruleIdx = 0;
    setRules(rules.split("\n").filter((line) => { if (line.trim().length === 0) return true; return ruleIdx++ !== idx; }).join("\n").trim());
  };

  const handleSave = async (): Promise<void> => {
    setSaving(true); setSuccess(null);
    try {
      const res = await authFetch(`/api/bots/${botId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ agent_rules: rules, ...(presetDirty ? { agent_config: { builtin_presets: presetConfigs } } : {}) }) });
      if (!res.ok) throw new Error();
      setSuccess(t.customize.ruleSaved);
    } catch { /* ignore */ } finally { setSaving(false); }
  };

  if (!open) return null;

  return (
    <Modal open={open} onClose={closeModal} className="!max-w-2xl !max-h-[90vh]">
      <div className="p-5">
        {/* Header */}
        <div className="flex items-start justify-between mb-0.5">
          <h2 className="text-base font-semibold text-foreground">{t.customize.ruleModalTitle}</h2>
          <button type="button" onClick={closeModal} className="text-secondary hover:text-foreground transition-colors p-1 -mr-1 -mt-1">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
        <p className="text-[11px] text-secondary mb-4">{t.customize.ruleModalDesc}</p>

        {step !== "list" && <StepIndicator current={currentStepIdx} total={BUILDER_STEPS.length} />}

        {/* ─── List ─── */}
        {step === "list" && (
          <div className="space-y-4">
            {/* Categorized verification presets */}
            <div className="space-y-0.5 max-h-[65vh] overflow-y-auto pr-1">
              {PRESET_CATEGORY_DEFS.map((cat) => {
                const presets = cat.presetIds.map((id) => PRESET_MAP.get(id)!).filter(Boolean);
                const catEnabled = presets.filter((p) => presetConfigs[p.id]?.enabled).length;
                const c = t.customize as Record<string, string>;
                return (
                  <details key={cat.id} className="group">
                    <summary className="flex items-center gap-2 px-3 py-2 rounded-xl cursor-pointer hover:bg-black/[0.02] transition-colors select-none">
                      <span className="text-sm">{cat.icon}</span>
                      <span className="text-xs font-semibold text-foreground flex-1">{c[cat.titleKey] ?? cat.titleKey}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-medium">
                        {catEnabled}/{presets.length}
                      </span>
                      <svg className="w-3.5 h-3.5 text-secondary transition-transform group-open:rotate-180" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                      </svg>
                    </summary>
                    <div className="pl-2 pr-1 pb-2 space-y-1.5 mt-1">
                      {presets.map((preset) => {
                        const config = presetConfigs[preset.id] ?? { enabled: preset.defaultEnabled, mode: "hybrid" as const };
                        return (
                          <div
                            key={preset.id}
                            className={`rounded-xl border px-3 py-2.5 transition-all ${config.enabled ? "border-emerald-200 bg-emerald-50/20" : "border-black/[0.04] bg-white"}`}
                          >
                            <div className="flex items-center gap-2.5">
                              <span className="text-sm shrink-0">{preset.icon}</span>
                              <div className="flex-1 min-w-0">
                                <p className="text-[13px] font-medium text-foreground">{c[preset.titleKey] ?? preset.titleKey}</p>
                                <p className="text-[11px] text-secondary leading-snug">{c[preset.descKey] ?? preset.descKey}</p>
                              </div>
                              <button
                                type="button"
                                onClick={() => {
                                  setPresetConfigs((prev) => ({ ...prev, [preset.id]: { ...config, enabled: !config.enabled } }));
                                  setPresetDirty(true);
                                }}
                                className={`shrink-0 w-8 h-[18px] rounded-full transition-colors ${config.enabled ? "bg-emerald-500" : "bg-black/10"}`}
                              >
                                <div className={`w-3 h-3 rounded-full bg-white mt-[3px] transition-transform ${config.enabled ? "translate-x-[14px]" : "translate-x-[3px]"}`} />
                              </button>
                            </div>
                            {config.enabled && (
                              <div className="mt-1.5 flex gap-1 ml-7">
                                {(["hybrid", "deterministic", "llm"] as const).map((mode) => (
                                  <button
                                    key={mode}
                                    type="button"
                                    onClick={() => {
                                      setPresetConfigs((prev) => ({ ...prev, [preset.id]: { ...config, mode } }));
                                      setPresetDirty(true);
                                    }}
                                    className={`text-[10px] px-2 py-0.5 rounded-md transition-all ${
                                      config.mode === mode
                                        ? "bg-emerald-500 text-white font-medium"
                                        : "bg-white border border-black/[0.06] text-secondary hover:border-black/[0.12]"
                                    }`}
                                  >
                                    {c[MODE_KEYS[mode]] ?? mode}
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </details>
                );
              })}

              {/* Custom rules category */}
              <details className="group" open={activeRules.length > 0}>
                <summary className="flex items-center gap-2 px-3 py-2 rounded-xl cursor-pointer hover:bg-black/[0.02] transition-colors select-none">
                  <span className="text-sm">✏️</span>
                  <span className="text-xs font-semibold text-foreground flex-1">{(t.customize as Record<string, string>).presetCatCustom ?? "Custom"}</span>
                  {activeRules.length > 0 && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-50 text-blue-600 font-medium">
                      {activeRules.length}
                    </span>
                  )}
                </summary>
                <div className="pl-2 pr-1 pb-2 space-y-1.5 mt-1">
                  {activeRules.length > 0 ? activeRules.map((rule, idx) => (
                    <div key={idx} className="rounded-xl border border-black/[0.04] bg-white px-3 py-2.5 flex items-start gap-2.5">
                      <div className="w-5 h-5 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-0.5">
                        <span className="text-[10px] font-bold text-primary">{idx + 1}</span>
                      </div>
                      <p className="flex-1 text-[13px] text-foreground leading-relaxed">{rule.replace(/^\s*[-*+]\s+/, "")}</p>
                      <button type="button" onClick={() => removeRule(idx)} className="text-secondary hover:text-red-500 shrink-0 mt-0.5 transition-colors">
                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
                      </button>
                    </div>
                  )) : (
                    <p className="text-[11px] text-secondary px-3 py-2">{(t.customize as Record<string, string>).presetCustomEmpty ?? "No custom rules yet"}</p>
                  )}
                </div>
              </details>

              {/* Security always-on */}
              <details className="group">
                <summary className="flex items-center gap-2 px-3 py-2 rounded-xl cursor-pointer hover:bg-black/[0.02] transition-colors select-none">
                  <span className="text-sm">🔒</span>
                  <span className="text-xs font-semibold text-secondary flex-1">{(t.customize as Record<string, string>).presetCatSecurity ?? "Security (always on)"}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500 font-medium">
                    {SECURITY_HOOK_KEYS.length}/{SECURITY_HOOK_KEYS.length}
                  </span>
                </summary>
                <div className="pl-2 pr-1 pb-2 mt-1 flex flex-wrap gap-1.5">
                  {SECURITY_HOOK_KEYS.map((h) => (
                    <span key={h.key} className="text-[11px] text-secondary/70 bg-black/[0.03] rounded-lg px-2.5 py-1">
                      {h.icon} {(t.customize as Record<string, string>)[h.key] ?? h.key}
                    </span>
                  ))}
                </div>
              </details>
            </div>

            {/* Custom rules as category */}
            <Button variant="secondary" className="!w-full !border-dashed !border-2 !mt-2" onClick={() => { resetBuilder(); setStep("hookPoint"); }}>
              + {t.customize.ruleAdd}
            </Button>

            {(activeRules.length > 0 || presetDirty) && (
              <Button variant="cta" className="!w-full" onClick={() => void handleSave()} disabled={saving}>
                {saving ? t.customize.ruleSaving : t.customize.ruleSave}
              </Button>
            )}
            {success && <p className="text-xs text-emerald-600 text-center">{success}</p>}
          </div>
        )}

        {/* ─── Step 1: Hook Point ─── */}
        {step === "hookPoint" && (
          <div className="space-y-3">
            <p className="text-sm font-semibold text-foreground">{t.customize.stepWhen}</p>
            <p className="text-xs text-secondary mb-1">{t.customize.stepWhenDesc}</p>
            <div className="space-y-1.5">
              {HOOK_POINTS.map((hp) => (
                <OptionCard key={hp.value} onClick={() => { setSelectedHookPoint(hp.value); setStep("condition"); }}>
                  <div className="w-8 h-8 rounded-xl bg-black/5 flex items-center justify-center shrink-0"><span className="text-base">{hp.icon}</span></div>
                  <span className="text-sm font-medium text-foreground flex-1">{hookPointLabels[hp.value]}</span>
                  <svg className="w-4 h-4 text-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
                </OptionCard>
              ))}
            </div>
          </div>
        )}

        {/* ─── Step 2: Condition ─── */}
        {step === "condition" && (
          <div className="space-y-3">
            <p className="text-sm font-semibold text-foreground">{t.customize.stepCondition}</p>
            <p className="text-xs text-secondary mb-1">{t.customize.stepConditionDesc}</p>
            <div className="space-y-1.5">
              {allConditions.map((cond) => (
                <OptionCard key={cond.id} onClick={() => { setSelectedCondition(cond.id); setStep("check"); }}>
                  <span className="text-sm font-medium text-foreground flex-1">{cond.label}</span>
                  {!cond.isPreset && <span className="text-[10px] px-2 py-0.5 rounded-full bg-primary/10 text-primary font-medium">{t.customize.conditionCustomBadge}</span>}
                  <svg className="w-4 h-4 text-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
                </OptionCard>
              ))}
            </div>
            <div className="flex gap-2 pt-1">
              <input
                type="text"
                value={newConditionInput}
                onChange={(e) => setNewConditionInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && newConditionInput.trim()) { const id = `custom_${Date.now()}`; setSavedConditions((prev) => [...prev, { id, label: newConditionInput.trim(), technical: newConditionInput.trim(), isPreset: false }]); setSelectedCondition(id); setNewConditionInput(""); setStep("check"); } }}
                placeholder={t.customize.conditionCustomPlaceholder}
                className="flex-1 bg-white border border-black/10 rounded-xl px-4 py-3 text-sm text-foreground placeholder:text-gray-400 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors duration-200"
              />
              <Button variant="secondary" size="sm" disabled={!newConditionInput.trim()} onClick={() => { if (!newConditionInput.trim()) return; const id = `custom_${Date.now()}`; setSavedConditions((prev) => [...prev, { id, label: newConditionInput.trim(), technical: newConditionInput.trim(), isPreset: false }]); setSelectedCondition(id); setNewConditionInput(""); setStep("check"); }}>
                {t.customize.conditionCustomAdd}
              </Button>
            </div>
            <BackButton onClick={() => setStep("hookPoint")} label={t.customize.stepBack} />
          </div>
        )}

        {/* ─── Step 3: Check (input only, no presets) ─── */}
        {step === "check" && (
          <div className="space-y-4">
            <p className="text-sm font-semibold text-foreground">{t.customize.stepCheck}</p>
            <p className="text-xs text-secondary">{t.customize.stepCheckDesc}</p>

            <Textarea
              value={customCheckInput}
              onChange={(e) => { setCustomCheckInput(e.target.value); setSelectedCheck(null); }}
              placeholder={t.customize.checkCustomPlaceholder}
              rows={3}
            />

            {savedChecks.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-xs text-secondary font-medium">{t.customize.checkSavedLabel}</p>
                {savedChecks.map((chk) => (
                  <OptionCard key={chk.id} selected={selectedCheck === chk.id} onClick={() => { setSelectedCheck(chk.id); setCustomCheckInput(chk.label); }}>
                    <span className="text-sm text-foreground flex-1">{chk.label}</span>
                  </OptionCard>
                ))}
              </div>
            )}

            <div className="flex items-center justify-between pt-1">
              <BackButton onClick={() => setStep("condition")} label={t.customize.stepBack} />
              <Button
                variant="primary"
                onClick={() => {
                  if (customCheckInput.trim() && !selectedCheck) {
                    const id = `custom_${Date.now()}`;
                    setSavedChecks((prev) => [...prev, { id, label: customCheckInput.trim(), technical: customCheckInput.trim() }]);
                    setSelectedCheck(id);
                  }
                  setStep("failBehavior");
                }}
                disabled={!customCheckInput.trim()}
              >
                {t.customize.stepNext}
              </Button>
            </div>
          </div>
        )}

        {/* ─── Step 4: Fail + Error ─── */}
        {step === "failBehavior" && (
          <div className="space-y-5">
            <div>
              <p className="text-sm font-semibold text-foreground">{t.customize.stepFail}</p>
              <p className="text-xs text-secondary mt-0.5">{t.customize.stepFailDesc}</p>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {FAIL_BEHAVIORS.map((fb) => (
                <button
                  key={fb.value}
                  type="button"
                  onClick={() => setFailBehavior(fb.value)}
                  className={`glass flex flex-col items-center gap-2 rounded-2xl px-4 py-4 transition-all duration-200 ${
                    failBehavior === fb.value ? "!border-primary/30 !bg-primary/[0.04]" : "hover:border-primary/20"
                  }`}
                >
                  <span className="text-xl">{fb.icon}</span>
                  <span className="text-xs font-medium text-center">{failBehaviorLabels[fb.value]}</span>
                </button>
              ))}
            </div>

            <div>
              <p className="text-sm font-semibold text-foreground mb-2">{t.customize.stepError}</p>
              <div className="grid grid-cols-2 gap-2">
                <button type="button" onClick={() => setFailOpen(true)} className={`glass flex flex-col items-center gap-1.5 rounded-2xl px-4 py-3.5 transition-all duration-200 ${failOpen ? "!border-primary/30 !bg-primary/[0.04]" : "hover:border-primary/20"}`}>
                  <span className="text-lg">✅</span><span className="text-xs font-medium">{t.customize.failOpenLabel}</span>
                </button>
                <button type="button" onClick={() => setFailOpen(false)} className={`glass flex flex-col items-center gap-1.5 rounded-2xl px-4 py-3.5 transition-all duration-200 ${!failOpen ? "!border-primary/30 !bg-primary/[0.04]" : "hover:border-primary/20"}`}>
                  <span className="text-lg">🚫</span><span className="text-xs font-medium">{t.customize.failClosedLabel}</span>
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between pt-1">
              <BackButton onClick={() => setStep("check")} label={t.customize.stepBack} />
              <Button variant="primary" onClick={() => setStep("preview")}>{t.customize.stepNext}</Button>
            </div>
          </div>
        )}

        {/* ─── Step 5: Preview ─── */}
        {step === "preview" && (
          <div className="space-y-4">
            <p className="text-sm font-semibold text-foreground">{t.customize.stepPreview}</p>
            <p className="text-xs text-secondary">{t.customize.stepPreviewDesc}</p>

            <div className="glass rounded-2xl p-5 !border-primary/15 space-y-3">
              <div className="flex items-center gap-2 flex-wrap">
                {[
                  hookPointLabels[selectedHookPoint],
                  selectedCondition ? allConditions.find((c) => c.id === selectedCondition)?.label : null,
                  customCheckInput,
                  failBehaviorLabels[failBehavior],
                ].filter(Boolean).map((label, i, arr) => (
                  <span key={i} className="flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground">{label}</span>
                    {i < arr.length - 1 && <span className="text-secondary">→</span>}
                  </span>
                ))}
              </div>
              <p className="text-xs text-secondary">{failOpen ? t.customize.failOpenLabel : t.customize.failClosedLabel}</p>
            </div>

            <details className="glass rounded-2xl overflow-hidden">
              <summary className="cursor-pointer px-4 py-2.5 text-xs font-medium text-secondary hover:text-foreground transition-colors">{t.customize.previewTechnical}</summary>
              <div className="border-t border-black/5 px-4 py-3">
                <p className="text-xs text-foreground font-mono leading-relaxed break-all">{buildRuleText()}</p>
              </div>
            </details>

            <div className="flex items-center justify-between pt-1">
              <BackButton onClick={() => setStep("failBehavior")} label={t.customize.stepBack} />
              <Button variant="cta" onClick={addRule}>{t.customize.ruleConfirmAdd}</Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
