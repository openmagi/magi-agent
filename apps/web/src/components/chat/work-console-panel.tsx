"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import {
  deriveWorkConsoleRows,
  type WorkConsoleRow,
  type WorkConsoleRowGroup,
  type WorkConsoleRowStatus,
} from "@/chat-core";
import type {
  ProductPlaneArtifactRow,
  ProductPlaneGateRow,
  ProductPlaneJobRow,
  ProductPlanePermissionRow,
  ProductPlaneRecipeSelectionRow,
  ProductPlaneRuntimeActivityRow,
  ProductPlaneStorageRow,
  ProductPlaneUnsupportedEventRow,
  ProductPlaneViewModel,
  ProductPlaneWarningRow,
} from "@/lib/agent-runtime/product-plane-reducer";
import {
  WORK_CONSOLE_MOTION_TICK_MS,
  smoothedHeartbeatElapsedMs,
  workConsoleRowDelayMs,
} from "@/chat-core";
import { BrowserFramePreview } from "./browser-frame-preview";
import { DocumentDraftPreviewCard } from "./document-draft-preview";
import { SubagentWorkPanel } from "./subagent-work-panel";
import type {
  ChannelState,
  ChatResponseLanguage,
  ControlRequestRecord,
  LiveTranscriptWorkItem,
  QueuedMessage,
} from "@/chat-core";

interface WorkConsolePanelProps {
  channelState: ChannelState;
  queuedMessages?: QueuedMessage[];
  controlRequests?: ControlRequestRecord[];
  suppressInlineRunDetails?: boolean;
  uiLanguage?: ChatResponseLanguage;
}

const GROUP_LABELS: Record<WorkConsoleRowGroup, string> = {
  status: "Now",
  mission: "Missions",
  trace: "Runtime checks",
  tool: "Current steps",
  subagent: "Agents",
  task: "Plan",
  queue: "Queued messages",
  control: "Needs input",
};

const GROUP_LABELS_KO: Record<WorkConsoleRowGroup, string> = {
  status: "현재",
  mission: "미션",
  trace: "런타임 검증",
  tool: "현재 단계",
  subagent: "에이전트",
  task: "계획",
  queue: "대기 메시지",
  control: "입력 필요",
};

