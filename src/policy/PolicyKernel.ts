import type { Workspace, WorkspaceIdentity } from "../storage/Workspace.js";
import type {
  RuntimePolicy,
  RuntimePolicySnapshot,
  RuntimePolicyStatus,
  SupportedLanguage,
} from "./policyTypes.js";

const DEFAULT_POLICY: RuntimePolicy = {
  approval: {
    explicitConsentForExternalActions: true,
  },
  verification: {
    requireCompletionEvidence: true,
    honorTaskContractVerificationMode: true,
  },
  delivery: {
    requireDeliveredArtifactsBeforeCompletion: true,
  },
  async: {
    requireRealNotificationMechanism: true,
  },
  retry: {
    retryTransientToolFailures: true,
    defaultBackoffSeconds: [0, 10, 30],
  },
  responseMode: {},
  citations: {},
};

const DIRECTIVE_LINE_PREFIX_RE = /^\s*(?:[-*+]\s+|\d+[.)]\s+)?/;
const MARKDOWN_HEADING_RE = /^\s*#+\s+/;

function cloneDefaultPolicy(): RuntimePolicy {
  return {
    approval: { ...DEFAULT_POLICY.approval },
    verification: { ...DEFAULT_POLICY.verification },
    delivery: { ...DEFAULT_POLICY.delivery },
    async: { ...DEFAULT_POLICY.async },
    retry: {
      ...DEFAULT_POLICY.retry,
      defaultBackoffSeconds: [...DEFAULT_POLICY.retry.defaultBackoffSeconds],
    },
    responseMode: { ...DEFAULT_POLICY.responseMode },
    citations: { ...DEFAULT_POLICY.citations },
  };
}

function cleanRuleLine(line: string): string {
  return line
    .replace(MARKDOWN_HEADING_RE, "")
    .replace(DIRECTIVE_LINE_PREFIX_RE, "")
    .trim();
}

function isLanguageDirective(text: string): SupportedLanguage | null {
  if (
    /(?:always|reply|answer).*(?:korean)|(?:항상|한국어).*(?:답|응답)|한국어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "ko";
  }
  if (
    /(?:always|reply|answer).*(?:english)|(?:항상|영어).*(?:답|응답)|영어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "en";
  }
  if (
    /(?:always|reply|answer).*(?:japanese)|(?:항상|일본어).*(?:답|응답)|일본어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "ja";
  }
  if (
    /(?:always|reply|answer).*(?:chinese)|(?:항상|중국어).*(?:답|응답)|중국어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "zh";
  }
  if (
    /(?:always|reply|answer).*(?:spanish)|(?:항상|스페인어).*(?:답|응답)|스페인어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "es";
  }
  return null;
}

function normalizeUserRules(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw
    .split("\n")
    .map((line) => cleanRuleLine(line))
    .filter((line) => line.length > 0 && line !== "[truncated]");
}

function buildPlatformDirectives(policy: RuntimePolicy): string[] {
  return [
    `approval.explicit_consent_for_external_actions=${String(policy.approval.explicitConsentForExternalActions)}`,
    `verification.require_completion_evidence=${String(policy.verification.requireCompletionEvidence)}`,
    `verification.honor_task_contract_verification_mode=${String(policy.verification.honorTaskContractVerificationMode)}`,
    `delivery.require_delivered_artifacts_before_completion=${String(policy.delivery.requireDeliveredArtifactsBeforeCompletion)}`,
    `async.require_real_notification_mechanism=${String(policy.async.requireRealNotificationMechanism)}`,
    `retry.retry_transient_tool_failures=${String(policy.retry.retryTransientToolFailures)}`,
    `retry.default_backoff_seconds=${policy.retry.defaultBackoffSeconds.join(",")}`,
  ];
}

function buildUserDirectives(policy: RuntimePolicy): string[] {
  const directives: string[] = [];
  if (policy.responseMode.language) {
    directives.push(`response.language=${policy.responseMode.language}`);
  }
  if (policy.citations.requireSources) {
    directives.push("citations.require_sources=true");
  }
  if (policy.citations.includePageNumbers) {
    directives.push("citations.include_page_numbers=true");
  }
  if (policy.responseMode.noProfanity) {
    directives.push("response.no_profanity=true");
  }
  if (policy.responseMode.concise) {
    directives.push("response.concise=true");
  }
  return directives;
}

function parseUserRules(identity: WorkspaceIdentity): RuntimePolicySnapshot {
  const policy = cloneDefaultPolicy();
  const warnings: string[] = [];
  const advisoryDirectives: string[] = [];
  const lines = normalizeUserRules(identity.userRules);

  for (const line of lines) {
    const language = isLanguageDirective(line);
    if (language) {
      if (policy.responseMode.language && policy.responseMode.language !== language) {
        warnings.push(
          `conflicting response.language directives detected; keeping response.language=${language}`,
        );
      }
      policy.responseMode.language = language;
      continue;
    }

    if (
      /(?:page\s*number|페이지\s*번호).*(?:cit|인용|출처)|(?:cit|인용|출처).*(?:page\s*number|페이지\s*번호)/i.test(
        line,
      )
    ) {
      policy.citations.requireSources = true;
      policy.citations.includePageNumbers = true;
      continue;
    }

    if (/(?:cit|source|출처|인용)/i.test(line)) {
      policy.citations.requireSources = true;
      continue;
    }

    if (/(?:no\s*profanity|don't\s*swear|do not swear|비속어\s*금지|욕설\s*금지)/i.test(line)) {
      policy.responseMode.noProfanity = true;
      continue;
    }

    if (/(?:be\s*concise|brief|concise|간결|짧게)/i.test(line)) {
      policy.responseMode.concise = true;
      continue;
    }

    advisoryDirectives.push(line);
  }

  return {
    policy,
    status: {
      executableDirectives: buildPlatformDirectives(policy),
      userDirectives: buildUserDirectives(policy),
      advisoryDirectives,
      warnings,
    },
  };
}

export class PolicyKernel {
  constructor(private readonly workspace: Workspace) {}

  async current(): Promise<RuntimePolicySnapshot> {
    const identity = await this.workspace.loadIdentity();
    return parseUserRules(identity);
  }

  async status(): Promise<RuntimePolicyStatus> {
    return (await this.current()).status;
  }
}

export function buildRuntimePolicyBlock(snapshot: RuntimePolicySnapshot): string {
  const lines: string[] = [];
  lines.push(`<runtime_policy source="policy-kernel">`);

  if (snapshot.status.executableDirectives.length > 0) {
    lines.push("[platform]");
    lines.push(...snapshot.status.executableDirectives);
  }
  if (snapshot.status.userDirectives.length > 0) {
    lines.push("[user]");
    lines.push(...snapshot.status.userDirectives);
  }
  if (snapshot.status.advisoryDirectives.length > 0) {
    lines.push("[advisory]");
    lines.push(...snapshot.status.advisoryDirectives);
  }
  if (snapshot.status.warnings.length > 0) {
    lines.push("[warnings]");
    lines.push(...snapshot.status.warnings);
  }

  lines.push("</runtime_policy>");
  return lines.join("\n");
}
