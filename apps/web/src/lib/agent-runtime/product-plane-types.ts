export type ProductPlaneContractSemantic =
  | "configured_policy"
  | "effective_policy_snapshot_digest"
  | "hard_invariant"
  | "pending_approval"
  | "blocked_guardrail"
  | "uncertain_fail_passthrough"
  | "delivery_render_receipt"
  | "unsupported_deferred_backend_capability"
  | "default_off_disabled_backend_status";

export type ProductPlaneBackendState =
  | "active"
  | "default_off"
  | "deferred"
  | "disabled"
  | "unsupported";

export type ProductPlaneReadiness =
  | "blocked"
  | "degraded"
  | "disabled"
  | "ready"
  | "unknown";

export type ProductPlaneDecision = "allow" | "block" | "defer" | "review";
export type ProductPlaneGateStatus = "blocked" | "failed" | "passed" | "pending";
export type ProductPlaneJobState =
  | "blocked"
  | "completed"
  | "failed"
  | "pending"
  | "running";
export type ProductPlaneReceiptState =
  | "delivered"
  | "failed"
  | "pending"
  | "rendered";
export type ProductPlaneTrustLevel = "blocked" | "review" | "trusted" | "unknown";
export type ProductPlaneStorageMode =
  | "filesystem"
  | "memory"
  | "object"
  | "postgres"
  | "sqlite"
  | "supabase";
export type ProductPlaneStoragePurpose =
  | "artifact_blob"
  | "artifact_index"
  | "hosted_sync"
  | "runtime_state";
export type ProductPlaneStorageSupport =
  | "hosted_adapter"
  | "optional"
  | "supported_default"
  | "test_dev_only";
export type ProductPlaneStorageWarningReasonCode =
  | "corruption_recovery_unavailable"
  | "migration_pending"
  | "missing_backup_export_config"
  | "multi_writer_sqlite_risk"
  | "runtime_state_pvc_missing";
export type ProductPlaneStorageWarningSeverity = "blocked" | "info" | "warning";

export interface ProductPlanePolicyRef {
  semantic: "configured_policy";
  configId: string;
  label?: string;
  ownerLabel?: string;
}

export interface ProductPlanePolicySnapshot {
  semantic: "effective_policy_snapshot_digest";
  digest: string;
  policyId?: string;
}

export interface ProductPlaneInvariantSummary {
  semantic: "hard_invariant";
  invariantId: string;
  status: ProductPlaneGateStatus;
  reasonCodes: string[];
}

export interface ProductPlaneApprovalSummary {
  semantic: "pending_approval";
  approvalId: string;
  status: "pending";
  reasonCodes: string[];
}

export interface ProductPlaneGuardrailSummary {
  semantic: "blocked_guardrail";
  guardrailId: string;
  status: "blocked";
  reasonCodes: string[];
  decisionId?: string;
}

export interface ProductPlaneFailPassthroughSummary {
  semantic: "uncertain_fail_passthrough";
  reasonCode: string;
  fallbackCapabilityId?: string;
}

export interface ProductPlaneBackendStatus {
  state: ProductPlaneBackendState;
  semantic:
    | "default_off_disabled_backend_status"
    | "unsupported_deferred_backend_capability";
  reasonCode: string;
  capabilityId?: string;
}

export interface ProductPlaneOpsSummary {
  readiness: ProductPlaneReadiness;
  summary: string;
  configuredPolicy: ProductPlanePolicyRef;
  effectivePolicySnapshot: ProductPlanePolicySnapshot;
  hardInvariants: ProductPlaneInvariantSummary[];
  failPassthrough?: ProductPlaneFailPassthroughSummary;
}

export interface ProductPlaneJobLifecycleSummary {
  jobId: string;
  lifecycle: {
    state: ProductPlaneJobState;
    reasonCodes: string[];
    checkpointId?: string;
  };
  ownerLabel?: string;
}

export interface ProductPlaneSandboxDecisionSummary {
  decisionId: string;
  decision: ProductPlaneDecision;
  reasonCodes: string[];
  guardrail?: ProductPlaneGuardrailSummary;
}

export interface ProductPlaneSandboxSummary {
  readiness: ProductPlaneReadiness;
  decisions: ProductPlaneSandboxDecisionSummary[];
}

