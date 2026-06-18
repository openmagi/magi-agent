import {
  parseProductPlaneProjection,
  productPlaneDisabledProjection,
  type ProductPlaneGateStatus,
  type ProductPlaneJobState,
  type ProductPlaneProjection,
  type ProductPlaneReadiness,
  type ProductPlaneReceiptState,
  type ProductPlaneStorageMode,
  type ProductPlaneStoragePurpose,
  type ProductPlaneStorageSupport,
  type ProductPlaneTrustLevel,
} from "./product-plane-types";
import { parseOpenMagiRuntimeEvent } from "@/chat-core";

export interface ProductPlaneReducerEventInput {
  sequence?: number;
  receivedAt?: number;
  event: unknown;
}

export interface ProductPlaneStorageRow {
  storeId: string;
  mode: ProductPlaneStorageMode;
  purpose: ProductPlaneStoragePurpose;
  readiness: ProductPlaneReadiness;
  support: ProductPlaneStorageSupport;
  reasonCodes: string[];
  warningCodes: string[];
  optional: boolean;
  requiredForOss: boolean;
}

export interface ProductPlaneJobRow {
  jobId: string;
  state: ProductPlaneJobState;
  reasonCodes: string[];
  ownerLabel?: string;
  checkpointId?: string;
  policySnapshotDigest?: string;
}

export type ProductPlaneArtifactReceiptStatus =
  | ProductPlaneReceiptState
  | "missing_receipt";

export interface ProductPlaneArtifactRow {
  artifactId: string;
  renderStatus: ProductPlaneArtifactReceiptStatus;
  deliveryStatus: ProductPlaneArtifactReceiptStatus;
  warningCodes: string[];
  renderReceiptId?: string;
  deliveryReceiptId?: string;
  renderDigest?: string;
  deliveryDigest?: string;
}

export interface ProductPlanePermissionRow {
  reviewId: string;
  status: ProductPlaneGateStatus;
  reasonCodes: string[];
  approvalId?: string;
}

export interface ProductPlaneGateRow {
  gateId: string;
  status: ProductPlaneGateStatus;
  reasonCodes: string[];
  stage?: string;
}

export interface ProductPlanePluginTrustRow {
  targetId: string;
  targetType: "connector" | "plugin";
  trustLevel: ProductPlaneTrustLevel;
  reasonCodes: string[];
  policyConfigId?: string;
}

export interface ProductPlaneWarningRow {
  code: string;
  severity: "info" | "warning" | "blocked";
}

export interface ProductPlaneUnsupportedEventRow {
  eventType: string;
  reasonCode: "unsupported_event_type";
  followUpTask?: string;
}

export type ProductPlaneRuntimeActivityKind = "spawn" | "tool";
export type ProductPlaneRuntimeActivityStatus =
  | "done"
  | "error"
  | "running"
  | "waiting";

export interface ProductPlaneRuntimeActivityRow {
  activityId: string;
  kind: ProductPlaneRuntimeActivityKind;
  label: string;
  status: ProductPlaneRuntimeActivityStatus;
  detail?: string;
  durationMs?: number;
}

export interface ProductPlaneAppliedRecipeRow {
  recipeId: string;
  version: string;
  role: "default" | "dependency" | "hard_safety" | "primary";
  governed: boolean;
  sourceDigest?: string;
}

export type ProductPlaneRecipeSelectionStatus =
  | "auto_selected"
  | "explicit_applied"
  | "explicit_blocked"
  | "explicit_incompatible"
  | "explicit_requested"
  | "explicit_unavailable";

export interface ProductPlaneRecipeRef {
  recipeId: string;
  version?: string;
  digest?: string;
}

export interface ProductPlaneRecipeSelectionRow {
  status: ProductPlaneRecipeSelectionStatus;
  selectionSource?: "auto" | "explicit" | "session_default";
  requestedRecipeRefs: ProductPlaneRecipeRef[];
  appliedRecipeRefs: ProductPlaneRecipeRef[];
  omittedRecipeRefs: ProductPlaneRecipeRef[];
  omissionReasons: string[];
  policySnapshotDigest?: string;
  turnBlocked?: boolean;
  fallbackUsed?: boolean;
  nextAction?: string;
}

