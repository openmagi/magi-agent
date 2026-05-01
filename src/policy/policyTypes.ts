export type SupportedLanguage = "ko" | "en" | "ja" | "zh" | "es";

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
  language?: SupportedLanguage;
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
}

export type HarnessRuleAction =
  | {
      type: "require_tool";
      toolName: string;
    }
  | {
      type: "llm_verifier";
      prompt: string;
    }
  | {
      type: "block";
      reason: string;
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