export interface ProductPlaneCheckpointSummary {
  checkpointId: string;
  ledgerDigest: string;
  parentCheckpointId?: string;
  reasonCodes: string[];
}

export interface ProductPlaneLineageSummary {
  checkpoints: ProductPlaneCheckpointSummary[];
}

export interface ProductPlaneQuotaSpendDecisionSummary {
  decisionId: string;
  decision: ProductPlaneDecision;
  reasonCodes: string[];
  policySnapshotDigest?: string;
}

export interface ProductPlaneQuotaSpendSummary {
  readiness: ProductPlaneReadiness;
  budgetLabel?: string;
  decisions: ProductPlaneQuotaSpendDecisionSummary[];
}

export interface ProductPlaneCredentialLeaseSummary {
  leaseId: string;
  connectorId: string;
  status: "active" | "expired" | "pending" | "revoked";
  ownerLabel?: string;
  policySnapshotDigest?: string;
}

export interface ProductPlaneReceiptSummary {
  semantic: "delivery_render_receipt";
  receiptId: string;
  state: ProductPlaneReceiptState;
  digest?: string;
  reasonCodes: string[];
}

export interface ProductPlaneArtifactSummary {
  artifactId: string;
  renderReceipt: ProductPlaneReceiptSummary;
  deliveryReceipt?: ProductPlaneReceiptSummary;
}

export interface ProductPlaneSelfReviewSummary {
  reviewId: string;
  status: ProductPlaneGateStatus;
  reasonCodes: string[];
  pendingApproval?: ProductPlaneApprovalSummary;
}

export interface ProductPlanePermissionSummary {
  selfReviews: ProductPlaneSelfReviewSummary[];
}

export interface ProductPlaneReleaseEvalGateSummary {
  gateId: string;
  status: ProductPlaneGateStatus;
  reasonCodes: string[];
  backendCapability?: ProductPlaneBackendStatus;
}

export interface ProductPlanePluginConnectorTrustSummary {
  targetId: string;
  targetType: "connector" | "plugin";
  trustLevel: ProductPlaneTrustLevel;
  reasonCodes: string[];
  configuredPolicy?: ProductPlanePolicyRef;
}

export interface ProductPlaneStorageWarning {
  reasonCode: ProductPlaneStorageWarningReasonCode;
  severity: ProductPlaneStorageWarningSeverity;
  label?: string;
}

export interface ProductPlaneStoreSummary {
  storeId: string;
  mode: ProductPlaneStorageMode;
  purpose: ProductPlaneStoragePurpose;
  readiness: ProductPlaneReadiness;
  support: ProductPlaneStorageSupport;
  reasonCodes: string[];
  warnings: ProductPlaneStorageWarning[];
  optional?: boolean;
  requiredForOss?: boolean;
  pathLabel?: string;
  pathDigest?: string;
}

export interface ProductPlaneStorageSummary {
  durableStore: ProductPlaneStoreSummary;
  hostedSync?: ProductPlaneStoreSummary;
  artifactIndex: ProductPlaneStoreSummary;
  artifactBlobStore: ProductPlaneStoreSummary;
}

export interface ProductPlaneProjection {
  version: 1;
  projectionId: string;
  generatedAt: string;
  backendStatus: ProductPlaneBackendStatus;
  contractSemantics: ProductPlaneContractSemantic[];
  storage: ProductPlaneStorageSummary;
  ops: ProductPlaneOpsSummary;
  jobs: ProductPlaneJobLifecycleSummary[];
  sandbox: ProductPlaneSandboxSummary;
  lineage: ProductPlaneLineageSummary;
  quotaSpend: ProductPlaneQuotaSpendSummary;
  credentialLeases: ProductPlaneCredentialLeaseSummary[];
  artifacts: ProductPlaneArtifactSummary[];
  permissions: ProductPlanePermissionSummary;
  releaseEvalGates: ProductPlaneReleaseEvalGateSummary[];
  pluginConnectorTrust: ProductPlanePluginConnectorTrustSummary[];
}

