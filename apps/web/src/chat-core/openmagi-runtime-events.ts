export type ValidatorTrustClass = "deterministic" | "llm_assisted" | "runtime";
export type GuardrailStatus =
  | "passed"
  | "blocked"
  | "repair"
  | "approval_required"
  | "abstained"
  | "fallback";
export type ProjectionMode =
  | "structured_claims_only"
  | "artifact_projection"
  | "raw_text_allowed";

export interface DeterministicGuardrailEvent {
  type: "deterministic_guardrail";
  guardrailId: string;
  stage: string;
  status: GuardrailStatus;
  reasonCodes: string[];
  policyDecisionId: string;
  validatorTrustClass: ValidatorTrustClass;
  evidenceRefs: string[];
  redactionStatus: "redacted" | "none";
}

export interface DeterministicWorkflowEvent {
  type: "deterministic_workflow";
  workflowId: string;
  workflowVersion: string;
  effectivePolicySnapshotDigest: string;
  ledgerHeadDigest?: string;
  checkpointId?: string;
  routeId?: string;
  governed?: boolean;
}

export interface DeterministicProjectionEvent {
  type: "deterministic_projection";
  projectionMode: ProjectionMode;
  outputAllowed: boolean;
  blockedReasonCodes: string[];
  claimCount?: number;
  renderedClaimCount?: number;
}

export interface DeterministicFallbackEvent {
  type: "deterministic_fallback";
  fromAuthority: "python" | "typescript" | "none";
  toAuthority: "typescript" | "none";
  reasonCode: string;
  requestDigest?: string;
}

export interface DeterministicRecipeSelectionEvent {
  type: "deterministic_recipe_selection";
  selectionSource?: "auto" | "explicit" | "session_default";
  status?:
    | "auto_selected"
    | "explicit_applied"
    | "explicit_blocked"
    | "explicit_incompatible"
    | "explicit_requested"
    | "explicit_unavailable";
  requestedRecipeRefs?: Array<{
    recipeId: string;
    version?: string;
    digest?: string;
  }>;
  appliedRecipeRefs?: Array<{
    recipeId: string;
    version?: string;
    digest?: string;
  }>;
  omittedRecipeRefs?: Array<{
    recipeId: string;
    version?: string;
    digest?: string;
  }>;
  omissionReasons?: string[];
  policySnapshotDigest?: string;
  turnBlocked?: boolean;
  fallbackUsed?: boolean;
  nextAction?: string;
  appliedRecipes: Array<{
    recipeId: string;
    version: string;
    sourceDigest?: string;
    role: "primary" | "dependency" | "hard_safety" | "default";
    governed: boolean;
  }>;
}

export interface DeterministicVerificationGateEvent {
  type: "deterministic_verification_gate";
  gateId: string;
  stage: string;
  status: "pending" | GuardrailStatus;
  validatorTrustClass: ValidatorTrustClass;
  reasonCodes: string[];
  evidenceRefs: string[];
  policyDecisionId?: string;
  checkedAt?: number;
}

export interface TextDeltaEvent {
  type: "text_delta";
  delta: string;
}

export interface ThinkingDeltaEvent {
  type: "thinking_delta";
  delta: string;
}

export type OpenMagiRuntimeEvent =
  | TextDeltaEvent
  | ThinkingDeltaEvent
  | DeterministicGuardrailEvent
  | DeterministicWorkflowEvent
  | DeterministicProjectionEvent
  | DeterministicFallbackEvent
  | DeterministicRecipeSelectionEvent
  | DeterministicVerificationGateEvent;

const PRIVATE_KEY_RE =
  /(?:^(?:args?|auth|google\.adk|private|result|results?|session)$|token|secret|cookie|authorization|password|api[_-]?key|session[_-]?key|raw[_-]?transcript|tool[_-]?args?|tool[_-]?results?|function[_-]?call|private[_-]?metadata|auth[_-]?header)/i;
