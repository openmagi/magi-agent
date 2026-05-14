import { useState, useCallback } from "react";
import { ButtonLike } from "../shared";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface BuiltinPresetConfig {
  enabled: boolean;
  mode: "hybrid" | "deterministic" | "llm";
}

interface VerificationPreset {
  id: string;
  title: string;
  description: string;
  defaultEnabled: boolean;
}

interface PresetCategory {
  id: string;
  title: string;
  presets: VerificationPreset[];
}

export interface VerificationRuleModalProps {
  open: boolean;
  onClose: () => void;
  getJson: (path: string) => Promise<Record<string, unknown>>;
  sendJson: (
    path: string,
    body: Record<string, unknown>,
  ) => Promise<Record<string, unknown>>;
}

/* ------------------------------------------------------------------ */
/*  Data                                                               */
/* ------------------------------------------------------------------ */

const PRESET_CATEGORIES: PresetCategory[] = [
  {
    id: "answer",
    title: "Answer Quality",
    presets: [
      {
        id: "answer-quality",
        title: "Answer Completeness",
        description: "Verify the response actually answers the question",
        defaultEnabled: true,
      },
      {
        id: "completion-evidence",
        title: "Completion Evidence",
        description:
          "Check that completion claims have actual evidence backing them",
        defaultEnabled: true,
      },
      {
        id: "pre-refusal",
        title: "Pre-Refusal Guard",
        description: "Prevent premature refusal of achievable tasks",
        defaultEnabled: true,
      },
      {
        id: "output-purity",
        title: "Output Format",
        description:
          "Ensure no raw JSON or internal data leaks into the response",
        defaultEnabled: true,
      },
      {
        id: "deferral-blocker",
        title: "Deferral Blocker",
        description:
          "Block promises to do work later; force immediate completion",
        defaultEnabled: true,
      },
    ],
  },
  {
    id: "fact",
    title: "Fact Verification",
    presets: [
      {
        id: "fact-grounding",
        title: "Fact Cross-Verification",
        description: "Cross-check tool results against response claims",
        defaultEnabled: false,
      },
      {
        id: "self-claim",
        title: "File Claim Verification",
        description: "Block claims about file contents without reading first",
        defaultEnabled: true,
      },
      {
        id: "resource-existence",
        title: "Resource Existence Check",
        description: "Verify referenced files actually exist",
        defaultEnabled: true,
      },
      {
        id: "claim-citation",
        title: "Claim Citation Gate",
        description: "Require sources for factual claims",
        defaultEnabled: true,
      },
      {
        id: "deterministic-evidence",
        title: "Numeric Evidence",
        description:
          "Verify numbers and dates are backed by tool evidence",
        defaultEnabled: true,
      },
    ],
  },
  {
    id: "coding",
    title: "Coding",
    presets: [
      {
        id: "coding-verification",
        title: "Coding Result Verification",
        description: "Confirm tests/build pass after code changes",
        defaultEnabled: true,
      },
      {
        id: "coding-context",
        title: "Coding Context",
        description: "Auto-inject repo map and symbols for code tasks",
        defaultEnabled: true,
      },
      {
        id: "coding-workspace-lock",
        title: "Workspace Lock",
        description: "Prevent unrelated file modifications during coding",
        defaultEnabled: true,
      },
      {
        id: "coding-child-review",
        title: "Subagent Review",
        description: "Auto-review subagent code output",
        defaultEnabled: true,
      },
      {
        id: "benchmark-verifier",
        title: "Benchmark Verification",
        description: "Detect and block performance regressions",
        defaultEnabled: false,
      },
    ],
  },
  {
    id: "task",
    title: "Task Management",
    presets: [
      {
        id: "task-contract",
        title: "Task Contract",
        description: "Enforce goal > plan > evidence lifecycle",
        defaultEnabled: true,
      },
      {
        id: "goal-progress",
        title: "Goal Progress",
        description: "Block completion claims without actual progress",
        defaultEnabled: true,
      },
      {
        id: "task-board-completion",
        title: "Task Board Completion",
        description:
          "Block completion when unfinished tasks remain on the board",
        defaultEnabled: true,
      },
    ],
  },
  {
    id: "output",
    title: "Output & Delivery",
    presets: [
      {
        id: "output-delivery",
        title: "Output Delivery",
        description: "Verify created files are actually delivered",
        defaultEnabled: true,
      },
      {
        id: "artifact-delivery",
        title: "Artifact Delivery",
        description: "Verify promised artifacts are actually delivered",
        defaultEnabled: true,
      },
      {
        id: "response-language",
        title: "Response Language",
        description: "Ensure responses follow the configured language policy",
        defaultEnabled: true,
      },
    ],
  },
  {
    id: "research",
    title: "Research",
    presets: [
      {
        id: "parallel-research",
        title: "Parallel Research",
        description: "Verify and cross-check research sources",
        defaultEnabled: true,
      },
      {
        id: "source-authority",
        title: "Source Authority",
        description: "Enforce memory vs live source priority",
        defaultEnabled: true,
      },
    ],
  },
  {
    id: "memory",
    title: "Memory",
    presets: [
      {
        id: "memory-continuity",
        title: "Memory Continuity",
        description: "Maintain cross-session memory consistency",
        defaultEnabled: true,
      },
    ],
  },
];

