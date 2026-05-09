import { createHash } from "node:crypto";
import type {
  DeterministicRequirementKind,
  FinalAnswerMetaClassificationResult,
  PlanningNeed,
  RequestMetaClassificationResult,
} from "../../execution/ExecutionContract.js";
import type { LongTermMemoryPolicy } from "../../reliability/SourceAuthority.js";
import { buildSourceAuthorityClassifierContext } from "../../reliability/SourceAuthority.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import type { HookContext } from "../types.js";

const REQUEST_CLASSIFIER_TIMEOUT_MS = 8_000;
const FINAL_CLASSIFIER_TIMEOUT_MS = 6_000;
const CLASSIFIER_HOOK_DEADLINE_HEADROOM_MS = 250;

const VALID_KINDS: readonly DeterministicRequirementKind[] = [
  "clock",
  "date_range",
  "calculation",
  "counting",
  "data_query",
  "comparison",
];
const VALID_KIND_SET = new Set<string>(VALID_KINDS);
const VALID_PLANNING_NEEDS: readonly PlanningNeed[] = [
  "none",
  "inline",
  "task_board",
  "approval_plan",
  "pipeline_or_bulk",
];
const VALID_PLANNING_NEED_SET = new Set<string>(VALID_PLANNING_NEEDS);
const VALID_MEMORY_POLICIES: readonly LongTermMemoryPolicy[] = [
  "normal",
  "background_only",
  "disabled",
];
const VALID_MEMORY_POLICY_SET = new Set<string>(VALID_MEMORY_POLICIES);

