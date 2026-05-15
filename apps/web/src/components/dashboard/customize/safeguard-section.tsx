"use client";

import { useState } from "react";
import { agentFetch } from "@/lib/local-api";
import type { DimensionDef } from "./classifier-section";

const MAX_CHARS = 5000;

interface BuiltinPresetConfig {
  enabled: boolean;
  mode: "hybrid" | "deterministic" | "llm";
}

interface SafeguardSectionProps {
  botId: string;
  initialRules: string | null;
  initialAgentConfig?: { builtin_presets?: Record<string, BuiltinPresetConfig> };
  classifierDimensions: DimensionDef[];
  disabled?: boolean;
}

interface SafeguardPreset {
  id: string;
  icon: string;
  title: string;
  description: string;
  ruleText: string;
}

const PRESETS: SafeguardPreset[] = [
  {
    id: "file-delivery",
    icon: "📎",
    title: "파일 만들면 꼭 첨부하기",
    description: "파일이나 보고서를 생성하면 반드시 채팅에 첨부합니다",
    ruleText: "After creating a file or document, you must attach it in chat before saying the task is complete. If the check fails, block completion and retry the missing work.",
  },
  {
    id: "source-grounding",
    icon: "🔍",
    title: "사실 확인은 출처와 함께",
    description: "사실 기반 답변에는 반드시 출처를 포함합니다",
    ruleText: "For research or factual answers, the agent checks that important claims have sources. If the check fails, block completion and retry the missing work.",
  },
  {
    id: "external-confirm",
    icon: "✉️",
    title: "이메일/결제 전에 확인받기",
    description: "외부 서비스 호출 전에 사용자 확인을 받습니다",
    ruleText: "Before sending email, uploading outside the workspace, paying, or posting, the agent asks for confirmation. If the check fails, ask the user before continuing.",
  },
  {
    id: "progress-update",
    icon: "📊",
    title: "10분 넘으면 중간보고",
    description: "긴 작업 중에는 짧은 진행 상황을 알려줍니다",
    ruleText: "During long work, the agent gives short progress updates instead of going silent.",
  },
  {
    id: "final-answer",
    icon: "✅",
    title: "최종 답변 퀄리티 체크",
    description: "최종 답변 전에 요청사항을 모두 충족했는지 확인합니다",
    ruleText: "Before the final reply, the agent checks whether it satisfied every requested deliverable. If the check fails, block completion and retry the missing work.",
  },
  {
    id: "korean-only",
    icon: "🇰🇷",
    title: "항상 한국어로 답변",
    description: "사용자의 언어와 관계없이 한국어로 답변합니다",
    ruleText: "Always respond in Korean regardless of the language used in the question.",
  },
  {
    id: "backup-before-edit",
    icon: "🔒",
    title: "코드 수정 전 백업 생성",
    description: "파일을 수정하기 전에 원본을 백업합니다",
    ruleText: "Before modifying any code file, create a backup copy first. If the backup fails, ask the user before continuing.",
  },
];

interface VerificationPreset {
  id: string;
  icon: string;
  title: string;
  description: string;
  defaultEnabled: boolean;
}

const VERIFICATION_PRESETS: VerificationPreset[] = [
  {
    id: "fact-grounding",
    icon: "🔬",
    title: "사실 검증",
    description: "도구 결과와 응답이 일치하는지 교차 검증합니다",
    defaultEnabled: false,
  },
  {
    id: "answer-quality",
    icon: "✅",
    title: "답변 품질",
    description: "응답이 질문에 실제로 답했는지 검증합니다",
    defaultEnabled: true,
  },
  {
    id: "self-claim",
    icon: "📂",
    title: "파일 claim 검증",
    description: "파일 내용을 주장하려면 먼저 읽었는지 확인합니다",
    defaultEnabled: true,
  },
  {
    id: "response-language",
    icon: "🌐",
    title: "응답 언어",
    description: "설정된 언어 정책에 맞게 응답하는지 확인합니다",
    defaultEnabled: true,
  },
  {
    id: "deterministic-evidence",
    icon: "🔢",
    title: "수치 증거 검증",
    description: "숫자/날짜 등 정확한 값은 도구 증거로 뒷받침되는지 확인합니다",
    defaultEnabled: true,
  },
];