const CONTRACT_SEMANTICS: ProductPlaneContractSemantic[] = [
  "configured_policy",
  "effective_policy_snapshot_digest",
  "hard_invariant",
  "pending_approval",
  "blocked_guardrail",
  "uncertain_fail_passthrough",
  "delivery_render_receipt",
  "unsupported_deferred_backend_capability",
  "default_off_disabled_backend_status",
];

const DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const PUBLIC_ID_RE = /^[a-zA-Z0-9._:-]{1,160}$/;
const SAFE_TEXT_RE = /^[a-zA-Z0-9 ._:/()[\]#-]{0,240}$/;
const PRIVATE_KEY_RE =
  /(?:^auth$|^output$|^session$|^token$|raw|prompt|reasoning|model.*output|tool.*(?:log|args?|results?)|cookie|authorization|bearer|api[_-]?key|session[_-]?key|connector.*token|service.*secret|private.*path|browser.*snapshot|transcript|google.*adk|evidence.*ledger)/i;
const PRIVATE_VALUE_RE =
  /(?:\bauth\b|\boutput\b|\bsession\b|\btoken\b|bearer\s+|api[._-]?key|authorization|cookie|connector[._-]?token|google[._-]?adk|model[._-]?output|private[._-]?path|raw[._-]?(?:prompt|reasoning|transcript)|service[._-]?secret|session[._-]?key|tool[._-]?(?:args?|logs?|results?)|transcript)/i;
const PRIVATE_PATH_RE =
  /(?:^|[\s"'`(])(?:\/[A-Za-z0-9._-]+(?:\/|$)|~[\\/]|(?:\.\.)+[\\/]|[a-zA-Z]:[\\/])|(?:^|[\\/])[^\\/ ]+\.(?:db|env|key|pem|sqlite|sqlite3)(?:$|\b)/i;
const SECRET_SHAPE_RE =
  /(?:^|[^a-z0-9])(?:sk-[a-z0-9_-]{6,}|sk-proj-[a-z0-9_-]{6,}|github_pat_[a-z0-9_]{12,}|gh[pousr]_[a-z0-9_]{12,}|xox[abprs]-[a-z0-9-]{12,}|akia[0-9a-z]{16}|eyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})(?:$|[^a-z0-9])/i;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function safePublicId(value: unknown): string | null {
  if (typeof value !== "string" || !PUBLIC_ID_RE.test(value)) return null;
  if (PRIVATE_VALUE_RE.test(value) || SECRET_SHAPE_RE.test(value)) return null;
  return value;
}

function safeDigest(value: unknown): string | undefined {
  return typeof value === "string" && DIGEST_RE.test(value) ? value : undefined;
}

function safeText(value: unknown): string | undefined {
  if (typeof value !== "string" || !SAFE_TEXT_RE.test(value)) return undefined;
  if (
    PRIVATE_PATH_RE.test(value) ||
    PRIVATE_VALUE_RE.test(value) ||
    SECRET_SHAPE_RE.test(value)
  ) return undefined;
  return value;
}

function safeDate(value: unknown): string | null {
  return typeof value === "string" && ISO_DATE_RE.test(value) ? value : null;
}

function safeReasonCodes(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const code = safePublicId(item);
    return code ? [code] : [];
  });
}

function isPrivateKey(key: string): boolean {
  return PRIVATE_KEY_RE.test(key);
}

function hasPrivateKnownKeys(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasPrivateKnownKeys);
  const item = record(value);
  if (!item) return false;
  return Object.entries(item).some(([key, child]) => {
    if (isPrivateKey(key)) return true;
    return hasPrivateKnownKeys(child);
  });
}

function hasPrivatePayload(value: unknown): boolean {
  if (typeof value === "string") {
    return (
      PRIVATE_PATH_RE.test(value) ||
      PRIVATE_VALUE_RE.test(value) ||
      SECRET_SHAPE_RE.test(value)
    );
  }
  if (Array.isArray(value)) return value.some(hasPrivatePayload);
  const item = record(value);
  if (!item) return false;
  return Object.entries(item).some(([key, child]) => {
    if (isPrivateKey(key)) return true;
    return hasPrivatePayload(child);
  });
}

function parseEnum<T extends string>(
  value: unknown,
  allowed: readonly T[],
): T | null {
  return typeof value === "string" && allowed.includes(value as T)
    ? (value as T)
    : null;
}