const REQUEST_CLASSIFIER_SYSTEM = [
  "You are a runtime-control classifier for an AI agent.",
  "Classify the user's current request once so multiple hooks do not each call an LLM.",
  "Use semantic judgment across languages. Do not rely on keyword matching.",
  "",
  "Return ONLY strict JSON with this shape:",
  "{",
  '  "turnMode": {"label": "coding" | "exploratory" | "other", "confidence": number},',
  '  "skipTdd": boolean,',
  '  "implementationIntent": boolean,',
  '  "documentOrFileOperation": boolean,',
  '  "deterministic": {',
  '    "requiresDeterministic": boolean,',
  '    "kinds": ["clock" | "date_range" | "calculation" | "counting" | "data_query" | "comparison"],',
  '    "reason": string,',
  '    "suggestedTools": string[],',
  '    "acceptanceCriteria": string[]',
  "  },",
  '  "fileDelivery": {',
  '    "intent": "deliver_existing" | "none",',
  '    "path": string | null,',
  '    "wantsChatDelivery": boolean,',
  '    "wantsKbDelivery": boolean,',
  '    "wantsFileOutput": boolean',
  "  },",
  '  "planning": {',
  '    "need": "none" | "inline" | "task_board" | "approval_plan" | "pipeline_or_bulk",',
  '    "reason": string,',
  '    "suggestedStrategy": string',
  "  },",
  '  "goalProgress": {',
  '    "requiresAction": boolean,',
  '    "actionKinds": string[],',
  '    "reason": string',
  "  },",
  '  "sourceAuthority": {',
  '    "longTermMemoryPolicy": "normal" | "background_only" | "disabled",',
  '    "currentSourcesAuthoritative": boolean,',
  '    "reason": string',
  "  },",
  '  "clarification": {',
  '    "needed": boolean,',
  '    "reason": string,',
  '    "question": string | null,',
  '    "choices": string[],',
  '    "allowFreeText": boolean,',
  '    "riskIfAssumed": string',
  "  },",
  '  "memoryMutation": {',
  '    "intent": "none" | "redact",',
  '    "target": string | null,',
  '    "rawFileRedactionRequested": boolean,',
  '    "reason": string',
  "  }",
  "}",
  "",
  "turnMode:",
  "- coding: author, fix, debug, refactor, test, review, or deploy code.",
  "- exploratory: prototype or throwaway experiment.",
  "- other: analysis, document writing, file delivery, research, chat, planning, or questions.",
  "",
  "skipTdd is true only when the user explicitly asks to skip tests/TDD.",
  "implementationIntent is true for non-trivial code implementation, not document/file creation.",
  "documentOrFileOperation is true for document/file/report/spreadsheet/export/create/convert/deliver requests.",
  "deterministic.requiresDeterministic is true for exact arithmetic, counts, dates, time windows, averages, financial metrics, database/table analytics, or exact comparisons.",
  "fileDelivery.intent is deliver_existing only when the user asks to send/attach/deliver an existing file, not to create, read, summarize, or analyze it.",
  "planning.need:",
  "- none: greetings, short factual answers, simple explanations, existing-file delivery, or 1-2 step work.",
  "- inline: small multi-step work where a brief checklist is enough and no tool/state gate is needed.",
  "- task_board: business work with multiple coordinated steps, multiple sources/files, document creation, deterministic analysis, or deliverables that should be tracked.",
  "- approval_plan: risky or mutating work such as code/infra changes, deploys, DB/auth/billing/security changes, external sends/uploads/payments/public posts, or work requiring user approval before execution.",
  "- pipeline_or_bulk: large repeated/batch/parallel/long-running work that should use pipeline or bulk execution.",
  [
    "goalProgress.requiresAction is true when the request needs the agent to make concrete progress with runtime resources, tools, or external state before answering.",
    "Examples: browser interaction, file/document work, sending/uploading/delivering, web or KB research, coding/deploying, debugging by inspection, API/integration checks, data extraction/analysis, subagent dispatch, or workspace operations.",
    "Set goalProgress.requiresAction=true for explicit work orders like:",
    '- English: "Spawn 4 subagents with different SOTA LLMs, compute 1+1, cross-validate, and return the final answer as a .md file."',
    '- Korean: "서브에이전트 4개 띄워서 각각 계산시키고 검증한 뒤 md 파일로 줘."',
    '- English: "Open this site, click through it like a human, and report what happens."',
    '- Korean: "사람처럼 브라우저에서 직접 클릭하고 입력하면서 테스트해봐."',
    '- Korean: "파일 만들어서 채팅에 첨부해줘."',
    "goalProgress.requiresAction is false for pure explanation, opinion, brainstorming, or answering from supplied context without needing tool evidence.",
    "For browser, app, web UI, or human-like GUI operation requests, include browser_interaction in actionKinds so the runtime can enforce short checkpointed tool loops.",
    "actionKinds should use short semantic labels such as browser_interaction, file_delivery, file_editing, research, debugging, coding, integration_check, data_analysis, subagent_dispatch, communication, or other.",
  ].join("\n"),
  "When the user explicitly asks the agent to run tools, spawn subagents, create/send files, browse, inspect, compute from data, or operate on external/workspace state, set goalProgress.requiresAction=true even if the exact tool is unknown.",
  [
    "sourceAuthority:",
    "- Use semantic judgment across languages, not regex or keyword matching.",
    "- longTermMemoryPolicy=disabled only when the user explicitly says not to use prior memory/history, to ignore old context, to reset, or to answer only from the current source.",
    "- longTermMemoryPolicy=background_only when the latest user message makes current files, selected KB, current images, or newly supplied information the basis/source of truth, but does not explicitly forbid memory.",
    "- longTermMemoryPolicy=normal when ordinary memory continuity is allowed, including when the user explicitly asks to continue an earlier topic.",
    "- currentSourcesAuthoritative=true when the latest request treats current-turn files, selected KB, current images, pasted data, or newly supplied facts as authoritative for the answer.",
  ].join("\n"),
  [
    "clarification:",
    "- needed=true only for non-trivial work where missing information materially changes the outcome: code changes, deploys, DB/auth/billing/security changes, document/artifact creation, multi-step analysis, external sends/uploads, deterministic data work, or other concrete tool-backed work.",
    "- needed=false for greetings, casual chat, simple factual questions, simple explanations, simple file-understanding, and cases where a reversible/safe assumption can be stated.",
    "- Ask one focused question. If several details are missing, combine only the critical missing decision into one concise question.",
    "- choices should contain 2-4 short likely options when useful. Put the recommended/default option first only if it is genuinely safe.",
    "- allowFreeText=true when the choices may not cover the user's intended answer.",
    "- riskIfAssumed should state what would go wrong if the agent guessed.",
  ].join("\n"),
  [
    "memoryMutation:",
    "- Use semantic judgment across languages, not regex or keyword matching.",
    "- intent=redact when the user asks the agent to delete, erase, remove, redact, forget, or stop retaining specific content from the bot's Hipocampus memory files or persistent memory.",
    "- intent=none for ordinary discussion about memory architecture, summaries, or non-mutating questions.",
    "- target should be the shortest user-specified phrase/entity/topic to remove when available; use null when no target is supplied.",
    "- rawFileRedactionRequested=true when the user explicitly wants content removed from memory files, not merely ignored in the next answer.",
  ].join("\n"),
  "For purely conversational ambiguity, choose the safer non-triggering value.",
].join("\n");