const ALL_PRESETS = PRESET_CATEGORIES.flatMap((c) => c.presets);

const SECURITY_HOOKS = [
  { title: "Dangerous Command Blocking" },
  { title: "Path Traversal Prevention" },
  { title: "Secret Exposure Prevention" },
  { title: "Git Safety Guard" },
  { title: "Sealed File Protection" },
  { title: "Permission-Based Command Control" },
];

const MODE_LABELS: Record<string, string> = {
  hybrid: "Hybrid",
  deterministic: "Deterministic",
  llm: "LLM Verify",
};

const HOOK_POINTS = [
  { value: "beforeCommit", label: "Before Commit" },
  { value: "beforeToolUse", label: "Before Tool Use" },
  { value: "afterToolUse", label: "After Tool Use" },
  { value: "beforeLLMCall", label: "Before LLM Call" },
  { value: "afterLLMCall", label: "After LLM Call" },
  { value: "beforeTurnStart", label: "Before Turn Start" },
  { value: "afterTurnEnd", label: "After Turn End" },
] as const;

const FAIL_BEHAVIORS = [
  { value: "blockAndRetry", label: "Block & Retry" },
  { value: "askUser", label: "Ask User" },
  { value: "warnOnly", label: "Warn Only" },
  { value: "recordOnly", label: "Record Only" },
] as const;

const PRESET_CONDITIONS = [
  {
    id: "research",
    label: "Research / source-sensitive turn",
    technical: "research.sourceSensitive",
  },
  {
    id: "coding",
    label: "Coding implementation turn",
    technical: "coding.implementation",
  },
  {
    id: "fileCreate",
    label: "After file creation",
    technical: "output.fileCreated",
  },
  {
    id: "externalAction",
    label: "Before external action",
    technical: "action.external",
  },
  {
    id: "longTask",
    label: "During long-running task",
    technical: "task.longRunning",
  },
  { id: "always", label: "Always (every turn)", technical: "always" },
];

type Step = "list" | "hookPoint" | "condition" | "check" | "failBehavior" | "preview";
const BUILDER_STEPS: Step[] = [
  "hookPoint",
  "condition",
  "check",
  "failBehavior",
  "preview",
];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function normalizeRule(rule: string): string {
  return rule
    .replace(/\s+/g, " ")
    .replace(/[.]+$/g, "")
    .toLowerCase()
    .trim();
}

function hasRule(rules: string, rule: string): boolean {
  const needle = normalizeRule(rule);
  if (!needle) return false;
  return rules
    .split("\n")
    .some(
      (line) => normalizeRule(line.replace(/^\s*[-*+]\s+/, "")) === needle,
    );
}

