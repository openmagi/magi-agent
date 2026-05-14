export interface ControlEventBase {
  v: 1;
  eventId: string;
  seq: number;
  ts: number;
  sessionKey: string;
  turnId?: string;
  idempotencyKey?: string;
}

export type ControlRequestKind =
  | "tool_permission"
  | "plan_approval"
  | "user_question";

export type ControlRequestSource =
  | "turn"
  | "mcp"
  | "child-agent"
  | "plan"
  | "system";

export type ControlRequestDecision =
  | "approved"
  | "denied"
  | "answered";

export interface ControlRequestRecord {
  requestId: string;
  kind: ControlRequestKind;
  state: "pending" | "approved" | "denied" | "answered" | "cancelled" | "timed_out";
  sessionKey: string;
  turnId?: string;
  channelName?: string;
  source: ControlRequestSource;
  prompt: string;
  proposedInput?: unknown;
  createdAt: number;
  expiresAt: number;
  resolvedAt?: number;
  decision?: ControlRequestDecision;
  feedback?: string;
  updatedInput?: unknown;
  answer?: string;
  cancelReason?: string;
}

export interface PlanProjection {
  planId: string;
  state: string;
  turnId?: string;
  requestId?: string;
  plan?: string;
  feedback?: string;
}

export interface ChildAgentProjection {
  taskId: string;
  state: "running" | "cancelled" | "failed" | "completed";
  parentTurnId?: string;
  lastEventSeq: number;
  summary?: unknown;
  errorMessage?: string;
}

export type RetryControlEvent = ControlEventBase & {
  type: "retry";
  turnId: string;
  reason: string;
  attempt: number;
  maxAttempts: number;
  visibleToUser: boolean;
};

export type PermissionDecisionControlEvent = ControlEventBase & {
  type: "permission_decision";
  turnId?: string;
  requestId?: string;
  source: ControlRequestSource;
  toolName?: string;
  decision: "allow" | "deny" | "ask";
  reason?: string;
  updatedInput?: unknown;
};

export type ControlRequestCreatedEvent = ControlEventBase & {
  type: "control_request_created";
  request: ControlRequestRecord;
};

export type ControlRequestResolvedEvent = ControlEventBase & {
  type: "control_request_resolved";
  requestId: string;
  decision: ControlRequestDecision;
  feedback?: string;
  updatedInput?: unknown;
  answer?: string;
};

export type ControlRequestCancelledEvent = ControlEventBase & {
  type: "control_request_cancelled";
  requestId: string;
  reason: string;
};

export type ControlRequestTimedOutEvent = ControlEventBase & {
  type: "control_request_timed_out";
  requestId: string;
};

export type PlanLifecycleControlEvent = ControlEventBase & {
  type: "plan_lifecycle";
  planId: string;
  state:
    | "entered"
    | "ready"
    | "awaiting_approval"
    | "approved"
    | "rejected"
    | "verification_pending"
    | "verified"
    | "cancelled";
  requestId?: string;
  plan?: string;
  feedback?: string;
};

export type ToolUseSummaryControlEvent = ControlEventBase & {
  type: "tool_use_summary";
  turnId: string;
  toolName: string;
  toolUseId?: string;
  status: "ok" | "error" | "denied" | "timeout";
  inputPreview?: string;
  outputPreview?: string;
};

export type StructuredOutputControlEvent = ControlEventBase & {
  type: "structured_output";
  turnId: string;
  status: "valid" | "invalid" | "retry_exhausted";
  schemaName?: string;
  reason?: string;
};

export type VerificationControlEvent = ControlEventBase & {
  type: "verification";
  turnId?: string;
  status: "pending" | "passed" | "failed" | "missing";
  evidence?: unknown;
  reason?: string;
};

export type StopReasonControlEvent = ControlEventBase & {
  type: "stop_reason";
  turnId: string;
  reason: string;
};

export type TaskBoardSnapshotControlEvent = ControlEventBase & {
  type: "task_board_snapshot";
  turnId?: string;
  taskBoard: unknown;
};

export type ChildStartedControlEvent = ControlEventBase & {
  type: "child_started";
  taskId: string;
  parentTurnId?: string;
  prompt?: string;
  detail?: string;
};

export type ChildProgressControlEvent = ControlEventBase & {
  type: "child_progress";
  taskId: string;
  detail: string;
};

export type ChildToolRequestControlEvent = ControlEventBase & {
  type: "child_tool_request";
  taskId: string;
  requestId: string;
  toolName: string;
};

export type ChildPermissionDecisionControlEvent = ControlEventBase & {
  type: "child_permission_decision";
  taskId: string;
  decision: "allow" | "deny" | "ask";
  reason?: string;
};

export type ChildCancelledControlEvent = ControlEventBase & {
  type: "child_cancelled";
  taskId: string;
  reason: string;
};

export type ChildFailedControlEvent = ControlEventBase & {
  type: "child_failed";
  taskId: string;
  errorMessage: string;
};

export type ChildCompletedControlEvent = ControlEventBase & {
  type: "child_completed";
  taskId: string;
  summary?: unknown;
};

export type CompactionBoundaryControlEvent = ControlEventBase & {
  type: "compaction_boundary";
  turnId?: string;
  boundaryId: string;
  beforeTokenCount?: number;
  afterTokenCount?: number;
  summaryHash?: string;
};

export type RuntimeTraceControlEvent = ControlEventBase & {
  type: "runtime_trace";
  turnId: string;
  phase:
    | "verifier_blocked"
    | "retry_scheduled"
    | "retry_aborted"
    | "terminal_abort";
  severity: "info" | "warning" | "error";
  title: string;
  detail?: string;
  reasonCode?: string;
  ruleId?: string;
  attempt?: number;
  maxAttempts?: number;
  retryable?: boolean;
  requiredAction?: string;
};

export type ControlEvent =
  | RetryControlEvent
  | PermissionDecisionControlEvent
  | ControlRequestCreatedEvent
  | ControlRequestResolvedEvent
  | ControlRequestCancelledEvent
  | ControlRequestTimedOutEvent
  | PlanLifecycleControlEvent
  | ToolUseSummaryControlEvent
  | StructuredOutputControlEvent
  | VerificationControlEvent
  | StopReasonControlEvent
  | TaskBoardSnapshotControlEvent
  | ChildStartedControlEvent
  | ChildProgressControlEvent
  | ChildToolRequestControlEvent
  | ChildPermissionDecisionControlEvent
  | ChildCancelledControlEvent
  | ChildFailedControlEvent
  | ChildCompletedControlEvent
  | CompactionBoundaryControlEvent
  | RuntimeTraceControlEvent;

type DistributiveOmit<T, K extends keyof any> = T extends unknown
  ? Omit<T, K>
  : never;

export type ControlEventInput = DistributiveOmit<
  ControlEvent,
  "v" | "eventId" | "seq" | "ts" | "sessionKey"
> &
  Partial<Pick<ControlEventBase, "ts" | "sessionKey" | "idempotencyKey">>;

export const CONTROL_EVENT_TYPES = new Set<ControlEvent["type"]>([
  "retry",
  "permission_decision",
  "control_request_created",
  "control_request_resolved",
  "control_request_cancelled",
  "control_request_timed_out",
  "plan_lifecycle",
  "tool_use_summary",
  "structured_output",
  "verification",
  "stop_reason",
  "task_board_snapshot",
  "child_started",
  "child_progress",
  "child_tool_request",
  "child_permission_decision",
  "child_cancelled",
  "child_failed",
  "child_completed",
  "compaction_boundary",
  "runtime_trace",
]);