const FINAL_ANSWER_CLASSIFIER_SYSTEM = [
  "You are a final-answer meta classifier for an AI agent runtime.",
  "Classify one drafted assistant answer once so multiple beforeCommit gates can share the result.",
  "Use semantic judgment across languages. Do not rely on keyword matching.",
  "",
  "Return ONLY strict JSON with this shape:",
  "{",
  '  "internalReasoningLeak": boolean,',
  '  "lazyRefusal": boolean,',
  '  "selfClaim": boolean,',
  '  "deferralPromise": boolean,',
  '  "assistantClaimsFileCreated": boolean,',
  '  "assistantClaimsChatDelivery": boolean,',
  '  "assistantClaimsKbDelivery": boolean,',
  '  "assistantReportsDeliveryFailure": boolean,',
  '  "assistantReportsDeliveryUnverified": boolean,',
  '  "assistantGivesUpEarly": boolean,',
  '  "assistantClaimsActionWithoutEvidence": boolean,',
  '  "assistantEndsWithUnexecutedPlan": boolean,',
  '  "assistantClaimsMemoryMutation": boolean,',
  '  "assistantReportsMemoryMutationFailure": boolean,',
  '  "sourceAuthorityViolation": boolean,',
  '  "reason": string',
  "}",
  "",
  "internalReasoningLeak: answer exposes hidden planning or meta-reasoning such as what the model should do next, rather than user-facing prose.",
  "lazyRefusal: answer claims it cannot find, access, verify, or provide something without evidence of actual investigation.",
  "selfClaim: answer asserts something about the bot's own workspace, prompt, configuration, or memory.",
  [
    "deferralPromise: answer promises to start, continue, finish, complete, send, deliver, share, or update results after this message instead of completing or plainly failing in this turn.",
    "Set deferralPromise=true for deferred-work final answers, including time promises and 'when done' promises such as:",
    '- Korean: "지금 1-3 마무리 시작할 게. 10분 내 완성할 거야."',
    '- Korean: "완료되면 결과 보내드리겠습니다."',
    '- English: "I will start now and finish later."',
    '- English: "I will send the results when done."',
    "Set deferralPromise=false when the answer already provides the requested result, or plainly says the work cannot be completed now without promising future delivery.",
  ].join("\n"),
  "assistantClaimsFileCreated: answer says a file/document/report/artifact was created/generated/written/saved/prepared.",
  "assistantClaimsChatDelivery: answer says a file/result was sent, attached, uploaded, delivered, or made available in the current chat/channel.",
  "assistantClaimsKbDelivery: answer says something was saved/uploaded/added to KB, knowledge base, memory, or a persistent document store.",
  "assistantReportsDeliveryFailure: answer plainly says delivery failed, was not possible, was not sent, or asks the user for another path instead of claiming success.",
  "assistantReportsDeliveryUnverified: answer says delivery status is only system-side/transport-level, asks the user to confirm whether the file arrived, or says user-visible receipt/display is not verified.",
  [
    "assistantGivesUpEarly: answer stops short of a concrete goal after a small failed attempt, asks the user to choose a next path, or treats one recoverable tool failure as terminal while plausible alternatives remain.",
    "Set false when the answer completed the goal, gives the requested result, or reports a hard blocker after multiple concrete attempts with evidence.",
  ].join("\n"),
  [
    "assistantClaimsActionWithoutEvidence: answer claims the assistant already investigated, checked, debugged, clicked, opened, filled, ran, sent, created, uploaded, modified, or otherwise took concrete action.",
    "This is a semantic claim detector only; runtime gates compare it against tool evidence. Set false for hypotheticals, plans, or advice that clearly does not claim an action was already performed.",
  ].join("\n"),
  [
    "assistantEndsWithUnexecutedPlan: answer ends the turn with a plan, procedure, next-step promise, or dispatch announcement instead of executing the next needed runtime action and returning the requested result.",
    "Use semantic judgment across languages. Set true even if some preparatory work happened, when the answer's substance is 'now I will do the real work' and no result/deliverable is returned.",
    "Set true for plan-only or dispatch-only drafts such as:",
    '- English: "I\'ll spawn 4 subagents with different SOTA LLMs to compute 1+1, then cross-validate and deliver the result as a markdown file."',
    '- Korean: "이제 서브에이전트를 띄우겠습니다."',
    '- Korean: "컨텍스트 파일이 준비되었습니다. 이제 낙관 파트너와 회의 파트너를 병렬 디스패치하겠습니다."',
    '- English: "I will start the browser review now."',
    "These do not complete the turn because they announce the real work instead of returning the requested result or evidence-backed blocker.",
    "Set false when the answer includes the actual requested result, reports a hard blocker with concrete evidence, or confirms a real asynchronous handoff already scheduled by tools.",
  ].join("\n"),
  "When the user asked for concrete runtime progress and the draft only announces future work, choose true for assistantEndsWithUnexecutedPlan rather than false.",
  "assistantClaimsMemoryMutation: answer claims persistent memory, Hipocampus memory, remembered facts, memory files, KB-like memory, or prior remembered content was deleted, erased, removed, redacted, forgotten, or updated to no longer contain something.",
  "assistantReportsMemoryMutationFailure: answer plainly says the requested memory deletion/redaction was not completed, the target was not found, raw deletion requires confirmation, or memory could not be modified.",
  [
    "sourceAuthorityViolation: use this only when a Source authority context is provided.",
    "Set true when the draft uses long-term memory as evidence despite long_term_memory_policy=disabled, or lets recalled memory override/replace/reinterpret L0 latest user instructions or L1 current-turn sources.",
    "Set true when the draft answers from an old memory topic instead of the current file/KB/image/source the user supplied.",
    "Set false for passive background mentions, explicit user requests to continue prior topics, or drafts based on the latest user/current source.",
  ].join("\n"),
  "When uncertain, choose false.",
].join("\n");

