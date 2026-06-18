"use client";

import { useEffect, useState, type ReactNode } from "react";
import { AgentActivityTimeline } from "./agent-activity-timeline";
import { BrowserFramePreview } from "./browser-frame-preview";
import { DocumentDraftPreviewCard } from "./document-draft-preview";
import { TaskBoard } from "./task-board";
import type { ProductPlaneViewModel } from "@/lib/agent-runtime/product-plane-reducer";
import { deriveWorkStateSummary, type WorkStateSummary } from "@/chat-core/work-state";
import type {
  CitationGateStatus,
  ChannelState,
  ChatResponseLanguage,
  ControlRequestRecord,
  DeterministicGuardrailSummary,
  DeterministicRuntimeState,
  InspectedSource,
  QueuedMessage,
  SubagentActivity,
  TaskBoardSnapshot,
} from "@/chat-core";

interface RunInspectorDockProps {
  channelState: ChannelState;
  queuedMessages?: QueuedMessage[];
  controlRequests?: ControlRequestRecord[];
  cancelHint?: string | null;
  defaultHidden?: boolean;
  compactDetails?: boolean;
  uiLanguage?: ChatResponseLanguage;
}

type ChannelStateProductPlaneProjection = ChannelState & {
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

type ChannelStatePythonAdkDiagnostics = ChannelState & {
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

const PYTHON_ADK_SAFE_DIGEST_RE = /^sha256:[a-f0-9]{64}$/;

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
  if (typeof value !== "string" || !PYTHON_ADK_SAFE_DIGEST_RE.test(value)) {
    return undefined;
  }
  return value;
}

function safePythonAdkText(value: unknown): string | undefined {
  return typeof value === "string" ? safeProductPlaneText(value) : undefined;
}

function safePythonAdkReasonCodes(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const reasonCodes = value.flatMap((reasonCode) => {
    const safe = safePythonAdkText(reasonCode);
    return safe ? [safe] : [];
  });
  return reasonCodes.length > 0 ? reasonCodes : undefined;
}

type ProductPlaneStoreSummary = ProductPlaneViewModel["projection"]["storage"]["durableStore"];

interface ProductPlaneStatusItem {
  id: string;
  status: string;
  detail?: string;
}

function productPlaneFromChannelState(
  channelState: ChannelState,
): ProductPlaneViewModel | undefined {
  return (channelState as ChannelStateProductPlaneProjection).productPlane;
}

function pythonAdkDiagnosticsFromChannelState(
  channelState: ChannelState,
): PythonAdkDiagnostics | undefined {
  const diagnostics = (channelState as ChannelStatePythonAdkDiagnostics).pythonAdkDiagnostics;
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

function openTaskBoard(snapshot?: TaskBoardSnapshot | null): TaskBoardSnapshot | null {
  if (!snapshot?.tasks.length) return null;
  if (!snapshot.tasks.some((task) => task.status === "pending" || task.status === "in_progress")) {
    return null;
  }
  return snapshot;
}

function pendingControlRequests(
  requests?: ControlRequestRecord[],
): ControlRequestRecord[] {
  return (requests ?? []).filter((request) => request.state === "pending");
}

function hasVisibleRunState(
  channelState: ChannelState,
  queuedMessages: QueuedMessage[],
  pendingRequests: ControlRequestRecord[],
  taskBoard: TaskBoardSnapshot | null,
  subagents: SubagentActivity[],
): boolean {
  const inspectedSources = channelState.inspectedSources ?? [];
  const productPlane = productPlaneFromChannelState(channelState);
  const pythonAdkDiagnostics = pythonAdkDiagnosticsFromChannelState(channelState);
  return (
    channelState.streaming ||
    (channelState.activeTools ?? []).length > 0 ||
    !!channelState.browserFrame ||
    !!channelState.documentDraft ||
    subagents.length > 0 ||
    queuedMessages.length > 0 ||
    pendingRequests.length > 0 ||
    !!taskBoard ||
    inspectedSources.length > 0 ||
    !!channelState.citationGate ||
    !!productPlane ||
    !!pythonAdkDiagnostics
  );
}

function runIdentity(
  channelState: ChannelState,
  queuedMessages: QueuedMessage[],
  pendingRequests: ControlRequestRecord[],
  taskBoard: TaskBoardSnapshot | null,
  subagents: SubagentActivity[],
): string {
  const activeTools = channelState.activeTools ?? [];
  const inspectedSources = channelState.inspectedSources ?? [];
  const productPlane = productPlaneFromChannelState(channelState);
  const pythonAdkDiagnostics = pythonAdkDiagnosticsFromChannelState(channelState);
  const startedAt =
    channelState.thinkingStartedAt ??
    activeTools[0]?.startedAt ??
    channelState.browserFrame?.capturedAt ??
    channelState.documentDraft?.updatedAt ??
    subagents[0]?.startedAt ??
    inspectedSources[0]?.inspectedAt ??
    channelState.citationGate?.checkedAt ??
    null;
  if (startedAt !== null) return `run:${startedAt}`;

  if (productPlane) {
    return [
      "product-plane",
      productPlane.backendState,
      productPlane.readiness,
      productPlane.policySnapshotDigest,
      productPlane.policyConfigId,
      productPlane.appliedEventKeys.join(","),
      productPlane.warnings.map((warning) => warning.code).join(","),
    ].filter(Boolean).join(":");
  }

  if (pythonAdkDiagnostics) {
    return [
      "python-adk",
      pythonAdkDiagnostics.authoritySource,
      pythonAdkDiagnostics.runtimeStatus,
      pythonAdkDiagnostics.shadowStatus,
      pythonAdkDiagnostics.canaryStatus,
      pythonAdkDiagnostics.receiptDigest,
      ...(pythonAdkDiagnostics.blockerReasonCodes ?? []),
      pythonAdkDiagnostics.fallbackReasonCode,
    ].filter(Boolean).join(":");
  }

  if (pendingRequests.length > 0) {
    return `controls:${pendingRequests.map((request) => request.requestId).join(",")}`;
  }
  if (queuedMessages.length > 0) {
    return `queue:${queuedMessages.map((message) => message.id).join(",")}`;
  }
  if (taskBoard) {
    return `task-board:${taskBoard.tasks.map((task) => task.id).join(",")}`;
  }
  if (channelState.streaming) return "streaming";
  return "visible";
}

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function t(language: ChatResponseLanguage | undefined, en: string, ko: string): string {
  return isKorean(language) ? ko : en;
}

function phaseLabel(
  phase: ChannelState["turnPhase"],
  language?: ChatResponseLanguage,
): string | null {
  switch (phase) {
    case "pending":
      return t(language, "Preparing", "준비 중");
    case "planning":
      return t(language, "Planning", "계획 중");
    case "executing":
      return t(language, "Running", "실행 중");
    case "compacting":
      return t(language, "Compacting", "압축 중");
    case "verifying":
      return t(language, "Verifying", "검증 중");
    case "committing":
      return t(language, "Writing answer", "답변 작성 중");
    case "aborted":
      return t(language, "Stopping", "중단 중");
    case "committed":
      return t(language, "Finishing", "마무리 중");
    default:
      return null;
  }
}

const SUBAGENT_NAMES = [
  "Halley",
  "Meitner",
  "Kant",
  "Noether",
  "Turing",
  "Curie",
  "Hopper",
  "Lovelace",
  "Feynman",
  "Franklin",
  "Shannon",
  "Lamarr",
];

function subagentDisplayName(index: number): string {
  return SUBAGENT_NAMES[index % SUBAGENT_NAMES.length] ?? `Agent ${index + 1}`;
}

function subagentRoleLabel(role: string, language?: ChatResponseLanguage): string {
  const normalized = role.trim().toLowerCase();
  if (normalized === "explore" || normalized === "explorer" || normalized === "research") {
    return t(language, "explorer", "탐색");
  }
  if (normalized === "review" || normalized === "reviewer") return t(language, "reviewer", "검토");
  if (normalized === "worker" || normalized === "work") return t(language, "worker", "작업");
  return normalized || t(language, "subagent", "도우미");
}

function subagentStatusLabel(
  status: SubagentActivity["status"],
  language?: ChatResponseLanguage,
): string {
  const statusLanguage = language === "ko" ? "ko" : "en";
  switch (status) {
    case "waiting":
      return t(statusLanguage, "waiting", "승인 대기");
    case "done":
      return t(statusLanguage, "done", "완료");
    case "error":
      return t(statusLanguage, "failed", "실패");
    case "cancelled":
      return t(statusLanguage, "cancelled", "중단됨");
    case "running":
    default:
      return t(statusLanguage, "running", "작업 중");
  }
}

function subagentDotClass(status: SubagentActivity["status"]): string {
  switch (status) {
    case "waiting":
      return "bg-amber-500";
    case "done":
      return "bg-emerald-500";
    case "error":
    case "cancelled":
      return "bg-red-500";
    case "running":
    default:
      return "bg-[#7C3AED]";
  }
}

function BackgroundSubagents({
  subagents,
  language,
}: {
  subagents: SubagentActivity[];
  language?: ChatResponseLanguage;
}) {
  if (subagents.length === 0) return null;

  return (
    <div className="mt-2 border-t border-black/[0.06] pt-2" aria-label={t(language, "Background agents", "백그라운드 도우미")}>
      <div className="mb-1.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
        <span>{t(language, "Background agents", "백그라운드 도우미")}</span>
        <span className="font-medium normal-case tracking-normal text-secondary/35">
          {subagents.length}
        </span>
      </div>
      <div className="space-y-1">
        {subagents.map((subagent, index) => (
          <div key={subagent.taskId} className="flex min-w-0 items-center gap-2 text-xs">
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${subagentDotClass(subagent.status)}`}
              aria-hidden="true"
            />
            <span className="shrink-0 font-semibold text-secondary/80">
              {subagentDisplayName(index)}
            </span>
            <span className="shrink-0 text-secondary/45">
              ({subagentRoleLabel(subagent.role, language)})
            </span>
            <span className="shrink-0 text-secondary/55">-</span>
            <span className="shrink-0 text-secondary/70">
              {subagentStatusLabel(subagent.status, language)}
            </span>
            {subagent.detail && (
              <span className="min-w-0 truncate text-secondary/45">{subagent.detail}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function WorkStateSummaryRows({
  summary,
  language,
}: {
  summary: WorkStateSummary;
  language?: ChatResponseLanguage;
}) {
  const rows = [
    { label: t(language, "Goal", "목표"), value: summary.goal },
    { label: t(language, "Status", "상태"), value: summary.status },
    { label: t(language, "Progress", "진행"), value: summary.progress },
    { label: t(language, "Now", "현재"), value: summary.now },
    { label: t(language, "Next", "다음"), value: summary.next },
  ].filter((row): row is { label: string; value: string } => Boolean(row.value));

  return (
    <div className="mt-2 border-t border-black/[0.06] pt-2" aria-label={t(language, "Work state summary", "작업 상태 요약")}>
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
        {summary.title}
      </div>
      <dl className="grid gap-1.5 text-xs">
        {rows.map((row) => (
          <div key={row.label} className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-2">
            <dt className="shrink-0 font-medium text-secondary/45">{row.label}</dt>
            <dd className="min-w-0 break-words text-secondary/80">{row.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function sourceKindLabel(kind: InspectedSource["kind"], language?: ChatResponseLanguage): string {
  switch (kind) {
    case "web_search":
      return t(language, "search", "검색");
    case "web_fetch":
      return t(language, "web", "웹");
    case "browser":
      return t(language, "browser", "브라우저");
    case "kb":
      return "KB";
    case "file":
      return t(language, "file", "파일");
    case "external_repo":
      return t(language, "repo", "저장소");
    default:
      return t(language, "source", "출처");
  }
}

function displaySourceUri(uri: string): string {
  try {
    const parsed = new URL(uri);
    return `${parsed.hostname}${parsed.pathname}${parsed.search}`.replace(/\/$/, "");
  } catch {
    return uri.replace(/^external:/, "");
  }
}

function citationStatusLabel(
  status: CitationGateStatus,
  language?: ChatResponseLanguage,
): string {
  switch (status.verdict) {
    case "ok":
      return t(language, "covered", "충족");
    case "violation":
      return t(language, "needs citations", "인용 필요");
    case "pending":
    default:
      return t(language, "checking", "확인 중");
  }
}

function citationStatusClass(status: CitationGateStatus): string {
  switch (status.verdict) {
    case "ok":
      return "bg-emerald-500";
    case "violation":
      return "bg-amber-500";
    case "pending":
    default:
      return "bg-secondary/35";
  }
}

function ResearchEvidence({
  sources,
  citationGate,
  language,
}: {
  sources: InspectedSource[];
  citationGate?: CitationGateStatus | null;
  language?: ChatResponseLanguage;
}) {
  if (sources.length === 0 && !citationGate) return null;
  const recentSources = sources.slice(-5).reverse();

  return (
    <div className="mt-2 border-t border-black/[0.06] pt-2" aria-label={t(language, "Research evidence", "리서치 근거")}>
      <div className="mb-1.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
        <span>{t(language, "Research evidence", "리서치 근거")}</span>
        {sources.length > 0 && (
          <span className="font-medium normal-case tracking-normal text-secondary/35">
            {isKorean(language) ? `출처 ${sources.length}개` : `${sources.length} sources`}
          </span>
        )}
      </div>

      {citationGate && (
        <div className="mb-1.5 flex min-w-0 items-center gap-2 text-xs">
          <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${citationStatusClass(citationGate)}`}
            aria-hidden="true"
          />
          <span className="shrink-0 font-medium text-secondary/80">
            {t(language, "Citation coverage", "인용 커버리지")}
          </span>
          <span className="shrink-0 text-secondary/50">
            {citationStatusLabel(citationGate, language)}
          </span>
          {citationGate.detail && (
            <span className="min-w-0 truncate text-secondary/55">{citationGate.detail}</span>
          )}
        </div>
      )}

      {recentSources.length > 0 && (
        <div className="space-y-1" aria-label={t(language, "Sources", "출처")}>
          <div className="sr-only">{t(language, "Sources", "출처")}</div>
          {recentSources.map((source) => (
            <div key={source.sourceId} className="flex min-w-0 items-center gap-2 text-xs">
              <span className="shrink-0 rounded bg-black/[0.04] px-1.5 py-0.5 font-mono text-[10px] text-secondary/55">
                {source.sourceId}
              </span>
              <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-secondary/40">
                {sourceKindLabel(source.kind, language)}
              </span>
              {source.title && (
                <span className="min-w-0 truncate font-medium text-secondary/80">
                  {source.title}
                </span>
              )}
              <span className="min-w-0 truncate text-secondary/50">
                {displaySourceUri(source.uri)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function shortDigest(value?: string): string | undefined {
  if (!value) return undefined;
  return value.length > 24 ? `${value.slice(0, 13)}…${value.slice(-6)}` : value;
}

function guardrailDotClass(status: DeterministicGuardrailSummary["status"]): string {
  switch (status) {
    case "passed":
      return "bg-emerald-500";
    case "blocked":
      return "bg-red-500";
    case "approval_required":
    case "repair":
    case "fallback":
      return "bg-amber-500";
    case "abstained":
    default:
      return "bg-secondary/35";
  }
}

function productPlaneDotClass(status: string): string {
  switch (status) {
    case "passed":
    case "ready":
    case "trusted":
    case "active":
    case "completed":
    case "delivered":
    case "rendered":
      return "bg-emerald-500";
    case "blocked":
    case "failed":
    case "unsupported":
      return "bg-red-500";
    case "pending":
    case "deferred":
    case "review":
    case "degraded":
      return "bg-amber-500";
    case "default_off":
    case "disabled":
    case "unknown":
    default:
      return "bg-secondary/35";
  }
}

function productPlaneStateLabel(productPlane: ProductPlaneViewModel): string {
  if (
    productPlane.backendState === "disabled" &&
    productPlane.warnings.some((warning) => warning.code === "product_plane_default_off")
  ) {
    return "default off";
  }
  return productPlane.backendState.replaceAll("_", " ");
}

function productPlaneSupportLabel(
  support: ProductPlaneViewModel["storage"][number]["support"],
  language?: ChatResponseLanguage,
): string {
  switch (support) {
    case "hosted_adapter":
      return t(language, "hosted adapter", "호스팅 어댑터");
    case "optional":
      return t(language, "optional", "선택 사항");
    case "supported_default":
      return t(language, "supported default", "지원 기본값");
    case "test_dev_only":
      return t(language, "test/dev only", "테스트/개발 전용");
    default:
      return support;
  }
}

const PRODUCT_PLANE_UNSAFE_VALUE_RE =
  /api[._-]?key|auth|authorization|bearer|cookie|evidence[._-]?ledger|google[._-]?adk|model[._-]?output|private|prompt|raw|secret|session|token|tool[._-]?(?:args?|logs?|results?)|transcript/i;
const PRODUCT_PLANE_PRIVATE_PATH_RE =
  /(?:^|[\s"'`(])(?:\/[A-Za-z0-9._-]+(?:\/|$)|~[\\/]|(?:\.\.)+[\\/]|[a-zA-Z]:[\\/])|(?:^|[\\/])[^\\/ ]+\.(?:db|env|key|pem|sqlite|sqlite3)(?:$|\b)/i;
const PRODUCT_PLANE_SECRET_SHAPE_RE =
  /(?:^|[^a-z0-9])(?:sk-[a-z0-9_-]{6,}|sk-proj-[a-z0-9_-]{6,}|github_pat_[a-z0-9_]{12,}|gh[pousr]_[a-z0-9_]{12,}|xox[abprs]-[a-z0-9-]{12,}|akia[0-9a-z]{16}|eyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})(?:$|[^a-z0-9])/i;

function safeProductPlaneText(value?: string): string | undefined {
  if (!value) return undefined;
  if (
    PRODUCT_PLANE_UNSAFE_VALUE_RE.test(value) ||
    PRODUCT_PLANE_PRIVATE_PATH_RE.test(value) ||
    PRODUCT_PLANE_SECRET_SHAPE_RE.test(value)
  ) {
    return undefined;
  }
  return value;
}

function productPlaneDetail(parts: Array<string | undefined>): string | undefined {
  const safeParts = parts.flatMap((part) => {
    const safe = safeProductPlaneText(part);
    return safe ? [safe] : [];
  });
  return safeParts.length > 0 ? safeParts.join(" · ") : undefined;
}

function productPlaneReasonDetail(reasonCodes: string[], stage?: string): string | undefined {
  return productPlaneDetail([
    ...reasonCodes,
    stage,
  ]);
}

function ProductPlaneRows({
  rows,
}: {
  rows: Array<{ label: string; value: string; mono?: boolean }>;
}) {
  const safeRows = rows.flatMap((row) => {
    const value = safeProductPlaneText(row.value);
    return value ? [{ ...row, value }] : [];
  });
  if (safeRows.length === 0) return null;
  return (
    <dl className="grid gap-1.5 text-xs">
      {safeRows.map((row, index) => (
        <div
          key={`${row.label}:${row.value}:${index}`}
          className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-2"
        >
          <dt className="min-w-0 truncate font-medium text-secondary/45">{row.label}</dt>
          <dd
            className={`min-w-0 truncate text-secondary/80 ${row.mono ? "font-mono text-[11px]" : ""}`}
            translate={row.mono ? "no" : undefined}
          >
            {row.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ProductPlaneStatusRow({
  id,
  status,
  detail,
}: {
  id: string;
  status: string;
  detail?: string;
}) {
  const safeId = safeProductPlaneText(id);
  const safeDetail = safeProductPlaneText(detail);
  if (!safeId) return null;
  return (
    <div className="rounded-md border border-black/[0.06] bg-white/70 px-2 py-1">
      <div className="flex min-w-0 items-center gap-2">
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${productPlaneDotClass(status)}`}
          aria-hidden="true"
        />
        <span className="min-w-0 truncate text-xs font-medium text-secondary/80" translate="no">
          {safeId}
        </span>
        <span className="shrink-0 text-[11px] text-secondary/45">
          {status.replaceAll("_", " ")}
        </span>
      </div>
      {safeDetail && (
        <div className="mt-0.5 min-w-0 truncate text-[11px] text-secondary/55" translate="no">
          {safeDetail}
        </div>
      )}
    </div>
  );
}

function ProductPlaneSubsection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="mt-2">
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-secondary/45">
        {title}
      </div>
      {children}
    </div>
  );
}

function productPlaneGateGroups(productPlane: ProductPlaneViewModel): {
  sandbox: ProductPlaneStatusItem[];
  quota: ProductPlaneStatusItem[];
  release: ProductPlaneStatusItem[];
  verification: ProductPlaneStatusItem[];
} {
  const representedGateIds = new Set<string>();
  const groups: {
    sandbox: ProductPlaneStatusItem[];
    quota: ProductPlaneStatusItem[];
    release: ProductPlaneStatusItem[];
    verification: ProductPlaneStatusItem[];
  } = {
    sandbox: [],
    quota: [],
    release: [],
    verification: [],
  };

  for (const invariant of productPlane.projection.ops.hardInvariants) {
    representedGateIds.add(invariant.invariantId);
    groups.sandbox.push({
      id: invariant.invariantId,
      status: invariant.status,
      detail: productPlaneReasonDetail(invariant.reasonCodes),
    });
  }

  for (const decision of productPlane.projection.sandbox.decisions) {
    if (!decision.guardrail) continue;
    representedGateIds.add(decision.guardrail.guardrailId);
    groups.sandbox.push({
      id: decision.guardrail.guardrailId,
      status: decision.guardrail.status,
      detail: productPlaneReasonDetail(decision.guardrail.reasonCodes),
    });
  }

  for (const decision of productPlane.projection.quotaSpend.decisions) {
    const gateId = `quota:${decision.decisionId}`;
    representedGateIds.add(gateId);
    groups.quota.push({
      id: gateId,
      status: decision.decision === "block" ? "blocked" : "passed",
      detail: productPlaneReasonDetail(decision.reasonCodes),
    });
  }

  for (const gate of productPlane.projection.releaseEvalGates) {
    representedGateIds.add(gate.gateId);
    groups.release.push({
      id: gate.gateId,
      status: gate.status,
      detail: productPlaneReasonDetail(gate.reasonCodes),
    });
  }

  for (const gate of productPlane.gates) {
    if (representedGateIds.has(gate.gateId)) continue;
    groups.verification.push({
      id: gate.gateId,
      status: gate.status,
      detail: productPlaneReasonDetail(gate.reasonCodes, gate.stage),
    });
  }

  return groups;
}

function productPlaneProjectionStores(
  productPlane: ProductPlaneViewModel,
): Map<string, ProductPlaneStoreSummary> {
  const stores = [
    productPlane.projection.storage.durableStore,
    productPlane.projection.storage.hostedSync,
    productPlane.projection.storage.artifactIndex,
    productPlane.projection.storage.artifactBlobStore,
  ].filter((store): store is ProductPlaneStoreSummary => Boolean(store));
  return new Map(stores.map((store) => [store.storeId, store]));
}

function ProductPlaneDetails({
  productPlane,
  language,
}: {
  productPlane?: ProductPlaneViewModel;
  language?: ChatResponseLanguage;
}) {
  if (!productPlane) return null;

  const state = productPlaneStateLabel(productPlane);
  const checkpointRows = productPlane.projection.lineage.checkpoints.slice(-3);
  const groups = productPlaneGateGroups(productPlane);
  const storesById = productPlaneProjectionStores(productPlane);
  const safeWarningCodes = productPlane.warnings.flatMap((warning) => {
    const code = safeProductPlaneText(warning.code);
    return code ? [code] : [];
  });
  const primaryRows = [
    {
      label: t(language, "State", "상태"),
      value: [state, productPlane.readiness].filter(Boolean).join(" · "),
    },
    {
      label: t(language, "Policy", "정책"),
      value: shortDigest(productPlane.policySnapshotDigest),
      mono: true,
    },
    {
      label: t(language, "Config", "설정"),
      value: productPlane.policyConfigId,
      mono: true,
    },
    ...productPlane.jobs.slice(0, 3).flatMap((job) => [
      {
        label: t(language, "Checkpoint", "체크포인트"),
        value: job.checkpointId,
        mono: true,
      },
      {
        label: t(language, "Job policy", "작업 정책"),
        value: shortDigest(job.policySnapshotDigest),
        mono: true,
      },
    ]),
    ...checkpointRows.flatMap((checkpoint) => [
      {
        label: t(language, "Ledger", "원장"),
        value: shortDigest(checkpoint.ledgerDigest),
        mono: true,
      },
      {
        label: t(language, "Checkpoint", "체크포인트"),
        value: checkpoint.checkpointId,
        mono: true,
      },
    ]),
    ...productPlane.artifacts.slice(0, 2).flatMap((artifact) => [
      {
        label: t(language, "Artifact render", "아티팩트 렌더"),
        value: productPlaneDetail([
          artifact.renderStatus,
          shortDigest(artifact.renderDigest),
        ]),
        mono: true,
      },
      {
        label: t(language, "Artifact delivery", "아티팩트 전달"),
        value: productPlaneDetail([
          artifact.deliveryStatus,
          shortDigest(artifact.deliveryDigest),
        ]),
        mono: true,
      },
    ]),
  ].filter((row): row is { label: string; value: string; mono?: boolean } => Boolean(row.value));

  return (
    <div
      className="mt-2 border-t border-black/[0.06] pt-2"
      aria-label={t(language, "Product plane", "제품 플레인")}
      data-run-inspector-product-plane="true"
    >
      <div className="mb-1.5 flex min-w-0 items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
        <span>{t(language, "Product plane", "제품 플레인")}</span>
        <span className="min-w-0 truncate font-medium normal-case tracking-normal text-secondary/35">
          {[state, productPlane.readiness].filter(Boolean).join(" · ")}
        </span>
      </div>

      <ProductPlaneRows rows={primaryRows} />

      {safeWarningCodes.length > 0 && (
        <div className="mt-1.5 rounded-md border border-amber-500/15 bg-amber-50/60 px-2 py-1 text-[11px] text-amber-950/75">
          <div className="min-w-0 break-words" translate="no">
            {safeWarningCodes.join(", ")}
          </div>
        </div>
      )}

      {productPlane.jobs.length > 0 && (
        <ProductPlaneSubsection title={t(language, "Jobs", "작업")}>
          <div className="space-y-1">
            {productPlane.jobs.slice(0, 4).map((job) => (
              <ProductPlaneStatusRow
                key={job.jobId}
                id={job.jobId}
                status={job.state}
                detail={productPlaneReasonDetail(job.reasonCodes)}
              />
            ))}
          </div>
        </ProductPlaneSubsection>
      )}

      {groups.sandbox.length > 0 && (
        <ProductPlaneSubsection title={t(language, "Sandbox / guardrails", "샌드박스 / 가드레일")}>
          <div className="space-y-1">
            {groups.sandbox.slice(0, 4).map((gate) => (
              <ProductPlaneStatusRow
                key={gate.id}
                id={gate.id}
                status={gate.status}
                detail={gate.detail}
              />
            ))}
          </div>
        </ProductPlaneSubsection>
      )}

      {groups.quota.length > 0 && (
        <ProductPlaneSubsection title={t(language, "Quota / spend", "쿼터 / 비용")}>
          <div className="space-y-1">
            {groups.quota.slice(0, 4).map((gate) => (
              <ProductPlaneStatusRow
                key={gate.id}
                id={gate.id}
                status={gate.status}
                detail={gate.detail}
              />
            ))}
          </div>
        </ProductPlaneSubsection>
      )}

      {groups.release.length > 0 && (
        <ProductPlaneSubsection title={t(language, "Release / eval", "릴리스 / 평가")}>
          <div className="space-y-1">
            {groups.release.slice(0, 4).map((gate) => (
              <ProductPlaneStatusRow
                key={gate.id}
                id={gate.id}
                status={gate.status}
                detail={gate.detail}
              />
            ))}
          </div>
        </ProductPlaneSubsection>
      )}

      {groups.verification.length > 0 && (
        <ProductPlaneSubsection title={t(language, "Verification gates", "검증 게이트")}>
          <div className="space-y-1">
            {groups.verification.slice(0, 4).map((gate) => (
              <ProductPlaneStatusRow
                key={gate.id}
                id={gate.id}
                status={gate.status}
                detail={gate.detail}
              />
            ))}
          </div>
        </ProductPlaneSubsection>
      )}

      {productPlane.pluginTrust.length > 0 && (
        <ProductPlaneSubsection title={t(language, "Plugin / connector trust", "플러그인 / 커넥터 신뢰")}>
          <div className="space-y-1">
            {productPlane.pluginTrust.slice(0, 4).map((trust) => (
              <ProductPlaneStatusRow
                key={`${trust.targetType}:${trust.targetId}`}
                id={trust.targetId}
                status={trust.trustLevel}
                detail={productPlaneDetail([
                  trust.targetType,
                  ...trust.reasonCodes,
                  trust.policyConfigId,
                ])}
              />
            ))}
          </div>
        </ProductPlaneSubsection>
      )}

      {productPlane.storage.length > 0 && (
        <ProductPlaneSubsection title={t(language, "Storage", "스토리지")}>
          <div className="space-y-1">
            {productPlane.storage.map((store) => {
              const projectionStore = storesById.get(store.storeId);
              const safeStoreId = safeProductPlaneText(store.storeId);
              const storageDetail = productPlaneDetail([
                store.purpose,
                productPlaneSupportLabel(store.support, language),
                store.optional ? t(language, "optional", "선택 사항") : undefined,
                ...store.reasonCodes,
              ]);
              const storageRef = productPlaneDetail([
                projectionStore?.pathLabel,
                shortDigest(projectionStore?.pathDigest),
              ]);
              const warningDetail = productPlaneDetail(store.warningCodes);
              if (!safeStoreId) return null;
              return (
                <div
                  key={store.storeId}
                  className="rounded-md border border-black/[0.06] bg-white/70 px-2 py-1"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${productPlaneDotClass(store.readiness)}`}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 truncate text-xs font-medium text-secondary/80" translate="no">
                      {safeStoreId}
                    </span>
                    <span className="shrink-0 text-[11px] text-secondary/45" translate="no">
                      {store.mode}
                    </span>
                  </div>
                  {storageDetail && (
                    <div className="mt-0.5 min-w-0 truncate text-[11px] text-secondary/55" translate="no">
                      {storageDetail}
                    </div>
                  )}
                  {storageRef && (
                    <div className="mt-0.5 min-w-0 truncate font-mono text-[11px] text-secondary/50" translate="no">
                      {storageRef}
                    </div>
                  )}
                  {warningDetail && (
                    <div className="mt-0.5 min-w-0 break-words text-[11px] text-amber-700/80" translate="no">
                      {warningDetail}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </ProductPlaneSubsection>
      )}

      {productPlane.unsupportedEvents.length > 0 && (
        <ProductPlaneSubsection title={t(language, "Unsupported events", "미지원 이벤트")}>
          <div className="space-y-1">
            {productPlane.unsupportedEvents.slice(0, 3).map((event, index) => (
              <ProductPlaneStatusRow
                key={`${event.eventType}:${index}`}
                id={event.eventType}
                status={event.reasonCode}
                detail={safeProductPlaneText(event.followUpTask)}
              />
            ))}
          </div>
        </ProductPlaneSubsection>
      )}
    </div>
  );
}

function PythonAdkDiagnosticsDetails({
  diagnostics,
  language,
}: {
  diagnostics?: PythonAdkDiagnostics;
  language?: ChatResponseLanguage;
}) {
  if (!diagnostics) return null;

  const authoritySource = safeProductPlaneText(diagnostics.authoritySource);
  const receiptDigest = diagnostics.receiptDigest
    ? shortDigest(diagnostics.receiptDigest)
    : undefined;
  const blockerReasonCodes = (diagnostics.blockerReasonCodes ?? []).flatMap((reasonCode) => {
    const safe = safeProductPlaneText(reasonCode);
    return safe ? [safe] : [];
  });
  const fallbackReasonCode = safeProductPlaneText(diagnostics.fallbackReasonCode);
  const rows = [
    {
      label: t(language, "Authority", "권한"),
      value: authoritySource,
    },
    {
      label: t(language, "Runtime", "런타임"),
      value: diagnostics.runtimeStatus.replaceAll("_", " "),
    },
    {
      label: t(language, "Shadow", "섀도"),
      value: diagnostics.shadowStatus,
    },
    {
      label: t(language, "Canary", "카나리"),
      value: diagnostics.canaryStatus,
    },
    {
      label: t(language, "Receipt", "수신증"),
      value: receiptDigest,
    },
  ].filter((row): row is { label: string; value: string } => Boolean(row.value));

  return (
    <div
      className="mt-2 border-t border-black/[0.06] pt-2"
      aria-label="Python ADK diagnostics"
      data-run-inspector-python-adk="true"
    >
      <div className="mb-1.5 flex min-w-0 items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
        <span>Python ADK</span>
        <span className="min-w-0 truncate font-medium normal-case tracking-normal text-secondary/35">
          {[diagnostics.runtimeStatus, diagnostics.shadowStatus, diagnostics.canaryStatus]
            .map((value) => value.replaceAll("_", " "))
            .join(" · ")}
        </span>
      </div>
      <dl className="grid gap-1.5 text-xs">
        {rows.map((row) => (
          <div key={row.label} className="grid grid-cols-[5rem_minmax(0,1fr)] gap-2">
            <dt className="shrink-0 font-medium text-secondary/45">{row.label}</dt>
            <dd className="min-w-0 truncate font-mono text-[11px] text-secondary/80" translate="no">
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
      {(blockerReasonCodes.length > 0 || fallbackReasonCode) && (
        <div className="mt-1.5 rounded-md border border-amber-500/15 bg-amber-50/60 px-2 py-1 text-[11px] text-amber-950/75">
          <div className="min-w-0 break-words" translate="no">
            {[...blockerReasonCodes, fallbackReasonCode].filter(Boolean).join(", ")}
          </div>
        </div>
      )}
    </div>
  );
}

function DeterminismDetails({
  determinism,
  language,
}: {
  determinism?: DeterministicRuntimeState;
  language?: ChatResponseLanguage;
}) {
  if (!determinism) return null;
  const rows = [
    { label: t(language, "Workflow", "워크플로우"), value: determinism.workflowId },
    { label: t(language, "Version", "버전"), value: determinism.workflowVersion },
    { label: t(language, "Route", "라우트"), value: determinism.routeId },
    {
      label: t(language, "Policy", "정책"),
      value: shortDigest(determinism.effectivePolicySnapshotDigest),
    },
    {
      label: t(language, "Ledger", "원장"),
      value: shortDigest(determinism.ledgerHeadDigest),
    },
    { label: t(language, "Checkpoint", "체크포인트"), value: determinism.checkpointId },
    { label: t(language, "Projection", "프로젝션"), value: determinism.projectionMode },
    {
      label: t(language, "Fallback", "폴백"),
      value: determinism.fallbackReasonCode,
    },
  ].filter((row): row is { label: string; value: string } => Boolean(row.value));
  const recentGuardrails = (determinism.guardrails ?? []).slice(-3);
  const appliedRecipes = determinism.appliedRecipes ?? [];
  const recipeSelection = determinism.recipeSelection;
  const verificationGates = (determinism.verificationGates ?? []).slice(-4);
  const blockedReasonCodes = determinism.blockedReasonCodes?.join(", ");

  return (
    <div
      className="mt-2 border-t border-black/[0.06] pt-2"
      aria-label={t(language, "Determinism", "결정론 제어")}
      data-run-inspector-determinism="true"
    >
      <div className="mb-1.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
        <span>{t(language, "Determinism", "결정론 제어")}</span>
        {typeof determinism.governed === "boolean" && (
          <span className="font-medium normal-case tracking-normal text-secondary/35">
            {determinism.governed
              ? t(language, "governed", "거버넌스 적용")
              : t(language, "ungoverned", "거버넌스 미적용")}
          </span>
        )}
      </div>

      {rows.length > 0 && (
        <dl className="grid gap-1.5 text-xs">
          {rows.map((row) => (
            <div key={row.label} className="grid grid-cols-[5rem_minmax(0,1fr)] gap-2">
              <dt className="shrink-0 font-medium text-secondary/45">{row.label}</dt>
              <dd className="min-w-0 truncate font-mono text-[11px] text-secondary/80" translate="no">
                {row.value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {determinism.outputAllowed === false && blockedReasonCodes && (
        <div className="mt-1.5 rounded-md border border-amber-500/15 bg-amber-50/60 px-2 py-1 text-[11px] text-amber-950/75">
          {blockedReasonCodes}
        </div>
      )}

      {recipeSelection && (
        <div className="mt-2">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-secondary/45">
            {t(language, "Recipe request", "레시피 요청")}
          </div>
          <div className="rounded-md border border-black/[0.06] bg-white/70 px-2 py-1">
            <div className="flex min-w-0 items-center justify-between gap-2">
              <span className="min-w-0 truncate text-xs font-medium text-secondary/80" translate="no">
                {recipeSelection.status.replaceAll("_", " ")}
              </span>
              {recipeSelection.selectionSource && (
                <span className="shrink-0 text-[11px] text-secondary/45" translate="no">
                  {recipeSelection.selectionSource}
                </span>
              )}
            </div>
            <div className="mt-0.5 truncate text-[11px] text-secondary/55" translate="no">
              {[
                ...recipeSelection.requestedRecipeRefs.map((ref) => ref.recipeId),
                ...recipeSelection.omittedRecipeRefs.map((ref) => ref.recipeId),
                ...recipeSelection.omissionReasons,
                recipeSelection.turnBlocked ? "turn blocked" : undefined,
                recipeSelection.fallbackUsed ? "fell back" : undefined,
                recipeSelection.nextAction,
                shortDigest(recipeSelection.policySnapshotDigest),
              ].filter(Boolean).join(" · ")}
            </div>
          </div>
        </div>
      )}

      {appliedRecipes.length > 0 && (
        <div className="mt-2">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-secondary/45">
            {t(language, "Applied recipes", "적용된 레시피")}
          </div>
          <div className="space-y-1">
            {appliedRecipes.map((recipe) => (
              <div
                key={recipe.recipeId}
                className="rounded-md border border-black/[0.06] bg-white/70 px-2 py-1"
              >
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="min-w-0 truncate text-xs font-medium text-secondary/80" translate="no">
                    {recipe.recipeId}
                  </span>
                  <span className="shrink-0 text-[11px] text-secondary/45">
                    {recipe.role}
                  </span>
                </div>
                <div className="mt-0.5 truncate text-[11px] text-secondary/55" translate="no">
                  {[recipe.version, recipe.governed ? "governed" : "ungoverned", shortDigest(recipe.sourceDigest)]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {verificationGates.length > 0 && (
        <div className="mt-2">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-secondary/45">
            {t(language, "Verification gates", "검증 게이트")}
          </div>
          <div className="space-y-1">
            {verificationGates.map((gate) => (
              <div
                key={gate.gateId}
                className="rounded-md border border-black/[0.06] bg-white/70 px-2 py-1"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${guardrailDotClass(gate.status === "pending" ? "repair" : gate.status)}`}
                    aria-hidden="true"
                  />
                  <span className="min-w-0 truncate text-xs font-medium text-secondary/80" translate="no">
                    {gate.gateId}
                  </span>
                </div>
                <div className="mt-0.5 truncate text-[11px] text-secondary/55">
                  {[gate.status, gate.reasonCodes.join(", ") || gate.stage, gate.validatorTrustClass]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {recentGuardrails.length > 0 && (
        <div className="mt-1.5 space-y-1">
          {recentGuardrails.map((guardrail) => (
            <div
              key={`${guardrail.guardrailId}:${guardrail.policyDecisionId}`}
              className="rounded-md border border-black/[0.06] bg-white/70 px-2 py-1"
            >
              <div className="flex min-w-0 items-center gap-2">
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${guardrailDotClass(guardrail.status)}`}
                  aria-hidden="true"
                />
                <span className="min-w-0 truncate text-xs font-medium text-secondary/80">
                  {guardrail.guardrailId}
                </span>
              </div>
              <div className="mt-0.5 truncate text-[11px] text-secondary/55">
                {[guardrail.status, guardrail.reasonCodes.join(", ") || guardrail.stage]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function RunInspectorDock({
  channelState,
  queuedMessages = [],
  controlRequests = [],
  cancelHint,
  defaultHidden = false,
  compactDetails = false,
  uiLanguage,
}: RunInspectorDockProps) {
  const language = uiLanguage ?? channelState.responseLanguage;
  const taskBoard = openTaskBoard(channelState.taskBoard);
  const subagents = channelState.subagents ?? [];
  const productPlane = productPlaneFromChannelState(channelState);
  const pythonAdkDiagnostics = pythonAdkDiagnosticsFromChannelState(channelState);
  const pendingRequests = pendingControlRequests(controlRequests);
  const visibleRunState = hasVisibleRunState(
    channelState,
    queuedMessages,
    pendingRequests,
    taskBoard,
    subagents,
  );
  const identity = runIdentity(channelState, queuedMessages, pendingRequests, taskBoard, subagents);
  const [hiddenRunIdentity, setHiddenRunIdentity] = useState<string | null>(() =>
    defaultHidden ? identity : null,
  );

  useEffect(() => {
    if (!visibleRunState || (hiddenRunIdentity !== null && hiddenRunIdentity !== identity)) {
      setHiddenRunIdentity(null);
    }
  }, [hiddenRunIdentity, identity, visibleRunState]);

  if (!visibleRunState) {
    return null;
  }

  if (hiddenRunIdentity === identity) {
    return (
      <div className="px-4 md:px-8 lg:px-12 pb-2" aria-label={t(language, "Current run hidden", "현재 실행 숨김")}>
        <div className="mx-auto flex max-w-3xl justify-end">
          <button
            type="button"
            onClick={() => setHiddenRunIdentity(null)}
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-black/[0.08] bg-white/85 px-2.5 text-[11px] font-medium text-secondary/65 shadow-sm transition-colors hover:bg-white hover:text-secondary"
          >
            {t(language, "Show current run", "현재 실행 보기")}
          </button>
        </div>
      </div>
    );
  }

  const nextQueued = queuedMessages[0];
  const activeTools = channelState.activeTools ?? [];
  const inspectedSources = channelState.inspectedSources ?? [];
  const activeToolCount = activeTools.filter((activity) => activity.status === "running").length;
  const activeSubagentCount = subagents.filter(
    (subagent) => subagent.status === "running" || subagent.status === "waiting",
  ).length;
  const phase = phaseLabel(channelState.turnPhase ?? null, language);
  const workState = deriveWorkStateSummary({
    channelState,
    queuedMessages,
    controlRequests,
    uiLanguage: language,
  });

  return (
    <div className="px-4 md:px-8 lg:px-12 pb-2" aria-label={t(language, "Current run", "현재 실행")}>
      <div className="mx-auto max-w-3xl rounded-lg border border-black/[0.08] bg-white/90 px-3 py-2.5 shadow-sm backdrop-blur">
        <div className="flex min-w-0 items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
              {t(language, "Current run", "현재 실행")}
            </div>
            <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-secondary/70">
              {channelState.reconnecting && (
                <span className="font-medium text-amber-600">
                  {t(language, "Reconnecting…", "다시 연결 중…")}
                </span>
              )}
              {phase && !channelState.reconnecting && <span>{phase}</span>}
              {activeToolCount > 0 && (
                <span>
                  {isKorean(language)
                    ? `${activeToolCount}개 실행 중`
                    : `${activeToolCount} active action${activeToolCount === 1 ? "" : "s"}`}
                </span>
              )}
              {activeSubagentCount > 0 && (
                <span>
                  {isKorean(language)
                    ? `${activeSubagentCount}명 백그라운드 작업 중`
                    : `${activeSubagentCount} background agent${activeSubagentCount === 1 ? "" : "s"}`}
                </span>
              )}
              {queuedMessages.length > 0 && (
                <span>
                  {isKorean(language)
                    ? `${queuedMessages.length}개 대기`
                    : `${queuedMessages.length} queued`}
                </span>
              )}
              {pendingRequests.length > 0 && (
                <span className="font-medium text-primary">
                  {t(language, "Needs approval", "승인 필요")}
                </span>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {cancelHint && (
              <div className="rounded-md bg-black/[0.04] px-2 py-1 text-[11px] font-medium text-secondary/70">
                {cancelHint}
              </div>
            )}
            <button
              type="button"
              aria-label={t(language, "Hide current run", "현재 실행 숨기기")}
              title={t(language, "Hide current run", "현재 실행 숨기기")}
              onClick={() => setHiddenRunIdentity(identity)}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-secondary/45 transition-colors hover:bg-black/[0.04] hover:text-secondary/80"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        <WorkStateSummaryRows summary={workState} language={language} />

        {compactDetails ? null : (
        <div className="mt-2 max-h-[min(50vh,34rem)] overflow-y-auto overscroll-contain pr-1 [scrollbar-gutter:stable]">
          {channelState.browserFrame && (
            <BrowserFramePreview
              frame={channelState.browserFrame}
              language={language}
              surface="run-inspector"
              className="mt-2 overflow-hidden rounded-lg border border-black/[0.08] bg-white"
              imageClassName="block aspect-video w-full max-h-64 bg-black/[0.03] object-contain"
            />
          )}
          {channelState.documentDraft && (
            <DocumentDraftPreviewCard
              draft={channelState.documentDraft}
              language={language}
              surface="run-inspector"
            />
          )}

          <ResearchEvidence
            sources={inspectedSources}
            citationGate={channelState.citationGate}
            language={language}
          />

          <DeterminismDetails
            determinism={channelState.determinism}
            language={language}
          />

          <ProductPlaneDetails
            productPlane={productPlane}
            language={language}
          />

          <PythonAdkDiagnosticsDetails
            diagnostics={pythonAdkDiagnostics}
            language={language}
          />

          {(channelState.streaming || activeTools.length > 0) && (
            <div>
              <AgentActivityTimeline
                live={channelState.streaming}
                startedAt={channelState.thinkingStartedAt ?? null}
                fileProcessing={channelState.fileProcessing}
                turnPhase={channelState.turnPhase ?? null}
                heartbeatElapsedMs={channelState.heartbeatElapsedMs ?? null}
                pendingInjectionCount={channelState.pendingInjectionCount ?? 0}
                activities={activeTools}
                taskBoard={taskBoard}
                responseLanguage={language}
              />
            </div>
          )}

          <BackgroundSubagents subagents={subagents} language={language} />

          {pendingRequests.length > 0 && (
            <div className="mt-2 space-y-1 border-t border-black/[0.06] pt-2">
              {pendingRequests.slice(0, 3).map((request) => (
                <div key={request.requestId} className="flex min-w-0 items-center gap-2 text-xs">
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                  <span className="shrink-0 font-medium text-secondary/80">
                    {request.kind.replace("_", " ")}
                  </span>
                  <span className="min-w-0 truncate text-secondary/70">{request.prompt}</span>
                </div>
              ))}
            </div>
          )}

          {queuedMessages.length > 0 && (
            <div
              className="mt-2 rounded-xl border border-amber-500/25 bg-amber-50 px-3 py-2 text-xs text-amber-950 shadow-[0_1px_8px_rgba(245,158,11,0.10)]"
              data-run-inspector-queue-card="true"
            >
              <div className="mb-1 flex items-center justify-between gap-2 text-[10px] font-semibold uppercase tracking-wide text-amber-800/70">
                <span>{t(language, "Queued after current run", "현재 실행 후 전송 대기")}</span>
                <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[9px] text-amber-800">
                  {isKorean(language)
                    ? `${queuedMessages.length}개 대기`
                    : `${queuedMessages.length} waiting`}
                </span>
              </div>
              {nextQueued && (
                <div className="break-words text-amber-950/75">{nextQueued.content}</div>
              )}
            </div>
          )}

          {taskBoard && (
            <div className="mt-2 border-t border-black/[0.06] pt-2">
              <TaskBoard snapshot={taskBoard} />
            </div>
          )}
        </div>
        )}
      </div>
    </div>
  );
}