const RAW_EVENT_TYPE_RE = /^(?:adk_event|google\.adk\.events\.event|tool_result)$/i;
const DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
const EVIDENCE_REF_RE = /^evidence:sha256:[a-f0-9]{64}$/;
const PUBLIC_ID_RE = /^[a-zA-Z0-9._:-]+$/;
const PRIVATE_NORMALIZED_KEYS = new Set([
  "adkevent",
  "apikey",
  "args",
  "arg",
  "auth",
  "authheader",
  "authorization",
  "cookie",
  "conversationtranscript",
  "functioncall",
  "fulltranscript",
  "googleadkevent",
  "googleadk",
  "password",
  "private",
  "privatemetadata",
  "rawtranscript",
  "result",
  "results",
  "secret",
  "session",
  "sessionkey",
  "token",
  "toolarg",
  "toolargs",
  "toolresult",
  "toolresults",
  "transcript",
]);
const PRIVATE_VALUE_RE =
  /google[._-]?adk|api[._-]?key|authorization|auth[._-]?header|cookie|function[._-]?call|password|private[._-]?metadata|raw[._-]?transcript|(?:^|[._:-])session(?:$|[._:-])|secret|token|tool[._-]?args?|tool[._-]?results?|transcript/i;
const SECRET_SHAPE_RE =
  /(?:^|[^a-z0-9])(?:sk-[a-z0-9_-]{6,}|sk-proj-[a-z0-9_-]{6,}|github_pat_[a-z0-9_]{12,}|gh[pousr]_[a-z0-9_]{12,}|xox[abprs]-[a-z0-9-]{12,}|akia[0-9a-z]{16}|eyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}|bearer-[a-z0-9_-]{4,})(?:$|[^a-z0-9])/i;
const PRIVATE_NORMALIZED_VALUE_FRAGMENTS = [
  "apikey",
  "authheader",
  "authorization",
  "bearer",
  "cookie",
  "functioncall",
  "googleadk",
  "password",
  "privatekey",
  "privatemetadata",
  "rawtranscript",
  "secret",
  "session",
  "token",
  "toolarg",
  "toolargs",
  "toolresult",
  "toolresults",
  "transcript",
];

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function string(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function strictStringArray(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item): item is string => typeof item === "string")
    ? value
    : null;
}

function isPrivateKey(key: string): boolean {
  const normalized = key.replace(/[^a-z0-9]/gi, "").toLowerCase();
  return (
    PRIVATE_KEY_RE.test(key) ||
    PRIVATE_NORMALIZED_KEYS.has(normalized) ||
    normalized.includes("session") ||
    normalized.startsWith("auth")
  );
}

function publicIdentifier(value: unknown): string | null {
  if (typeof value !== "string" || !value || value.length > 160) return null;
  const normalized = value.replace(/[^a-z0-9]/gi, "").toLowerCase();
  if (
    !PUBLIC_ID_RE.test(value) ||
    PRIVATE_VALUE_RE.test(value) ||
    SECRET_SHAPE_RE.test(value) ||
    normalized.startsWith("auth") ||
    PRIVATE_NORMALIZED_VALUE_FRAGMENTS.some((fragment) => normalized.includes(fragment))
  ) return null;
  return value;
}

function publicIdentifierArray(value: unknown): string[] | null {
  if (!Array.isArray(value)) return [];
  const items: string[] = [];
  for (const item of value) {
    const id = publicIdentifier(item);
    if (!id) return null;
    items.push(id);
  }
  return items;
}

export function isPublicRuntimeEventPayload(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.every((item) => {
      if (!item || typeof item !== "object") return true;
      return isPublicRuntimeEventPayload(item);
    });
  }
  const item = record(value);
  if (!item) return false;
  for (const [key, child] of Object.entries(item)) {
    if (isPrivateKey(key)) return false;
    if (key === "type" && typeof child === "string" && RAW_EVENT_TYPE_RE.test(child)) return false;
    if (child && typeof child === "object" && !isPublicRuntimeEventPayload(child)) {
      return false;
    }
  }
  return true;
}