export interface ProductPlaneViewModel {
  enabled: boolean;
  backendState: ProductPlaneProjection["backendStatus"]["state"];
  readiness: ProductPlaneReadiness;
  projection: ProductPlaneProjection;
  appliedEventKeys: string[];
  policySnapshotDigest?: string;
  policyConfigId?: string;
  storage: ProductPlaneStorageRow[];
  jobs: ProductPlaneJobRow[];
  artifacts: ProductPlaneArtifactRow[];
  permissions: ProductPlanePermissionRow[];
  gates: ProductPlaneGateRow[];
  pluginTrust: ProductPlanePluginTrustRow[];
  appliedRecipes: ProductPlaneAppliedRecipeRow[];
  recipeSelections: ProductPlaneRecipeSelectionRow[];
  runtimeActivities: ProductPlaneRuntimeActivityRow[];
  warnings: ProductPlaneWarningRow[];
  unsupportedEvents: ProductPlaneUnsupportedEventRow[];
}

interface OrderedInput extends ProductPlaneReducerEventInput {
  index: number;
}

interface ArtifactAccumulator {
  artifactId: string;
  expectedRender: boolean;
  expectedDelivery: boolean;
  renderReceipt?: SafeReceipt;
  deliveryReceipt?: SafeReceipt;
}

interface SafeReceipt {
  receiptId: string;
  state: ProductPlaneReceiptState;
  reasonCodes: string[];
  digest?: string;
}

const PUBLIC_ID_RE = /^[a-zA-Z0-9._:-]{1,160}$/;
const DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
const UNSAFE_VALUE_RE =
  /api[._-]?key|auth|authorization|bearer|cookie|google[._-]?adk|private|prompt|raw|secret|session|token|tool[._-]?(?:args?|logs?|results?)|transcript/i;
const UNSAFE_FOLLOW_UP_RE =
  /api[._-]?key|auth|authorization|bearer|cookie|google[._-]?adk|private|prompt|raw|secret|session|token|transcript/i;
