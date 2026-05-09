import { describe, expect, it } from "vitest";
import { safeAgentEvent } from "./safeAgentEvent.js";

describe("safeAgentEvent", () => {
  it("preserves whitespace in text deltas", () => {
    expect(safeAgentEvent({
      type: "text_delta",
      delta: " world ",
    })).toEqual({
      type: "text_delta",
      delta: " world ",
    });
  });

  it("preserves bounded redacted tool previews for public work visibility", () => {
    expect(safeAgentEvent({
      type: "tool_start",
      id: "tool-1",
      name: "FileWrite",
      input_preview: "{\"path\":\"book/FINAL_MANUSCRIPT.md\",\"content\":\"ok\",\"Authorization\":\"Bearer secret\"}",
    })).toEqual({
      type: "tool_start",
      id: "tool-1",
      name: "FileWrite",
      input_preview: "{\"path\":\"book/FINAL_MANUSCRIPT.md\",\"content\":\"ok\",\"Authorization\":\"Bearer [redacted]\"}",
    });

    expect(safeAgentEvent({
      type: "tool_end",
      id: "tool-1",
      status: "ok",
      output_preview: `raw stdout with token=ghp_supersecret ${"x".repeat(500)}`,
      durationMs: 42,
    })).toEqual({
      type: "tool_end",
      id: "tool-1",
      status: "ok",
      output_preview: expect.stringContaining("token=[redacted]"),
      durationMs: 42,
    });
    const toolEnd = safeAgentEvent({
      type: "tool_end",
      id: "tool-1",
      status: "ok",
      output_preview: `raw stdout ${"x".repeat(500)}`,
      durationMs: 42,
    });
    expect(toolEnd && "output_preview" in toolEnd ? toolEnd.output_preview.length : 0).toBeLessThanOrEqual(400);
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

  it("allows background task status events without exposing prompts", () => {
    expect(safeAgentEvent({
      type: "background_task",
      taskId: "task-1",
      persona: "writer",
      status: "running",
      detail: "Drafting chapter 4",
      prompt: "private full task prompt",
      resultText: "private result",
    })).toEqual({
      type: "background_task",
      taskId: "task-1",
      persona: "writer",
      status: "running",
      detail: "Drafting chapter 4",
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

  it("allows structured patch previews without file contents", () => {
    expect(safeAgentEvent({
      type: "patch_preview",
      toolUseId: "tu_patch",
      dryRun: false,
      changedFiles: ["src/app.ts"],
      createdFiles: ["src/new.ts"],
      deletedFiles: [],
      files: [
        {
          path: "src/app.ts",
          operation: "update",
          hunks: 1,
          addedLines: 2,
          removedLines: 1,
          oldSha256: "a".repeat(64),
          newSha256: "b".repeat(64),
          before: "private file content",
          after: "private file content",
        },
      ],
    })).toEqual({
      type: "patch_preview",
      toolUseId: "tu_patch",
      dryRun: false,
      changedFiles: ["src/app.ts"],
      createdFiles: ["src/new.ts"],
      deletedFiles: [],
      files: [
        {
          path: "src/app.ts",
          operation: "update",
          hunks: 1,
          addedLines: 2,
          removedLines: 1,
          oldSha256: "a".repeat(64),
          newSha256: "b".repeat(64),
        },
      ],
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

  it("passes browser preview frames without exposing browser transport secrets", () => {
    const imageBase64 = Buffer.from("tiny-frame").toString("base64");

    expect(safeAgentEvent({
      type: "browser_frame",
      action: "open",
      url: "https://example.com/dashboard",
      imageBase64,
      contentType: "image/png",
      capturedAt: 123,
      sessionId: "browser-session-fixture",
      cdpEndpoint: "ws://browser-worker.magi-system:9222/cdp/browser-session-fixture/secret",
      cdpToken: "secret",
    })).toEqual({
      type: "browser_frame",
      action: "open",
      url: "https://example.com/dashboard",
      imageBase64,
      contentType: "image/png",
      capturedAt: 123,
    });
  });

  it("passes inspected source records while dropping arbitrary metadata", () => {
    expect(safeAgentEvent({
      type: "source_inspected",
      source: {
        sourceId: "src_1",
        turnId: "turn-1",
        toolName: "WebFetch",
        kind: "web_fetch",
        uri: "https://example.com/docs",
        title: "Example docs",
        contentHash: "sha256:abc",
        contentType: "text/html",
        trustTier: "official",
        snippets: ["short excerpt"],
        inspectedAt: 123,
        metadata: { rawResponse: "private transport details" },
      },
    })).toEqual({
      type: "source_inspected",
      source: {
        sourceId: "src_1",
        kind: "web_fetch",
        uri: "https://example.com/docs",
        inspectedAt: 123,
        turnId: "turn-1",
        toolName: "WebFetch",
        title: "Example docs",
        contentHash: "sha256:abc",
        contentType: "text/html",
        trustTier: "official",
        snippets: ["short excerpt"],
      },
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
        type: "control_request_created",
        request: {
          requestId: "cr-patch",
          kind: "tool_permission",
          state: "pending",
          sessionKey: "agent:main:app:general",
          source: "turn",
          prompt: "Review PatchApply changes before applying.",
          proposedInput: {
            toolName: "PatchApply",
            patch: "--- a/secret.txt\n+++ b/secret.txt\n@@ -1 +1 @@\n-token=abc\n+token=def\n",
            patchPreview: {
              dryRun: false,
              changedFiles: ["secret.txt"],
              createdFiles: [],
              deletedFiles: [],
              files: [
                {
                  path: "secret.txt",
                  operation: "update",
                  hunks: 1,
                  addedLines: 1,
                  removedLines: 1,
                  oldSha256: "old",
                  newSha256: "new",
                },
              ],
            },
          },
          createdAt: 1,
          expiresAt: 2,
        },
      },
    })).toEqual({
      type: "control_event",
      seq: 12,
      event: {
        type: "control_request_created",
        request: {
          requestId: "cr-patch",
          kind: "tool_permission",
          state: "pending",
          sessionKey: "agent:main:app:general",
          source: "turn",
          prompt: "Review PatchApply changes before applying.",
          proposedInput: {
            toolName: "PatchApply",
            patchPreview: {
              dryRun: false,
              changedFiles: ["secret.txt"],
              createdFiles: [],
              deletedFiles: [],
              files: [
                {
                  path: "secret.txt",
                  operation: "update",
                  hunks: 1,
                  addedLines: 1,
                  removedLines: 1,
                  oldSha256: "old",
                  newSha256: "new",
                },
              ],
            },
          },
          createdAt: 1,
          expiresAt: 2,
        },
      },
    });

    expect(safeAgentEvent({
      type: "control_event",
      seq: 13,
      event: {
        type: "control_request_resolved",
        requestId: "cr-1",
        decision: "denied",
        feedback: "더 좁게 고쳐줘",
        updatedInput: { hiddenField: "redacted by omission" },
      },
    })).toEqual({
      type: "control_event",
      seq: 13,
      event: {
        type: "control_request_resolved",
        requestId: "cr-1",
        decision: "denied",
        feedback: "더 좁게 고쳐줘",
      },
    });

    expect(safeAgentEvent({
      type: "control_replay_complete",
      lastSeq: 13,
    })).toEqual({
      type: "control_replay_complete",
      lastSeq: 13,
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

  it("allows bounded mission events while omitting private payload internals", () => {
    expect(safeAgentEvent({
      type: "mission_created",
      mission: {
        id: "m1",
        title: "x".repeat(500),
        kind: "goal",
        status: "running",
        payload: { private: true },
      },
    })).toEqual({
      type: "mission_created",
      mission: {
        id: "m1",
        title: `${"x".repeat(237)}...`,
        kind: "goal",
        status: "running",
      },
    });

    const result = safeAgentEvent({
      type: "mission_event",
      missionId: "m1",
      eventType: "blocked",
      message: "needs approval ".repeat(80),
      payload: { private: true },
    });

    expect(result).toEqual({
      type: "mission_event",
      missionId: "m1",
      eventType: "blocked",
      message: expect.any(String),
    });
    expect(String(result?.message).length).toBeLessThanOrEqual(400);
    expect(result).not.toHaveProperty("payload");
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