function parsePolicyRef(value: unknown): ProductPlanePolicyRef | null {
  const item = record(value);
  if (!item || item.semantic !== "configured_policy") return null;
  const configId = safePublicId(item.configId);
  if (!configId) return null;
  const label = safeText(item.label);
  const ownerLabel = safeText(item.ownerLabel);
  return {
    semantic: "configured_policy",
    configId,
    ...(label ? { label } : {}),
    ...(ownerLabel ? { ownerLabel } : {}),
  };
}

function parsePolicySnapshot(value: unknown): ProductPlanePolicySnapshot | null {
  const item = record(value);
  if (!item || item.semantic !== "effective_policy_snapshot_digest") return null;
  const digest = safeDigest(item.digest);
  if (!digest) return null;
  const policyId = safePublicId(item.policyId);
  return {
    semantic: "effective_policy_snapshot_digest",
    digest,
    ...(policyId ? { policyId } : {}),
  };
}

function parseBackendStatus(value: unknown): ProductPlaneBackendStatus | null {
  const item = record(value);
  if (!item) return null;
  const state = parseEnum(item.state, [
    "active",
    "default_off",
    "deferred",
    "disabled",
    "unsupported",
  ] as const);
  const semantic = parseEnum(item.semantic, [
    "default_off_disabled_backend_status",
    "unsupported_deferred_backend_capability",
  ] as const);
  const reasonCode = safePublicId(item.reasonCode);
  if (!state || !semantic || !reasonCode) return null;
  const capabilityId = safePublicId(item.capabilityId);
  return {
    state,
    semantic,
    reasonCode,
    ...(capabilityId ? { capabilityId } : {}),
  };
}

function parseInvariant(value: unknown): ProductPlaneInvariantSummary | null {
  const item = record(value);
  if (!item || item.semantic !== "hard_invariant") return null;
  const invariantId = safePublicId(item.invariantId);
  const status = parseEnum(item.status, ["blocked", "failed", "passed", "pending"] as const);
  if (!invariantId || !status) return null;
  return {
    semantic: "hard_invariant",
    invariantId,
    status,
    reasonCodes: safeReasonCodes(item.reasonCodes),
  };
}

function parseFailPassthrough(value: unknown): ProductPlaneFailPassthroughSummary | undefined {
  const item = record(value);
  if (!item || item.semantic !== "uncertain_fail_passthrough") return undefined;
  const reasonCode = safePublicId(item.reasonCode);
  if (!reasonCode) return undefined;
  const fallbackCapabilityId = safePublicId(item.fallbackCapabilityId);
  return {
    semantic: "uncertain_fail_passthrough",
    reasonCode,
    ...(fallbackCapabilityId ? { fallbackCapabilityId } : {}),
  };
}

function parseOps(value: unknown): ProductPlaneOpsSummary | null {
  const item = record(value);
  if (!item) return null;
  const readiness = parseEnum(item.readiness, [
    "blocked",
    "degraded",
    "disabled",
    "ready",
    "unknown",
  ] as const);
  const configuredPolicy = parsePolicyRef(item.configuredPolicy);
  const effectivePolicySnapshot = parsePolicySnapshot(item.effectivePolicySnapshot);
  if (!readiness || !configuredPolicy || !effectivePolicySnapshot) return null;
  const failPassthrough = parseFailPassthrough(item.failPassthrough);
  return {
    readiness,
    summary: safeText(item.summary) ?? "",
    configuredPolicy,
    effectivePolicySnapshot,
    hardInvariants: parseArray(item.hardInvariants, parseInvariant),
    ...(failPassthrough ? { failPassthrough } : {}),
  };
}

function parseGuardrail(value: unknown): ProductPlaneGuardrailSummary | undefined {
  const item = record(value);
  if (!item || item.semantic !== "blocked_guardrail") return undefined;
  const guardrailId = safePublicId(item.guardrailId);
  if (!guardrailId) return undefined;
  const decisionId = safePublicId(item.decisionId);
  return {
    semantic: "blocked_guardrail",
    guardrailId,
    status: "blocked",
    reasonCodes: safeReasonCodes(item.reasonCodes),
    ...(decisionId ? { decisionId } : {}),
  };
}