function defaultPresetConfigs(
  initial?: Record<string, BuiltinPresetConfig>,
): Record<string, BuiltinPresetConfig> {
  const configs: Record<string, BuiltinPresetConfig> = {};
  for (const p of ALL_PRESETS) {
    configs[p.id] = initial?.[p.id] ?? {
      enabled: p.defaultEnabled,
      mode: "hybrid",
    };
  }
  return configs;
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function StepIndicator({
  current,
  total,
}: {
  current: number;
  total: number;
}) {
  return (
    <div className="mb-6 flex items-center gap-1.5">
      {Array.from({ length: total }, (_, i) => (
        <div
          key={i}
          className={`h-1 rounded-full transition-all duration-300 ${i <= current ? "w-6 bg-primary" : "w-4 bg-white/10"}`}
        />
      ))}
    </div>
  );
}

function BackButton({
  onClick,
  label,
}: {
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex cursor-pointer items-center gap-1 text-xs text-secondary transition-colors hover:text-foreground"
    >
      <svg
        className="h-3.5 w-3.5"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M15 19l-7-7 7-7"
        />
      </svg>
      {label}
    </button>
  );
}

function OptionCard({
  selected,
  onClick,
  children,
}: {
  selected?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full cursor-pointer items-center gap-3 rounded-2xl border border-white/10 bg-white/5 px-4 py-3.5 text-left backdrop-blur-xl transition-all duration-200 hover:border-primary/20 hover:bg-white/[0.08] active:scale-[0.99] ${
        selected ? "!border-primary/30 !bg-primary/[0.06]" : ""
      }`}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  Modal                                                              */
/* ------------------------------------------------------------------ */

export function VerificationRuleModal({
  open,
  onClose,
  getJson,
  sendJson,
}: VerificationRuleModalProps) {
  const [rules, setRules] = useState("");
  const [presetConfigs, setPresetConfigs] = useState<
    Record<string, BuiltinPresetConfig>
  >(() => defaultPresetConfigs());
  const [presetDirty, setPresetDirty] = useState(false);
  const [step, setStep] = useState<Step>("list");
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);

  const [selectedHookPoint, setSelectedHookPoint] = useState("beforeCommit");
  const [selectedCondition, setSelectedCondition] = useState<string | null>(
    null,
  );
  const [customCheckInput, setCustomCheckInput] = useState("");
  const [failBehavior, setFailBehavior] = useState("blockAndRetry");
  const [failOpen, setFailOpen] = useState(true);

  const [savedConditions, setSavedConditions] = useState<
    Array<{ id: string; label: string; technical: string; isPreset: boolean }>
  >([]);
  const [newConditionInput, setNewConditionInput] = useState("");

  const allConditions = [
    ...PRESET_CONDITIONS.map((c) => ({ ...c, isPreset: true })),
    ...savedConditions,
  ];
  const activeRules = rules.split("\n").filter((l) => l.trim().length > 0);
  const currentStepIdx = BUILDER_STEPS.indexOf(step);

  const resetBuilder = useCallback(() => {
    setSelectedHookPoint("beforeCommit");
    setSelectedCondition(null);
    setCustomCheckInput("");
    setFailBehavior("blockAndRetry");
    setFailOpen(true);
    setNewConditionInput("");
  }, []);

  const closeModal = useCallback(() => {
    setStep("list");
    onClose();
  }, [onClose]);

  const hookPointLabel = (value: string): string =>
    HOOK_POINTS.find((hp) => hp.value === value)?.label ?? value;

  const failBehaviorLabel = (value: string): string =>
    FAIL_BEHAVIORS.find((fb) => fb.value === value)?.label ?? value;

  const buildRuleText = (): string => {
    const condTech = selectedCondition
      ? (allConditions.find((c) => c.id === selectedCondition)?.technical ?? "")
      : "";
    const parts = [`[${selectedHookPoint}]`];
    if (condTech && condTech !== "always") parts.push(`when ${condTech},`);
    parts.push(customCheckInput + ".");
    if (failBehavior === "blockAndRetry")
      parts.push("If the check fails, block and retry.");
    else if (failBehavior === "askUser")
      parts.push("If the check fails, ask the user.");
    else if (failBehavior === "warnOnly")
      parts.push("If the check fails, warn only.");
    else parts.push("Record result only.");
    parts.push(
      failOpen
        ? "If verification errors, pass through."
        : "If verification errors, block.",
    );
    return parts.join(" ");
  };

  const addRule = (): void => {
    const rule = buildRuleText();
    if (!hasRule(rules, rule)) {
      const prefix =
        rules.trim().length > 0 ? `${rules.trimEnd()}\n` : "";
      setRules(`${prefix}- ${rule}`);
    }
    resetBuilder();
    setStep("list");
  };

  const removeRule = (idx: number): void => {
    let ruleIdx = 0;
    setRules(
      rules
        .split("\n")
        .filter((line) => {
          if (line.trim().length === 0) return true;
          return ruleIdx++ !== idx;
        })
        .join("\n")
        .trim(),
    );
  };

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    setSuccess(null);
    try {
      await sendJson("/v1/config", {
        agent_rules: rules,
        ...(presetDirty ? { builtin_presets: presetConfigs } : {}),
      });
      setSuccess("Verification rules saved.");
    } catch {
      /* ignore */
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="mx-4 max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-white/10 bg-[#0f0f14] p-6 shadow-2xl">
        {/* Header */}
        <div className="mb-1 flex items-start justify-between">
          <h2 className="text-lg font-semibold text-foreground">
            Verification Rules
          </h2>
          <button
            type="button"
            onClick={closeModal}
            className="cursor-pointer p-1 text-secondary transition-colors hover:text-foreground"
          >
            <svg
              className="h-5 w-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>
        <p className="mb-6 text-xs text-secondary">
          Configure verification engines and custom rules that check every
          response before delivery.
        </p>

        {step !== "list" && (
          <StepIndicator current={currentStepIdx} total={BUILDER_STEPS.length} />
        )}

        {/* ---- List view ---- */}
        {step === "list" && (
          <div className="space-y-4">
            {/* Categorized verification presets */}
            <div className="max-h-[50vh] space-y-1 overflow-y-auto pr-1">
              {PRESET_CATEGORIES.map((cat) => {
                const catEnabled = cat.presets.filter(
                  (p) => presetConfigs[p.id]?.enabled,
                ).length;
                return (
                  <details
                    key={cat.id}
                    className="group"
                    open={cat.id === "answer" || cat.id === "fact"}
                  >
                    <summary className="flex cursor-pointer select-none items-center gap-2 rounded-xl px-3 py-2 transition-colors hover:bg-white/[0.03]">
                      <span className="flex-1 text-xs font-semibold text-foreground">
                        {cat.title}
                      </span>
                      <span className="rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-400">
                        {catEnabled}/{cat.presets.length}
                      </span>
                      <svg
                        className="h-3.5 w-3.5 text-secondary transition-transform group-open:rotate-180"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M19 9l-7 7-7-7"
                        />
                      </svg>
                    </summary>
                    <div className="mt-1 space-y-1.5 pb-2 pl-2 pr-1">
                      {cat.presets.map((preset) => {
                        const config = presetConfigs[preset.id] ?? {
                          enabled: preset.defaultEnabled,
                          mode: "hybrid" as const,
                        };
                        return (
                          <div
                            key={preset.id}
                            className={`rounded-xl border px-3 py-2.5 transition-all ${
                              config.enabled
                                ? "border-emerald-500/20 bg-emerald-500/5"
                                : "border-white/[0.06] bg-white/[0.02]"
                            }`}
                          >
                            <div className="flex items-center gap-2.5">
                              <div className="min-w-0 flex-1">
                                <p className="text-[13px] font-medium text-foreground">
                                  {preset.title}
                                </p>
                                <p className="text-[11px] leading-snug text-secondary">
                                  {preset.description}
                                </p>
                              </div>
                              <button
                                type="button"
                                onClick={() => {
                                  setPresetConfigs((prev) => ({
                                    ...prev,
                                    [preset.id]: {
                                      ...config,
                                      enabled: !config.enabled,
                                    },
                                  }));
                                  setPresetDirty(true);
                                }}
                                className={`h-[18px] w-8 shrink-0 cursor-pointer rounded-full transition-colors ${
                                  config.enabled
                                    ? "bg-emerald-500"
                                    : "bg-white/10"
                                }`}
                              >
                                <div
                                  className={`mt-[3px] h-3 w-3 rounded-full bg-white transition-transform ${
                                    config.enabled
                                      ? "translate-x-[14px]"
                                      : "translate-x-[3px]"
                                  }`}
                                />
                              </button>
                            </div>
                            {config.enabled && (
                              <div className="ml-0 mt-1.5 flex gap-1">
                                {(
                                  ["hybrid", "deterministic", "llm"] as const
                                ).map((mode) => (
                                  <button
                                    key={mode}
                                    type="button"
                                    onClick={() => {
                                      setPresetConfigs((prev) => ({
                                        ...prev,
                                        [preset.id]: { ...config, mode },
                                      }));
                                      setPresetDirty(true);
                                    }}
                                    className={`cursor-pointer rounded-md px-2 py-0.5 text-[10px] transition-all ${
                                      config.mode === mode
                                        ? "bg-emerald-500 font-medium text-white"
                                        : "border border-white/[0.08] bg-white/[0.03] text-secondary hover:border-white/[0.15]"
                                    }`}
                                  >
                                    {MODE_LABELS[mode]}
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

              {/* Custom rules */}
              <details className="group" open={activeRules.length > 0}>
                <summary className="flex cursor-pointer select-none items-center gap-2 rounded-xl px-3 py-2 transition-colors hover:bg-white/[0.03]">
                  <span className="flex-1 text-xs font-semibold text-foreground">
                    Custom
                  </span>
                  {activeRules.length > 0 && (
                    <span className="rounded-full bg-blue-500/10 px-1.5 py-0.5 text-[10px] font-medium text-blue-400">
                      {activeRules.length}
                    </span>
                  )}
                </summary>
                <div className="mt-1 space-y-1.5 pb-2 pl-2 pr-1">
                  {activeRules.length > 0 ? (
                    activeRules.map((rule, idx) => (
                      <div
                        key={idx}
                        className="flex items-start gap-2.5 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5"
                      >
                        <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10">
                          <span className="text-[10px] font-bold text-primary">
                            {idx + 1}
                          </span>
                        </div>
                        <p className="flex-1 text-[13px] leading-relaxed text-foreground">
                          {rule.replace(/^\s*[-*+]\s+/, "")}
                        </p>
                        <button
                          type="button"
                          onClick={() => removeRule(idx)}
                          className="mt-0.5 shrink-0 cursor-pointer text-secondary transition-colors hover:text-red-500"
                        >
                          <svg
                            className="h-3.5 w-3.5"
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="M6 18L18 6M6 6l12 12"
                            />
                          </svg>
                        </button>
                      </div>
                    ))
                  ) : (
                    <p className="px-3 py-2 text-[11px] text-secondary">
                      No custom rules yet
                    </p>
                  )}
                </div>
              </details>

              {/* Security always-on */}
              <details className="group">
                <summary className="flex cursor-pointer select-none items-center gap-2 rounded-xl px-3 py-2 transition-colors hover:bg-white/[0.03]">
                  <span className="flex-1 text-xs font-semibold text-secondary">
                    Security (always on)
                  </span>
                  <span className="rounded-full bg-white/5 px-1.5 py-0.5 text-[10px] font-medium text-secondary">
                    {SECURITY_HOOKS.length}/{SECURITY_HOOKS.length}
                  </span>
                </summary>
                <div className="mt-1 flex flex-wrap gap-1.5 pb-2 pl-2 pr-1">
                  {SECURITY_HOOKS.map((h) => (
                    <span
                      key={h.title}
                      className="rounded-lg bg-white/[0.04] px-2.5 py-1 text-[11px] text-secondary/70"
                    >
                      {h.title}
                    </span>
                  ))}
                </div>
              </details>
            </div>

            <ButtonLike
              variant="secondary"
              className="!w-full !border-dashed !border-2"
              onClick={() => {
                resetBuilder();
                setStep("hookPoint");
              }}
            >
              + Add Custom Rule
            </ButtonLike>

            {(activeRules.length > 0 || presetDirty) && (
              <ButtonLike
                className="!w-full"
                onClick={() => void handleSave()}
                disabled={saving}
              >
                {saving ? "Saving..." : "Save Rules"}
              </ButtonLike>
            )}
            {success && (
              <p className="text-center text-xs text-emerald-400">{success}</p>
            )}
          </div>
        )}

        {/* ---- Step 1: Hook Point ---- */}
        {step === "hookPoint" && (
          <div className="space-y-3">
            <p className="text-sm font-semibold text-foreground">
              When should this rule run?
            </p>
            <p className="mb-1 text-xs text-secondary">
              Choose the hook point in the agent lifecycle.
            </p>
            <div className="space-y-1.5">
              {HOOK_POINTS.map((hp) => (
                <OptionCard
                  key={hp.value}
                  onClick={() => {
                    setSelectedHookPoint(hp.value);
                    setStep("condition");
                  }}
                >
                  <span className="flex-1 text-sm font-medium text-foreground">
                    {hp.label}
                  </span>
                  <svg
                    className="h-4 w-4 text-secondary"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M9 5l7 7-7 7"
                    />
                  </svg>
                </OptionCard>
              ))}
            </div>
          </div>
        )}

        {/* ---- Step 2: Condition ---- */}
        {step === "condition" && (
          <div className="space-y-3">
            <p className="text-sm font-semibold text-foreground">
              Under what condition?
            </p>
            <p className="mb-1 text-xs text-secondary">
              Pick a predefined condition or create a custom one.
            </p>
            <div className="space-y-1.5">
              {allConditions.map((cond) => (
                <OptionCard
                  key={cond.id}
                  onClick={() => {
                    setSelectedCondition(cond.id);
                    setStep("check");
                  }}
                >
                  <span className="flex-1 text-sm font-medium text-foreground">
                    {cond.label}
                  </span>
                  {!cond.isPreset && (
                    <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                      Custom
                    </span>
                  )}
                  <svg
                    className="h-4 w-4 text-secondary"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M9 5l7 7-7 7"
                    />
                  </svg>
                </OptionCard>
              ))}
            </div>
            <div className="flex gap-2 pt-1">
              <input
                type="text"
                value={newConditionInput}
                onChange={(e) => setNewConditionInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && newConditionInput.trim()) {
                    const id = `custom_${Date.now()}`;
                    setSavedConditions((prev) => [
                      ...prev,
                      {
                        id,
                        label: newConditionInput.trim(),
                        technical: newConditionInput.trim(),
                        isPreset: false,
                      },
                    ]);
                    setSelectedCondition(id);
                    setNewConditionInput("");
                    setStep("check");
                  }
                }}
                placeholder="Describe a custom condition..."
                className="flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-foreground placeholder:text-gray-500 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20"
              />
              <ButtonLike
                variant="secondary"
                disabled={!newConditionInput.trim()}
                onClick={() => {
                  if (!newConditionInput.trim()) return;
                  const id = `custom_${Date.now()}`;
                  setSavedConditions((prev) => [
                    ...prev,
                    {
                      id,
                      label: newConditionInput.trim(),
                      technical: newConditionInput.trim(),
                      isPreset: false,
                    },
                  ]);
                  setSelectedCondition(id);
                  setNewConditionInput("");
                  setStep("check");
                }}
              >
                Add
              </ButtonLike>
            </div>
            <BackButton onClick={() => setStep("hookPoint")} label="Back" />
          </div>
        )}

        {/* ---- Step 3: Check ---- */}
        {step === "check" && (
          <div className="space-y-4">
            <p className="text-sm font-semibold text-foreground">
              What should be checked?
            </p>
            <p className="text-xs text-secondary">
              Describe the verification logic in natural language.
            </p>
            <textarea
              value={customCheckInput}
              onChange={(e) => setCustomCheckInput(e.target.value)}
              placeholder="e.g. Verify that all file references in the response actually exist in the workspace"
              rows={3}
              className="w-full resize-y rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-foreground placeholder:text-gray-500 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20"
            />
            <div className="flex items-center justify-between pt-1">
              <BackButton onClick={() => setStep("condition")} label="Back" />
              <ButtonLike
                onClick={() => setStep("failBehavior")}
                disabled={!customCheckInput.trim()}
              >
                Next
              </ButtonLike>
            </div>
          </div>
        )}

        {/* ---- Step 4: Fail behavior ---- */}
        {step === "failBehavior" && (
          <div className="space-y-5">
            <div>
              <p className="text-sm font-semibold text-foreground">
                What happens if the check fails?
              </p>
              <p className="mt-0.5 text-xs text-secondary">
                Choose how the agent should respond to a verification failure.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {FAIL_BEHAVIORS.map((fb) => (
                <button
                  key={fb.value}
                  type="button"
                  onClick={() => setFailBehavior(fb.value)}
                  className={`flex cursor-pointer flex-col items-center gap-2 rounded-2xl border border-white/10 bg-white/5 px-4 py-4 transition-all duration-200 ${
                    failBehavior === fb.value
                      ? "!border-primary/30 !bg-primary/[0.06]"
                      : "hover:border-primary/20"
                  }`}
                >
                  <span className="text-xs font-medium">{fb.label}</span>
                </button>
              ))}
            </div>

            <div>
              <p className="mb-2 text-sm font-semibold text-foreground">
                If verification itself errors?
              </p>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setFailOpen(true)}
                  className={`flex cursor-pointer flex-col items-center gap-1.5 rounded-2xl border border-white/10 bg-white/5 px-4 py-3.5 transition-all duration-200 ${
                    failOpen
                      ? "!border-primary/30 !bg-primary/[0.06]"
                      : "hover:border-primary/20"
                  }`}
                >
                  <span className="text-xs font-medium">
                    Pass through (fail-open)
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => setFailOpen(false)}
                  className={`flex cursor-pointer flex-col items-center gap-1.5 rounded-2xl border border-white/10 bg-white/5 px-4 py-3.5 transition-all duration-200 ${
                    !failOpen
                      ? "!border-primary/30 !bg-primary/[0.06]"
                      : "hover:border-primary/20"
                  }`}
                >
                  <span className="text-xs font-medium">
                    Block (fail-closed)
                  </span>
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between pt-1">
              <BackButton onClick={() => setStep("check")} label="Back" />
              <ButtonLike onClick={() => setStep("preview")}>Next</ButtonLike>
            </div>
          </div>
        )}

        {/* ---- Step 5: Preview ---- */}
        {step === "preview" && (
          <div className="space-y-4">
            <p className="text-sm font-semibold text-foreground">
              Review your rule
            </p>
            <p className="text-xs text-secondary">
              Confirm the rule before adding it to your verification set.
            </p>

            <div className="space-y-3 rounded-2xl border border-primary/15 bg-white/5 p-5">
              <div className="flex flex-wrap items-center gap-2">
                {[
                  hookPointLabel(selectedHookPoint),
                  selectedCondition
                    ? allConditions.find((c) => c.id === selectedCondition)
                        ?.label
                    : null,
                  customCheckInput,
                  failBehaviorLabel(failBehavior),
                ]
                  .filter(Boolean)
                  .map((label, i, arr) => (
                    <span key={i} className="flex items-center gap-2">
                      <span className="text-sm font-medium text-foreground">
                        {label}
                      </span>
                      {i < arr.length - 1 && (
                        <span className="text-secondary">&rarr;</span>
                      )}
                    </span>
                  ))}
              </div>
              <p className="text-xs text-secondary">
                {failOpen ? "Pass through (fail-open)" : "Block (fail-closed)"}
              </p>
            </div>

            <details className="overflow-hidden rounded-2xl border border-white/10 bg-white/5">
              <summary className="cursor-pointer px-4 py-2.5 text-xs font-medium text-secondary transition-colors hover:text-foreground">
                Technical output
              </summary>
              <div className="border-t border-white/5 px-4 py-3">
                <p className="break-all font-mono text-xs leading-relaxed text-foreground">
                  {buildRuleText()}
                </p>
              </div>
            </details>

            <div className="flex items-center justify-between pt-1">
              <BackButton
                onClick={() => setStep("failBehavior")}
                label="Back"
              />
              <ButtonLike onClick={addRule}>Add Rule</ButtonLike>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