export function hashMetaInput(...parts: readonly string[]): string {
  const hash = createHash("sha256");
  for (const part of parts) {
    hash.update(part);
    hash.update("\0");
  }
  return hash.digest("hex").slice(0, 24);
}

function normalizeStrings(value: unknown, limit: number): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter((item) => item.length > 0)
    .slice(0, limit);
}

function normalizeKinds(value: unknown): DeterministicRequirementKind[] {
  return normalizeStrings(value, VALID_KINDS.length).filter(
    (kind): kind is DeterministicRequirementKind => VALID_KIND_SET.has(kind),
  );
}

function normalizePlanningNeed(value: unknown): PlanningNeed {
  const raw = typeof value === "string" ? value.trim() : "";
  return VALID_PLANNING_NEED_SET.has(raw) ? (raw as PlanningNeed) : "none";
}

function normalizeLongTermMemoryPolicy(value: unknown): LongTermMemoryPolicy {
  const raw = typeof value === "string" ? value.trim() : "";
  return VALID_MEMORY_POLICY_SET.has(raw) ? (raw as LongTermMemoryPolicy) : "normal";
}

function normalizeClarificationChoices(value: unknown): string[] {
  return normalizeStrings(value, 4).map((choice) => choice.slice(0, 80));
}

function extractJsonObject(raw: string): Record<string, unknown> | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    const start = trimmed.indexOf("{");
    const end = trimmed.lastIndexOf("}");
    if (start === -1 || end <= start) return null;
    try {
      const parsed = JSON.parse(trimmed.slice(start, end + 1)) as unknown;
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : null;
    } catch {
      return null;
    }
  }
}

function bool(value: unknown): boolean {
  return value === true;
}

function stringOrNull(value: unknown, max = 500): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed.slice(0, max) : null;
}