function parseJob(value: unknown): ProductPlaneJobLifecycleSummary | null {
  const item = record(value);
  const lifecycle = record(item?.lifecycle);
  const jobId = safePublicId(item?.jobId);
  const state = parseEnum(lifecycle?.state, [
    "blocked",
    "completed",
    "failed",
    "pending",
    "running",
  ] as const);
  if (!jobId || !lifecycle || !state) return null;
  const checkpointId = safePublicId(lifecycle.checkpointId);
  const ownerLabel = safeText(item?.ownerLabel);
  return {
    jobId,
    lifecycle: {
      state,
      reasonCodes: safeReasonCodes(lifecycle.reasonCodes),
      ...(checkpointId ? { checkpointId } : {}),
    },
    ...(ownerLabel ? { ownerLabel } : {}),
  };
}

function parseSandboxDecision(value: unknown): ProductPlaneSandboxDecisionSummary | null {
  const item = record(value);
  if (!item) return null;
  const decisionId = safePublicId(item.decisionId);
  const decision = parseEnum(item.decision, ["allow", "block", "defer", "review"] as const);
  if (!decisionId || !decision) return null;
  const guardrail = parseGuardrail(item.guardrail);
  return {
    decisionId,
    decision,
    reasonCodes: safeReasonCodes(item.reasonCodes),
    ...(guardrail ? { guardrail } : {}),
  };
}

function parseSandbox(value: unknown): ProductPlaneSandboxSummary | null {
  const item = record(value);
  if (!item) return null;
  const readiness = parseEnum(item.readiness, [
    "blocked",
    "degraded",
    "disabled",
    "ready",
    "unknown",
  ] as const);
  if (!readiness) return null;
  return {
    readiness,
    decisions: parseArray(item.decisions, parseSandboxDecision),
  };
}

function parseCheckpoint(value: unknown): ProductPlaneCheckpointSummary | null {
  const item = record(value);
  if (!item) return null;
  const checkpointId = safePublicId(item.checkpointId);
  const ledgerDigest = safeDigest(item.ledgerDigest);
  if (!checkpointId || !ledgerDigest) return null;
  const parentCheckpointId = safePublicId(item.parentCheckpointId);
  return {
    checkpointId,
    ledgerDigest,
    ...(parentCheckpointId ? { parentCheckpointId } : {}),
    reasonCodes: safeReasonCodes(item.reasonCodes),
  };
}

function parseLineage(value: unknown): ProductPlaneLineageSummary | null {
  const item = record(value);
  if (!item) return null;
  return { checkpoints: parseArray(item.checkpoints, parseCheckpoint) };
}

function parseQuotaDecision(value: unknown): ProductPlaneQuotaSpendDecisionSummary | null {
  const item = record(value);
  if (!item) return null;
  const decisionId = safePublicId(item.decisionId);
  const decision = parseEnum(item.decision, ["allow", "block", "defer", "review"] as const);
  if (!decisionId || !decision) return null;
  const policySnapshotDigest = safeDigest(item.policySnapshotDigest);
  return {
    decisionId,
    decision,
    reasonCodes: safeReasonCodes(item.reasonCodes),
    ...(policySnapshotDigest ? { policySnapshotDigest } : {}),
  };
}

function parseQuotaSpend(value: unknown): ProductPlaneQuotaSpendSummary | null {
  const item = record(value);
  if (!item) return null;
  const readiness = parseEnum(item.readiness, [
    "blocked",
    "degraded",
    "disabled",
    "ready",
    "unknown",
  ] as const);
  if (!readiness) return null;
  const budgetLabel = safeText(item.budgetLabel);
  return {
    readiness,
    ...(budgetLabel ? { budgetLabel } : {}),
    decisions: parseArray(item.decisions, parseQuotaDecision),
  };
}

