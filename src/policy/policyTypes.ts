export type SupportedLanguage = "ko" | "en" | "ja" | "zh" | "es";
export type ResponseLanguagePolicy = SupportedLanguage | "auto";

export interface ApprovalPolicy {
  explicitConsentForExternalActions: boolean;
}

export interface VerificationPolicy {
  requireCompletionEvidence: boolean;
  honorTaskContractVerificationMode: boolean;
}

export interface DeliveryPolicy {
  requireDeliveredArtifactsBeforeCompletion: boolean;
}

export interface AsyncPolicy {
  requireRealNotificationMechanism: boolean;
}

export interface RetryPolicy {
  retryTransientToolFailures: boolean;
  defaultBackoffSeconds: number[];
}

export interface ResponseModePolicy {
  language?: ResponseLanguagePolicy;
  concise?: boolean;
  noProfanity?: boolean;
}

export interface CitationsPolicy {
  requireSources?: boolean;
  includePageNumbers?: boolean;
}

export type HarnessRuleTrigger = "beforeCommit" | "afterToolUse";
export type HarnessRuleEnforcement = "audit" | "block_on_fail";

export interface HarnessRuleCondition {
  toolName?: string;
  anyToolUsed?: string[];
  userMessageIncludes?: string[];
  userMessageMatches?: string;
}

export type BuiltinPresetId =
  | "fact-grounding"
  | "answer-quality"
  | "self-claim"
  | "response-language"
  | "deterministic-evidence";

export type BuiltinPresetMode = "hybrid" | "deterministic" | "llm";

export interface BuiltinPresetConfig {
  enabled: boolean;
  mode: BuiltinPresetMode;
}

export type HarnessRuleAction =
  | {
      type: "require_tool";
      toolName: string;
    }
  | {
      type: "require_tool_input_match";
      toolName: string;
      inputPath: string;
      pattern: string;
    }
  | {
      type: "llm_verifier";
      prompt: string;
    }
  | {
      type: "block";
      reason: string;
    }
  | {
      type: "builtin_preset";
      preset: BuiltinPresetId;
      config?: BuiltinPresetConfig;
    };

export interface HarnessRule {
  id: string;
  sourceText: string;
  enabled: boolean;
  trigger: HarnessRuleTrigger;
  condition?: HarnessRuleCondition;
  action: HarnessRuleAction;
  enforcement: HarnessRuleEnforcement;
  timeoutMs: number;
  priority?: number;
}

export interface RuntimePolicy {
  approval: ApprovalPolicy;
  verification: VerificationPolicy;
  delivery: DeliveryPolicy;
  async: AsyncPolicy;
  retry: RetryPolicy;
  responseMode: ResponseModePolicy;
  citations: CitationsPolicy;
  harnessRules: HarnessRule[];
}

export interface RuntimePolicyStatus {
  executableDirectives: string[];
  userDirectives: string[];
  harnessDirectives: string[];
  advisoryDirectives: string[];
  warnings: string[];
}

export interface RuntimePolicySnapshot {
  policy: RuntimePolicy;
  status: RuntimePolicyStatus;
}