function classifierTimeoutWithinHook(maxMs: number, hookDeadlineMs: number): number {
  const finiteDeadline = Number.isFinite(hookDeadlineMs) ? Math.max(1, hookDeadlineMs) : maxMs;
  const headroom =
    finiteDeadline > CLASSIFIER_HOOK_DEADLINE_HEADROOM_MS + 1
      ? CLASSIFIER_HOOK_DEADLINE_HEADROOM_MS
      : Math.max(1, Math.floor(finiteDeadline / 2));
  return Math.max(1, Math.min(maxMs, finiteDeadline - headroom));
}

function makeClassifierSignal(parent?: AbortSignal): {
  signal: AbortSignal;
  abort: (reason: Error) => void;
  cleanup: () => void;
} {
  const controller = new AbortController();
  const abort = (reason: Error): void => {
    if (!controller.signal.aborted) controller.abort(reason);
  };
  const onParentAbort = (): void => {
    const reason = (parent as (AbortSignal & { reason?: unknown }) | undefined)?.reason;
    abort(reason instanceof Error ? reason : new Error("classifier aborted"));
  };

  if (parent?.aborted) {
    onParentAbort();
  } else if (parent) {
    parent.addEventListener("abort", onParentAbort, { once: true });
  }

  return {
    signal: controller.signal,
    abort,
    cleanup: () => parent?.removeEventListener("abort", onParentAbort),
  };
}

type TimeoutResult = { timedOut: true };