function parseCredentialLease(value: unknown): ProductPlaneCredentialLeaseSummary | null {
  const item = record(value);
  if (!item) return null;
  const leaseId = safePublicId(item.leaseId);
  const connectorId = safePublicId(item.connectorId);
  const status = parseEnum(item.status, ["active", "expired", "pending", "revoked"] as const);
  if (!leaseId || !connectorId || !status) return null;
  const ownerLabel = safeText(item.ownerLabel);
  const policySnapshotDigest = safeDigest(item.policySnapshotDigest);
  return {
    leaseId,
    connectorId,
    status,
    ...(ownerLabel ? { ownerLabel } : {}),
    ...(policySnapshotDigest ? { policySnapshotDigest } : {}),
  };
}

function parseReceipt(value: unknown): ProductPlaneReceiptSummary | null {
  const item = record(value);
  if (!item || item.semantic !== "delivery_render_receipt") return null;
  const receiptId = safePublicId(item.receiptId);
  const state = parseEnum(item.state, ["delivered", "failed", "pending", "rendered"] as const);
  if (!receiptId || !state) return null;
  const digest = safeDigest(item.digest);
  return {
    semantic: "delivery_render_receipt",
    receiptId,
    state,
    ...(digest ? { digest } : {}),
    reasonCodes: safeReasonCodes(item.reasonCodes),
  };
}

function parseArtifact(value: unknown): ProductPlaneArtifactSummary | null {
  const item = record(value);
  if (!item) return null;
  const artifactId = safePublicId(item.artifactId);
  const renderReceipt = parseReceipt(item.renderReceipt);
  if (!artifactId || !renderReceipt) return null;
  const deliveryReceipt = parseReceipt(item.deliveryReceipt);
  return {
    artifactId,
    renderReceipt,
    ...(deliveryReceipt ? { deliveryReceipt } : {}),
  };
}

function parseApproval(value: unknown): ProductPlaneApprovalSummary | undefined {
  const item = record(value);
  if (!item || item.semantic !== "pending_approval") return undefined;
  const approvalId = safePublicId(item.approvalId);
  if (!approvalId) return undefined;
  return {
    semantic: "pending_approval",
    approvalId,
    status: "pending",
    reasonCodes: safeReasonCodes(item.reasonCodes),
  };
}

function parseSelfReview(value: unknown): ProductPlaneSelfReviewSummary | null {
  const item = record(value);
  if (!item) return null;
  const reviewId = safePublicId(item.reviewId);
  const status = parseEnum(item.status, ["blocked", "failed", "passed", "pending"] as const);
  if (!reviewId || !status) return null;
  const pendingApproval = parseApproval(item.pendingApproval);
  return {
    reviewId,
    status,
    reasonCodes: safeReasonCodes(item.reasonCodes),
    ...(pendingApproval ? { pendingApproval } : {}),
  };
}

function parsePermissions(value: unknown): ProductPlanePermissionSummary | null {
  const item = record(value);
  if (!item) return null;
  return { selfReviews: parseArray(item.selfReviews, parseSelfReview) };
}

function parseReleaseEvalGate(value: unknown): ProductPlaneReleaseEvalGateSummary | null {
  const item = record(value);
  if (!item) return null;
  const gateId = safePublicId(item.gateId);
  const status = parseEnum(item.status, ["blocked", "failed", "passed", "pending"] as const);
  if (!gateId || !status) return null;
  const backendCapability = parseBackendStatus(item.backendCapability);
  return {
    gateId,
    status,
    reasonCodes: safeReasonCodes(item.reasonCodes),
    ...(backendCapability ? { backendCapability } : {}),
  };
}

function parsePluginConnectorTrust(value: unknown): ProductPlanePluginConnectorTrustSummary | null {
  const item = record(value);
  if (!item) return null;
  const targetId = safePublicId(item.targetId);
  const targetType = parseEnum(item.targetType, ["connector", "plugin"] as const);
  const trustLevel = parseEnum(item.trustLevel, [
    "blocked",
    "review",
    "trusted",
    "unknown",
  ] as const);
  if (!targetId || !targetType || !trustLevel) return null;
  const configuredPolicy = parsePolicyRef(item.configuredPolicy);
  return {
    targetId,
    targetType,
    trustLevel,
    reasonCodes: safeReasonCodes(item.reasonCodes),
    ...(configuredPolicy ? { configuredPolicy } : {}),
  };
}

