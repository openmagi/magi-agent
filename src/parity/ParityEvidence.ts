import type {
  ControlEvent,
  ControlRequestKind,
} from "../control/ControlEvents.js";
import type {
  BuildInfo,
  RuntimeFeatureStatus,
} from "../transport/routes/health.js";

interface ImageIdentityLike {
  buildSha: string | null;
  imageRepo: string | null;
  imageTag: string | null;
  imageDigest: string | null;
}

export interface RuntimeParityInput {
  ok: boolean;
  degradedReasons: readonly string[];
  buildInfo: BuildInfo;
  features: RuntimeFeatureStatus;
  skills: {
    loaded: number;
    issues: unknown[];
    runtimeHooks: number;
    executableRuntimeHooks: number;
  } | null;
}

export interface ParityCheck {
  id: string;
  label: string;
  passed: boolean;
  detail?: string;
}

export interface RuntimeParityReport {
  ready: boolean;
  missing: string[];
  capabilities: ParityCheck[];
}

export interface ControlEventParityReport {
  ready: boolean;
  missing: string[];
  eventCount: number;
  lastSeq: number;
  eventTypes: Record<string, number>;
  checks: ParityCheck[];
}

export interface ParityEvidenceReport {
  ready: boolean;
  runtime: RuntimeParityReport;
  control: ControlEventParityReport;
}

export function evaluateRuntimeParity(input: RuntimeParityInput): RuntimeParityReport {
  const build = input.buildInfo;
  const completeBuiltIdentity = hasCompleteImageIdentity(build.builtImage);
  const completeExpectedIdentity = hasCompleteImageIdentity(build.expectedImage);
  const matchingImageIdentity =
    completeBuiltIdentity &&
    completeExpectedIdentity &&
    sameImageIdentity(build.builtImage, build.expectedImage);
  const skillsReady =
    input.skills !== null &&
    input.skills.issues.length === 0 &&
    input.skills.runtimeHooks > 0 &&
    input.skills.executableRuntimeHooks > 0;
  const capabilities: ParityCheck[] = [
    {
      id: "health_ok",
      label: "runtime health is not degraded",
      passed: input.ok && input.degradedReasons.length === 0,
      detail: input.degradedReasons.join(", "),
    },
    {
      id: "deploy_identity",
      label: "baked and expected image identity are complete and matching",
      passed: matchingImageIdentity,
      detail: `built=${identitySummary(build.builtImage)} expected=${identitySummary(build.expectedImage)}`,
    },
    {
      id: "control_ledger",
      label: "control ledger feature is enabled",
      passed: input.features.controlLedger === true,
    },
    {
      id: "control_request",
      label: "durable control request feature is enabled",
      passed: input.features.controlRequest === true,
    },
    {
      id: "permission_arbiter",
      label: "permission arbiter feature is enabled",
      passed: input.features.permissionArbiter === true,
    },
    {
      id: "plan_approval",
      label: "plan approval feature is enabled",
      passed: input.features.planApproval === true,
    },
    {
      id: "structured_output",
      label: "structured output feature is enabled",
      passed: input.features.structuredOutput === true,
    },
    {
      id: "child_harness",
      label: "child-agent harness feature is enabled",
      passed: input.features.childHarness === true,
    },
    {
      id: "transport_reliability",
      label: "transport reliability feature is enabled",
      passed: input.features.transportReliability === true,
    },
    {
      id: "trusted_skill_hooks",
      label: "trusted executable skill runtime hooks are loaded without issues",
      passed: skillsReady,
      detail:
        input.skills === null
          ? "skill report missing"
          : `issues=${input.skills.issues.length} runtimeHooks=${input.skills.runtimeHooks} executable=${input.skills.executableRuntimeHooks}`,
    },
  ];
  return checksToReport(capabilities);
}

export function evaluateControlEventParity(
  events: readonly ControlEvent[],
): ControlEventParityReport {
  const counts: Record<string, number> = {};
  for (const event of events) counts[event.type] = (counts[event.type] ?? 0) + 1;

  const toolPermission = requestLifecycle(events, "tool_permission");
  const planApproval = requestLifecycle(events, "plan_approval");
  const hasPlanExecutionState = hasPlanLifecycleForRequest(
    events,
    planApproval.requestIds,
  );
  const hasVerificationGate = events.some((event) => event.type === "verification");
  const hasRetryLoop = events.some((event) => event.type === "retry");
  const hasStructuredRetry = events.some(
    (event) =>
      event.type === "structured_output" &&
      (event.status === "invalid" || event.status === "retry_exhausted"),
  );
  const hasChildLifecycle = hasCoherentChildLifecycle(events);
  const hasStopReason = events.some((event) => event.type === "stop_reason");
  const seqStrict = events.every((event, index) => {
    if (index === 0) return event.seq > 0;
    const previous = events[index - 1];
    return previous !== undefined && event.seq > previous.seq;
  });

  const checks: ParityCheck[] = [
    {
      id: "control_event_replay",
      label: "control event replay has ordered sequence numbers",
      passed: events.length > 0 && seqStrict,
      detail: `events=${events.length}`,
    },
    {
      id: "tool_permission_request",
      label: "tool permission request was created and resolved",
      passed: toolPermission.created && toolPermission.resolved,
      detail: lifecycleDetail(toolPermission),
    },
    {
      id: "tool_permission_updated_input",
      label: "approved tool permission can carry user-edited input",
      passed: toolPermission.updatedInput,
      detail: lifecycleDetail(toolPermission),
    },
    {
      id: "plan_approval_request",
      label: "plan approval request was created, resolved, and projected",
      passed: planApproval.created && planApproval.resolved && hasPlanExecutionState,
      detail: lifecycleDetail(planApproval),
    },
    {
      id: "verification_gate",
      label: "verification gate emitted durable evidence",
      passed: hasVerificationGate,
    },
    {
      id: "retry_loop",
      label: "runtime retry loop emitted durable evidence",
      passed: hasRetryLoop || hasStructuredRetry,
    },
    {
      id: "structured_output_retry",
      label: "structured-output retry or exhaustion emitted durable evidence",
      passed: hasStructuredRetry,
    },
    {
      id: "child_lifecycle",
      label: "child-agent lifecycle emitted start, permission, and terminal events",
      passed: hasChildLifecycle,
    },
    {
      id: "stop_reason",
      label: "turn stop reason was recorded durably",
      passed: hasStopReason,
    },
  ];

  const base = checksToReport(checks);
  return {
    ...base,
    eventCount: events.length,
    lastSeq: events.reduce((max, event) => Math.max(max, event.seq), 0),
    eventTypes: counts,
    checks,
  };
}

