"use client";

import { useState } from "react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { GlassCard } from "@/components/ui/glass-card";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useMessages } from "@/lib/i18n";
import {
  compileAgentRulesPreview,
  type AgentRulesPreviewControl,
} from "@/lib/agent-harness/preview";

const MAX_CHARS = 5000;
const WARN_CHARS = 4000;

interface AgentRulesSectionProps {
  botId: string;
  initialRules: string | null;
  disabled?: boolean;
}

const PLACEHOLDER_FALLBACK =
  "You can give this bot additional rules. They get injected into every turn's system prompt. E.g., 'Never swear.' 'Always respond in Korean.' 'Always cite sources with page numbers.'";

interface SafeguardTemplate {
  id: string;
  category: string;
  title: string;
  description: string;
  addLabel: string;
  ruleText: string;
}

type CustomSafeguardTrigger =
  | "beforeToolUse"
  | "afterToolUse"
  | "beforeCommit"
  | "afterFileCreate"
  | "beforeExternalAction"
  | "duringLongTask";

type CustomSafeguardAction =
  | "verifyDeliverables"
  | "verifySources"
  | "requireFileDelivery"
  | "askConfirmation"
  | "sendProgressUpdate"
  | "customInstruction";

type CustomSafeguardEnforcement =
  | "blockAndRetry"
  | "askUser"
  | "warnOnly"
  | "recordOnly";

interface CustomSafeguardDraft {
  trigger: CustomSafeguardTrigger;
  action: CustomSafeguardAction;
  enforcement: CustomSafeguardEnforcement;
  target: string;
}

interface CustomSafeguardOption<T extends string> {
  value: T;
  label: string;
  description: string;
  technical: string;
}

const DEFAULT_CUSTOM_SAFEGUARD: CustomSafeguardDraft = {
  trigger: "beforeCommit",
  action: "verifyDeliverables",
  enforcement: "blockAndRetry",
  target: "",
};

const RULE_LINE_PREFIX_RE = /^\s*(?:[-*+]\s+|\d+[.)]\s+)?/;
const RULE_HEADING_PREFIX_RE = /^\s*#+\s+/;

function normalizedTarget(target: string): string {
  return target.trim().replace(/\s+/g, " ");
}

function cleanRuleText(rule: string): string {
  return rule
    .replace(RULE_HEADING_PREFIX_RE, "")
    .replace(RULE_LINE_PREFIX_RE, "")
    .trim();
}

function comparableRule(rule: string): string {
  return cleanRuleText(rule)
    .replace(/\s+/g, " ")
    .replace(/[.。]+$/g, "")
    .toLowerCase();
}

export function hasAgentRule(current: string, rule: string): boolean {
  const needle = comparableRule(rule);
  if (!needle) return false;
  return current
    .split("\n")
    .map((line) => comparableRule(line))
    .some((line) => line === needle);
}

export function appendUniqueAgentRule(current: string, rule: string): string {
  const clean = cleanRuleText(rule);
  if (!clean) return current;
  if (hasAgentRule(current, clean)) return current;
  const prefix = current.trim().length > 0 ? `${current.trimEnd()}\n` : "";
  return `${prefix}- ${clean}`;
}

export function removeAgentRule(current: string, rule: string): string {
  const needle = comparableRule(rule);
  if (!needle) return current;

  return current
    .split("\n")
    .filter((line) => comparableRule(line) !== needle)
    .join("\n")
    .trim();
}

export function buildCustomSafeguardRule(draft: CustomSafeguardDraft): string {
  const target = normalizedTarget(draft.target);
  const triggerText: Record<CustomSafeguardTrigger, string> = {
    beforeToolUse: "Before each tool call",
    afterToolUse: "After each tool call",
    beforeCommit: "Before the final answer",
    afterFileCreate: "After creating a file or document",
    beforeExternalAction: "Before external actions",
    duringLongTask: "During long-running work",
  };
  const actionText: Record<CustomSafeguardAction, string> = {
    verifyDeliverables: `verify ${target || "every requested deliverable"}`,
    verifySources: `verify source grounding${target ? ` for ${target}` : ""}`,
    requireFileDelivery: target
      ? `deliver created files in chat for ${target}`
      : "deliver created files in chat before saying the task is complete",
    askConfirmation: `ask for confirmation before ${target || "continuing"}`,
    sendProgressUpdate: `provide a brief progress update${
      target ? ` about ${target}` : ""
    }`,
    customInstruction: `run this custom check: ${target || "the requested condition"}`,
  };
  const enforcementText: Record<CustomSafeguardEnforcement, string> = {
    blockAndRetry:
      "If the check fails, block completion and retry the missing work.",
    askUser: "If the check fails, ask the user before continuing.",
    warnOnly: "If the check fails, continue only after noting the risk.",
    recordOnly: "Record the result as an audit note without blocking the task.",
  };

  return `${triggerText[draft.trigger]}, ${actionText[draft.action]}. ${enforcementText[draft.enforcement]}`;
}