const SECURITY_HOOKS = [
  { icon: "🛑", title: "위험 명령 차단", description: "rm -rf, force push 등 위험한 명령을 차단합니다" },
  { icon: "🔒", title: "경로 탈출 방지", description: "워크스페이스 외부 파일 접근을 차단합니다" },
  { icon: "🔑", title: "비밀키 노출 방지", description: "API 키, 비밀번호가 응답에 포함되지 않게 합니다" },
  { icon: "⚠️", title: "Git 안전 장치", description: "위험한 Git 작업을 차단합니다" },
];

const MODE_LABELS: Record<string, string> = {
  hybrid: "하이브리드",
  deterministic: "규칙 기반",
  llm: "AI 검증",
};

type BuilderCondition = "afterFileCreate" | "beforeExternalAction" | "beforeCommit" | "duringLongTask" | "beforeToolUse" | "afterToolUse" | string;
type BuilderAction = "verifyDeliverables" | "verifySources" | "requireFileDelivery" | "askConfirmation" | "sendProgressUpdate" | "customInstruction";
type BuilderEnforcement = "blockAndRetry" | "askUser" | "warnOnly" | "recordOnly";

const CONDITION_OPTIONS: { value: BuilderCondition; label: string; group?: string }[] = [
  { value: "afterFileCreate", label: "파일 생성 후" },
  { value: "beforeExternalAction", label: "외부 작업 전" },
  { value: "beforeCommit", label: "최종 답변 전" },
  { value: "duringLongTask", label: "긴 작업 중 (10분+)" },
  { value: "beforeToolUse", label: "도구 사용 전" },
  { value: "afterToolUse", label: "도구 사용 후" },
];

const ACTION_OPTIONS: { value: BuilderAction; label: string }[] = [
  { value: "requireFileDelivery", label: "파일 첨부 확인" },
  { value: "verifySources", label: "출처 확인" },
  { value: "verifyDeliverables", label: "결과물 검증" },
  { value: "askConfirmation", label: "확인받기" },
  { value: "sendProgressUpdate", label: "중간보고" },
  { value: "customInstruction", label: "직접 입력" },
];

const ENFORCEMENT_OPTIONS: { value: BuilderEnforcement; label: string }[] = [
  { value: "blockAndRetry", label: "차단 후 재시도" },
  { value: "askUser", label: "사용자에게 확인" },
  { value: "warnOnly", label: "경고만 표시" },
  { value: "recordOnly", label: "기록만" },
];

function normalizeRule(rule: string): string {
  return rule.replace(/\s+/g, " ").replace(/[.。]+$/g, "").toLowerCase().trim();
}

function hasRule(rules: string, rule: string): boolean {
  const needle = normalizeRule(rule);
  if (!needle) return false;
  return rules.split("\n").some((line) => normalizeRule(line.replace(/^\s*[-*+]\s+/, "")) === needle);
}