function parseStorageWarning(value: unknown): ProductPlaneStorageWarning | null {
  const item = record(value);
  if (!item) return null;
  const reasonCode = parseEnum(item.reasonCode, [
    "corruption_recovery_unavailable",
    "migration_pending",
    "missing_backup_export_config",
    "multi_writer_sqlite_risk",
    "runtime_state_pvc_missing",
  ] as const);
  const severity = parseEnum(item.severity, ["blocked", "info", "warning"] as const);
  if (!reasonCode || !severity) return null;
  const label = safeText(item.label);
  return {
    reasonCode,
    severity,
    ...(label ? { label } : {}),
  };
}

function parseStorageWarnings(value: unknown): ProductPlaneStorageWarning[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (hasPrivateKnownKeys(item)) return [];
    const parsed = parseStorageWarning(item);
    return parsed ? [parsed] : [];
  });
}

function parseStore(value: unknown): ProductPlaneStoreSummary | null {
  const item = record(value);
  if (!item) return null;
  const storeId = safePublicId(item.storeId);
  const mode = parseEnum(item.mode, [
    "filesystem",
    "memory",
    "object",
    "postgres",
    "sqlite",
    "supabase",
  ] as const);
  const purpose = parseEnum(item.purpose, [
    "artifact_blob",
    "artifact_index",
    "hosted_sync",
    "runtime_state",
  ] as const);
  const readiness = parseEnum(item.readiness, [
    "blocked",
    "degraded",
    "disabled",
    "ready",
    "unknown",
  ] as const);
  const support = parseEnum(item.support, [
    "hosted_adapter",
    "optional",
    "supported_default",
    "test_dev_only",
  ] as const);
  if (!storeId || !mode || !purpose || !readiness || !support) return null;
  const requiredForOss =
    mode === "postgres" || mode === "supabase"
      ? false
      : item.requiredForOss;
  const pathLabel = safePublicId(item.pathLabel);
  const pathDigest = safeDigest(item.pathDigest);
  return {
    storeId,
    mode,
    purpose,
    readiness,
    support,
    reasonCodes: safeReasonCodes(item.reasonCodes),
    warnings: parseStorageWarnings(item.warnings),
    ...(typeof item.optional === "boolean" ? { optional: item.optional } : {}),
    ...(typeof requiredForOss === "boolean"
      ? { requiredForOss }
      : {}),
    ...(pathLabel ? { pathLabel } : {}),
    ...(pathDigest ? { pathDigest } : {}),
  };
}

function parseStorage(value: unknown): ProductPlaneStorageSummary | null {
  const item = record(value);
  if (!item) return null;
  const durableStore = parseStore(item.durableStore);
  const hostedSync = parseStore(item.hostedSync);
  const artifactIndex = parseStore(item.artifactIndex);
  const artifactBlobStore = parseStore(item.artifactBlobStore);
  if (!durableStore || !artifactIndex || !artifactBlobStore) return null;
  if (durableStore.purpose !== "runtime_state") return null;
  if (artifactIndex.purpose !== "artifact_index") return null;
  if (artifactBlobStore.purpose !== "artifact_blob") return null;
  if (
    durableStore.mode !== "memory" &&
    durableStore.mode !== "postgres" &&
    durableStore.mode !== "sqlite" &&
    durableStore.mode !== "supabase"
  ) return null;
  if (artifactIndex.mode === "filesystem" || artifactIndex.mode === "object") {
    return null;
  }
  if (
    artifactBlobStore.mode !== "filesystem" &&
    artifactBlobStore.mode !== "object"
  ) return null;
  const storeIds = new Set([
    durableStore.storeId,
    artifactIndex.storeId,
    artifactBlobStore.storeId,
  ]);
  if (storeIds.size !== 3) return null;
  if (hostedSync) {
    if (hostedSync.purpose !== "hosted_sync") return null;
    if (storeIds.has(hostedSync.storeId)) return null;
  }
  return {
    durableStore,
    ...(hostedSync ? { hostedSync } : {}),
    artifactIndex,
    artifactBlobStore,
  };
}

function parseSemanticArray(value: unknown): ProductPlaneContractSemantic[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const semantic = parseEnum(item, CONTRACT_SEMANTICS);
    return semantic ? [semantic] : [];
  });
}