async function nextWithTimeout<T>(
  iterator: AsyncIterator<T>,
  timeoutMs: number,
): Promise<IteratorResult<T> | TimeoutResult> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const timeout = new Promise<TimeoutResult>((resolve) => {
    timer = setTimeout(() => resolve({ timedOut: true }), Math.max(1, timeoutMs));
  });
  const next = iterator.next();
  next.catch(() => undefined);
  try {
    return await Promise.race([next, timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

function objectField(obj: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = obj[key];
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function clampConfidence(value: unknown): number {
  const n = typeof value === "number" && Number.isFinite(value) ? value : 0.5;
  return Math.max(0, Math.min(1, n));
}

export function defaultRequestMeta(reason = "classifier unavailable"): RequestMetaClassificationResult {
  return {
    turnMode: { label: "other", confidence: 0.5 },
    skipTdd: false,
    implementationIntent: false,
    documentOrFileOperation: false,
    deterministic: {
      requiresDeterministic: false,
      kinds: [],
      reason,
      suggestedTools: [],
      acceptanceCriteria: [],
    },
    fileDelivery: {
      intent: "none",
      path: null,
      wantsChatDelivery: false,
      wantsKbDelivery: false,
      wantsFileOutput: false,
    },
    planning: {
      need: "none",
      reason: "No runtime planning required.",
      suggestedStrategy: "Answer directly.",
    },
    goalProgress: {
      requiresAction: false,
      actionKinds: [],
      reason,
    },
    sourceAuthority: {
      longTermMemoryPolicy: "normal",
      currentSourcesAuthoritative: false,
      reason: "No source authority override required.",
    },
    clarification: {
      needed: false,
      reason: "No clarification required.",
      question: null,
      choices: [],
      allowFreeText: false,
      riskIfAssumed: "",
    },
    memoryMutation: {
      intent: "none",
      target: null,
      rawFileRedactionRequested: false,
      reason,
    },
  };
}

export function parseRequestMetaOutput(raw: string): RequestMetaClassificationResult {
  const parsed = extractJsonObject(raw);
  if (!parsed) return defaultRequestMeta("classifier output was not valid JSON");

  const mode = objectField(parsed, "turnMode");
  const rawLabel = stringOrNull(mode.label, 30);
  const label =
    rawLabel === "coding" || rawLabel === "exploratory" || rawLabel === "other"
      ? rawLabel
      : "other";
  const deterministic = objectField(parsed, "deterministic");
  const kinds = normalizeKinds(deterministic.kinds);
  const requiresDeterministic = bool(deterministic.requiresDeterministic);
  const fileDelivery = objectField(parsed, "fileDelivery");
  const rawIntent = stringOrNull(fileDelivery.intent, 30);
  const intent = rawIntent === "deliver_existing" ? "deliver_existing" : "none";
  const planning = objectField(parsed, "planning");
  const planningNeed = normalizePlanningNeed(planning.need);
  const goalProgress = objectField(parsed, "goalProgress");
  const sourceAuthority = objectField(parsed, "sourceAuthority");
  const clarification = objectField(parsed, "clarification");
  const memoryMutation = objectField(parsed, "memoryMutation");
  const clarificationQuestion = stringOrNull(clarification.question, 500);
  const clarificationChoices = normalizeClarificationChoices(clarification.choices);
  const clarificationNeeded = bool(clarification.needed) && clarificationQuestion !== null;
  const memoryMutationIntent =
    stringOrNull(memoryMutation.intent, 30) === "redact" ? "redact" : "none";

  return {
    turnMode: {
      label,
      confidence: clampConfidence(mode.confidence),
    },
    skipTdd: bool(parsed.skipTdd),
    implementationIntent: bool(parsed.implementationIntent),
    documentOrFileOperation: bool(parsed.documentOrFileOperation),
    deterministic: {
      requiresDeterministic,
      kinds: requiresDeterministic && kinds.length === 0 ? ["calculation"] : kinds,
      reason:
        stringOrNull(deterministic.reason) ??
        (requiresDeterministic
          ? "The request needs deterministic evidence."
          : "The request does not need deterministic evidence."),
      suggestedTools: normalizeStrings(deterministic.suggestedTools, 8),
      acceptanceCriteria: normalizeStrings(deterministic.acceptanceCriteria, 8),
    },
    fileDelivery: {
      intent,
      path: stringOrNull(fileDelivery.path, 1_000),
      wantsChatDelivery: bool(fileDelivery.wantsChatDelivery),
      wantsKbDelivery: bool(fileDelivery.wantsKbDelivery),
      wantsFileOutput: bool(fileDelivery.wantsFileOutput),
    },
    planning: {
      need: planningNeed,
      reason:
        stringOrNull(planning.reason) ??
        (planningNeed === "none"
          ? "No runtime planning required."
          : "The request needs runtime planning discipline."),
      suggestedStrategy:
        stringOrNull(planning.suggestedStrategy) ??
        (planningNeed === "none" ? "Answer directly." : "Plan before executing."),
    },
    goalProgress: {
      requiresAction: bool(goalProgress.requiresAction),
      actionKinds: normalizeStrings(goalProgress.actionKinds, 8),
      reason:
        stringOrNull(goalProgress.reason) ??
        (bool(goalProgress.requiresAction)
          ? "The request requires concrete action evidence."
          : "The request does not require concrete action evidence."),
    },
    sourceAuthority: {
      longTermMemoryPolicy: normalizeLongTermMemoryPolicy(
        sourceAuthority.longTermMemoryPolicy,
      ),
      currentSourcesAuthoritative: bool(sourceAuthority.currentSourcesAuthoritative),
      reason:
        stringOrNull(sourceAuthority.reason) ??
        "No source authority override required.",
    },
    clarification: {
      needed: clarificationNeeded,
      reason:
        stringOrNull(clarification.reason) ??
        (clarificationNeeded
          ? "Clarification is required before execution."
          : "No clarification required."),
      question: clarificationNeeded ? clarificationQuestion : null,
      choices: clarificationNeeded ? clarificationChoices : [],
      allowFreeText:
        clarificationNeeded &&
        (bool(clarification.allowFreeText) || clarificationChoices.length === 0),
      riskIfAssumed: clarificationNeeded
        ? stringOrNull(clarification.riskIfAssumed) ?? ""
        : "",
    },
    memoryMutation: {
      intent: memoryMutationIntent,
      target:
        memoryMutationIntent === "redact"
          ? stringOrNull(memoryMutation.target, 1_000)
          : null,
      rawFileRedactionRequested:
        memoryMutationIntent === "redact" && bool(memoryMutation.rawFileRedactionRequested),
      reason:
        stringOrNull(memoryMutation.reason) ??
        (memoryMutationIntent === "redact"
          ? "The user asked to remove content from persistent memory."
          : "No memory mutation requested."),
    },
  };
}

export function defaultFinalAnswerMeta(
  reason = "classifier unavailable",
): FinalAnswerMetaClassificationResult {
  return {
    internalReasoningLeak: false,
    lazyRefusal: false,
    selfClaim: false,
    deferralPromise: false,
    assistantClaimsFileCreated: false,
    assistantClaimsChatDelivery: false,
    assistantClaimsKbDelivery: false,
    assistantReportsDeliveryFailure: false,
    assistantReportsDeliveryUnverified: false,
    assistantGivesUpEarly: false,
    assistantClaimsActionWithoutEvidence: false,
    assistantEndsWithUnexecutedPlan: false,
    assistantClaimsMemoryMutation: false,
    assistantReportsMemoryMutationFailure: false,
    sourceAuthorityViolation: false,
    reason,
  };
}

export function parseFinalAnswerMetaOutput(raw: string): FinalAnswerMetaClassificationResult {
  const parsed = extractJsonObject(raw);
  if (!parsed) return defaultFinalAnswerMeta("classifier output was not valid JSON");
  return {
    internalReasoningLeak: bool(parsed.internalReasoningLeak),
    lazyRefusal: bool(parsed.lazyRefusal),
    selfClaim: bool(parsed.selfClaim),
    deferralPromise: bool(parsed.deferralPromise),
    assistantClaimsFileCreated: bool(parsed.assistantClaimsFileCreated),
    assistantClaimsChatDelivery: bool(parsed.assistantClaimsChatDelivery),
    assistantClaimsKbDelivery: bool(parsed.assistantClaimsKbDelivery),
    assistantReportsDeliveryFailure: bool(parsed.assistantReportsDeliveryFailure),
    assistantReportsDeliveryUnverified: bool(parsed.assistantReportsDeliveryUnverified),
    assistantGivesUpEarly: bool(parsed.assistantGivesUpEarly),
    assistantClaimsActionWithoutEvidence: bool(parsed.assistantClaimsActionWithoutEvidence),
    assistantEndsWithUnexecutedPlan: bool(parsed.assistantEndsWithUnexecutedPlan),
    assistantClaimsMemoryMutation: bool(parsed.assistantClaimsMemoryMutation),
    assistantReportsMemoryMutationFailure: bool(parsed.assistantReportsMemoryMutationFailure),
    sourceAuthorityViolation: bool(parsed.sourceAuthorityViolation),
    reason: stringOrNull(parsed.reason) ?? "classified final answer metadata.",
  };
}

async function streamClassifier(input: {
  llm: LLMClient;
  model: string;
  system: string;
  userText: string;
  maxTokens: number;
  timeoutMs: number;
  signal?: AbortSignal;
}): Promise<string> {
  const deadline = Date.now() + input.timeoutMs;
  let output = "";
  let timedOut = false;
  const classifierSignal = makeClassifierSignal(input.signal);
  const stream = input.llm.stream({
    model: input.model,
    system: input.system,
    messages: [{ role: "user", content: [{ type: "text", text: input.userText }] }],
    max_tokens: input.maxTokens,
    temperature: 0,
    signal: classifierSignal.signal,
  });
  const iterator = stream[Symbol.asyncIterator]();
  try {
    while (true) {
      const remainingMs = deadline - Date.now();
      if (remainingMs <= 0) {
        timedOut = true;
        break;
      }

      const next = await nextWithTimeout(iterator, remainingMs);
      if ("timedOut" in next) {
        timedOut = true;
        break;
      }
      if (next.done) break;

      const event = next.value;
      if (event.kind === "text_delta") output += event.delta;
      if (event.kind === "message_end" || event.kind === "error") break;
    }
  } finally {
    if (timedOut) {
      classifierSignal.abort(new Error("classifier timeout"));
      const closePromise = iterator.return?.();
      void closePromise?.catch(() => undefined);
    }
    classifierSignal.cleanup();
  }
  return output;
}

export async function classifyRequestMeta(input: {
  llm: LLMClient;
  model: string;
  userMessage: string;
  timeoutMs?: number;
  signal?: AbortSignal;
}): Promise<RequestMetaClassificationResult> {
  if (!input.userMessage.trim()) return defaultRequestMeta("empty request");
  try {
    const raw = await streamClassifier({
      llm: input.llm,
      model: input.model,
      system: REQUEST_CLASSIFIER_SYSTEM,
      userText: [
        "Classify this user request:",
        "",
        input.userMessage.slice(0, 4_000),
        "",
        "Return strict JSON only.",
      ].join("\n"),
      maxTokens: 900,
      timeoutMs: input.timeoutMs ?? REQUEST_CLASSIFIER_TIMEOUT_MS,
      signal: input.signal,
    });
    return parseRequestMetaOutput(raw);
  } catch {
    return defaultRequestMeta("classifier failed open");
  }
}

export async function classifyFinalAnswerMeta(input: {
  llm: LLMClient;
  model: string;
  userMessage: string;
  assistantText: string;
  sourceAuthorityContext?: string;
  timeoutMs?: number;
  signal?: AbortSignal;
}): Promise<FinalAnswerMetaClassificationResult> {
  if (!input.assistantText.trim()) return defaultFinalAnswerMeta("empty final answer");
  try {
    const raw = await streamClassifier({
      llm: input.llm,
      model: input.model,
      system: FINAL_ANSWER_CLASSIFIER_SYSTEM,
      userText: [
        "User request:",
        input.userMessage.slice(0, 2_000),
        "",
        ...(input.sourceAuthorityContext
          ? [
              "Source authority context:",
              input.sourceAuthorityContext.slice(0, 2_000),
              "",
            ]
          : []),
        "Draft assistant answer:",
        input.assistantText.slice(0, 4_000),
        "",
        "Return strict JSON only.",
      ].join("\n"),
      maxTokens: 700,
      timeoutMs: input.timeoutMs ?? FINAL_CLASSIFIER_TIMEOUT_MS,
      signal: input.signal,
    });
    return parseFinalAnswerMetaOutput(raw);
  } catch {
    return defaultFinalAnswerMeta("classifier failed open");
  }
}

export async function getOrClassifyRequestMeta(
  ctx: HookContext,
  input: { userMessage: string },
): Promise<RequestMetaClassificationResult> {
  const inputHash = hashMetaInput(input.userMessage);
  const cached = ctx.executionContract?.getRequestMetaClassification(ctx.turnId, inputHash);
  if (cached) return cached;
  if (!ctx.llm) return defaultRequestMeta("no LLM context");

  const result = await classifyRequestMeta({
    llm: ctx.llm,
    model: ctx.agentModel,
    userMessage: input.userMessage,
    timeoutMs: classifierTimeoutWithinHook(REQUEST_CLASSIFIER_TIMEOUT_MS, ctx.deadlineMs),
    signal: ctx.abortSignal,
  });
  ctx.executionContract?.recordRequestMetaClassification({
    turnId: ctx.turnId,
    inputHash,
    source: "llm_classifier",
    result,
  });
  return result;
}

export async function getOrClassifyFinalAnswerMeta(
  ctx: HookContext,
  input: { userMessage: string; assistantText: string },
): Promise<FinalAnswerMetaClassificationResult> {
  const sourceAuthorityContext = buildSourceAuthorityClassifierContext({
    records: ctx.executionContract?.sourceAuthorityForTurn(ctx.turnId) ?? [],
    memoryPhrases: [
      ...new Set(
        (ctx.executionContract?.memoryRecallForTurn(ctx.turnId) ?? [])
          .filter((record) => record.continuity === "background")
          .flatMap((record) => record.distinctivePhrases)
          .slice(0, 12),
      ),
    ],
  });
  const inputHash = hashMetaInput(
    input.userMessage,
    input.assistantText,
    sourceAuthorityContext,
  );
  const cached = ctx.executionContract?.getFinalAnswerClassification(ctx.turnId, inputHash);
  if (cached) return cached;
  if (!ctx.llm) return defaultFinalAnswerMeta("no LLM context");

  const result = await classifyFinalAnswerMeta({
    llm: ctx.llm,
    model: ctx.agentModel,
    userMessage: input.userMessage,
    assistantText: input.assistantText,
    sourceAuthorityContext,
    timeoutMs: classifierTimeoutWithinHook(FINAL_CLASSIFIER_TIMEOUT_MS, ctx.deadlineMs),
    signal: ctx.abortSignal,
  });
  ctx.executionContract?.recordFinalAnswerClassification({
    turnId: ctx.turnId,
    inputHash,
    source: "llm_classifier",
    result,
  });
  return result;
}