export function AgentRulesSection({
  botId,
  initialRules,
  disabled = false,
}: AgentRulesSectionProps) {
  const authFetch = useAuthFetch();
  const t = useMessages();
  const [value, setValue] = useState<string>(initialRules ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [customDraft, setCustomDraft] = useState<CustomSafeguardDraft>(
    DEFAULT_CUSTOM_SAFEGUARD,
  );
  const [customBuilderOpen, setCustomBuilderOpen] = useState(false);
  const [customBuilderStep, setCustomBuilderStep] = useState(0);

  const count = value.length;
  const overLimit = count > MAX_CHARS;
  const warn = !overLimit && count > WARN_CHARS;
  const preview = compileAgentRulesPreview(value);

  const title = t.settingsPage?.agentRules ?? "Agent Rules";
  const description =
    t.settingsPage?.agentRulesDesc ??
    "Define behavior rules your agent will follow. These are injected into the agent's system prompt.";
  const placeholder =
    t.settingsPage?.agentRulesPlaceholder ?? PLACEHOLDER_FALLBACK;
  const saveLabel = t.settingsPage?.agentRulesSaveButton ?? "Save rules";
  const clearLabel = t.settingsPage?.agentRulesClearButton ?? "Clear";
  const successMsg =
    t.settingsPage?.agentRulesSaveSuccess ??
    "Agent rules updated — bot will reprovision shortly.";
  const errorMsg =
    t.settingsPage?.agentRulesSaveError ?? "Failed to save agent rules.";
  const ownerOnlyMsg =
    t.settingsPage?.agentRulesOwnerOnly ?? "Only the bot owner can edit rules.";
  const countTemplate =
    t.settingsPage?.agentRulesCharacterCount ?? "{count} / {max}";
  const libraryTitle =
    t.settingsPage?.agentRulesSafeguardLibraryTitle ?? "Safeguard library";
  const libraryDesc =
    t.settingsPage?.agentRulesSafeguardLibraryDesc ??
    "Choose what the agent must check or do in common work situations. Each item adds a plain-language rule below.";
  const activeTitle =
    t.settingsPage?.agentRulesActiveSafeguardsTitle ?? "Active safeguards";
  const activeDesc =
    t.settingsPage?.agentRulesActiveSafeguardsDesc ??
    "These are the rules the runtime can enforce or check before the agent finishes.";
  const harnessTitle =
    t.settingsPage?.agentRulesHarnessTitle ?? activeTitle;
  const harnessDesc =
    t.settingsPage?.agentRulesHarnessDesc ??
    activeDesc;
  const harnessEmpty =
    t.settingsPage?.agentRulesHarnessEmpty ??
    "No active safeguards yet. Add one from the library or write a rule directly.";
  const advisoryTitle =
    t.settingsPage?.agentRulesAdvisoryTitle ?? "Saved as prompt rules";
  const directEditTitle =
    t.settingsPage?.agentRulesDirectEditTitle ?? "Edit rules directly";
  const directEditDesc =
    t.settingsPage?.agentRulesDirectEditDesc ??
    "Use this when you want to write your own rule. The preview above shows which parts can be enforced natively.";
  const technicalDetailsLabel =
    t.settingsPage?.agentRulesTechnicalDetails ?? "Show technical details";
  const ruleSourceLabel =
    t.settingsPage?.agentRulesRuleSource ?? "Rule";
  const customBuilderTitle =
    t.settingsPage?.agentRulesCustomBuilderTitle ?? "Custom safeguard builder";
  const customBuilderDesc =
    t.settingsPage?.agentRulesCustomBuilderDesc ??
    "Build your own rule from a hook point, a check or action, a failure behavior, and an optional target.";
  const customTriggerLabel =
    t.settingsPage?.agentRulesCustomTriggerLabel ?? "When should it run?";
  const customActionLabel =
    t.settingsPage?.agentRulesCustomActionLabel ?? "What should it do?";
  const customEnforcementLabel =
    t.settingsPage?.agentRulesCustomEnforcementLabel ?? "If it fails?";
  const customTargetLabel =
    t.settingsPage?.agentRulesCustomTargetLabel ?? "Target or condition";
  const customTargetPlaceholder =
    t.settingsPage?.agentRulesCustomTargetPlaceholder ??
    "e.g. web search results, generated files, external uploads";
  const customPreviewLabel =
    t.settingsPage?.agentRulesCustomPreviewLabel ?? "Generated rule";
  const customAddLabel =
    t.settingsPage?.agentRulesCustomAdd ?? "Add custom safeguard";
  const removeLabel =
    t.settingsPage?.agentRulesRemove ?? "Remove";
  const customOpenLabel =
    t.settingsPage?.agentRulesCustomOpen ?? "Create custom safeguard";
  const customCloseLabel =
    t.settingsPage?.agentRulesCustomClose ?? "Close";
  const customBackLabel =
    t.settingsPage?.agentRulesCustomBack ?? "Back";
  const customNextLabel =
    t.settingsPage?.agentRulesCustomNext ?? "Next";
  const customStepTemplate =
    t.settingsPage?.agentRulesCustomStep ?? "Step {current} of {total}";
  const customNativeHint =
    t.settingsPage?.agentRulesCustomNativeHint ??
    "Rules that match supported runtime controls appear in Active safeguards. Other combinations remain prompt rules.";
  const customSkillsLinkTitle =
    t.settingsPage?.agentRulesCustomSkillsLinkTitle ?? "Custom skills";
  const customSkillsLinkDesc =
    t.settingsPage?.agentRulesCustomSkillsLinkDesc ??
    "Install reusable SKILL.md-style capabilities on the Skills page.";
  const customSkillsLinkAction =
    t.settingsPage?.agentRulesCustomSkillsLinkAction ?? "Open Skills";
  const templates: SafeguardTemplate[] = [
    {
      id: "file-delivery",
      category: t.settingsPage?.agentRulesCategoryOutputs ?? "Outputs",
      title: t.settingsPage?.agentRulesTemplateFileTitle ?? "File delivery",
      description:
        t.settingsPage?.agentRulesTemplateFileDesc ??
        "If the agent creates a file or report, it must attach it in chat before saying the task is complete.",
      addLabel:
        t.settingsPage?.agentRulesTemplateFileAdd ?? "Add file delivery check",
      ruleText:
        "When you create a file or document, deliver it in chat before saying the task is complete.",
    },
    {
      id: "final-answer",
      category: t.settingsPage?.agentRulesCategoryAnswerQuality ?? "Answer quality",
      title: t.settingsPage?.agentRulesTemplateFinalTitle ?? "Final answer check",
      description:
        t.settingsPage?.agentRulesTemplateFinalDesc ??
        "Before the final reply, the agent checks whether it satisfied every requested deliverable.",
      addLabel:
        t.settingsPage?.agentRulesTemplateFinalAdd ?? "Add final answer check",
      ruleText:
        "Before the final answer, verify once more that every requested deliverable is satisfied.",
    },
    {
      id: "source-grounding",
      category: t.settingsPage?.agentRulesCategoryResearch ?? "Research",
      title: t.settingsPage?.agentRulesTemplateSourcesTitle ?? "Source grounding",
      description:
        t.settingsPage?.agentRulesTemplateSourcesDesc ??
        "For research or factual answers, the agent checks that important claims have sources.",
      addLabel:
        t.settingsPage?.agentRulesTemplateSourcesAdd ?? "Add source check",
      ruleText:
        "For answers that need sources, verify source grounding before replying.",
    },
    {
      id: "external-action",
      category: t.settingsPage?.agentRulesCategoryExternalActions ?? "External actions",
      title: t.settingsPage?.agentRulesTemplateExternalTitle ?? "Ask before external actions",
      description:
        t.settingsPage?.agentRulesTemplateExternalDesc ??
        "Before sending email, uploading outside the workspace, paying, or posting, the agent asks for confirmation.",
      addLabel:
        t.settingsPage?.agentRulesTemplateExternalAdd ?? "Add confirmation rule",
      ruleText:
        "Before sending email, uploading files externally, making payments, or posting publicly, ask for confirmation.",
    },
    {
      id: "long-task",
      category: t.settingsPage?.agentRulesCategoryLongTasks ?? "Long tasks",
      title: t.settingsPage?.agentRulesTemplateProgressTitle ?? "Progress updates",
      description:
        t.settingsPage?.agentRulesTemplateProgressDesc ??
        "During long work, the agent gives short progress updates instead of going silent.",
      addLabel:
        t.settingsPage?.agentRulesTemplateProgressAdd ?? "Add progress updates",
      ruleText:
        "For long-running work, provide brief progress updates and do not go silent until everything is done.",
    },
  ];
  const triggerOptions: CustomSafeguardOption<CustomSafeguardTrigger>[] = [
    {
      value: "beforeToolUse",
      label: t.settingsPage?.agentRulesCustomTriggerBeforeTool ?? "Before tool call",
      description:
        t.settingsPage?.agentRulesCustomTriggerBeforeToolDesc ??
        "Run before the agent uses a tool.",
      technical: "pre-tool hook",
    },
    {
      value: "afterToolUse",
      label: t.settingsPage?.agentRulesCustomTriggerAfterTool ?? "After tool call",
      description:
        t.settingsPage?.agentRulesCustomTriggerAfterToolDesc ??
        "Run after a tool returns a result.",
      technical: "post-tool hook",
    },
    {
      value: "beforeCommit",
      label:
        t.settingsPage?.agentRulesCustomTriggerBeforeCommit ??
        "Before final answer",
      description:
        t.settingsPage?.agentRulesCustomTriggerBeforeCommitDesc ??
        "Run before the agent says the task is done.",
      technical: "beforeCommit checkpoint",
    },
    {
      value: "afterFileCreate",
      label:
        t.settingsPage?.agentRulesCustomTriggerAfterFile ??
        "After file or document creation",
      description:
        t.settingsPage?.agentRulesCustomTriggerAfterFileDesc ??
        "Run when the task creates an artifact.",
      technical: "artifact checkpoint",
    },
    {
      value: "beforeExternalAction",
      label:
        t.settingsPage?.agentRulesCustomTriggerExternal ??
        "Before external action",
      description:
        t.settingsPage?.agentRulesCustomTriggerExternalDesc ??
        "Run before email, uploads, payment, or public posting.",
      technical: "external-action policy",
    },
    {
      value: "duringLongTask",
      label:
        t.settingsPage?.agentRulesCustomTriggerLongTask ??
        "During long-running work",
      description:
        t.settingsPage?.agentRulesCustomTriggerLongTaskDesc ??
        "Run while a task is taking time.",
      technical: "progress checkpoint",
    },
  ];
  const actionOptions: CustomSafeguardOption<CustomSafeguardAction>[] = [
    {
      value: "verifyDeliverables",
      label:
        t.settingsPage?.agentRulesCustomActionDeliverables ??
        "Check requested deliverables",
      description:
        t.settingsPage?.agentRulesCustomActionDeliverablesDesc ??
        "Verify the answer did not skip a requested output or constraint.",
      technical: "verifier",
    },
    {
      value: "verifySources",
      label:
        t.settingsPage?.agentRulesCustomActionSources ??
        "Check source grounding",
      description:
        t.settingsPage?.agentRulesCustomActionSourcesDesc ??
        "Verify factual claims are backed by named sources.",
      technical: "grounding verifier",
    },
    {
      value: "requireFileDelivery",
      label:
        t.settingsPage?.agentRulesCustomActionFileDelivery ??
        "Require file delivery",
      description:
        t.settingsPage?.agentRulesCustomActionFileDeliveryDesc ??
        "Require created files to be attached in chat.",
      technical: "artifact gate",
    },
    {
      value: "askConfirmation",
      label:
        t.settingsPage?.agentRulesCustomActionConfirm ??
        "Ask for confirmation",
      description:
        t.settingsPage?.agentRulesCustomActionConfirmDesc ??
        "Pause and ask the user before continuing.",
      technical: "human approval",
    },
    {
      value: "sendProgressUpdate",
      label:
        t.settingsPage?.agentRulesCustomActionProgress ??
        "Send progress update",
      description:
        t.settingsPage?.agentRulesCustomActionProgressDesc ??
        "Tell the user what is happening during a long task.",
      technical: "progress event",
    },
    {
      value: "customInstruction",
      label:
        t.settingsPage?.agentRulesCustomActionCustom ??
        "Run custom instruction",
      description:
        t.settingsPage?.agentRulesCustomActionCustomDesc ??
        "Use the target field as the check or instruction.",
      technical: "custom prompt check",
    },
  ];
  const enforcementOptions: CustomSafeguardOption<CustomSafeguardEnforcement>[] = [
    {
      value: "blockAndRetry",
      label:
        t.settingsPage?.agentRulesCustomEnforcementBlock ??
        "Block and retry",
      description:
        t.settingsPage?.agentRulesCustomEnforcementBlockDesc ??
        "Do not allow completion until the issue is fixed.",
      technical: "block_on_fail",
    },
    {
      value: "askUser",
      label:
        t.settingsPage?.agentRulesCustomEnforcementAskUser ??
        "Ask user",
      description:
        t.settingsPage?.agentRulesCustomEnforcementAskUserDesc ??
        "Pause and ask the user what to do next.",
      technical: "human_in_loop",
    },
    {
      value: "warnOnly",
      label:
        t.settingsPage?.agentRulesCustomEnforcementWarn ??
        "Warn only",
      description:
        t.settingsPage?.agentRulesCustomEnforcementWarnDesc ??
        "Continue, but mention the risk.",
      technical: "warn_only",
    },
    {
      value: "recordOnly",
      label:
        t.settingsPage?.agentRulesCustomEnforcementRecord ??
        "Record only",
      description:
        t.settingsPage?.agentRulesCustomEnforcementRecordDesc ??
        "Log the check without blocking.",
      technical: "audit_only",
    },
  ];
  const customRulePreview = buildCustomSafeguardRule(customDraft);
  const customRuleAlreadyAdded = hasAgentRule(value, customRulePreview);
  const customStepTotal = 4;
  const customStepLabel = customStepTemplate
    .replace("{current}", String(customBuilderStep + 1))
    .replace("{total}", String(customStepTotal));
  const countLabel = countTemplate
    .replace("{count}", String(count))
    .replace("{max}", String(MAX_CHARS));

  async function saveRules(nextValue: string) {
    if (disabled || saving) return;
    if (nextValue.length > MAX_CHARS) {
      setError(errorMsg);
      return;
    }
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await authFetch(`/api/bots/${botId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_rules: nextValue }),
      });
      if (!res.ok) {
        let message = errorMsg;
        try {
          const data = (await res.json()) as { error?: string };
          if (data?.error) message = data.error;
        } catch {
          /* ignore */
        }
        throw new Error(message);
      }
      setSuccess(successMsg);
    } catch (err) {
      setError(err instanceof Error ? err.message : errorMsg);
    } finally {
      setSaving(false);
    }
  }

  async function handleSave() {
    await saveRules(value);
  }

  async function handleClear() {
    setValue("");
    await saveRules("");
  }

  function toggleQuickRule(rule: string) {
    setValue((current) => {
      if (hasAgentRule(current, rule)) {
        return removeAgentRule(current, rule);
      }
      return appendUniqueAgentRule(current, rule);
    });
    setSuccess(null);
    setError(null);
  }

  function closeCustomBuilder() {
    setCustomBuilderOpen(false);
    setCustomBuilderStep(0);
  }

  function toggleCustomRule() {
    toggleQuickRule(customRulePreview);
    closeCustomBuilder();
  }

  function updateCustomDraft<K extends keyof CustomSafeguardDraft>(
    key: K,
    nextValue: CustomSafeguardDraft[K],
  ) {
    setCustomDraft((current) => ({ ...current, [key]: nextValue }));
    setSuccess(null);
    setError(null);
  }

  function controlView(item: AgentRulesPreviewControl) {
    if (item.id === "user-harness:file-delivery-after-create") {
      return {
        title:
          t.settingsPage?.agentRulesPreviewFileTitle ?? "File attachment check",
        summary:
          t.settingsPage?.agentRulesPreviewFileSummary ??
          "Blocks completion until the created file is delivered in chat.",
        stage: t.settingsPage?.agentRulesPreviewStageBeforeDone ?? "Before completion",
        behavior:
          t.settingsPage?.agentRulesPreviewBehaviorBlock ?? "Can block completion",
      };
    }
    if (item.id === "user-harness:final-answer-verifier") {
      return {
        title:
          t.settingsPage?.agentRulesPreviewFinalTitle ?? "Final answer check",
        summary:
          t.settingsPage?.agentRulesPreviewFinalSummary ??
          "Checks that the final answer did not skip requested deliverables.",
        stage: t.settingsPage?.agentRulesPreviewStageBeforeDone ?? "Before completion",
        behavior:
          t.settingsPage?.agentRulesPreviewBehaviorVerify ?? "Runs a verifier",
      };
    }
    if (item.id === "user-harness:source-grounding-verifier") {
      return {
        title:
          t.settingsPage?.agentRulesPreviewSourcesTitle ?? "Source grounding check",
        summary:
          t.settingsPage?.agentRulesPreviewSourcesSummary ??
          "Checks that factual claims that need support are grounded in named sources.",
        stage: t.settingsPage?.agentRulesPreviewStageBeforeDone ?? "Before completion",
        behavior:
          t.settingsPage?.agentRulesPreviewBehaviorVerify ?? "Runs a verifier",
      };
    }
    if (item.id === "user-harness:external-action-confirmation") {
      return {
        title:
          t.settingsPage?.agentRulesPreviewExternalTitle ??
          "External action confirmation",
        summary:
          t.settingsPage?.agentRulesPreviewExternalSummary ??
          "Asks before email, external uploads, payments, or public posting.",
        stage:
          t.settingsPage?.agentRulesPreviewStageBeforeExternal ??
          "Before external action",
        behavior:
          t.settingsPage?.agentRulesPreviewBehaviorAsk ?? "Asks user first",
      };
    }
    if (item.id === "policy:progress-updates") {
      return {
        title:
          t.settingsPage?.agentRulesPreviewProgressTitle ?? "Progress updates",
        summary:
          t.settingsPage?.agentRulesPreviewProgressSummary ??
          "Sends brief progress updates during long-running work instead of going silent.",
        stage:
          t.settingsPage?.agentRulesPreviewStageLongTask ??
          "During long task",
        behavior:
          t.settingsPage?.agentRulesPreviewBehaviorProgress ??
          "Sends progress",
      };
    }
    if (item.kind === "policy") {
      return {
        title: item.title,
        summary: item.summary,
        stage: t.settingsPage?.agentRulesPreviewStageEveryTurn ?? "Every turn",
        behavior:
          t.settingsPage?.agentRulesPreviewBehaviorPolicy ?? "Applies as policy",
      };
    }
    return {
      title: item.title,
      summary: item.summary,
      stage: item.trigger,
      behavior: item.action,
    };
  }

  function customOptionGrid<T extends string>(
    options: CustomSafeguardOption<T>[],
    selectedValue: T,
    onSelect: (value: T) => void,
  ) {
    return (
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map((option) => {
          const selected = option.value === selectedValue;
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => onSelect(option.value)}
              disabled={disabled || saving}
              className={`rounded-lg border px-3 py-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                selected
                  ? "border-primary/30 bg-primary/10 text-foreground"
                  : "border-black/[0.06] bg-white hover:border-primary/25 hover:bg-primary/[0.03]"
              }`}
            >
              <span className="block text-xs font-semibold text-foreground">
                {option.label}
              </span>
              <span className="mt-1 block text-[11px] leading-relaxed text-secondary">
                {option.description}
              </span>
            </button>
          );
        })}
      </div>
    );
  }

  const activeSafeguardsPanel = (
    <div className="rounded-xl border border-black/10 bg-gray-50 px-4 py-3">
      <div className="mb-3">
        <p className="text-xs font-semibold text-foreground">{harnessTitle}</p>
        <p className="mt-0.5 text-[11px] leading-relaxed text-secondary">
          {harnessDesc}
        </p>
      </div>

      {preview.controls.length > 0 ? (
        <div className="space-y-2">
          {preview.controls.map((item) => {
            const view = controlView(item);
            return (
              <div
                key={item.id}
                className="rounded-lg border border-black/[0.06] bg-white px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium text-foreground">
                    {view.title}
                  </span>
                  <span className="rounded-md bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                    {view.stage}
                  </span>
                  <span className="rounded-md bg-black/[0.04] px-1.5 py-0.5 text-[10px] text-secondary">
                    {view.behavior}
                  </span>
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-secondary">
                  {view.summary}
                </p>
                <p className="mt-1 text-[10px] text-secondary/60">
                  {ruleSourceLabel}: {item.sourceText}
                </p>
                <details className="mt-2">
                  <summary className="cursor-pointer text-[10px] text-secondary/70 hover:text-secondary">
                    {technicalDetailsLabel}
                  </summary>
                  <p className="mt-1 text-[10px] text-secondary/60">
                    {item.trigger} · {item.action} · {item.enforcement}
                  </p>
                </details>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-[11px] leading-relaxed text-secondary">
          {harnessEmpty}
        </p>
      )}

      {preview.warnings.length > 0 && (
        <div className="mt-3 space-y-1">
          {preview.warnings.map((warning) => (
            <p key={warning} className="text-[11px] text-amber-600">
              {warning}
            </p>
          ))}
        </div>
      )}

      {preview.advisoryRules.length > 0 && (
        <div className="mt-3 border-t border-black/[0.06] pt-3">
          <p className="text-[11px] font-medium text-secondary">
            {advisoryTitle}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {preview.advisoryRules.map((rule) => (
              <span
                key={rule}
                className="max-w-full truncate rounded-md bg-white px-2 py-1 text-[11px] text-secondary ring-1 ring-black/[0.06]"
              >
                {rule}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );

  return (
    <>
    <GlassCard>
      <div className="space-y-3">
        <div>
          <h3 className="font-medium text-foreground text-sm">{title}</h3>
          <p className="text-xs text-secondary mt-0.5 leading-relaxed">
            {description}
          </p>
        </div>

        {disabled && (
          <p className="text-xs text-amber-400/80">{ownerOnlyMsg}</p>
        )}

        <div className="flex flex-col gap-2 rounded-xl border border-primary/10 bg-primary/[0.03] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-semibold text-foreground">
              {customSkillsLinkTitle}
            </p>
            <p className="mt-0.5 text-[11px] leading-relaxed text-secondary">
              {customSkillsLinkDesc}
            </p>
          </div>
          <a
            href={`/dashboard/${botId}/skills`}
            className="inline-flex min-h-9 shrink-0 items-center justify-center rounded-lg border border-primary/20 bg-white px-3 text-xs font-medium text-primary transition-colors hover:border-primary/40 hover:bg-primary/5"
          >
            {customSkillsLinkAction}
          </a>
        </div>

        <div className="rounded-xl border border-black/10 bg-gray-50 px-4 py-3">
          <div>
            <p className="text-xs font-semibold text-foreground">{libraryTitle}</p>
            <p className="mt-0.5 text-[11px] leading-relaxed text-secondary">
              {libraryDesc}
            </p>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {templates.map((template) => {
              const alreadyAdded = hasAgentRule(value, template.ruleText);
              return (
                <div
                  key={template.id}
                  className={`rounded-lg border px-3 py-3 ${
                    alreadyAdded
                      ? "border-primary/20 bg-primary/5"
                      : "border-black/[0.06] bg-white"
                  }`}
                >
                  <p className="text-[10px] font-medium uppercase text-secondary/70">
                    {template.category}
                  </p>
                  <p className="mt-1 text-xs font-semibold text-foreground">
                    {template.title}
                  </p>
                  <p className="mt-1 min-h-10 text-[11px] leading-relaxed text-secondary">
                    {template.description}
                  </p>
                  <button
                    type="button"
                    onClick={() => toggleQuickRule(template.ruleText)}
                    disabled={disabled || saving}
                    aria-pressed={alreadyAdded}
                    className={`mt-3 min-h-9 w-full rounded-lg border px-3 text-xs font-medium transition-colors disabled:cursor-not-allowed ${
                      alreadyAdded
                        ? "border-primary/20 bg-white text-primary hover:border-primary/40 hover:bg-white/90"
                        : "border-primary/20 bg-primary/5 text-primary hover:border-primary/40 hover:bg-primary/10 disabled:opacity-40"
                    }`}
                  >
                    {alreadyAdded ? removeLabel : template.addLabel}
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        <div className="flex flex-col gap-2 rounded-xl border border-black/10 bg-gray-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-semibold text-foreground">
              {customBuilderTitle}
            </p>
            <p className="mt-0.5 text-[11px] leading-relaxed text-secondary">
              {customBuilderDesc}
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              setCustomBuilderStep(0);
              setCustomBuilderOpen(true);
            }}
            disabled={disabled || saving}
            className="inline-flex min-h-9 shrink-0 items-center justify-center rounded-lg border border-primary/20 bg-white px-3 text-xs font-medium text-primary transition-colors hover:border-primary/40 hover:bg-primary/5 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {customOpenLabel}
          </button>
        </div>

        {activeSafeguardsPanel}

        <details className="rounded-xl border border-black/10 bg-white px-4 py-3">
          <summary className="cursor-pointer text-xs font-semibold text-foreground">
            {directEditTitle}
          </summary>
          <p className="mt-2 text-[11px] leading-relaxed text-secondary">
            {directEditDesc}
          </p>
          <textarea
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              setSuccess(null);
              setError(null);
            }}
            disabled={disabled || saving}
            placeholder={placeholder}
            rows={8}
            spellCheck={false}
            className={`mt-3 w-full bg-white border border-black/10 rounded-xl px-4 py-3 text-foreground placeholder:text-gray-400 focus:outline-none focus:ring-1 transition-colors duration-200 font-mono text-sm leading-relaxed resize-y ${
              overLimit
                ? "border-red-500/40 focus:border-red-500/60 focus:ring-red-500/20"
                : warn
                  ? "border-amber-500/30 focus:border-amber-500/50 focus:ring-amber-500/20"
                  : "focus:border-primary/50 focus:ring-primary/20"
            } ${disabled ? "opacity-60 cursor-not-allowed" : ""}`}
          />
        </details>

        <div className="flex items-center justify-between gap-3 flex-wrap">
          <span
            className={`text-[11px] tabular-nums ${
              overLimit
                ? "text-red-400"
                : warn
                  ? "text-amber-400"
                  : "text-secondary/70"
            }`}
          >
            {countLabel}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleClear}
              disabled={disabled || saving || value.length === 0}
            >
              {clearLabel}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleSave}
              disabled={disabled || saving || overLimit}
            >
              {saving ? (
                <span className="inline-flex items-center gap-2">
                  <span className="h-3 w-3 rounded-full border-2 border-current border-t-transparent animate-spin" />
                  {saveLabel}
                </span>
              ) : (
                saveLabel
              )}
            </Button>
          </div>
        </div>

        {success && (
          <p className="text-xs text-emerald-400">{success}</p>
        )}
        {error && <p className="text-xs text-red-400">{error}</p>}
      </div>
    </GlassCard>

    <Modal
      open={customBuilderOpen}
      onClose={closeCustomBuilder}
      className="max-w-2xl"
    >
      <div className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-foreground">
              {customBuilderTitle}
            </p>
            <p className="mt-1 text-xs leading-relaxed text-secondary">
              {customBuilderDesc}
            </p>
          </div>
          <button
            type="button"
            onClick={closeCustomBuilder}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-secondary transition-colors hover:bg-black/[0.04] hover:text-foreground"
            aria-label={customCloseLabel}
          >
            <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4">
              <path
                d="M5 5L15 15M15 5L5 15"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        <div className="mt-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <span className="text-[11px] font-medium text-secondary">
              {customStepLabel}
            </span>
            <div className="flex gap-1">
              {Array.from({ length: customStepTotal }).map((_, index) => (
                <span
                  key={index}
                  className={`h-1.5 w-8 rounded-full ${
                    index <= customBuilderStep ? "bg-primary" : "bg-black/10"
                  }`}
                />
              ))}
            </div>
          </div>

          {customBuilderStep === 0 && (
            <div>
              <p className="mb-2 text-xs font-semibold text-foreground">
                {customTriggerLabel}
              </p>
              {customOptionGrid(triggerOptions, customDraft.trigger, (next) =>
                updateCustomDraft("trigger", next),
              )}
            </div>
          )}

          {customBuilderStep === 1 && (
            <div>
              <p className="mb-2 text-xs font-semibold text-foreground">
                {customActionLabel}
              </p>
              {customOptionGrid(actionOptions, customDraft.action, (next) =>
                updateCustomDraft("action", next),
              )}
            </div>
          )}

          {customBuilderStep === 2 && (
            <div>
              <p className="mb-2 text-xs font-semibold text-foreground">
                {customEnforcementLabel}
              </p>
              {customOptionGrid(
                enforcementOptions,
                customDraft.enforcement,
                (next) => updateCustomDraft("enforcement", next),
              )}
            </div>
          )}

          {customBuilderStep === 3 && (
            <div>
              <label className="block">
                <span className="text-xs font-semibold text-foreground">
                  {customTargetLabel}
                </span>
                <input
                  value={customDraft.target}
                  onChange={(e) => updateCustomDraft("target", e.target.value)}
                  disabled={disabled || saving}
                  placeholder={customTargetPlaceholder}
                  className="mt-2 min-h-10 w-full rounded-lg border border-black/10 bg-white px-3 text-xs text-foreground placeholder:text-gray-400 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50"
                />
                <p className="mt-1 text-[10px] text-secondary/70">
                  {customNativeHint}
                </p>
              </label>

              <div className="mt-3 rounded-lg border border-black/[0.06] bg-gray-50 px-3 py-3">
                <span className="text-[10px] font-medium uppercase text-secondary/70">
                  {customPreviewLabel}
                </span>
                <p className="mt-2 text-[11px] leading-relaxed text-secondary">
                  {customRulePreview}
                </p>
              </div>
            </div>
          )}
        </div>

        <div className="mt-5 flex items-center justify-between gap-2 border-t border-black/[0.06] pt-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              customBuilderStep === 0
                ? closeCustomBuilder()
                : setCustomBuilderStep((current) => current - 1)
            }
          >
            {customBuilderStep === 0 ? customCloseLabel : customBackLabel}
          </Button>

          {customBuilderStep < customStepTotal - 1 ? (
            <Button
              variant="primary"
              size="sm"
              onClick={() => setCustomBuilderStep((current) => current + 1)}
            >
              {customNextLabel}
            </Button>
          ) : (
            <Button
              variant={customRuleAlreadyAdded ? "ghost" : "primary"}
              size="sm"
              onClick={toggleCustomRule}
              disabled={disabled || saving}
            >
              {customRuleAlreadyAdded ? removeLabel : customAddLabel}
            </Button>
          )}
        </div>
      </div>
    </Modal>
    </>
  );
}