function parseGuardrail(payload: Record<string, unknown>): DeterministicGuardrailEvent | null {
  const status = payload.status;
  const validatorTrustClass = payload.validatorTrustClass;
  if (
    status !== "passed" &&
    status !== "blocked" &&
    status !== "repair" &&
    status !== "approval_required" &&
    status !== "abstained" &&
    status !== "fallback"
  ) return null;
  if (
    validatorTrustClass !== "deterministic" &&
    validatorTrustClass !== "llm_assisted" &&
    validatorTrustClass !== "runtime"
  ) return null;
  const guardrailId = publicIdentifier(payload.guardrailId);
  const stage = publicIdentifier(payload.stage);
  const policyDecisionId = publicIdentifier(payload.policyDecisionId);
  const reasonCodes = publicIdentifierArray(payload.reasonCodes);
  if (!guardrailId || !stage || !policyDecisionId) return null;
  if (!reasonCodes) return null;
  const evidenceRefs = strictStringArray(payload.evidenceRefs);
  if (!evidenceRefs || !evidenceRefs.every((ref) => EVIDENCE_REF_RE.test(ref))) return null;
  return {
    type: "deterministic_guardrail",
    guardrailId,
    stage,
    status,
    reasonCodes,
    policyDecisionId,
    validatorTrustClass,
    evidenceRefs,
    redactionStatus: payload.redactionStatus === "none" ? "none" : "redacted",
  };
}

function parseWorkflow(payload: Record<string, unknown>): DeterministicWorkflowEvent | null {
  const workflowId = publicIdentifier(payload.workflowId);
  const workflowVersion = publicIdentifier(payload.workflowVersion);
  const digest = string(payload.effectivePolicySnapshotDigest);
  if (!workflowId || !workflowVersion || !digest || !DIGEST_RE.test(digest)) return null;
  const ledgerHeadDigest = string(payload.ledgerHeadDigest);
  const checkpointId = publicIdentifier(payload.checkpointId);
  const routeId = publicIdentifier(payload.routeId);
  return {
    type: "deterministic_workflow",
    workflowId,
    workflowVersion,
    effectivePolicySnapshotDigest: digest,
    ...(ledgerHeadDigest && DIGEST_RE.test(ledgerHeadDigest) ? { ledgerHeadDigest } : {}),
    ...(checkpointId ? { checkpointId } : {}),
    ...(routeId ? { routeId } : {}),
    ...(typeof payload.governed === "boolean" ? { governed: payload.governed } : {}),
  };
}

function parseProjection(payload: Record<string, unknown>): DeterministicProjectionEvent | null {
  const projectionMode = payload.projectionMode;
  if (
    projectionMode !== "structured_claims_only" &&
    projectionMode !== "artifact_projection" &&
    projectionMode !== "raw_text_allowed"
  ) return null;
  if (typeof payload.outputAllowed !== "boolean") return null;
  const blockedReasonCodes = publicIdentifierArray(payload.blockedReasonCodes);
  if (!blockedReasonCodes) return null;
  return {
    type: "deterministic_projection",
    projectionMode,
    outputAllowed: payload.outputAllowed,
    blockedReasonCodes,
    ...(typeof payload.claimCount === "number" ? { claimCount: payload.claimCount } : {}),
    ...(typeof payload.renderedClaimCount === "number" ? { renderedClaimCount: payload.renderedClaimCount } : {}),
  };
}

