import type {
  ChildAgentProjection,
  ControlEvent,
  ControlRequestRecord,
  PlanProjection,
} from "./ControlEvents.js";

export interface ControlProjection {
  lastSeq: number;
  pendingRequests: ControlRequestRecord[];
  requests: Record<string, ControlRequestRecord>;
  activePlan: PlanProjection | null;
  taskBoard: unknown | null;
  verification: unknown | null;
  retryCounts: Record<string, number>;
  lastStopReasonByTurn: Record<string, string>;
  childAgents: Record<string, ChildAgentProjection>;
}

export function emptyControlProjection(): ControlProjection {
  return {
    lastSeq: 0,
    pendingRequests: [],
    requests: {},
    activePlan: null,
    taskBoard: null,
    verification: null,
    retryCounts: {},
    lastStopReasonByTurn: {},
    childAgents: {},
  };
}

export function projectControlEvents(
  events: ControlEvent[],
  opts: { now?: number } = {},
): ControlProjection {
  const projection = emptyControlProjection();
  for (const event of events) {
    projection.lastSeq = Math.max(projection.lastSeq, event.seq);
    switch (event.type) {
      case "retry": {
        projection.retryCounts[event.turnId] =
          (projection.retryCounts[event.turnId] ?? 0) + 1;
        break;
      }
      case "control_request_created": {
        projection.requests[event.request.requestId] = { ...event.request };
        break;
      }
      case "control_request_resolved": {
        const existing = projection.requests[event.requestId];
        if (!existing || existing.state !== "pending") break;
        projection.requests[event.requestId] = {
          ...existing,
          state:
            event.decision === "approved"
              ? "approved"
              : event.decision === "denied"
                ? "denied"
                : "answered",
          resolvedAt: event.ts,
          decision: event.decision,
          feedback: event.feedback,
          updatedInput: event.updatedInput,
          answer: event.answer,
        };
        break;
      }
      case "control_request_cancelled": {
        const existing = projection.requests[event.requestId];
        if (!existing || existing.state !== "pending") break;
        projection.requests[event.requestId] = {
          ...existing,
          state: "cancelled",
          resolvedAt: event.ts,
          cancelReason: event.reason,
        };
        break;
      }
      case "control_request_timed_out": {
        const existing = projection.requests[event.requestId];
        if (!existing || existing.state !== "pending") break;
        projection.requests[event.requestId] = {
          ...existing,
          state: "timed_out",
          resolvedAt: event.ts,
        };
        break;
      }
      case "plan_lifecycle": {
        projection.activePlan = {
          planId: event.planId,
          state: event.state,
          turnId: event.turnId,
          requestId: event.requestId,
          plan: event.plan,
          feedback: event.feedback,
        };
        break;
      }
      case "task_board_snapshot": {
        projection.taskBoard = event.taskBoard;
        break;
      }
      case "verification": {
        projection.verification = event;
        break;
      }
      case "stop_reason": {
        projection.lastStopReasonByTurn[event.turnId] = event.reason;
        break;
      }
      case "child_started": {
        projection.childAgents[event.taskId] = {
          taskId: event.taskId,
          state: "running",
          parentTurnId: event.parentTurnId,
          lastEventSeq: event.seq,
        };
        break;
      }
      case "child_progress":
      case "child_tool_request":
      case "child_permission_decision": {
        const existing = projection.childAgents[event.taskId];
        if (existing) existing.lastEventSeq = event.seq;
        break;
      }
      case "child_cancelled": {
        const existing = projection.childAgents[event.taskId];
        projection.childAgents[event.taskId] = {
          ...(existing ?? { taskId: event.taskId, state: "running" as const }),
          state: "cancelled",
          lastEventSeq: event.seq,
          errorMessage: event.reason,
        };
        break;
      }
      case "child_failed": {
        const existing = projection.childAgents[event.taskId];
        projection.childAgents[event.taskId] = {
          ...(existing ?? { taskId: event.taskId, state: "running" as const }),
          state: "failed",
          lastEventSeq: event.seq,
          errorMessage: event.errorMessage,
        };
        break;
      }
      case "child_completed": {
        const existing = projection.childAgents[event.taskId];
        projection.childAgents[event.taskId] = {
          ...(existing ?? { taskId: event.taskId, state: "running" as const }),
          state: "completed",
          lastEventSeq: event.seq,
          summary: event.summary,
        };
        break;
      }
      default:
        break;
    }
  }

  const now = opts.now ?? Date.now();
  for (const [requestId, request] of Object.entries(projection.requests)) {
    if (request.state === "pending" && request.expiresAt <= now) {
      projection.requests[requestId] = {
        ...request,
        state: "timed_out",
        resolvedAt: request.expiresAt,
      };
    }
  }
  projection.pendingRequests = Object.values(projection.requests).filter(
    (request) => request.state === "pending",
  );
  return projection;
}