function buildPreviewText(condition: BuilderCondition, action: BuilderAction, enforcement: BuilderEnforcement, customText: string, conditionLabel: string): string {
  const condMap: Record<string, string> = {
    afterFileCreate: "파일 생성 후",
    beforeExternalAction: "외부 작업 전",
    beforeCommit: "최종 답변 전",
    duringLongTask: "긴 작업 중",
    beforeToolUse: "도구 사용 전",
    afterToolUse: "도구 사용 후",
  };
  const actMap: Record<string, string> = {
    requireFileDelivery: "채팅에 첨부됐는지 확인합니다",
    verifySources: "출처가 포함됐는지 확인합니다",
    verifyDeliverables: "요청사항을 모두 충족했는지 확인합니다",
    askConfirmation: "사용자에게 확인을 요청합니다",
    sendProgressUpdate: "진행 상황을 알려줍니다",
    customInstruction: customText || "커스텀 검증을 수행합니다",
  };
  const enfMap: Record<string, string> = {
    blockAndRetry: "안 되면 재시도합니다",
    askUser: "안 되면 사용자에게 물어봅니다",
    warnOnly: "안 되면 경고를 표시합니다",
    recordOnly: "결과를 기록만 합니다",
  };

  const cond = condMap[condition] || conditionLabel;
  return `${cond}, ${actMap[action]}. ${enfMap[enforcement]}.`;
}

function buildRuleText(condition: BuilderCondition, action: BuilderAction, enforcement: BuilderEnforcement, customText: string): string {
  const condMap: Record<string, string> = {
    afterFileCreate: "After creating a file or document",
    beforeExternalAction: "Before external actions",
    beforeCommit: "Before the final answer",
    duringLongTask: "During long-running work",
    beforeToolUse: "Before each tool call",
    afterToolUse: "After each tool call",
  };
  const actMap: Record<string, string> = {
    requireFileDelivery: "deliver created files in chat before saying the task is complete",
    verifySources: "verify source grounding",
    verifyDeliverables: "verify every requested deliverable",
    askConfirmation: "ask for confirmation before continuing",
    sendProgressUpdate: "provide a brief progress update",
    customInstruction: `run this custom check: ${customText || "the requested condition"}`,
  };
  const enfMap: Record<string, string> = {
    blockAndRetry: "If the check fails, block completion and retry the missing work.",
    askUser: "If the check fails, ask the user before continuing.",
    warnOnly: "If the check fails, continue only after noting the risk.",
    recordOnly: "Record the result as an audit note without blocking the task.",
  };

  const cond = condMap[condition] || `When ${condition}`;
  return `${cond}, ${actMap[action]}. ${enfMap[enforcement]}`;
}

function defaultPresetConfigs(initial?: Record<string, BuiltinPresetConfig>): Record<string, BuiltinPresetConfig> {
  const configs: Record<string, BuiltinPresetConfig> = {};
  for (const p of VERIFICATION_PRESETS) {
    configs[p.id] = initial?.[p.id] ?? { enabled: p.defaultEnabled, mode: "hybrid" };
  }
  return configs;
}

