import { describe, expect, it } from "vitest";
import { safeAgentEvent } from "./safeAgentEvent.js";

describe("safeAgentEvent", () => {
  it("removes raw tool previews from tool events", () => {
    expect(safeAgentEvent({
      type: "tool_start",
      id: "tool-1",
      name: "exec_command",
      input_preview: "{\"Authorization\":\"Bearer secret\"}",
    })).toEqual({
      type: "tool_start",
      id: "tool-1",
      name: "exec_command",
    });

    expect(safeAgentEvent({
      type: "tool_end",
      id: "tool-1",
      status: "ok",
      output_preview: "raw stdout with secrets",
      durationMs: 42,
    })).toEqual({
      type: "tool_end",
      id: "tool-1",
      status: "ok",
      durationMs: 42,
    });
  });

  it("removes private prompt and answer text from spawn events", () => {
    expect(safeAgentEvent({
      type: "spawn_started",
      taskId: "task-1",
      persona: "reviewer",
      prompt: "private delegated prompt",
      deliver: "background",
    })).toEqual({
      type: "spawn_started",
      taskId: "task-1",
      persona: "reviewer",
      deliver: "background",
    });

    expect(safeAgentEvent({
      type: "spawn_result",
      taskId: "task-1",
      status: "ok",
      finalText: "private child final answer",
      toolCallCount: 3,
    })).toEqual({
      type: "spawn_result",
      taskId: "task-1",
      status: "ok",
      toolCallCount: 3,
    });
  });

  it("removes queued message text while preserving queue counts", () => {
    expect(safeAgentEvent({
      type: "injection_queued",
      injectionId: "inj-1",
      text: "follow-up content",
      queuedCount: 2,
    })).toEqual({
      type: "injection_queued",
      injectionId: "inj-1",
      queuedCount: 2,
    });
  });

  it("allows safe progress, retry, interrupt, heartbeat, and drain events", () => {
    expect(safeAgentEvent({
      type: "tool_progress",
      id: "tool-1",
      label: "Running shell command",
    })).toEqual({
      type: "tool_progress",
      id: "tool-1",
      label: "Running shell command",
    });

    expect(safeAgentEvent({
      type: "retry",
      reason: "rate_limit",
      retryNo: 2,
      toolUseId: "tool-1",
      toolName: "web_search",
    })).toEqual({
      type: "retry",
      reason: "rate_limit",
      retryNo: 2,
      toolUseId: "tool-1",
      toolName: "web_search",
    });

    expect(safeAgentEvent({
      type: "turn_interrupted",
      turnId: "turn-1",
      handoffRequested: true,
      source: "web",
    })).toEqual({
      type: "turn_interrupted",
      turnId: "turn-1",
      handoffRequested: true,
      source: "web",
    });

    expect(safeAgentEvent({
      type: "heartbeat",
      turnId: "turn-1",
      iter: 3,
      elapsedMs: 12000,
      lastEventAt: 123,
    })).toEqual({
      type: "heartbeat",
      turnId: "turn-1",
      iter: 3,
      elapsedMs: 12000,
      lastEventAt: 123,
    });

    expect(safeAgentEvent({
      type: "injection_drained",
      count: 2,
      iteration: 4,
    })).toEqual({
      type: "injection_drained",
      count: 2,
      iteration: 4,
    });
  });

  it("passes durable control replay events without leaking request internals", () => {
    expect(safeAgentEvent({
      type: "control_event",
      seq: 11,
      event: {
        type: "control_request_created",
        request: {
          requestId: "cr-tool",
          kind: "tool_permission",
          state: "pending",
          sessionKey: "agent:main:app:general",
          source: "turn",
          prompt: "Allow Bash?",
          proposedInput: { command: "echo secret-token" },
          createdAt: 1,
          expiresAt: 2,
        },
      },
    })).toEqual({
      type: "control_event",
      seq: 11,
      event: {
        type: "control_request_created",
        request: {
          requestId: "cr-tool",
          kind: "tool_permission",
          state: "pending",
          sessionKey: "agent:main:app:general",
          source: "turn",
          prompt: "Allow Bash?",
          createdAt: 1,
          expiresAt: 2,
        },
      },
    });

    expect(safeAgentEvent({
      type: "control_event",
      seq: 12,
      event: {
        type: "control_request_resolved",
        requestId: "cr-1",
        decision: "approved",
        updatedInput: { hiddenField: "redacted by omission" },
      },
    })).toEqual({
      type: "control_event",
      seq: 12,
      event: {
        type: "control_request_resolved",
        requestId: "cr-1",
        decision: "approved",
      },
    });

    expect(safeAgentEvent({
      type: "control_replay_complete",
      lastSeq: 12,
    })).toEqual({
      type: "control_replay_complete",
      lastSeq: 12,
    });
  });

  it("keeps plan approval identifiers needed by live approval cards", () => {
    expect(safeAgentEvent({
      type: "plan_ready",
      planId: "plan-1",
      requestId: "cr-plan",
      state: "awaiting_approval",
      plan: "- test\n- ship",
    })).toEqual({
      type: "plan_ready",
      planId: "plan-1",
      requestId: "cr-plan",
      state: "awaiting_approval",
      plan: "- test\n- ship",
    });
  });

  it("passes child lifecycle summaries while dropping prompts and raw outputs", () => {
    expect(safeAgentEvent({
      type: "child_started",
      taskId: "task-1",
      parentTurnId: "turn-1",
      prompt: "private child prompt",
    })).toEqual({
      type: "child_started",
      taskId: "task-1",
      parentTurnId: "turn-1",
    });

    expect(safeAgentEvent({
      type: "child_completed",
      taskId: "task-1",
      summary: { text: "private child answer" },
    })).toEqual({
      type: "child_completed",
      taskId: "task-1",
    });
  });

  it("bounds user-visible labels and drops malformed events", () => {
    const result = safeAgentEvent({
      type: "tool_progress",
      id: "tool-1",
      label: "x".repeat(500),
    });

    expect(result).toEqual({
      type: "tool_progress",
      id: "tool-1",
      label: `${"x".repeat(237)}...`,
    });
    expect(safeAgentEvent({ type: "unknown", unexpectedField: "value" })).toBeNull();
    expect(safeAgentEvent(null)).toBeNull();
  });
});