function parseArray<T>(
  value: unknown,
  parser: (item: unknown) => T | null,
): T[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (hasPrivateKnownKeys(item)) return [];
    const parsed = parser(item);
    return parsed ? [parsed] : [];
  });
}

export function productPlaneDisabledProjection(): ProductPlaneProjection {
  const disabledDigest = `sha256:${"0".repeat(64)}`;
  return {
    version: 1,
    projectionId: "product-plane-disabled",
    generatedAt: "2026-05-23T00:00:00Z",
    backendStatus: {
      state: "disabled",
      semantic: "default_off_disabled_backend_status",
      reasonCode: "product_plane_default_off",
      capabilityId: "python-adk-product-plane",
    },
    contractSemantics: ["default_off_disabled_backend_status"],
    storage: {
      durableStore: {
        storeId: "runtime-store-disabled",
        mode: "memory",
        purpose: "runtime_state",
        readiness: "disabled",
        support: "test_dev_only",
        reasonCodes: ["product_plane_default_off"],
        warnings: [],
        pathLabel: "disabled-runtime-state",
        pathDigest: disabledDigest,
      },
      artifactIndex: {
        storeId: "artifact-index-disabled",
        mode: "sqlite",
        purpose: "artifact_index",
        readiness: "disabled",
        support: "supported_default",
        reasonCodes: ["product_plane_default_off"],
        warnings: [],
        pathLabel: "disabled-artifact-index",
        pathDigest: disabledDigest,
      },
      artifactBlobStore: {
        storeId: "artifact-blob-disabled",
        mode: "filesystem",
        purpose: "artifact_blob",
        readiness: "disabled",
        support: "supported_default",
        reasonCodes: ["product_plane_default_off"],
        warnings: [],
        pathLabel: "disabled-artifact-blob",
        pathDigest: disabledDigest,
      },
    },
    ops: {
      readiness: "disabled",
      summary: "Product plane disabled by default.",
      configuredPolicy: {
        semantic: "configured_policy",
        configId: "policy.product_plane.disabled",
        label: "Default off",
      },
      effectivePolicySnapshot: {
        semantic: "effective_policy_snapshot_digest",
        digest: disabledDigest,
        policyId: "policy.product_plane.disabled",
      },
      hardInvariants: [],
    },
    jobs: [],
    sandbox: { readiness: "disabled", decisions: [] },
    lineage: { checkpoints: [] },
    quotaSpend: { readiness: "disabled", decisions: [] },
    credentialLeases: [],
    artifacts: [],
    permissions: { selfReviews: [] },
    releaseEvalGates: [],
    pluginConnectorTrust: [],
  };
}

export function parseProductPlaneProjection(
  value: unknown,
): ProductPlaneProjection | null {
  const item = record(value);
  if (!item || item.version !== 1) return null;
  if (hasPrivatePayload(item)) return null;

  const projectionId = safePublicId(item.projectionId);
  const generatedAt = safeDate(item.generatedAt);
  const backendStatus = parseBackendStatus(item.backendStatus);
  const ops = parseOps(item.ops);
  const storage = parseStorage(item.storage);
  const sandbox = parseSandbox(item.sandbox);
  const lineage = parseLineage(item.lineage);
  const quotaSpend = parseQuotaSpend(item.quotaSpend);
  const permissions = parsePermissions(item.permissions);

  if (
    !projectionId ||
    !generatedAt ||
    !backendStatus ||
    !storage ||
    !ops ||
    !sandbox ||
    !lineage ||
    !quotaSpend ||
    !permissions
  ) {
    return null;
  }

  return {
    version: 1,
    projectionId,
    generatedAt,
    backendStatus,
    contractSemantics: parseSemanticArray(item.contractSemantics),
    storage,
    ops,
    jobs: parseArray(item.jobs, parseJob),
    sandbox,
    lineage,
    quotaSpend,
    credentialLeases: parseArray(item.credentialLeases, parseCredentialLease),
    artifacts: parseArray(item.artifacts, parseArtifact),
    permissions,
    releaseEvalGates: parseArray(item.releaseEvalGates, parseReleaseEvalGate),
    pluginConnectorTrust: parseArray(
      item.pluginConnectorTrust,
      parsePluginConnectorTrust,
    ),
  };
}
