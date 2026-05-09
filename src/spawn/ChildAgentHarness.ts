import type { ControlEventInput } from "../control/ControlEvents.js";
import type { SpawnChildResult } from "./ChildAgentLoop.js";

export interface ChildAgentLifecycle {
  started(): Promise<void>;
  progress(detail: string): Promise<void>;
  toolRequest(input: { requestId: string; toolName: string }): Promise<void>;
  permissionDecision(input: {
    decision: "allow" | "deny" | "ask";
    reason?: string;
  }): Promise<void>;
  cancelled(reason: string): Promise<void>;
  failed(errorMessage: string): Promise<void>;
  completed(summary?: unknown): Promise<void>;
}

export interface ChildAgentHarnessOptions {
  taskId: string;
  parentTurnId: string;
  prompt?: string;
  detail?: string;
  emitControlEvent?(event: ControlEventInput): Promise<unknown> | unknown;
  emitAgentEvent?(event: unknown): void;
}

export function createChildAgentHarness(
  options: ChildAgentHarnessOptions,
): ChildAgentLifecycle {
  let terminalRecorded = false;

  async function emit(event: ControlEventInput): Promise<void> {
    try {
      await options.emitControlEvent?.(event);
    } catch {
      /* best-effort: child work should not fail because the event sink is down */
    }
    try {
      options.emitAgentEvent?.(event);
    } catch {
      /* best-effort live telemetry */
    }
  }

  async function terminal(event: ControlEventInput): Promise<void> {
    if (terminalRecorded) return;
    terminalRecorded = true;
    await emit(event);
  }

  return {
    async started() {
      await emit({
        type: "child_started",
        taskId: options.taskId,
        parentTurnId: options.parentTurnId,
        ...(options.prompt ? { prompt: options.prompt } : {}),
        ...(options.detail ? { detail: options.detail } : {}),
      });
    },
    async progress(detail: string) {
      await emit({ type: "child_progress", taskId: options.taskId, detail });
    },
    async toolRequest(input: { requestId: string; toolName: string }) {
      await emit({
        type: "child_tool_request",
        taskId: options.taskId,
        requestId: input.requestId,
        toolName: input.toolName,
      });
    },
    async permissionDecision(input: {
      decision: "allow" | "deny" | "ask";
      reason?: string;
    }) {
      await emit({
        type: "child_permission_decision",
        taskId: options.taskId,
        decision: input.decision,
        ...(input.reason ? { reason: input.reason } : {}),
      });
    },
    async cancelled(reason: string) {
      await terminal({ type: "child_cancelled", taskId: options.taskId, reason });
    },
    async failed(errorMessage: string) {
      await terminal({
        type: "child_failed",
        taskId: options.taskId,
        errorMessage,
      });
    },
    async completed(summary?: unknown) {
      await terminal({
        type: "child_completed",
        taskId: options.taskId,
        ...(summary !== undefined ? { summary } : {}),
      });
    },
  };
}

export async function recordChildTerminal(
  lifecycle: ChildAgentLifecycle | undefined,
  result: SpawnChildResult,
): Promise<void> {
  if (!lifecycle) return;
  if (result.status === "ok") {
    await lifecycle.completed({
      status: result.status,
      finalText: result.finalText,
      toolCallCount: result.toolCallCount,
    });
    return;
  }
  if (result.status === "aborted") {
    await lifecycle.cancelled(result.errorMessage ?? "parent aborted");
    return;
  }
  await lifecycle.failed(result.errorMessage ?? "child failed");
}