function parseFallback(payload: Record<string, unknown>): DeterministicFallbackEvent | null {
  const fromAuthority = payload.fromAuthority;
  const toAuthority = payload.toAuthority;
  const reasonCode = publicIdentifier(payload.reasonCode);
  if (
    (fromAuthority !== "python" && fromAuthority !== "typescript" && fromAuthority !== "none") ||
    (toAuthority !== "typescript" && toAuthority !== "none") ||
    !reasonCode
  ) return null;
  const requestDigest = string(payload.requestDigest);
  return {
    type: "deterministic_fallback",
    fromAuthority,
    toAuthority,
    reasonCode,
    ...(requestDigest && DIGEST_RE.test(requestDigest) ? { requestDigest } : {}),
  };
}

function parseRecipeRole(value: unknown): DeterministicRecipeSelectionEvent["appliedRecipes"][number]["role"] | null {
  return value === "primary" ||
    value === "dependency" ||
    value === "hard_safety" ||
    value === "default"
    ? value
    : null;
}

function parseRecipeSelectionSource(value: unknown): DeterministicRecipeSelectionEvent["selectionSource"] | undefined {
  return value === "auto" || value === "explicit" || value === "session_default"
    ? value
    : undefined;
}

function parseRecipeSelectionStatus(value: unknown): DeterministicRecipeSelectionEvent["status"] | undefined {
  return value === "auto_selected" ||
    value === "explicit_requested" ||
    value === "explicit_applied" ||
    value === "explicit_blocked" ||
    value === "explicit_unavailable" ||
    value === "explicit_incompatible"
    ? value
    : undefined;
}

function parseRecipeRef(value: unknown): NonNullable<DeterministicRecipeSelectionEvent["requestedRecipeRefs"]>[number] | null {
  const item = record(value);
  if (!item) return null;
  const recipeId = publicIdentifier(item.recipeId);
  if (!recipeId) return null;
  const version = publicIdentifier(item.version);
  const digest = string(item.digest);
  if (digest && !DIGEST_RE.test(digest)) return null;
  return {
    recipeId,
    ...(version ? { version } : {}),
    ...(digest ? { digest } : {}),
  };
}

function parseRecipeRefs(value: unknown): Array<NonNullable<DeterministicRecipeSelectionEvent["requestedRecipeRefs"]>[number]> | undefined {
  if (!Array.isArray(value)) return undefined;
  const refs: Array<NonNullable<DeterministicRecipeSelectionEvent["requestedRecipeRefs"]>[number]> = [];
  for (const item of value) {
    const ref = parseRecipeRef(item);
    if (!ref) return undefined;
    refs.push(ref);
  }
  return refs;
}

function parseRecipeSelection(payload: Record<string, unknown>): DeterministicRecipeSelectionEvent | null {
  const appliedRecipeItems = Array.isArray(payload.appliedRecipes) ? payload.appliedRecipes : [];
  const appliedRecipes: DeterministicRecipeSelectionEvent["appliedRecipes"] = [];
  for (const item of appliedRecipeItems) {
    const recipe = record(item);
    if (!recipe) return null;
    const recipeId = publicIdentifier(recipe.recipeId);
    const version = publicIdentifier(recipe.version);
    const role = parseRecipeRole(recipe.role);
    const sourceDigest = string(recipe.sourceDigest);
    if (
      !recipeId ||
      !version ||
      !role ||
      typeof recipe.governed !== "boolean" ||
      (sourceDigest && !DIGEST_RE.test(sourceDigest))
    ) {
      return null;
    }
    appliedRecipes.push({
      recipeId,
      version,
      role,
      governed: recipe.governed,
      ...(sourceDigest ? { sourceDigest } : {}),
    });
  }
  const requestedRecipeRefs = parseRecipeRefs(payload.requestedRecipeRefs);
  const appliedRecipeRefs = parseRecipeRefs(payload.appliedRecipeRefs);
  const omittedRecipeRefs = parseRecipeRefs(payload.omittedRecipeRefs);
  const omissionReasons = publicIdentifierArray(payload.omissionReasons);
  if (
    requestedRecipeRefs === undefined && "requestedRecipeRefs" in payload ||
    appliedRecipeRefs === undefined && "appliedRecipeRefs" in payload ||
    omittedRecipeRefs === undefined && "omittedRecipeRefs" in payload ||
    omissionReasons === null
  ) return null;
  const policySnapshotDigest = string(payload.policySnapshotDigest);
  if (policySnapshotDigest && !DIGEST_RE.test(policySnapshotDigest)) return null;
  const nextAction = publicIdentifier(payload.nextAction);
  return {
    type: "deterministic_recipe_selection",
    appliedRecipes,
    ...(parseRecipeSelectionSource(payload.selectionSource)
      ? { selectionSource: parseRecipeSelectionSource(payload.selectionSource) }
      : {}),
    ...(parseRecipeSelectionStatus(payload.status)
      ? { status: parseRecipeSelectionStatus(payload.status) }
      : {}),
    ...(requestedRecipeRefs ? { requestedRecipeRefs } : {}),
    ...(appliedRecipeRefs ? { appliedRecipeRefs } : {}),
    ...(omittedRecipeRefs ? { omittedRecipeRefs } : {}),
    ...(omissionReasons ? { omissionReasons } : {}),
    ...(policySnapshotDigest ? { policySnapshotDigest } : {}),
    ...(typeof payload.turnBlocked === "boolean" ? { turnBlocked: payload.turnBlocked } : {}),
    ...(typeof payload.fallbackUsed === "boolean" ? { fallbackUsed: payload.fallbackUsed } : {}),
    ...(nextAction ? { nextAction } : {}),
  };
}