const INLINE_RUN_DETAIL_GROUPS = new Set<WorkConsoleRowGroup>([
  "tool",
  "trace",
  "task",
  "queue",
  "control",
]);
const MAIN_AGENT_GROUPS = new Set<WorkConsoleRowGroup>([
  "status",
  "trace",
  "tool",
  "task",
  "queue",
  "control",
]);
const MAX_DISPLAY_GOAL_CHARS = 140;
const EXPANDABLE_SNIPPET_CHARS = 96;
const PRODUCT_PLANE_ROW_LIMIT = 5;
const SAFE_DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
const SAFE_TEXT_RE = /^[a-zA-Z0-9 ._:/()[\]#|·-]{1,240}$/;
const UNSAFE_PRODUCT_PLANE_TEXT_RE =
  /api[._-]?key|auth|authorization|bearer|cookie|connector[._-]?token|google[._-]?adk|hidden[._-]?reasoning|model[._-]?output|private|prompt|raw|secret|session|token|tool[._-]?(?:args?|logs?|results?)|transcript/i;
const PRIVATE_PATH_RE =
  /(?:^|[\s"'`(])(?:\/[A-Za-z0-9._-]+(?:\/|$)|~[\\/]|(?:\.\.)+[\\/]|[a-zA-Z]:[\\/])|(?:^|[\\/])[^\\/ ]+\.(?:db|env|key|pem|sqlite|sqlite3)(?:$|\b)/i;
const RELATIVE_PRIVATE_PATH_RE =
  /(?:^|[\s"'`(])(?:[A-Za-z0-9._-]+\/){1,}[A-Za-z0-9._-]+(?:$|[\s"'`)])/;
const SECRET_SHAPE_RE =
  /(?:^|[^a-z0-9])(?:sk-[a-z0-9_-]{6,}|sk-proj-[a-z0-9_-]{6,}|[rs]k_(?:live|test)_[a-z0-9_]{8,}|github_pat_[a-z0-9_]{12,}|gh[pousr]_[a-z0-9_]{12,}|xox[abprs]-[a-z0-9-]{12,}|akia[0-9a-z]{16}|eyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})(?:$|[^a-z0-9])/i;

type ProductPlaneChannelState = ChannelState & {
  productPlane?: ProductPlaneViewModel;
};

type PythonAdkRuntimeDiagnosticStatus =
  | "blocked"
  | "default_off"
  | "degraded"
  | "disabled"
  | "ready";

type PythonAdkRouteDiagnosticStatus =
  | "accepted"
  | "disabled"
  | "failed"
  | "skipped";

type PythonAdkDiagnosticsChannelState = ChannelState & {
  pythonAdkDiagnostics?: unknown;
};

interface PythonAdkDiagnostics {
  authoritySource: string;
  runtimeStatus: PythonAdkRuntimeDiagnosticStatus;
  shadowStatus: PythonAdkRouteDiagnosticStatus;
  canaryStatus: PythonAdkRouteDiagnosticStatus;
  receiptDigest?: string;
  blockerReasonCodes?: string[];
  fallbackReasonCode?: string;
}

const PYTHON_ADK_RUNTIME_DIAGNOSTIC_STATUSES = new Set<string>([
  "blocked",
  "default_off",
  "degraded",
  "disabled",
  "ready",
]);

const PYTHON_ADK_ROUTE_DIAGNOSTIC_STATUSES = new Set<string>([
  "accepted",
  "disabled",
  "failed",
  "skipped",
]);

type WorkConsoleMotionStyle = CSSProperties & {
  "--work-console-row-delay"?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safePythonAdkRuntimeStatus(
  value: unknown,
): PythonAdkRuntimeDiagnosticStatus | undefined {
  if (typeof value !== "string" || !PYTHON_ADK_RUNTIME_DIAGNOSTIC_STATUSES.has(value)) {
    return undefined;
  }
  return value as PythonAdkRuntimeDiagnosticStatus;
}

function safePythonAdkRouteStatus(
  value: unknown,
): PythonAdkRouteDiagnosticStatus | undefined {
  if (typeof value !== "string" || !PYTHON_ADK_ROUTE_DIAGNOSTIC_STATUSES.has(value)) {
    return undefined;
  }
  return value as PythonAdkRouteDiagnosticStatus;
}

function safePythonAdkDigest(value: unknown): string | undefined {
  if (typeof value !== "string" || !SAFE_DIGEST_RE.test(value)) return undefined;
  return value;
}

function safePythonAdkText(value: unknown): string | undefined {
  return typeof value === "string" ? productPlaneSafeText(value) : undefined;
}

function safePythonAdkReasonCodes(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const reasonCodes = value.flatMap((reasonCode) => {
    const safe = safePythonAdkText(reasonCode);
    return safe ? [safe] : [];
  });
  return reasonCodes.length > 0 ? reasonCodes : undefined;
}

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function t(language: ChatResponseLanguage | undefined, en: string, ko: string): string {
  return isKorean(language) ? ko : en;
}

function groupLabel(group: WorkConsoleRowGroup, language?: ChatResponseLanguage): string {
  return isKorean(language) ? GROUP_LABELS_KO[group] : GROUP_LABELS[group];
}

function statusClass(status: WorkConsoleRowStatus): string {
  switch (status) {
    case "running":
      return "bg-[#7C3AED]";
    case "done":
      return "bg-emerald-500";
    case "waiting":
      return "bg-amber-500";
    case "error":
      return "bg-red-500";
    case "info":
    default:
      return "bg-secondary/25";
  }
}

function groupRows(rows: WorkConsoleRow[]): Array<[WorkConsoleRowGroup, WorkConsoleRow[]]> {
  const groups = new Map<WorkConsoleRowGroup, WorkConsoleRow[]>();
  for (const row of rows) {
    const existing = groups.get(row.group) ?? [];
    existing.push(row);
    groups.set(row.group, existing);
  }
  return Array.from(groups.entries());
}

function isDeterminismRow(row: WorkConsoleRow): boolean {
  return row.id.startsWith("determinism:");
}

function isProductPlaneRow(row: WorkConsoleRow): boolean {
  return row.id.startsWith("product-plane:");
}

function productPlaneFromChannelState(
  channelState: ChannelState,
): ProductPlaneViewModel | undefined {
  return (channelState as ProductPlaneChannelState).productPlane;
}

function pythonAdkDiagnosticsFromChannelState(
  channelState: ChannelState,
): PythonAdkDiagnostics | undefined {
  const diagnostics = (channelState as PythonAdkDiagnosticsChannelState).pythonAdkDiagnostics;
  if (!isRecord(diagnostics)) return undefined;

  const authoritySource = safePythonAdkText(diagnostics.authoritySource);
  const runtimeStatus = safePythonAdkRuntimeStatus(diagnostics.runtimeStatus);
  const shadowStatus = safePythonAdkRouteStatus(diagnostics.shadowStatus);
  const canaryStatus = safePythonAdkRouteStatus(diagnostics.canaryStatus);
  if (!authoritySource || !runtimeStatus || !shadowStatus || !canaryStatus) {
    return undefined;
  }

  const receiptDigest = safePythonAdkDigest(diagnostics.receiptDigest);
  const blockerReasonCodes = safePythonAdkReasonCodes(diagnostics.blockerReasonCodes);
  const fallbackReasonCode = safePythonAdkText(diagnostics.fallbackReasonCode);

  return {
    authoritySource,
    runtimeStatus,
    shadowStatus,
    canaryStatus,
    ...(receiptDigest ? { receiptDigest } : {}),
    ...(blockerReasonCodes ? { blockerReasonCodes } : {}),
    ...(fallbackReasonCode ? { fallbackReasonCode } : {}),
  };
}

function productPlaneSafeText(value?: string | null): string | undefined {
  const normalized = value?.replace(/\s+/g, " ").trim();
  if (!normalized || !SAFE_TEXT_RE.test(normalized)) return undefined;
  if (normalized === "test/dev only") return normalized;
  if (
    UNSAFE_PRODUCT_PLANE_TEXT_RE.test(normalized) ||
    PRIVATE_PATH_RE.test(normalized) ||
    RELATIVE_PRIVATE_PATH_RE.test(normalized) ||
    SECRET_SHAPE_RE.test(normalized)
  ) return undefined;
  return normalized;
}

function productPlaneSafeItems(values: Array<string | undefined | null>): string[] {
  return values.flatMap((value) => {
    const safe = productPlaneSafeText(value);
    return safe ? [safe] : [];
  });
}

function productPlaneSafeReasonCodes(values: string[]): string[] {
  return productPlaneSafeItems(values);
}

function productPlaneSafeDetail(values: Array<string | undefined | null>): string | undefined {
  return productPlaneSafeItems(values).join(" · ") || undefined;
}

function productPlaneLabelWithId(
  language: ChatResponseLanguage | undefined,
  enPrefix: string,
  koPrefix: string,
  value: string,
): string {
  const safe = productPlaneSafeText(value);
  const prefix = t(language, enPrefix, koPrefix);
  return safe ? `${prefix}: ${safe}` : prefix;
}

function productPlaneDigest(value?: string): string | undefined {
  if (!value || !SAFE_DIGEST_RE.test(value)) return undefined;
  return value.length > 24 ? `${value.slice(0, 13)}…${value.slice(-6)}` : value;
}

function productPlaneStatusText(value: string): string | undefined {
  return productPlaneSafeText(value.replaceAll("_", " "));
}

function productPlaneSupportText(
  value: ProductPlaneStorageRow["support"],
): string | undefined {
  switch (value) {
    case "test_dev_only":
      return "test/dev only";
    case "supported_default":
      return "supported default";
    case "hosted_adapter":
      return "hosted adapter";
    case "optional":
    default:
      return productPlaneStatusText(value);
  }
}

function productPlaneUnsupportedEventText(value: string): string | undefined {
  if (/^python_adk_(?:function_call|tool_result)$/.test(value)) return value;
  return productPlaneSafeText(value);
}

function productPlaneReasonDetail(values: string[]): string | undefined {
  const safe = productPlaneSafeReasonCodes(values);
  return safe.length > 0 ? safe.join(" ") : undefined;
}

function productPlaneRowStatusFromReadiness(
  readiness: ProductPlaneViewModel["readiness"],
): WorkConsoleRowStatus {
  switch (readiness) {
    case "ready":
      return "done";
    case "blocked":
      return "error";
    case "degraded":
      return "waiting";
    case "disabled":
    case "unknown":
    default:
      return "info";
  }
}

function productPlaneRowStatusFromBackend(
  state: ProductPlaneViewModel["backendState"],
): WorkConsoleRowStatus {
  switch (state) {
    case "active":
      return "done";
    case "deferred":
    case "unsupported":
      return "waiting";
    case "default_off":
    case "disabled":
    default:
      return "info";
  }
}

function productPlaneRowStatusFromJob(
  state: ProductPlaneJobRow["state"],
): WorkConsoleRowStatus {
  switch (state) {
    case "running":
      return "running";
    case "completed":
      return "done";
    case "blocked":
    case "failed":
      return "error";
    case "pending":
    default:
      return "waiting";
  }
}

function productPlaneRowStatusFromGate(
  status: ProductPlaneGateRow["status"],
): WorkConsoleRowStatus {
  switch (status) {
    case "passed":
      return "done";
    case "blocked":
    case "failed":
      return "error";
    case "pending":
    default:
      return "waiting";
  }
}

function productPlaneRowStatusFromArtifact(
  artifact: ProductPlaneArtifactRow,
): WorkConsoleRowStatus {
  if (artifact.renderStatus === "failed" || artifact.deliveryStatus === "failed") {
    return "error";
  }
  if (
    artifact.renderStatus === "missing_receipt" ||
    artifact.deliveryStatus === "missing_receipt" ||
    artifact.renderStatus === "pending" ||
    artifact.deliveryStatus === "pending"
  ) {
    return "waiting";
  }
  return "done";
}

function productPlaneRowStatusFromWarning(
  warning: ProductPlaneWarningRow,
): WorkConsoleRowStatus {
  if (warning.severity === "blocked") return "error";
  if (warning.severity === "warning") return "waiting";
  return "info";
}

function productPlaneRowStatusFromRuntimeActivity(
  activity: ProductPlaneRuntimeActivityRow,
): WorkConsoleRowStatus {
  switch (activity.status) {
    case "done":
      return "done";
    case "error":
      return "error";
    case "waiting":
      return "waiting";
    case "running":
    default:
      return "running";
  }
}

function productPlaneStorageLabel(
  purpose: ProductPlaneStorageRow["purpose"],
  language?: ChatResponseLanguage,
): string {
  switch (purpose) {
    case "runtime_state":
      return t(language, "Runtime store", "런타임 저장소");
    case "hosted_sync":
      return t(language, "Hosted sync", "호스팅 동기화");
    case "artifact_index":
      return t(language, "Artifact index", "아티팩트 색인");
    case "artifact_blob":
      return t(language, "Artifact blob store", "아티팩트 Blob 저장소");
    default:
      return t(language, "Storage", "저장소");
  }
}

function productPlaneStorageDetail(
  store: ProductPlaneStorageRow,
): string | undefined {
  return productPlaneSafeDetail([
    store.mode,
    productPlaneSupportText(store.support),
    productPlaneStatusText(store.readiness),
    store.optional ? "optional" : undefined,
    ...store.reasonCodes,
    ...store.warningCodes,
  ]);
}

function productPlaneJobSummaryRow(
  jobs: ProductPlaneJobRow[],
  language?: ChatResponseLanguage,
): WorkConsoleRow | null {
  if (jobs.length === 0) return null;
  const counts = new Map<ProductPlaneJobRow["state"], number>();
  for (const job of jobs) {
    counts.set(job.state, (counts.get(job.state) ?? 0) + 1);
  }
  const detail = productPlaneSafeItems(
    [...counts.entries()].map(([state, count]) => `${count} ${state}`),
  ).join(" · ");
  const hasError = jobs.some((job) => job.state === "blocked" || job.state === "failed");
  const hasRunning = jobs.some((job) => job.state === "running");
  return {
    id: "product-plane:background-jobs",
    group: "trace",
    label: t(language, "Background product jobs", "백그라운드 제품 작업"),
    ...(detail ? { detail } : {}),
    status: hasError ? "error" : hasRunning ? "running" : "info",
  };
}

function productPlaneJobRow(
  job: ProductPlaneJobRow,
  language?: ChatResponseLanguage,
): WorkConsoleRow {
  return {
    id: `product-plane:job:${job.jobId}`,
    group: "trace",
    label: productPlaneLabelWithId(language, "Job", "작업", job.jobId),
    detail: productPlaneSafeDetail([
      job.state,
      job.ownerLabel,
      job.checkpointId,
      productPlaneReasonDetail(job.reasonCodes),
    ]),
    status: productPlaneRowStatusFromJob(job.state),
    ...(productPlaneDigest(job.policySnapshotDigest)
      ? { meta: productPlaneDigest(job.policySnapshotDigest) }
      : {}),
  };
}

function productPlanePermissionRow(
  permission: ProductPlanePermissionRow,
  language?: ChatResponseLanguage,
): WorkConsoleRow {
  return {
    id: `product-plane:permission:${permission.reviewId}`,
    group: "trace",
    label: productPlaneLabelWithId(language, "Permission", "권한", permission.reviewId),
    detail: productPlaneSafeDetail([
      permission.status,
      permission.approvalId ? `approval ${permission.approvalId}` : undefined,
      productPlaneReasonDetail(permission.reasonCodes),
    ]),
    status: productPlaneRowStatusFromGate(permission.status),
  };
}

function productPlaneGateRow(
  gate: ProductPlaneGateRow,
  labelPrefix: "Eval gate" | "Gate",
  language?: ChatResponseLanguage,
): WorkConsoleRow {
  return {
    id: `product-plane:gate:${labelPrefix}:${gate.gateId}`,
    group: "trace",
    label: productPlaneLabelWithId(
      language,
      labelPrefix,
      labelPrefix === "Eval gate" ? "평가 게이트" : "게이트",
      gate.gateId,
    ),
    detail: productPlaneSafeDetail([
      gate.status,
      gate.stage,
      productPlaneReasonDetail(gate.reasonCodes),
    ]),
    status: productPlaneRowStatusFromGate(gate.status),
  };
}

function productPlaneArtifactRow(
  artifact: ProductPlaneArtifactRow,
  language?: ChatResponseLanguage,
): WorkConsoleRow {
  const renderDigest = productPlaneDigest(artifact.renderDigest);
  const deliveryDigest = productPlaneDigest(artifact.deliveryDigest);
  return {
    id: `product-plane:artifact:${artifact.artifactId}`,
    group: "trace",
    label: productPlaneLabelWithId(language, "Artifact", "아티팩트", artifact.artifactId),
    detail: productPlaneSafeDetail([
      `render ${artifact.renderStatus}`,
      artifact.renderReceiptId ? `receipt ${artifact.renderReceiptId}` : undefined,
      renderDigest ? `digest ${renderDigest}` : undefined,
      `delivery ${artifact.deliveryStatus}`,
      artifact.deliveryReceiptId ? `receipt ${artifact.deliveryReceiptId}` : undefined,
      deliveryDigest ? `digest ${deliveryDigest}` : undefined,
      productPlaneReasonDetail(artifact.warningCodes),
    ]),
    status: productPlaneRowStatusFromArtifact(artifact),
  };
}

function productPlaneStorageRow(
  store: ProductPlaneStorageRow,
  language?: ChatResponseLanguage,
): WorkConsoleRow {
  return {
    id: `product-plane:storage:${store.purpose}:${store.storeId}`,
    group: "trace",
    label: productPlaneStorageLabel(store.purpose, language),
    detail: productPlaneStorageDetail(store),
    status: productPlaneRowStatusFromReadiness(store.readiness),
  };
}

function productPlaneWarningRow(
  warning: ProductPlaneWarningRow,
  index: number,
  language?: ChatResponseLanguage,
): WorkConsoleRow | null {
  const detail = productPlaneSafeText(warning.code);
  if (!detail) return null;
  return {
    id: `product-plane:warning:${index}:${detail}`,
    group: "trace",
    label: t(language, "Product-plane notice", "제품 플레인 알림"),
    detail,
    status: productPlaneRowStatusFromWarning(warning),
  };
}

function productPlaneRecipeSelectionRow(
  selection: ProductPlaneRecipeSelectionRow,
  index: number,
  language?: ChatResponseLanguage,
): WorkConsoleRow | null {
  const detail = productPlaneSafeDetail([
    productPlaneStatusText(selection.status),
    productPlaneSafeText(selection.selectionSource),
    productPlaneReasonDetail(selection.omissionReasons),
    selection.turnBlocked ? "turn blocked" : undefined,
    selection.fallbackUsed ? "fell back" : undefined,
    productPlaneSafeText(selection.nextAction),
  ]);
  return {
    id: `product-plane:recipe-selection:${index}`,
    group: "trace",
    label: t(language, "Recipe request", "레시피 요청"),
    detail,
    status:
      selection.status === "explicit_blocked" ||
      selection.status === "explicit_incompatible" ||
      selection.status === "explicit_unavailable"
        ? "error"
        : selection.status === "explicit_requested"
          ? "waiting"
          : "done",
    ...(productPlaneDigest(selection.policySnapshotDigest)
      ? { meta: productPlaneDigest(selection.policySnapshotDigest) }
      : {}),
  };
}

function productPlaneRuntimeActivityRow(
  activity: ProductPlaneRuntimeActivityRow,
  language?: ChatResponseLanguage,
): WorkConsoleRow | null {
  const fallbackLabel = activity.kind === "spawn"
    ? t(language, "Assigning helper", "도우미 배정")
    : t(language, "Runtime activity", "런타임 활동");
  const label = productPlaneSafeText(activity.label) ?? fallbackLabel;
  const detail = productPlaneSafeText(activity.detail);
  const duration = typeof activity.durationMs === "number" && activity.durationMs >= 0
    ? `${Math.round(activity.durationMs)}ms`
    : undefined;
  return {
    id: `product-plane:runtime:${activity.kind}:${activity.activityId}`,
    group: "trace",
    label,
    ...(detail ? { detail } : {}),
    status: productPlaneRowStatusFromRuntimeActivity(activity),
    ...(duration ? { meta: duration } : {}),
  };
}

function productPlaneUnsupportedRow(
  unsupported: ProductPlaneUnsupportedEventRow,
  index: number,
  language?: ChatResponseLanguage,
): WorkConsoleRow | null {
  const detail = [
    productPlaneUnsupportedEventText(unsupported.eventType),
    productPlaneSafeText(unsupported.reasonCode),
    productPlaneSafeText(unsupported.followUpTask),
  ].filter(Boolean).join(" · ");
  if (!detail) return null;
  return {
    id: `product-plane:unsupported:${index}`,
    group: "trace",
    label: t(language, "Unsupported event", "지원되지 않는 이벤트"),
    detail,
    status: "waiting",
  };
}

function productPlaneRowsFromView(
  productPlane: ProductPlaneViewModel | undefined,
  language?: ChatResponseLanguage,
): WorkConsoleRow[] {
  if (!productPlane) return [];

  const rows: WorkConsoleRow[] = [];
  rows.push({
    id: "product-plane:state",
    group: "trace",
    label: t(language, "Product plane", "제품 플레인"),
    detail: productPlaneSafeDetail([
      productPlaneStatusText(productPlane.backendState),
      productPlaneStatusText(productPlane.readiness),
      productPlane.enabled ? "read-only projection" : "default off",
      productPlane.policyConfigId,
      productPlaneDigest(productPlane.policySnapshotDigest),
    ]),
    status:
      productPlane.readiness === "ready"
        ? productPlaneRowStatusFromBackend(productPlane.backendState)
        : productPlaneRowStatusFromReadiness(productPlane.readiness),
  });

  const jobSummary = productPlaneJobSummaryRow(productPlane.jobs, language);
  if (jobSummary) rows.push(jobSummary);
  rows.push(
    ...productPlane.jobs
      .slice(0, PRODUCT_PLANE_ROW_LIMIT)
      .map((job) => productPlaneJobRow(job, language)),
  );

  rows.push(
    ...productPlane.permissions
      .slice(0, PRODUCT_PLANE_ROW_LIMIT)
      .map((permission) => productPlanePermissionRow(permission, language)),
  );

  const releaseEvalGateIds = new Set(
    productPlane.projection.releaseEvalGates.flatMap((gate) => {
      const safe = productPlaneSafeText(gate.gateId);
      return safe ? [safe] : [];
    }),
  );
  rows.push(
    ...productPlane.projection.releaseEvalGates
      .slice(0, PRODUCT_PLANE_ROW_LIMIT)
      .map((gate) => productPlaneGateRow(gate, "Eval gate", language)),
  );
  rows.push(
    ...productPlane.gates
      .filter((gate) => !releaseEvalGateIds.has(gate.gateId))
      .slice(0, PRODUCT_PLANE_ROW_LIMIT)
      .map((gate) => productPlaneGateRow(gate, "Gate", language)),
  );

  rows.push(
    ...productPlane.artifacts
      .slice(0, PRODUCT_PLANE_ROW_LIMIT)
      .map((artifact) => productPlaneArtifactRow(artifact, language)),
  );
  rows.push(
    ...productPlane.storage
      .slice(0, PRODUCT_PLANE_ROW_LIMIT)
      .map((store) => productPlaneStorageRow(store, language)),
  );
  rows.push(
    ...productPlane.recipeSelections
      .slice(0, PRODUCT_PLANE_ROW_LIMIT)
      .flatMap((selection, index) => {
        const row = productPlaneRecipeSelectionRow(selection, index, language);
        return row ? [row] : [];
      }),
  );
  rows.push(
    ...productPlane.runtimeActivities
      .slice(-PRODUCT_PLANE_ROW_LIMIT)
      .flatMap((activity) => {
        const row = productPlaneRuntimeActivityRow(activity, language);
        return row ? [row] : [];
      }),
  );
  rows.push(
    ...productPlane.warnings
      .slice(0, PRODUCT_PLANE_ROW_LIMIT)
      .flatMap((warning, index) => {
        const row = productPlaneWarningRow(warning, index, language);
        return row ? [row] : [];
      }),
  );
  rows.push(
    ...productPlane.unsupportedEvents
      .slice(0, PRODUCT_PLANE_ROW_LIMIT)
      .flatMap((unsupported, index) => {
        const row = productPlaneUnsupportedRow(unsupported, index, language);
        return row ? [row] : [];
      }),
  );

  return rows;
}

function pythonAdkRowsFromDiagnostics(
  diagnostics: PythonAdkDiagnostics | undefined,
  language?: ChatResponseLanguage,
): WorkConsoleRow[] {
  if (!diagnostics) return [];

  const authoritySource = productPlaneSafeText(diagnostics.authoritySource);
  const receiptDigest = productPlaneDigest(diagnostics.receiptDigest);
  const blockerReasonCodes = productPlaneSafeItems(diagnostics.blockerReasonCodes ?? []);
  const fallbackReasonCode = productPlaneSafeText(diagnostics.fallbackReasonCode);
  const runtimeStatus = productPlaneStatusText(diagnostics.runtimeStatus) ?? diagnostics.runtimeStatus;
  const shadowStatus = productPlaneStatusText(diagnostics.shadowStatus) ?? diagnostics.shadowStatus;
  const canaryStatus = productPlaneStatusText(diagnostics.canaryStatus) ?? diagnostics.canaryStatus;
  const rows: WorkConsoleRow[] = [
    {
      id: "python-adk:adk_runtime_status",
      group: "trace",
      label: t(language, "Python ADK runtime", "Python ADK 런타임"),
      detail: productPlaneSafeDetail([
        "adk_runtime_status",
        runtimeStatus,
        authoritySource,
      ]),
      ...(receiptDigest ? { meta: receiptDigest } : {}),
      status:
        diagnostics.runtimeStatus === "ready"
          ? "done"
          : diagnostics.runtimeStatus === "blocked"
            ? "error"
            : diagnostics.runtimeStatus === "degraded"
              ? "waiting"
              : "info",
    },
    {
      id: "python-adk:adk_shadow",
      group: "trace",
      label: "adk_shadow",
      detail: productPlaneSafeDetail([
        shadowStatus,
      ]),
      ...(receiptDigest ? { meta: receiptDigest } : {}),
      status:
        diagnostics.shadowStatus === "accepted"
          ? "done"
          : diagnostics.shadowStatus === "failed"
            ? "error"
            : diagnostics.shadowStatus === "skipped"
              ? "waiting"
              : "info",
    },
    {
      id: "python-adk:adk_canary",
      group: "trace",
      label: "adk_canary",
      detail: productPlaneSafeDetail([
        canaryStatus,
      ]),
      ...(receiptDigest ? { meta: receiptDigest } : {}),
      status:
        diagnostics.canaryStatus === "accepted"
          ? "done"
          : diagnostics.canaryStatus === "failed"
            ? "error"
            : diagnostics.canaryStatus === "skipped"
              ? "waiting"
              : "info",
    },
  ];

  if (fallbackReasonCode || blockerReasonCodes.length > 0) {
    rows.push({
      id: "python-adk:adk_fallback",
      group: "trace",
      label: "adk_fallback",
      detail: productPlaneSafeDetail([
        fallbackReasonCode,
        ...blockerReasonCodes,
      ]),
      ...(receiptDigest ? { meta: receiptDigest } : {}),
      status: "waiting",
    });
  }

  return rows;
}

function isMainLiveTranscriptWorkItem(
  item: NonNullable<ChannelState["liveTranscriptItems"]>[number],
): item is LiveTranscriptWorkItem {
  return item.kind === "work" && item.group !== "subagent";
}

function workRowsFromLiveTranscript(channelState: ChannelState): WorkConsoleRow[] {
  return (channelState.liveTranscriptItems ?? [])
    .filter(isMainLiveTranscriptWorkItem)
    .map((item) => ({
      id: item.id,
      group: item.group,
      label: item.label,
      detail: item.detail,
      snippet: item.snippet,
      status: item.status,
      meta: item.meta,
    }));
}

function mainAgentRows(
  rows: WorkConsoleRow[],
  channelState: ChannelState,
): WorkConsoleRow[] {
  const statusRows = rows.filter((row) => row.group === "status" && row.id !== "idle");
  const transcriptRows = workRowsFromLiveTranscript(channelState);
  if (transcriptRows.length > 0) return [...statusRows, ...transcriptRows].slice(-40);
  const activeRows = rows.filter((row) => MAIN_AGENT_GROUPS.has(row.group) && row.id !== "idle").slice(-40);
  if (activeRows.length > 0) return activeRows;
  return rows.filter((row) => row.group === "status").slice(0, 1);
}

function suppressInlineRunDetailRows(rows: WorkConsoleRow[]): WorkConsoleRow[] {
  return rows.filter((row) => !INLINE_RUN_DETAIL_GROUPS.has(row.group));
}

function compactDisplayGoal(value?: string | null): string | null {
  const normalized = value?.replace(/\s+/g, " ").trim();
  if (!normalized) return null;
  return normalized.length <= MAX_DISPLAY_GOAL_CHARS ? normalized : null;
}

function hasVisibleGoalMission(
  rows: WorkConsoleRow[],
  activeGoalMissionId?: string | null,
): boolean {
  const activeMissionRowId = activeGoalMissionId ? `mission:${activeGoalMissionId}` : null;
  return rows.some((row) => {
    if (row.group !== "mission") return false;
    if (activeMissionRowId && row.id === activeMissionRowId) return true;
    return row.meta?.split(/\s+/)[0] === "goal";
  });
}

function compactInlineOverviewRows(
  rows: WorkConsoleRow[],
  channelState: ChannelState,
  language?: ChatResponseLanguage,
): WorkConsoleRow[] {
  const visible = suppressInlineRunDetailRows(rows);
  const goal = hasVisibleGoalMission(visible, channelState.activeGoalMissionId)
    ? null
    : compactDisplayGoal(channelState.pendingGoalMissionTitle);
  if (goal) {
    visible.push({
      id: "overview:goal",
      group: "status",
      label: t(language, "Goal", "목표"),
      detail: goal,
      status: "info",
    });
  }
  return visible;
}

function sectionTone(
  group: WorkConsoleRowGroup,
): "status" | "mission" | "agents" | "actions" | "queue" | "default" {
  if (group === "status") return "status";
  if (group === "mission") return "mission";
  if (group === "subagent") return "agents";
  if (group === "tool") return "actions";
  if (group === "queue") return "queue";
  return "default";
}

function sectionClass(group: WorkConsoleRowGroup): string {
  switch (sectionTone(group)) {
    case "status":
      return "mb-3 min-h-0 rounded-xl border border-[#7C3AED]/15 bg-[#F8F6FF] p-2 shadow-[0_1px_6px_rgba(124,58,237,0.08)]";
    case "mission":
      return "mb-3 min-h-0 rounded-xl border border-sky-500/20 bg-sky-50/70 p-2 shadow-[0_1px_6px_rgba(14,165,233,0.08)]";
    case "agents":
      return "mb-3 min-h-0 rounded-xl border border-emerald-500/20 bg-white p-2 shadow-[0_1px_6px_rgba(16,185,129,0.08)]";
    case "actions":
      return "mb-3 min-h-0 rounded-xl border border-black/[0.08] bg-white p-2 shadow-[0_1px_6px_rgba(15,23,42,0.06)]";
    case "queue":
      return "mb-3 min-h-0 rounded-xl border border-amber-500/25 bg-amber-50 p-2 shadow-[0_1px_6px_rgba(245,158,11,0.10)]";
    case "default":
    default:
      return "mb-2 min-h-0";
  }
}

function actionRowToneClass(status: WorkConsoleRowStatus): string {
  switch (status) {
    case "running":
      return "border-[#7C3AED]/20 bg-[#F7F4FF] shadow-[0_1px_6px_rgba(124,58,237,0.08)]";
    case "done":
      return "border-emerald-500/15 bg-emerald-50/50 shadow-[0_1px_4px_rgba(16,185,129,0.06)]";
    case "error":
      return "border-red-500/15 bg-red-50/60 shadow-[0_1px_4px_rgba(239,68,68,0.06)]";
    case "waiting":
      return "border-amber-500/15 bg-amber-50/60 shadow-[0_1px_4px_rgba(245,158,11,0.06)]";
    case "info":
    default:
      return "border-black/[0.06] bg-white/70 shadow-[0_1px_0_rgba(0,0,0,0.03)]";
  }
}

function runningMotionClass(status: WorkConsoleRowStatus): string {
  return status === "running" ? "work-console-running-row" : "";
}

function runningDotMotionClass(status: WorkConsoleRowStatus): string {
  return status === "running" ? "work-console-running-dot" : "";
}

function motionStyle(delayMs: number): WorkConsoleMotionStyle {
  return { "--work-console-row-delay": `${delayMs}ms` };
}

function shouldUseExpandableSnippet(snippet: string): boolean {
  return snippet.length > EXPANDABLE_SNIPPET_CHARS || snippet.includes("\n");
}

function WorkConsoleSnippet({
  snippet,
  isActionRow,
}: {
  snippet: string;
  isActionRow: boolean;
}) {
  const preClassName = isActionRow
    ? "work-console-text-motion mt-2 max-h-20 overflow-auto rounded-md bg-black/[0.04] px-2 py-1.5 whitespace-pre-wrap break-words text-[10.5px] leading-snug text-secondary/70"
    : "work-console-text-motion mt-1 max-h-28 overflow-auto rounded-md bg-black/[0.04] px-2 py-1.5 whitespace-pre-wrap break-words text-[10.5px] leading-snug text-secondary/70";

  if (!shouldUseExpandableSnippet(snippet)) {
    return (
      <pre key={snippet} className={preClassName}>
        {snippet}
      </pre>
    );
  }

  return (
    <details
      key={snippet}
      className="work-console-text-motion mt-1.5 rounded-md bg-black/[0.04] px-2 py-1.5 text-[10.5px] leading-snug text-secondary/70"
      data-work-console-row-details="true"
    >
      <summary className="cursor-pointer select-none text-[10px] font-medium uppercase tracking-wide text-secondary/45">
        Details
      </summary>
      <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words">
        {snippet}
      </pre>
    </details>
  );
}

function useSmoothedChannelState(channelState: ChannelState): ChannelState {
  const [displayNowMs, setDisplayNowMs] = useState(() => Date.now());
  const heartbeatAnchorRef = useRef<{
    elapsedMs: number | null;
    observedAtMs: number;
  }>({
    elapsedMs: channelState.heartbeatElapsedMs ?? null,
    observedAtMs: displayNowMs,
  });
  const currentHeartbeatElapsedMs = channelState.heartbeatElapsedMs ?? null;

  if (heartbeatAnchorRef.current.elapsedMs !== currentHeartbeatElapsedMs) {
    heartbeatAnchorRef.current = {
      elapsedMs: currentHeartbeatElapsedMs,
      observedAtMs: displayNowMs,
    };
  }

  useEffect(() => {
    if (!channelState.streaming || currentHeartbeatElapsedMs === null) return;
    const tick = window.setInterval(() => {
      setDisplayNowMs(Date.now());
    }, WORK_CONSOLE_MOTION_TICK_MS);
    return () => window.clearInterval(tick);
  }, [channelState.streaming, currentHeartbeatElapsedMs]);

  const smoothedElapsedMs = smoothedHeartbeatElapsedMs(
    heartbeatAnchorRef.current.elapsedMs,
    heartbeatAnchorRef.current.observedAtMs,
    displayNowMs,
  );

  if (smoothedElapsedMs === currentHeartbeatElapsedMs) return channelState;
  return { ...channelState, heartbeatElapsedMs: smoothedElapsedMs };
}

function WorkConsoleRowItem({
  row,
  motionDelayMs,
}: {
  row: WorkConsoleRow;
  motionDelayMs: number;
}) {
  const isActionRow = row.group === "tool";
  const isStatusRow = row.group === "status";
  const isMissionRow = row.group === "mission";
  const isQueueRow = row.group === "queue";
  const isDeterministicRuntimeRow = isDeterminismRow(row);
  const isProductPlaneRuntimeRow = isProductPlaneRow(row);

  return (
    <li
      className={
        isStatusRow
          ? `work-console-row-motion flex min-w-0 items-start gap-2 rounded-lg border border-[#7C3AED]/20 bg-white/70 px-2.5 py-2.5 shadow-[0_1px_4px_rgba(124,58,237,0.08)] ${runningMotionClass(row.status)}`
          : isMissionRow
            ? `work-console-row-motion flex min-w-0 items-start gap-2 rounded-lg border border-sky-500/20 bg-white/75 px-2.5 py-2 shadow-[0_1px_4px_rgba(14,165,233,0.08)] ${runningMotionClass(row.status)}`
          : isActionRow
            ? `work-console-row-motion flex min-w-0 items-start gap-2 rounded-lg border px-2.5 py-2 ${actionRowToneClass(row.status)} ${runningMotionClass(row.status)}`
            : isQueueRow
              ? `work-console-row-motion flex min-w-0 items-start gap-2 rounded-lg border border-amber-500/20 bg-white/70 px-2.5 py-2 shadow-[0_1px_4px_rgba(245,158,11,0.08)] ${runningMotionClass(row.status)}`
              : `work-console-row-motion flex min-w-0 items-start gap-2 rounded-md px-2 py-1.5 ${runningMotionClass(row.status)}`
      }
      data-work-console-motion="true"
      data-work-console-action-row={isActionRow ? "true" : undefined}
      data-work-console-status-row={isStatusRow ? "true" : undefined}
      data-work-console-mission-row={isMissionRow ? "true" : undefined}
      data-work-console-queue-row={isQueueRow ? "true" : undefined}
      data-work-console-determinism-row={isDeterministicRuntimeRow ? "true" : undefined}
      data-work-console-product-plane-row={isProductPlaneRuntimeRow ? "true" : undefined}
      data-work-console-row-status={row.status}
      style={motionStyle(motionDelayMs)}
    >
      <span
        className={`${isStatusRow ? "mt-2 h-2.5 w-2.5 ring-4 ring-[#7C3AED]/10" : isMissionRow || isActionRow ? "mt-2 h-2 w-2" : "mt-1.5 h-1.5 w-1.5"} shrink-0 rounded-full ${statusClass(row.status)} ${runningDotMotionClass(row.status)}`}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <span
            key={row.label}
            className="work-console-text-motion min-w-0 truncate text-[12px] font-medium text-foreground/80"
          >
            {row.label}
          </span>
          {row.meta && (
            <span
              key={row.meta}
              className="work-console-text-motion shrink-0 text-[10px] text-secondary/40"
            >
              {row.meta}
            </span>
          )}
        </div>
        {row.detail && (
          <p
            key={row.detail}
            className={
              isStatusRow
                ? "work-console-text-motion mt-0.5 break-words text-[11.5px] leading-snug text-secondary/60"
                : isQueueRow
                  ? "work-console-text-motion mt-1 line-clamp-3 break-words text-[11.5px] leading-snug text-amber-950/75"
                : isActionRow
                ? "work-console-text-motion mt-1 line-clamp-2 break-words text-[11.5px] leading-snug text-secondary/65"
                : "work-console-text-motion mt-0.5 line-clamp-3 break-words text-[11px] leading-snug text-secondary/65"
            }
          >
            {row.detail}
          </p>
        )}
        {row.snippet && (
          <WorkConsoleSnippet snippet={row.snippet} isActionRow={isActionRow} />
        )}
      </div>
    </li>
  );
}

interface BrowserWorkSignal {
  detail?: string;
  url?: string;
}

const BROWSER_WORK_RE = /\b(?:browser|browseruse|browserworker|socialbrowser|agent-browser)\b/i;
const URL_RE = /\bhttps?:\/\/[^\s"'<>),]+/i;
const SENSITIVE_BROWSER_URL_PATH_RE = /(?:^|\/)(?:auth|callback|cookie|oauth|sessions|session)(?:[/?#]|$)/i;
const MAX_BROWSER_SIGNAL_URL_CHARS = 500;

function normalizedBrowserRouteSeparators(value: string): string {
  return value
    .replace(/%(?:2f|5c)/gi, "/")
    .replace(/%3f/gi, "?")
    .replace(/%23/gi, "#");
}

function hasSensitiveBrowserUrlPath(value: string): boolean {
  return (
    SENSITIVE_BROWSER_URL_PATH_RE.test(value) ||
    SENSITIVE_BROWSER_URL_PATH_RE.test(normalizedBrowserRouteSeparators(value))
  );
}

function safeBrowserSignalUrl(value?: string): string | undefined {
  const trimmed = value?.trim();
  if (!trimmed) return undefined;

  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return undefined;
    if (hasSensitiveBrowserUrlPath(parsed.pathname)) return parsed.origin;
    const publicUrl = `${parsed.origin}${parsed.pathname}`;
    return SECRET_SHAPE_RE.test(publicUrl) || PRIVATE_PATH_RE.test(publicUrl) ? undefined : publicUrl;
  } catch {
    if (
      hasSensitiveBrowserUrlPath(trimmed) ||
      SECRET_SHAPE_RE.test(trimmed) ||
      PRIVATE_PATH_RE.test(trimmed)
    ) {
      return undefined;
    }
    if (trimmed.startsWith("/")) {
      const publicPath = trimmed.split(/[?#]/, 1)[0];
      return publicPath || undefined;
    }
    const publicText = trimmed.split(/[?#]/, 1)[0] ?? "";
    const safeText = publicText || trimmed;
    return safeText.length > MAX_BROWSER_SIGNAL_URL_CHARS
      ? `${safeText.slice(0, MAX_BROWSER_SIGNAL_URL_CHARS - 3)}...`
      : safeText;
  }
}

function browserUrlFromPreview(value?: string): string | undefined {
  if (!value) return undefined;
  try {
    const parsed = JSON.parse(value) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const record = parsed as Record<string, unknown>;
      if (typeof record.url === "string" && record.url) return safeBrowserSignalUrl(record.url);
      if (typeof record.target === "string" && URL_RE.test(record.target)) {
        return safeBrowserSignalUrl(record.target.match(URL_RE)?.[0]);
      }
    }
  } catch {
    // Fall through to a lightweight URL scan for truncated previews.
  }
  return safeBrowserSignalUrl(value.match(URL_RE)?.[0]);
}

function browserWorkSignal(channelState: ChannelState): BrowserWorkSignal | null {
  if (channelState.browserFrame) {
    return {
      ...(channelState.browserFrame.url ? { url: channelState.browserFrame.url } : {}),
    };
  }

  for (const activity of channelState.activeTools ?? []) {
    const text = [activity.label, activity.inputPreview, activity.outputPreview]
      .filter(Boolean)
      .join(" ");
    if (activity.status !== "running" || !BROWSER_WORK_RE.test(text)) continue;
    const url = browserUrlFromPreview(activity.inputPreview) ?? browserUrlFromPreview(activity.outputPreview);
    return {
      detail: activity.label,
      ...(url ? { url } : {}),
    };
  }

  for (const events of Object.values(channelState.subagentProgress ?? {})) {
    for (const event of [...events].reverse()) {
      const text = [event.label, event.detail].filter(Boolean).join(" ");
      if (event.status !== "running" || !BROWSER_WORK_RE.test(text)) continue;
      const url = browserUrlFromPreview(event.detail);
      return {
        detail: event.detail ?? event.label,
        ...(url ? { url } : {}),
      };
    }
  }

  return null;
}

function BrowserWorkStream({
  frame,
  signal,
  language,
}: {
  frame: NonNullable<ChannelState["browserFrame"]> | null;
  signal: BrowserWorkSignal | null;
  language?: ChatResponseLanguage;
}) {
  if (frame) {
    return (
      <div data-work-console-browser-stream="frame">
        <BrowserFramePreview
          frame={frame}
          language={language}
          surface="work-console"
          className="mb-3 overflow-hidden rounded-xl border border-black/[0.08] bg-white shadow-[0_1px_6px_rgba(15,23,42,0.06)]"
          imageClassName="block aspect-video w-full bg-black/[0.03] object-contain"
        />
      </div>
    );
  }

  if (!signal) return null;

  return (
    <section
      className="mb-3 overflow-hidden rounded-xl border border-black/[0.08] bg-white shadow-[0_1px_6px_rgba(15,23,42,0.06)]"
      data-work-console-browser-stream="pending"
    >
      <div className="flex min-w-0 items-center justify-between gap-2 border-b border-black/[0.06] px-2.5 py-1.5">
        <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-secondary/45">
          {t(language, "Live browser", "실시간 브라우저")}
        </span>
        <span className="shrink-0 rounded-full bg-[#7C3AED]/10 px-1.5 py-0.5 text-[9px] font-semibold text-[#7C3AED]">
          {t(language, "Active", "실행 중")}
        </span>
      </div>
      <div className="flex aspect-video min-h-[8rem] flex-col items-center justify-center gap-2 bg-black/[0.03] px-3 text-center">
        <span className="h-2.5 w-2.5 rounded-full bg-[#7C3AED] work-console-running-dot" aria-hidden="true" />
        <span className="text-[12px] font-medium text-foreground/70">
          {t(language, "Waiting for browser frame", "브라우저 프레임 대기 중")}
        </span>
        {(signal.url ?? signal.detail) && (
          <span className="max-w-full truncate text-[10.5px] text-secondary/55">
            {signal.url ?? signal.detail}
          </span>
        )}
      </div>
    </section>
  );
}

export function WorkConsolePanel({
  channelState,
  queuedMessages = [],
  controlRequests = [],
  suppressInlineRunDetails = false,
  uiLanguage,
}: WorkConsolePanelProps): React.ReactElement {
  const actionsListRef = useRef<HTMLUListElement | null>(null);
  const smoothedChannelState = useSmoothedChannelState(channelState);
  const language = uiLanguage ?? smoothedChannelState.responseLanguage;
  const productPlaneRows = suppressInlineRunDetails
    ? []
    : productPlaneRowsFromView(
        productPlaneFromChannelState(smoothedChannelState),
        language,
      );
  const pythonAdkRows = suppressInlineRunDetails
    ? []
    : pythonAdkRowsFromDiagnostics(
        pythonAdkDiagnosticsFromChannelState(smoothedChannelState),
        language,
      );
  const allRows = deriveWorkConsoleRows({
    channelState: smoothedChannelState,
    queuedMessages,
    controlRequests,
    uiLanguage: language,
  });
  const visibleRows = suppressInlineRunDetails
    ? compactInlineOverviewRows(allRows, smoothedChannelState, language)
    : allRows;
  const rows = visibleRows.length > 0
    ? visibleRows
    : [
        {
          id: "inline-stream",
          group: "status" as const,
          label: t(language, "Waiting for activity", "활동 대기 중"),
          detail: t(
            language,
            "Live step details will appear here as the run reports progress.",
            "실행이 진행 상황을 보고하면 실시간 단계 상세가 여기에 표시됩니다.",
          ),
          status: "info" as const,
        },
      ];
  const deterministicRows = rows.filter(isDeterminismRow);
  const rowsWithoutDeterminism = rows.filter((row) => !isDeterminismRow(row));
  const agentMainRows = mainAgentRows(rowsWithoutDeterminism, smoothedChannelState);
  const agentRows = rows.filter((row) => row.group === "subagent");
  const groups = groupRows([
    ...deterministicRows,
    ...productPlaneRows,
    ...pythonAdkRows,
    ...rows.filter((row) => row.group === "mission"),
  ]);
  const actionRows = agentMainRows.filter((row) => row.group === "tool");
  const lastActionId = actionRows[actionRows.length - 1]?.id ?? "";
  const browserSignal = browserWorkSignal(smoothedChannelState);

  useEffect(() => {
    const el = actionsListRef.current;
    if (!el) return;

    const reduceMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({
      top: el.scrollHeight,
      behavior: reduceMotion ? "auto" : "smooth",
    });
  }, [actionRows.length, lastActionId]);

  return (
    <div className="flex min-h-0 flex-1 flex-col" aria-label={t(language, "Work in progress", "진행 중인 작업")}>
      <div className="border-b border-black/[0.06] px-3 py-2">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-secondary/70">
          {t(language, "Work in progress", "진행 중인 작업")}
        </div>
        <p className="mt-1 text-[11px] leading-snug text-secondary/45">
          {t(language, "Plain-language progress from the current run.", "현재 실행의 진행 상황입니다.")}
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        <BrowserWorkStream
          frame={smoothedChannelState.browserFrame ?? null}
          signal={browserSignal}
          language={language}
        />
        {channelState.documentDraft && (
          <DocumentDraftPreviewCard draft={channelState.documentDraft} language={language} />
        )}
        {(agentMainRows.length > 0 || agentRows.length > 0) && (
          <section
            className={sectionClass("subagent")}
            data-work-console-group="subagent"
            data-work-console-section-tone="agents"
            data-work-console-section-density="compact"
          >
            <div className="mb-1.5 flex items-center justify-between px-1 text-[10px] font-semibold uppercase tracking-wide text-secondary/45">
              <span>{groupLabel("subagent", language)}</span>
              <span className="rounded-full bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700">
                {isKorean(language)
                  ? `${(agentMainRows.length > 0 ? 1 : 0) + agentRows.length}명`
                  : `${(agentMainRows.length > 0 ? 1 : 0) + agentRows.length} agents`}
              </span>
            </div>
            <SubagentWorkPanel
              rows={agentRows}
              mainRows={agentMainRows}
              channelState={smoothedChannelState}
              language={language}
            />
          </section>
        )}
        {groups.map(([group, groupRows]) => {
          const isActionsGroup = group === "tool";
          const isSubagentGroup = group === "subagent";
          const tone = sectionTone(group);
          const isProminentGroup = tone !== "default";
          return (
            <section
              key={group}
              className={sectionClass(group)}
              data-work-console-group={group}
              data-work-console-section-tone={tone}
              data-work-console-section-density={isSubagentGroup || group === "trace" ? "compact" : undefined}
            >
              <div
                className={
                  isProminentGroup
                    ? "mb-1.5 flex items-center justify-between px-1 text-[10px] font-semibold uppercase tracking-wide text-secondary/45"
                    : "mb-1 px-2 text-[10px] font-semibold uppercase tracking-wide text-secondary/40"
                }
              >
                <span>{groupLabel(group, language)}</span>
                {tone === "status" && (
                  <span className="rounded-full bg-[#7C3AED]/10 px-1.5 py-0.5 text-[9px] font-semibold text-[#7C3AED]">
                    {t(language, "Live", "실시간")}
                  </span>
                )}
                {tone === "actions" && (
                  <span className="rounded-full bg-black/[0.04] px-1.5 py-0.5 text-[9px] font-semibold text-secondary/45">
                    {groupRows.length}
                  </span>
                )}
                {tone === "mission" && (
                  <span className="rounded-full bg-sky-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-sky-800">
                    {groupRows.length} tracked
                  </span>
                )}
                {tone === "agents" && (
                  <span className="rounded-full bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700">
                    {isKorean(language) ? `${groupRows.length}명` : `${groupRows.length} agents`}
                  </span>
                )}
                {tone === "queue" && (
                  <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-amber-800">
                    {isKorean(language) ? `${groupRows.length}개 대기` : `${groupRows.length} waiting`}
                  </span>
                )}
              </div>
              {isSubagentGroup ? (
                <SubagentWorkPanel
                  rows={groupRows}
                  channelState={smoothedChannelState}
                  language={language}
                />
              ) : (
                <ul
                  ref={isActionsGroup ? actionsListRef : undefined}
                  className={
                    isActionsGroup
                      ? "max-h-[44vh] space-y-0.5 overflow-y-auto overscroll-contain pr-1"
                      : "space-y-0.5"
                  }
                  data-work-console-actions-scroll={isActionsGroup ? "bottom" : undefined}
                  aria-label={isActionsGroup ? groupLabel("tool", language) : undefined}
                >
                  {groupRows.map((row, index) => (
                    <WorkConsoleRowItem
                      key={row.id}
                      row={row}
                      motionDelayMs={workConsoleRowDelayMs(index)}
                    />
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