export function SafeguardSection({
  botId,
  initialRules,
  initialAgentConfig,
  classifierDimensions,
  disabled = false,
}: SafeguardSectionProps): React.ReactElement {
  const authFetch = agentFetch;
  const [expanded, setExpanded] = useState(true);
  const [rules, setRules] = useState(initialRules ?? "");
  const [presetConfigs, setPresetConfigs] = useState<Record<string, BuiltinPresetConfig>>(
    () => defaultPresetConfigs(initialAgentConfig?.builtin_presets),
  );
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [builderOpen, setBuilderOpen] = useState(false);
  const [builderCondition, setBuilderCondition] = useState<BuilderCondition>("beforeCommit");
  const [builderAction, setBuilderAction] = useState<BuilderAction>("verifyDeliverables");
  const [builderEnforcement, setBuilderEnforcement] = useState<BuilderEnforcement>("blockAndRetry");
  const [builderCustomText, setBuilderCustomText] = useState("");
  const [directEditOpen, setDirectEditOpen] = useState(false);

  const activeCount = rules.split("\n").filter((l) => l.trim().length > 0).length;

  const conditionOptions = [
    ...CONDITION_OPTIONS,
    ...(classifierDimensions.length > 0
      ? [
          { value: "__divider__", label: "── 내 분류 기준 ──", group: "classifier" },
          ...classifierDimensions.map((d) => ({
            value: `classifier:${d.name}`,
            label: `${d.description || d.name}일 때`,
            group: "classifier",
          })),
        ]
      : []),
  ];

  const conditionLabel = conditionOptions.find((o) => o.value === builderCondition)?.label ?? "";

  const togglePreset = (ruleText: string): void => {
    if (hasRule(rules, ruleText)) {
      const needle = normalizeRule(ruleText);
      setRules(
        rules
          .split("\n")
          .filter((line) => normalizeRule(line.replace(/^\s*[-*+]\s+/, "")) !== needle)
          .join("\n")
          .trim(),
      );
    } else {
      const prefix = rules.trim().length > 0 ? `${rules.trimEnd()}\n` : "";
      setRules(`${prefix}- ${ruleText}`);
    }
    setSuccess(null);
    setError(null);
  };

  const addFromBuilder = (): void => {
    const rule = buildRuleText(builderCondition, builderAction, builderEnforcement, builderCustomText);
    if (!hasRule(rules, rule)) {
      const prefix = rules.trim().length > 0 ? `${rules.trimEnd()}\n` : "";
      setRules(`${prefix}- ${rule}`);
    }
    setBuilderCondition("beforeCommit");
    setBuilderAction("verifyDeliverables");
    setBuilderEnforcement("blockAndRetry");
    setBuilderCustomText("");
    setSuccess(null);
    setError(null);
  };

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await agentFetch(`/v1/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_rules: rules,
          agent_config: { builtin_presets: presetConfigs },
        }),
      });
      if (!res.ok) throw new Error("저장에 실패했습니다");
      setSuccess("세이프가드가 저장되었습니다");
    } catch {
      setError("저장에 실패했습니다");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-2xl border border-black/[0.06] bg-white overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-50/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-blue-50 flex items-center justify-center">
            <span className="text-sm">🛡️</span>
          </div>
          <div className="text-left">
            <p className="text-sm font-semibold text-foreground">세이프가드</p>
            <p className="text-xs text-secondary">어떤 상황에서 어떻게 행동할까요?</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {activeCount > 0 && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 font-medium">
              {activeCount}
            </span>
          )}
          <svg
            className={`w-4 h-4 text-secondary transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>

      {expanded && (
        <div className="border-t border-black/[0.04] px-5 py-4 space-y-4">
          {/* Preset toggles */}
          <div className="grid gap-2 sm:grid-cols-2">
            {PRESETS.map((preset) => {
              const active = hasRule(rules, preset.ruleText);
              return (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => togglePreset(preset.ruleText)}
                  disabled={disabled}
                  className={`flex items-start gap-3 rounded-xl border px-4 py-3 text-left transition-all ${
                    active
                      ? "border-primary/30 bg-primary/[0.03]"
                      : "border-black/[0.06] bg-white hover:border-black/[0.12] hover:bg-gray-50/50"
                  } disabled:opacity-40`}
                >
                  <span className="text-lg mt-0.5 shrink-0">{preset.icon}</span>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-foreground">{preset.title}</p>
                    <p className="text-xs text-secondary mt-0.5 leading-relaxed">{preset.description}</p>
                  </div>
                  <div className={`mt-1 shrink-0 w-9 h-5 rounded-full transition-colors ${active ? "bg-primary" : "bg-gray-200"}`}>
                    <div className={`w-3.5 h-3.5 rounded-full bg-white mt-[3px] transition-transform ${active ? "translate-x-4" : "translate-x-0.5"}`} />
                  </div>
                </button>
              );
            })}
          </div>

          {/* Verification Presets */}
          <div className="space-y-2">
            <p className="text-xs font-semibold text-foreground px-1">검증 엔진</p>
            <p className="text-xs text-secondary px-1">답변 품질을 자동으로 검증하는 런타임 체크입니다. 각각 on/off 하고 검증 방식을 선택하세요.</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {VERIFICATION_PRESETS.map((preset) => {
                const config = presetConfigs[preset.id] ?? { enabled: preset.defaultEnabled, mode: "hybrid" as const };
                return (
                  <div
                    key={preset.id}
                    className={`rounded-xl border px-4 py-3 transition-all ${
                      config.enabled
                        ? "border-emerald-200 bg-emerald-50/30"
                        : "border-black/[0.06] bg-white"
                    }`}
                  >
                    <div className="flex items-start gap-3">
                      <span className="text-lg mt-0.5 shrink-0">{preset.icon}</span>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium text-foreground">{preset.title}</p>
                        <p className="text-xs text-secondary mt-0.5 leading-relaxed">{preset.description}</p>
                      </div>
                      <button
                        type="button"
                        disabled={disabled}
                        onClick={() => setPresetConfigs((prev) => ({
                          ...prev,
                          [preset.id]: { ...config, enabled: !config.enabled },
                        }))}
                        className={`mt-1 shrink-0 w-9 h-5 rounded-full transition-colors ${config.enabled ? "bg-emerald-500" : "bg-gray-200"}`}
                      >
                        <div className={`w-3.5 h-3.5 rounded-full bg-white mt-[3px] transition-transform ${config.enabled ? "translate-x-4" : "translate-x-0.5"}`} />
                      </button>
                    </div>
                    {config.enabled && (
                      <div className="mt-2 flex gap-1 ml-8">
                        {(["hybrid", "deterministic", "llm"] as const).map((mode) => (
                          <button
                            key={mode}
                            type="button"
                            disabled={disabled}
                            onClick={() => setPresetConfigs((prev) => ({
                              ...prev,
                              [preset.id]: { ...config, mode },
                            }))}
                            className={`text-[11px] px-2.5 py-1 rounded-lg transition-all ${
                              config.mode === mode
                                ? "bg-emerald-500 text-white font-medium"
                                : "bg-white border border-black/[0.08] text-secondary hover:border-black/[0.15]"
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
          </div>

          {/* Security — always on */}
          <div className="space-y-2">
            <p className="text-xs font-semibold text-foreground px-1 flex items-center gap-1.5">
              <span>🔒</span> 보안 (항상 켜짐)
            </p>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {SECURITY_HOOKS.map((hook) => (
                <div
                  key={hook.title}
                  className="flex items-start gap-2.5 rounded-xl border border-black/[0.04] bg-gray-50/30 px-3.5 py-2.5"
                >
                  <span className="text-sm mt-0.5 shrink-0">{hook.icon}</span>
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-secondary">{hook.title}</p>
                    <p className="text-[11px] text-secondary/70 mt-0.5">{hook.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Builder */}
          <div className="rounded-xl border border-black/[0.06] bg-gray-50/50 overflow-hidden">
            <button
              type="button"
              onClick={() => setBuilderOpen(!builderOpen)}
              className="w-full flex items-center justify-between px-4 py-3 hover:bg-gray-50 transition-colors"
            >
              <span className="text-sm font-medium text-foreground">직접 만들기</span>
              <svg
                className={`w-4 h-4 text-secondary transition-transform duration-200 ${builderOpen ? "rotate-180" : ""}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {builderOpen && (
              <div className="border-t border-black/[0.04] px-4 py-4 space-y-4">
                {/* 3-column combinator */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  {/* Condition */}
                  <div>
                    <label className="text-xs font-medium text-secondary mb-1.5 block">조건 (언제)</label>
                    <select
                      value={builderCondition}
                      onChange={(e) => setBuilderCondition(e.target.value)}
                      disabled={disabled}
                      className="w-full rounded-xl border border-black/[0.08] bg-white px-3 py-2.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/30 disabled:opacity-50"
                    >
                      {conditionOptions.map((opt) =>
                        opt.value === "__divider__" ? (
                          <option key={opt.value} disabled>
                            {opt.label}
                          </option>
                        ) : (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ),
                      )}
                    </select>
                  </div>

                  {/* Action */}
                  <div>
                    <label className="text-xs font-medium text-secondary mb-1.5 block">동작 (뭘 할지)</label>
                    <select
                      value={builderAction}
                      onChange={(e) => setBuilderAction(e.target.value as BuilderAction)}
                      disabled={disabled}
                      className="w-full rounded-xl border border-black/[0.08] bg-white px-3 py-2.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/30 disabled:opacity-50"
                    >
                      {ACTION_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </div>

                  {/* Enforcement */}
                  <div>
                    <label className="text-xs font-medium text-secondary mb-1.5 block">방식 (어떻게)</label>
                    <select
                      value={builderEnforcement}
                      onChange={(e) => setBuilderEnforcement(e.target.value as BuilderEnforcement)}
                      disabled={disabled}
                      className="w-full rounded-xl border border-black/[0.08] bg-white px-3 py-2.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/30 disabled:opacity-50"
                    >
                      {ENFORCEMENT_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* Custom text input */}
                {builderAction === "customInstruction" && (
                  <input
                    type="text"
                    value={builderCustomText}
                    onChange={(e) => setBuilderCustomText(e.target.value)}
                    disabled={disabled}
                    placeholder="검증할 내용을 자유롭게 입력하세요"
                    className="w-full rounded-xl border border-black/[0.08] bg-white px-4 py-2.5 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-primary/30 disabled:opacity-50"
                  />
                )}

                {/* Preview */}
                <div className="rounded-xl border border-blue-100 bg-blue-50/50 px-4 py-3">
                  <p className="text-[10px] font-medium text-blue-400 uppercase mb-1">미리보기</p>
                  <p className="text-sm text-blue-700">
                    {buildPreviewText(builderCondition, builderAction, builderEnforcement, builderCustomText, conditionLabel)}
                  </p>
                </div>

                <button
                  type="button"
                  onClick={addFromBuilder}
                  disabled={disabled}
                  className="w-full rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-40 transition-colors"
                >
                  규칙 추가
                </button>
              </div>
            )}
          </div>

          {/* Direct edit */}
          <details
            open={directEditOpen}
            onToggle={(e) => setDirectEditOpen((e.target as HTMLDetailsElement).open)}
            className="rounded-xl border border-black/[0.06] bg-white"
          >
            <summary className="cursor-pointer px-4 py-3 text-xs font-medium text-secondary hover:text-foreground transition-colors">
              규칙 직접 편집
            </summary>
            <div className="border-t border-black/[0.04] px-4 py-3">
              <textarea
                value={rules}
                onChange={(e) => { setRules(e.target.value); setSuccess(null); setError(null); }}
                disabled={disabled || saving}
                rows={6}
                spellCheck={false}
                className="w-full bg-gray-50/50 border border-black/[0.06] rounded-xl px-4 py-3 text-foreground placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-primary/20 font-mono text-sm leading-relaxed resize-y disabled:opacity-50"
                placeholder="각 줄에 하나의 규칙을 작성하세요..."
              />
              <div className="flex items-center justify-between mt-2">
                <span className={`text-xs ${rules.length > MAX_CHARS ? "text-red-500" : "text-secondary"}`}>
                  {rules.length} / {MAX_CHARS}
                </span>
              </div>
            </div>
          </details>

          {/* Save / Status */}
          {success && <p className="text-xs text-emerald-600">{success}</p>}
          {error && <p className="text-xs text-red-500">{error}</p>}

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={disabled || saving || rules.length > MAX_CHARS}
              className="rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {saving ? "저장 중..." : "세이프가드 저장"}
            </button>
            {rules.trim().length > 0 && (
              <button
                type="button"
                onClick={() => { setRules(""); setSuccess(null); setError(null); }}
                disabled={disabled || saving}
                className="text-sm text-secondary hover:text-foreground transition-colors disabled:opacity-40"
              >
                초기화
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