function parseVerificationGate(payload: Record<string, unknown>): DeterministicVerificationGateEvent | null {
  const status = payload.status;
  const validatorTrustClass = payload.validatorTrustClass;
  if (
    status !== "pending" &&
    status !== "passed" &&
    status !== "blocked" &&
    status !== "repair" &&
    status !== "approval_required" &&
    status !== "abstained" &&
    status !== "fallback"
  ) return null;
  if (
    validatorTrustClass !== "deterministic" &&
    validatorTrustClass !== "llm_assisted" &&
    validatorTrustClass !== "runtime"
  ) return null;
  const gateId = publicIdentifier(payload.gateId);
  const stage = publicIdentifier(payload.stage);
  const reasonCodes = publicIdentifierArray(payload.reasonCodes);
  const policyDecisionId = publicIdentifier(payload.policyDecisionId);
  const evidenceRefs = strictStringArray(payload.evidenceRefs);
  if (!gateId || !stage || !reasonCodes || !evidenceRefs) return null;
  if (!evidenceRefs.every((ref) => EVIDENCE_REF_RE.test(ref))) return null;
  return {
    type: "deterministic_verification_gate",
    gateId,
    stage,
    status,
    validatorTrustClass,
    reasonCodes,
    evidenceRefs,
    ...(policyDecisionId ? { policyDecisionId } : {}),
    ...(typeof payload.checkedAt === "number" ? { checkedAt: payload.checkedAt } : {}),
  };
}

export function parseOpenMagiRuntimeEvent(value: unknown): OpenMagiRuntimeEvent | null {
  const payload = record(value);
  if (!payload) return null;
  if (payload.type === "deterministic_recipe_selection") {
    return parseRecipeSelection(payload);
  }
  if (!isPublicRuntimeEventPayload(payload)) return null;
  switch (payload.type) {
    case "text_delta": {
      const delta = string(payload.delta);
      return delta === null ? null : { type: "text_delta", delta };
    }
    case "thinking_delta": {
      const delta = string(payload.delta);
      return delta === null ? null : { type: "thinking_delta", delta };
    }
    case "deterministic_guardrail":
      return parseGuardrail(payload);
    case "deterministic_workflow":
      return parseWorkflow(payload);
    case "deterministic_projection":
      return parseProjection(payload);
    case "deterministic_fallback":
      return parseFallback(payload);
    case "deterministic_verification_gate":
      return parseVerificationGate(payload);
    default:
      return null;
  }
}