const PRIVATE_PATH_RE =
  /(?:^|[\s"'`(])(?:\/[A-Za-z0-9._-]+(?:\/|$)|~[\\/]|(?:\.\.)+[\\/]|[a-zA-Z]:[\\/])|(?:^|[\\/])[^\\/ ]+\.(?:db|env|key|pem|sqlite|sqlite3)(?:$|\b)/i;
const SAFE_DETAIL_TEXT_RE = /^[a-zA-Z0-9 ._:/()[\]#|\-]{1,240}$/;
const RUNTIME_DETAIL_SEPARATOR = " | ";
const URL_RE = /\bhttps?:\/\/[^\s"'<>),\]}]+/i;
const URL_GLOBAL_RE = /\bhttps?:\/\/[^\s"'<>),\]}]+/gi;
const SENSITIVE_URL_PATH_RE =
  /(?:^|\/)(?:auth|callback|callbacks|cookie|oauth|oauth2|sessions?|tokens?)(?:[/?#]|$)/i;
const SECRET_URL_PATH_VALUE_RE =
  /\b(?:api[._-]?key|access[._-]?token|authorization|bearer|client[._-]?secret|connector[._-]?token|id[._-]?token|password|refresh[._-]?token|secret|session[._-]?id)\b/i;
const RAW_UNSAFE_EVENT_TYPE_RE =
  /^(?:adk_event|google\.adk(?:\.|$)|tool_result$)/i;
const SAFE_UNSUPPORTED_PYTHON_ADK_EVENT_RE =
  /^python_adk_(?:function_call|tool_result)$/;
const SECRET_SHAPE_RE =
  /(?:^|[^a-z0-9])(?:sk-[a-z0-9_-]{6,}|sk-proj-[a-z0-9_-]{6,}|github_pat_[a-z0-9_]{12,}|gh[pousr]_[a-z0-9_]{12,}|xox[abprs]-[a-z0-9-]{12,}|akia[0-9a-z]{16}|eyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})(?:$|[^a-z0-9])/i;
const LEGACY_PUBLIC_EVENT_TYPES = new Set([
  "ask_user",
  "background_task",
  "browser_frame",
  "child_abort",
  "child_cancelled",
  "child_completed",
  "child_failed",
  "child_llm_end",
  "child_llm_start",
  "child_permission_decision",
  "child_progress",
  "child_started",
  "child_tool_batch_end",
  "child_tool_batch_start",
  "child_tool_request",
  "control_event",
  "cron_run",
  "document_draft",
  "goal_continue",
  "goal_status",
  "heartbeat",
  "mission_created",
  "mission_event",
  "mission_run",
  "mission_updated",
  "patch_preview",
  "plan_ready",
  "retry",
  "rule_check",
  "runtime_trace",
  "source_inspected",
  "spawn_result",
  "spawn_started",
  "task_board",
  "tool_end",
  "tool_progress",
  "tool_start",
  "turn_phase",
]);
const IGNORED_RUNTIME_EVENT_TYPES = new Set(["text_delta", "thinking_delta"]);
const TOOL_RUNTIME_EVENT_TYPES = new Set([
  "tool_end",
  "tool_progress",
  "tool_start",
]);
const SPAWN_RUNTIME_EVENT_TYPES = new Set([
  "background_task",
  "spawn_result",
  "spawn_started",
]);
const SAFE_RUNTIME_URL_KEYS = [
  "canonicalUrl",
  "canonical_url",
  "href",
  "link",
  "resultUrl",
  "result_url",
  "sourceUrl",
  "source_url",
  "targetUrl",
  "target_url",
  "uri",
  "url",
] as const;
const SAFE_RUNTIME_TEXT_KEYS = [
  "detail",
  "message",
  "summary",
  "target",
  "title",
] as const;
const SAFE_RUNTIME_QUERY_KEYS = ["q", "query", "search"] as const;
const SAFE_RUNTIME_PREVIEW_KEYS = [
  "glob",
  "path",
  "pattern",
  "target",
  "title",
] as const;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function safePublicId(value: unknown): string | null {
  if (typeof value !== "string" || !PUBLIC_ID_RE.test(value)) return null;
  if (UNSAFE_VALUE_RE.test(value) || SECRET_SHAPE_RE.test(value)) return null;
  return value;
}

function safeDigest(value: unknown): string | undefined {
  return typeof value === "string" && DIGEST_RE.test(value) ? value : undefined;
}

function safeReasonCodes(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const code = safePublicId(item);
    return code ? [code] : [];
  });
}

function eventType(value: unknown): string | null {
  const type = record(value)?.type;
  if (typeof type !== "string" || !PUBLIC_ID_RE.test(type)) return null;
  if (
    SAFE_UNSUPPORTED_PYTHON_ADK_EVENT_RE.test(type) &&
    !SECRET_SHAPE_RE.test(type)
  ) {
    return type;
  }
  return RAW_UNSAFE_EVENT_TYPE_RE.test(type) ||
    UNSAFE_VALUE_RE.test(type) ||
    SECRET_SHAPE_RE.test(type)
    ? "unsafe_event_type"
    : type;
}

function eventKey(input: ProductPlaneReducerEventInput): string {
  return `${input.sequence ?? "none"}:${input.receivedAt ?? "none"}:${eventType(input.event) ?? "unknown"}`;
}

function compareInputs(left: OrderedInput, right: OrderedInput): number {
  if (
    typeof left.sequence === "number" &&
    typeof right.sequence === "number" &&
    left.sequence !== right.sequence
  ) {
    return left.sequence - right.sequence;
  }

  if (
    typeof left.sequence !== "number" ||
    typeof right.sequence !== "number"
  ) {
    const leftReceivedAt =
      typeof left.receivedAt === "number" ? left.receivedAt : Number.MAX_SAFE_INTEGER;
    const rightReceivedAt =
      typeof right.receivedAt === "number" ? right.receivedAt : Number.MAX_SAFE_INTEGER;
    if (leftReceivedAt !== rightReceivedAt) return leftReceivedAt - rightReceivedAt;
  }

  return left.index - right.index;
}

function parseReceipt(value: unknown): SafeReceipt | null {
  const item = record(value);
  if (!item || item.semantic !== "delivery_render_receipt") return null;
  const receiptId = safePublicId(item.receiptId);
  const state = item.state;
  if (
    !receiptId ||
    (state !== "delivered" &&
      state !== "failed" &&
      state !== "pending" &&
      state !== "rendered")
  ) {
    return null;
  }
  const digest = safeDigest(item.digest);
  return {
    receiptId,
    state,
    reasonCodes: safeReasonCodes(item.reasonCodes),
    ...(digest ? { digest } : {}),
  };
}

function unsupportedRow(
  type: string,
  followUpTask?: unknown,
): ProductPlaneUnsupportedEventRow {
  return {
    eventType: type,
    reasonCode: "unsupported_event_type",
    ...(typeof followUpTask === "string" &&
    !UNSAFE_FOLLOW_UP_RE.test(followUpTask) &&
    !PRIVATE_PATH_RE.test(followUpTask) &&
    !SECRET_SHAPE_RE.test(followUpTask)
      ? { followUpTask }
      : {}),
  };
}

function normalizedUrlPath(value: string): string {
  return value
    .replace(/%(?:2f|5c)/gi, "/")
    .replace(/%3f/gi, "?")
    .replace(/%23/gi, "#");
}

function safeRuntimeUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const candidate = value.match(URL_RE)?.[0] ?? value;
  try {
    const parsed = new URL(candidate.trim());
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return undefined;
    if (parsed.username || parsed.password) return undefined;
    if (!parsed.host || SECRET_SHAPE_RE.test(parsed.host)) return undefined;
    const normalizedPath = normalizedUrlPath(parsed.pathname);
    if (SENSITIVE_URL_PATH_RE.test(normalizedPath)) return parsed.origin;
    if (
      SECRET_URL_PATH_VALUE_RE.test(normalizedPath) ||
      SECRET_SHAPE_RE.test(normalizedPath)
    ) {
      return undefined;
    }
    const publicUrl = `${parsed.origin}${parsed.pathname}`;
    return SECRET_SHAPE_RE.test(publicUrl) ? undefined : publicUrl;
  } catch {
    return undefined;
  }
}

function safeRuntimeText(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value
    .replace(/\s+/g, " ")
    .trim()
    .replace(URL_GLOBAL_RE, (url) => safeRuntimeUrl(url) ?? "[redacted url]");
  if (!normalized || normalized.length > 240) return undefined;
  if (
    !SAFE_DETAIL_TEXT_RE.test(normalized) ||
    UNSAFE_VALUE_RE.test(normalized) ||
    PRIVATE_PATH_RE.test(normalized) ||
    SECRET_SHAPE_RE.test(normalized)
  ) {
    return undefined;
  }
  return normalized;
}

function parseRuntimePreviewObject(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "string" || !value.trim().startsWith("{")) return null;
  try {
    const parsed = JSON.parse(value);
    return record(parsed);
  } catch {
    return null;
  }
}

function pushUnique(values: string[], value: string | undefined): void {
  if (!value || values.includes(value)) return;
  values.push(value);
}

function runtimeDetailPartsFromObject(
  source: Record<string, unknown> | null,
  parts: string[],
): void {
  if (!source) return;
  for (const key of SAFE_RUNTIME_URL_KEYS) {
    pushUnique(parts, safeRuntimeUrl(source[key]));
  }
  for (const key of SAFE_RUNTIME_QUERY_KEYS) {
    const text = safeRuntimeText(source[key]);
    if (text) pushUnique(parts, `query: ${text}`);
  }
  for (const key of SAFE_RUNTIME_PREVIEW_KEYS) {
    pushUnique(parts, safeRuntimeText(source[key]));
  }
  for (const key of SAFE_RUNTIME_TEXT_KEYS) {
    pushUnique(parts, safeRuntimeText(source[key]));
  }
}

function runtimeDetailFromPayload(payload: Record<string, unknown>): string | undefined {
  const parts: string[] = [];
  runtimeDetailPartsFromObject(payload, parts);

  const inputPreview = typeof payload.input_preview === "string"
    ? payload.input_preview
    : undefined;
  const outputPreview = typeof payload.output_preview === "string"
    ? payload.output_preview
    : undefined;
  runtimeDetailPartsFromObject(parseRuntimePreviewObject(inputPreview), parts);
  runtimeDetailPartsFromObject(parseRuntimePreviewObject(outputPreview), parts);
  pushUnique(parts, safeRuntimeUrl(inputPreview));
  pushUnique(parts, safeRuntimeUrl(outputPreview));

  return parts.join(RUNTIME_DETAIL_SEPARATOR) || undefined;
}

function runtimeToolStatus(value: unknown): ProductPlaneRuntimeActivityStatus {
  if (value === "ok" || value === "done" || value === "success" || value === "completed") {
    return "done";
  }
  if (
    value === "blocked" ||
    value === "denied" ||
    value === "error" ||
    value === "failed" ||
    value === "interrupted" ||
    value === "permission_denied" ||
    value === "rejected"
  ) {
    return "error";
  }
  if (value === "pending" || value === "needs_approval") return "waiting";
  return "running";
}

function runtimeSpawnStatus(value: unknown): ProductPlaneRuntimeActivityStatus {
  if (value === "ok" || value === "done" || value === "completed" || value === "success") {
    return "done";
  }
  if (value === "aborted" || value === "cancelled" || value === "error" || value === "failed") {
    return "error";
  }
  if (value === "pending" || value === "waiting") return "waiting";
  return "running";
}

function runtimeActivityLabel(
  payload: Record<string, unknown>,
  fallback: string,
): string {
  return safeRuntimeText(payload.label) ?? safeRuntimeText(payload.name) ?? fallback;
}

function mergeRuntimeDetails(
  current: string | undefined,
  next: string | undefined,
): string | undefined {
  const parts: string[] = [];
  for (const value of [current, next]) {
    if (!value) continue;
    for (const part of value.split(/\s+\|\s+/)) {
      pushUnique(parts, safeRuntimeText(part));
    }
  }
  return parts.join(RUNTIME_DETAIL_SEPARATOR) || undefined;
}

function upsertRuntimeActivity(
  rows: Map<string, ProductPlaneRuntimeActivityRow>,
  next: ProductPlaneRuntimeActivityRow,
): void {
  const existing = rows.get(next.activityId);
  if (!existing) {
    rows.set(next.activityId, next);
    return;
  }
  rows.set(next.activityId, {
    ...existing,
    ...next,
    label: next.label === "Tool" && existing.label ? existing.label : next.label,
    detail: mergeRuntimeDetails(existing.detail, next.detail),
    durationMs: next.durationMs ?? existing.durationMs,
  });
}

function runtimeActivityFromPayload(
  type: string,
  payload: Record<string, unknown>,
): ProductPlaneRuntimeActivityRow | null {
  if (TOOL_RUNTIME_EVENT_TYPES.has(type)) {
    const activityId = safePublicId(payload.id);
    if (!activityId) return null;
    const label = runtimeActivityLabel(payload, "Tool");
    const detail = runtimeDetailFromPayload(payload);
    const status = type === "tool_end"
      ? runtimeToolStatus(payload.status)
      : type === "tool_progress"
        ? runtimeToolStatus(payload.status)
        : "running";
    const durationMs = typeof payload.durationMs === "number"
      ? payload.durationMs
      : typeof payload.duration_ms === "number"
        ? payload.duration_ms
        : undefined;
    return {
      activityId,
      kind: "tool",
      label,
      status,
      ...(detail ? { detail } : {}),
      ...(durationMs !== undefined ? { durationMs } : {}),
    };
  }

  if (SPAWN_RUNTIME_EVENT_TYPES.has(type)) {
    const activityId = safePublicId(payload.taskId);
    if (!activityId) return null;
    const detail = runtimeDetailFromPayload(payload);
    return {
      activityId,
      kind: "spawn",
      label: "Assigning helper",
      status: type === "spawn_result"
        ? runtimeSpawnStatus(payload.status)
        : runtimeSpawnStatus(payload.status ?? "running"),
      ...(detail ? { detail } : {}),
    };
  }

  return null;
}

function upsertById<T extends Record<K, string>, K extends keyof T>(
  rows: T[],
  key: K,
  next: T,
): void {
  const index = rows.findIndex((row) => row[key] === next[key]);
  if (index === -1) {
    rows.push(next);
    return;
  }
  rows[index] = next;
}

function storageRows(projection: ProductPlaneProjection): ProductPlaneStorageRow[] {
  const stores = [
    projection.storage.durableStore,
    projection.storage.hostedSync,
    projection.storage.artifactIndex,
    projection.storage.artifactBlobStore,
  ].filter((store): store is NonNullable<typeof store> => Boolean(store));

  return stores.map((store) => ({
    storeId: store.storeId,
    mode: store.mode,
    purpose: store.purpose,
    readiness: store.readiness,
    support: store.support,
    reasonCodes: store.reasonCodes,
    warningCodes: store.warnings.map((warning) => warning.reasonCode),
    optional: store.optional ?? false,
    requiredForOss: store.requiredForOss ?? false,
  }));
}

function gateStatusFromGuardrail(status: string): ProductPlaneGateStatus {
  if (status === "passed") return "passed";
  if (status === "blocked" || status === "approval_required") return "blocked";
  if (status === "pending") return "pending";
  return "failed";
}

function projectionGateStatus(outputAllowed: boolean): ProductPlaneGateStatus {
  return outputAllowed ? "passed" : "blocked";
}

function addProjectionRows(
  projection: ProductPlaneProjection,
  jobs: ProductPlaneJobRow[],
  gates: ProductPlaneGateRow[],
  permissions: ProductPlanePermissionRow[],
  pluginTrust: ProductPlanePluginTrustRow[],
  artifactsById: Map<string, ArtifactAccumulator>,
): void {
  for (const job of projection.jobs) {
    upsertById(jobs, "jobId", {
      jobId: job.jobId,
      state: job.lifecycle.state,
      reasonCodes: job.lifecycle.reasonCodes,
      ...(job.ownerLabel ? { ownerLabel: job.ownerLabel } : {}),
      ...(job.lifecycle.checkpointId
        ? { checkpointId: job.lifecycle.checkpointId }
        : {}),
      policySnapshotDigest: projection.ops.effectivePolicySnapshot.digest,
    });
  }

  for (const invariant of projection.ops.hardInvariants) {
    upsertById(gates, "gateId", {
      gateId: invariant.invariantId,
      status: invariant.status,
      reasonCodes: invariant.reasonCodes,
    });
  }

  for (const decision of projection.sandbox.decisions) {
    if (decision.guardrail) {
      upsertById(gates, "gateId", {
        gateId: decision.guardrail.guardrailId,
        status: decision.guardrail.status,
        reasonCodes: decision.guardrail.reasonCodes,
      });
    }
  }

  for (const decision of projection.quotaSpend.decisions) {
    upsertById(gates, "gateId", {
      gateId: `quota:${decision.decisionId}`,
      status: decision.decision === "block" ? "blocked" : "passed",
      reasonCodes: decision.reasonCodes,
    });
  }

  for (const gate of projection.releaseEvalGates) {
    upsertById(gates, "gateId", {
      gateId: gate.gateId,
      status: gate.status,
      reasonCodes: gate.reasonCodes,
    });
  }

  for (const review of projection.permissions.selfReviews) {
    upsertById(permissions, "reviewId", {
      reviewId: review.reviewId,
      status: review.status,
      reasonCodes: review.reasonCodes,
      ...(review.pendingApproval
        ? { approvalId: review.pendingApproval.approvalId }
        : {}),
    });
  }

  for (const trust of projection.pluginConnectorTrust) {
    upsertById(pluginTrust, "targetId", {
      targetId: trust.targetId,
      targetType: trust.targetType,
      trustLevel: trust.trustLevel,
      reasonCodes: trust.reasonCodes,
      ...(trust.configuredPolicy
        ? { policyConfigId: trust.configuredPolicy.configId }
        : {}),
    });
  }

  for (const artifact of projection.artifacts) {
    artifactsById.set(artifact.artifactId, {
      artifactId: artifact.artifactId,
      expectedRender: true,
      expectedDelivery: true,
      renderReceipt: {
        receiptId: artifact.renderReceipt.receiptId,
        state: artifact.renderReceipt.state,
        reasonCodes: artifact.renderReceipt.reasonCodes,
        ...(artifact.renderReceipt.digest
          ? { digest: artifact.renderReceipt.digest }
          : {}),
      },
      ...(artifact.deliveryReceipt
        ? {
            deliveryReceipt: {
              receiptId: artifact.deliveryReceipt.receiptId,
              state: artifact.deliveryReceipt.state,
              reasonCodes: artifact.deliveryReceipt.reasonCodes,
              ...(artifact.deliveryReceipt.digest
                ? { digest: artifact.deliveryReceipt.digest }
                : {}),
            },
          }
        : {}),
    });
  }
}

function addArtifactShell(
  artifactsById: Map<string, ArtifactAccumulator>,
  payload: Record<string, unknown>,
): void {
  const artifactId = safePublicId(payload.artifactId);
  if (!artifactId) return;
  const existing = artifactsById.get(artifactId);
  artifactsById.set(artifactId, {
    artifactId,
    expectedRender: payload.expectedRender === true || existing?.expectedRender === true,
    expectedDelivery:
      payload.expectedDelivery === true || existing?.expectedDelivery === true,
    ...(existing?.renderReceipt ? { renderReceipt: existing.renderReceipt } : {}),
    ...(existing?.deliveryReceipt
      ? { deliveryReceipt: existing.deliveryReceipt }
      : {}),
  });
}

function addArtifactReceipt(
  artifactsById: Map<string, ArtifactAccumulator>,
  payload: Record<string, unknown>,
): void {
  const artifactId = safePublicId(payload.artifactId);
  const receiptKind =
    payload.receiptKind === "render" || payload.receiptKind === "delivery"
      ? payload.receiptKind
      : null;
  const receipt = parseReceipt(payload.receipt);
  if (!artifactId || !receiptKind || !receipt) return;
  const existing = artifactsById.get(artifactId) ?? {
    artifactId,
    expectedRender: receiptKind === "render",
    expectedDelivery: receiptKind === "delivery",
  };
  const next: ArtifactAccumulator = {
    ...existing,
    expectedRender: existing.expectedRender || receiptKind === "render",
    expectedDelivery: existing.expectedDelivery || receiptKind === "delivery",
    ...(receiptKind === "render" ? { renderReceipt: receipt } : {}),
    ...(receiptKind === "delivery" ? { deliveryReceipt: receipt } : {}),
  };
  artifactsById.set(artifactId, next);
}

function artifactRows(
  artifactsById: Map<string, ArtifactAccumulator>,
): ProductPlaneArtifactRow[] {
  return [...artifactsById.values()].map((artifact) => {
    const warningCodes: string[] = [];
    if (artifact.expectedRender && !artifact.renderReceipt) {
      warningCodes.push("missing_render_receipt");
    }
    if (artifact.expectedDelivery && !artifact.deliveryReceipt) {
      warningCodes.push("missing_delivery_receipt");
    }
    return {
      artifactId: artifact.artifactId,
      renderStatus: artifact.renderReceipt?.state ?? "missing_receipt",
      deliveryStatus: artifact.deliveryReceipt?.state ?? "missing_receipt",
      warningCodes,
      ...(artifact.renderReceipt
        ? { renderReceiptId: artifact.renderReceipt.receiptId }
        : {}),
      ...(artifact.deliveryReceipt
        ? { deliveryReceiptId: artifact.deliveryReceipt.receiptId }
        : {}),
      ...(artifact.renderReceipt?.digest
        ? { renderDigest: artifact.renderReceipt.digest }
        : {}),
      ...(artifact.deliveryReceipt?.digest
        ? { deliveryDigest: artifact.deliveryReceipt.digest }
        : {}),
    };
  });
}

export function reduceProductPlaneEvents(
  inputs: ProductPlaneReducerEventInput[],
): ProductPlaneViewModel {
  let projection = productPlaneDisabledProjection();
  let sawValidProjection = false;
  let sawValidProductPlaneEvent = false;
  let sawDeferredDeterministicEvent = false;
  let sawRuntimeActivity = false;
  const appliedEventKeys: string[] = [];
  const jobs: ProductPlaneJobRow[] = [];
  const gates: ProductPlaneGateRow[] = [];
  const permissions: ProductPlanePermissionRow[] = [];
  const pluginTrust: ProductPlanePluginTrustRow[] = [];
  const appliedRecipes: ProductPlaneAppliedRecipeRow[] = [];
  const recipeSelections: ProductPlaneRecipeSelectionRow[] = [];
  const warnings: ProductPlaneWarningRow[] = [];
  const unsupportedEvents: ProductPlaneUnsupportedEventRow[] = [];
  const artifactsById = new Map<string, ArtifactAccumulator>();
  const runtimeActivitiesById = new Map<string, ProductPlaneRuntimeActivityRow>();

  const ordered = inputs
    .map((input, index): OrderedInput => ({ ...input, index }))
    .sort(compareInputs);

  for (const input of ordered) {
    const payload = record(input.event);
    const type = eventType(input.event);
    if (!payload || !type) continue;
    appliedEventKeys.push(eventKey(input));

    if (type === "product_plane_projection") {
      const parsed = parseProductPlaneProjection(payload.projection);
      if (!parsed) continue;
      projection = parsed;
      sawValidProjection = true;
      sawValidProductPlaneEvent = true;
      jobs.length = 0;
      gates.length = 0;
      permissions.length = 0;
      pluginTrust.length = 0;
      artifactsById.clear();
      addProjectionRows(parsed, jobs, gates, permissions, pluginTrust, artifactsById);
      continue;
    }

    if (type === "product_plane_artifact") {
      addArtifactShell(artifactsById, payload);
      sawValidProductPlaneEvent = true;
      continue;
    }

    if (type === "product_plane_artifact_receipt") {
      addArtifactReceipt(artifactsById, payload);
      sawValidProductPlaneEvent = true;
      continue;
    }

    const runtimeActivity = runtimeActivityFromPayload(type, payload);
    if (runtimeActivity) {
      upsertRuntimeActivity(runtimeActivitiesById, runtimeActivity);
      sawRuntimeActivity = true;
      continue;
    }

    const runtimeEvent = parseOpenMagiRuntimeEvent(payload);
    if (runtimeEvent) {
      if (runtimeEvent.type === "deterministic_workflow") {
        sawDeferredDeterministicEvent = true;
        upsertById(jobs, "jobId", {
          jobId: runtimeEvent.workflowId,
          state: "running",
          reasonCodes: runtimeEvent.governed === false ? ["ungoverned"] : ["governed"],
          ...(runtimeEvent.checkpointId
            ? { checkpointId: runtimeEvent.checkpointId }
            : {}),
          policySnapshotDigest: runtimeEvent.effectivePolicySnapshotDigest,
        });
      } else if (runtimeEvent.type === "deterministic_guardrail") {
        sawDeferredDeterministicEvent = true;
        upsertById(gates, "gateId", {
          gateId: runtimeEvent.guardrailId,
          status: gateStatusFromGuardrail(runtimeEvent.status),
          reasonCodes: runtimeEvent.reasonCodes,
          stage: runtimeEvent.stage,
        });
      } else if (runtimeEvent.type === "deterministic_projection") {
        sawDeferredDeterministicEvent = true;
        upsertById(gates, "gateId", {
          gateId: `projection:${runtimeEvent.projectionMode}`,
          status: projectionGateStatus(runtimeEvent.outputAllowed),
          reasonCodes: runtimeEvent.blockedReasonCodes,
        });
      } else if (runtimeEvent.type === "deterministic_verification_gate") {
        sawDeferredDeterministicEvent = true;
        upsertById(gates, "gateId", {
          gateId: runtimeEvent.gateId,
          status: gateStatusFromGuardrail(runtimeEvent.status),
          reasonCodes: runtimeEvent.reasonCodes,
          stage: runtimeEvent.stage,
        });
      } else if (runtimeEvent.type === "deterministic_fallback") {
        sawDeferredDeterministicEvent = true;
        warnings.push({
          code: "python_adk_fallback_to_typescript",
          severity: "warning",
        });
      } else if (runtimeEvent.type === "deterministic_recipe_selection") {
        sawDeferredDeterministicEvent = true;
        if (runtimeEvent.status) {
          recipeSelections.push({
            status: runtimeEvent.status,
            ...(runtimeEvent.selectionSource
              ? { selectionSource: runtimeEvent.selectionSource }
              : {}),
            requestedRecipeRefs: [...(runtimeEvent.requestedRecipeRefs ?? [])],
            appliedRecipeRefs: [...(runtimeEvent.appliedRecipeRefs ?? [])],
            omittedRecipeRefs: [...(runtimeEvent.omittedRecipeRefs ?? [])],
            omissionReasons: [...(runtimeEvent.omissionReasons ?? [])],
            ...(runtimeEvent.policySnapshotDigest
              ? { policySnapshotDigest: runtimeEvent.policySnapshotDigest }
              : {}),
            ...(typeof runtimeEvent.turnBlocked === "boolean"
              ? { turnBlocked: runtimeEvent.turnBlocked }
              : {}),
            ...(typeof runtimeEvent.fallbackUsed === "boolean"
              ? { fallbackUsed: runtimeEvent.fallbackUsed }
              : {}),
            ...(runtimeEvent.nextAction
              ? { nextAction: runtimeEvent.nextAction }
              : {}),
          });
        }
        for (const recipe of runtimeEvent.appliedRecipes) {
          upsertById(appliedRecipes, "recipeId", {
            recipeId: recipe.recipeId,
            version: recipe.version,
            role: recipe.role,
            governed: recipe.governed,
            ...(recipe.sourceDigest ? { sourceDigest: recipe.sourceDigest } : {}),
          });
        }
      }
      continue;
    }

    if (IGNORED_RUNTIME_EVENT_TYPES.has(type)) continue;

    if (LEGACY_PUBLIC_EVENT_TYPES.has(type) || type.startsWith("python_adk_")) {
      unsupportedEvents.push(unsupportedRow(type, payload.followUpTask));
      continue;
    }

    unsupportedEvents.push(unsupportedRow(type));
  }

  const enabled = projection.backendStatus.state === "active";
  const readiness: ProductPlaneReadiness =
    enabled || sawValidProductPlaneEvent
      ? projection.ops.readiness
      : sawDeferredDeterministicEvent || sawRuntimeActivity
        ? "degraded"
        : "disabled";
  const backendState: ProductPlaneViewModel["backendState"] =
    enabled || sawValidProductPlaneEvent
      ? projection.backendStatus.state
      : sawDeferredDeterministicEvent || sawRuntimeActivity
        ? "deferred"
        : "disabled";
  const defaultWarnings: ProductPlaneWarningRow[] =
    sawValidProductPlaneEvent ||
    sawDeferredDeterministicEvent ||
    sawRuntimeActivity ||
    unsupportedEvents.length > 0
      ? []
      : [{ code: "product_plane_default_off", severity: "info" }];

  return {
    enabled,
    backendState,
    readiness,
    projection,
    appliedEventKeys,
    policySnapshotDigest: sawValidProjection
      ? projection.ops.effectivePolicySnapshot.digest
      : sawDeferredDeterministicEvent
        ? jobs.find((job) => job.policySnapshotDigest)?.policySnapshotDigest
        : undefined,
    policyConfigId: sawValidProductPlaneEvent
      ? projection.ops.configuredPolicy.configId
      : undefined,
    storage: sawValidProductPlaneEvent ? storageRows(projection) : [],
    jobs,
    artifacts: artifactRows(artifactsById),
    permissions,
    gates,
    pluginTrust,
    appliedRecipes,
    recipeSelections,
    runtimeActivities: [...runtimeActivitiesById.values()],
    warnings: [...defaultWarnings, ...warnings],
    unsupportedEvents,
  };
}