export function buildParityEvidenceReport(input: {
  runtime: RuntimeParityInput;
  controlEvents: readonly ControlEvent[];
}): ParityEvidenceReport {
  const runtime = evaluateRuntimeParity(input.runtime);
  const control = evaluateControlEventParity(input.controlEvents);
  return {
    ready: runtime.ready && control.ready,
    runtime,
    control,
  };
}

function hasCompleteImageIdentity(identity: ImageIdentityLike): boolean {
  return (
    !!identity.buildSha &&
    !!identity.imageRepo &&
    !!identity.imageTag &&
    !!identity.imageDigest
  );
}

function sameImageIdentity(a: ImageIdentityLike, b: ImageIdentityLike): boolean {
  return (
    a.buildSha === b.buildSha &&
    a.imageRepo === b.imageRepo &&
    a.imageTag === b.imageTag &&
    a.imageDigest === b.imageDigest
  );
}

function identitySummary(identity: ImageIdentityLike): string {
  return `${identity.imageRepo ?? "missing"}:${identity.imageTag ?? "missing"}@${identity.imageDigest ?? "missing"}#${identity.buildSha ?? "missing"}`;
}

function checksToReport<T extends ParityCheck>(
  checks: readonly T[],
): { ready: boolean; missing: string[]; capabilities: T[] };
function checksToReport<T extends ParityCheck>(
  checks: readonly T[],
): { ready: boolean; missing: string[]; checks: T[] };
function checksToReport(checks: readonly ParityCheck[]) {
  const missing = checks.filter((check) => !check.passed).map((check) => check.id);
  return {
    ready: missing.length === 0,
    missing,
    capabilities: [...checks],
    checks: [...checks],
  };
}

function requestLifecycle(
  events: readonly ControlEvent[],
  kind: ControlRequestKind,
): { created: boolean; resolved: boolean; updatedInput: boolean; requestIds: string[] } {
  const requestIds: string[] = [];
  for (const event of events) {
    if (event.type === "control_request_created" && event.request.kind === kind) {
      requestIds.push(event.request.requestId);
    }
  }
  const requestIdSet = new Set(requestIds);
  const resolved = events.some(
    (event) =>
      event.type === "control_request_resolved" &&
      requestIdSet.has(event.requestId),
  );
  const updatedInput = events.some(
    (event) =>
      event.type === "control_request_resolved" &&
      requestIdSet.has(event.requestId) &&
      event.updatedInput !== undefined,
  );
  return {
    created: requestIds.length > 0,
    resolved,
    updatedInput,
    requestIds,
  };
}

function hasPlanLifecycleForRequest(
  events: readonly ControlEvent[],
  requestIds: readonly string[],
): boolean {
  const requestIdSet = new Set(requestIds);
  return events.some(
    (event) =>
      event.type === "plan_lifecycle" &&
      event.requestId !== undefined &&
      requestIdSet.has(event.requestId) &&
      (event.state === "approved" ||
        event.state === "verification_pending" ||
        event.state === "verified"),
  );
}

function hasCoherentChildLifecycle(events: readonly ControlEvent[]): boolean {
  const lifecycle = new Map<
    string,
    { started: boolean; decision: boolean; terminal: boolean }
  >();
  for (const event of events) {
    if (
      event.type !== "child_started" &&
      event.type !== "child_permission_decision" &&
      event.type !== "child_completed" &&
      event.type !== "child_failed" &&
      event.type !== "child_cancelled"
    ) {
      continue;
    }

    const state =
      lifecycle.get(event.taskId) ??
      { started: false, decision: false, terminal: false };
    if (event.type === "child_started") state.started = true;
    if (event.type === "child_permission_decision") state.decision = true;
    if (
      event.type === "child_completed" ||
      event.type === "child_failed" ||
      event.type === "child_cancelled"
    ) {
      state.terminal = true;
    }
    lifecycle.set(event.taskId, state);
  }

  return [...lifecycle.values()].some(
    (state) => state.started && state.decision && state.terminal,
  );
}

function lifecycleDetail(input: {
  created: boolean;
  resolved: boolean;
  updatedInput: boolean;
  requestIds: string[];
}): string {
  return `created=${input.created} resolved=${input.resolved} updatedInput=${input.updatedInput} requestIds=${input.requestIds.join(",")}`;
}
