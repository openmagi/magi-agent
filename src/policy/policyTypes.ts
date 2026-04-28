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

export interface RuntimePolicy {
  approval: ApprovalPolicy;
  verification: VerificationPolicy;
  delivery: DeliveryPolicy;
  async: AsyncPolicy;
  retry: RetryPolicy;
  responseMode: ResponseModePolicy;
  citations: CitationsPolicy;
}

export interface RuntimePolicyStatus {
  executableDirectives: string[];
  userDirectives: string[];
  advisoryDirectives: string[];
  warnings: string[];
}

export interface RuntimePolicySnapshot {
  policy: RuntimePolicy;
  status: RuntimePolicyStatus;
}
