import { describe, expect, it } from "vitest";

import { tsStylePublicEvents } from "./fixtures/openmagi-public-events";
import {
  beginStreamChatTurn,
  foldRuntimeEvent,
  foldRuntimeEvents,
  initialStreamChatState,
} from "./stream-chat-reducer";

describe("stream-chat-reducer", () => {
  it("starts from an empty, non-streaming state", () => {
    const state = initialStreamChatState();
    expect(state.assistantText).toBe("");
    expect(state.thinkingText).toBe("");
    expect(state.tools.size).toBe(0);
    expect(state.controlRequest).toBeNull();
    expect(state.terminal).toBeNull();
    expect(state.streaming).toBe(false);
  });

  it("marks a turn as preparing before the first runtime event arrives", () => {
    const state = beginStreamChatTurn(initialStreamChatState());

    expect(state.streaming).toBe(true);
    expect(state.phase).toEqual({
      phase: "preparing",
      label: "Preparing",
      detail: null,
    });
    expect(state.terminal).toBeNull();
    expect(state.controlRequest).toBeNull();
  });

  it("folds llm_progress into a visible model progress card", () => {
    const state = foldRuntimeEvents([
      {
        type: "llm_progress",
        turnId: "turn-1",
        iter: 2,
        stage: "waiting",
        label: "Collecting sources",
        detail: "Checking public web results",
        elapsedMs: 42_000,
      },
    ]);

    expect(state.streaming).toBe(true);
    expect(state.heartbeatElapsedMs).toBe(42_000);
    expect(state.tools.get("llm:turn-1:2")).toMatchObject({
      id: "llm:turn-1:2",
      name: "ModelProgress",
      status: "running",
      inputPreview: JSON.stringify({
        stage: "waiting",
        label: "Collecting sources",
        detail: "Checking public web results",
        elapsedMs: 42_000,
      }),
      kind: "tool",
      rejected: false,
    });
  });

  it("folds heartbeat events into elapsed progress before tool events arrive", () => {
    const state = foldRuntimeEvents([
      { type: "heartbeat", elapsedMs: 15_000 },
    ]);

    expect(state.streaming).toBe(true);
    expect(state.heartbeatElapsedMs).toBe(15_000);
    expect(state.tools.get("llm:heartbeat")).toMatchObject({
      id: "llm:heartbeat",
      name: "ModelProgress",
      status: "running",
      inputPreview: JSON.stringify({
        stage: "heartbeat",
        label: "Still working",
        elapsedMs: 15_000,
      }),
      kind: "tool",
      rejected: false,
    });
  });

  it("accumulates text_delta and reads both `delta` and `text`", () => {
    const state = foldRuntimeEvents([
      { type: "text_delta", delta: "Hello" },
      { type: "text_delta", text: ", world" },
      { type: "text_delta", delta: "!" },
    ]);
    expect(state.assistantText).toBe("Hello, world!");
    expect(state.streaming).toBe(true);
  });

  it("accepts kind as an alternate RuntimeEvent discriminator", () => {
    const state = foldRuntimeEvents([
      { kind: "text_delta", delta: "Preparing " },
      { kind: "tool_start", id: "search-1", name: "WebSearch", input_preview: "{\"query\":\"openmagi docs\"}" },
      {
        kind: "tool_progress",
        id: "search-1",
        label: "Searching the web",
        url: "https://example.test/report?token=fixture-secret",
        detail: "Checking https://example.test/report?token=fixture-secret",
      },
      { kind: "tool_end", id: "search-1", status: "ok", duration_ms: 12 },
    ]);

    expect(state.assistantText).toBe("Preparing ");
    expect(state.textCommitted).toBe(true);
    expect(state.tools.get("search-1")).toMatchObject({
      name: "Searching the web",
      status: "ok",
      outputPreview: "{\"url\":\"https://example.test/report\",\"detail\":\"Checking https://example.test/report\"}",
      durationMs: 12,
    });
  });

  it("accepts Anthropic and OpenAI text delta RuntimeEvent variants", () => {
    const state = foldRuntimeEvents([
      { type: "content_block_delta", delta: { type: "text_delta", text: "Anthropic " } },
      { kind: "content_block_delta", delta: { text: "kind " } },
      { choices: [{ delta: { content: "OpenAI" } }] },
    ]);

    expect(state.assistantText).toBe("Anthropic kind OpenAI");
    expect(state.streaming).toBe(true);
  });

  it("accumulates thinking_delta separately from assistant text", () => {
    const state = foldRuntimeEvents([
      { type: "thinking_delta", delta: "Let me think" },
      { type: "text_delta", delta: "Answer" },
      { type: "thinking_delta", delta: " harder" },
    ]);
    expect(state.thinkingText).toBe("Let me think harder");
    expect(state.assistantText).toBe("Answer");
  });

  it("thinking_delta accumulates using the `text` key fallback", () => {
    const state = foldRuntimeEvents([
      { type: "thinking_delta", delta: "first " },
      { type: "thinking_delta", text: "second" },
    ]);
    expect(state.thinkingText).toBe("first second");
  });

  it("correlates tool_start/tool_progress/tool_end by id", () => {
    const state = foldRuntimeEvents([
      { type: "tool_start", id: "t1", name: "FileRead", input_preview: "{\"path\":\"x\"}" },
      { type: "tool_progress", id: "t1", label: "Reading", status: "running" },
      { type: "tool_end", id: "t1", status: "ok", output_preview: "120 bytes", durationMs: 10 },
    ]);
    const card = state.tools.get("t1");
    expect(card).toBeDefined();
    expect(card?.name).toBe("FileRead");
    expect(card?.kind).toBe("tool");
    expect(card?.inputPreview).toBe("{\"path\":\"x\"}");
    expect(card?.status).toBe("ok");
    expect(card?.outputPreview).toBe("120 bytes");
    expect(card?.rejected).toBe(false);
  });

  it("tool_end reads durationMs from camelCase (durationMs)", () => {
    const state = foldRuntimeEvents([
      { type: "tool_start", id: "t1", name: "X", input_preview: "" },
      { type: "tool_end", id: "t1", status: "ok", durationMs: 42 },
    ]);
    expect(state.tools.get("t1")?.durationMs).toBe(42);
  });

  it("tool_end reads durationMs from snake_case (duration_ms)", () => {
    const state = foldRuntimeEvents([
      { type: "tool_start", id: "t1", name: "X", input_preview: "" },
      { type: "tool_end", id: "t1", status: "ok", duration_ms: 99 },
    ]);
    expect(state.tools.get("t1")?.durationMs).toBe(99);
  });

  it("marks a card rejected when tool_end status is error (cancelled-tool shape, no interrupted flag)", () => {
    const state = foldRuntimeEvents([
      { type: "tool_start", id: "t1", name: "Bash", input_preview: "rm -rf" },
      { type: "tool_end", id: "t1", status: "error" },
    ]);
    const card = state.tools.get("t1");
    expect(card?.status).toBe("error");
    expect(card?.rejected).toBe(true);
  });

  it("marks blocked/interrupted/needs_approval/cancelled/timeout statuses as rejected", () => {
    const statuses = ["blocked", "interrupted", "needs_approval", "cancelled", "timeout"] as const;
    for (const status of statuses) {
      const s = foldRuntimeEvents([
        { type: "tool_start", id: "t", name: "X", input_preview: "" },
        { type: "tool_end", id: "t", status },
      ]);
      expect(s.tools.get("t")?.rejected).toBe(true);
    }
  });

  it("marks defensive extra status `denied` as rejected", () => {
    const state = foldRuntimeEvents([
      { type: "tool_start", id: "t1", name: "X", input_preview: "" },
      { type: "tool_end", id: "t1", status: "denied" },
    ]);
    expect(state.tools.get("t1")?.rejected).toBe(true);
  });

  it("tool_progress with no `status` preserves the existing card status", () => {
    const state = foldRuntimeEvents([
      { type: "tool_start", id: "t1", name: "X", input_preview: "" },
      { type: "tool_progress", id: "t1", label: "some label" },
    ]);
    expect(state.tools.get("t1")?.status).toBe("running");
  });

  it("tool_progress preserves safe public URL details and drops private-looking fields", () => {
    const state = foldRuntimeEvents([
      { type: "tool_start", id: "web-1", name: "WebSearch", input_preview: "{\"query\":\"openmagi docs\"}" },
      {
        type: "tool_progress",
        id: "web-1",
        label: "Searching the web",
        url: "https://example.test/report?token=fixture-secret",
        detail: "Checking https://example.test/report?ref=private-ref",
        rawPrompt: "private user prompt",
      },
    ]);

    const card = state.tools.get("web-1");
    expect(card?.name).toBe("Searching the web");
    expect(card?.outputPreview).toContain("https://example.test/report");
    expect(card?.outputPreview).not.toContain("fixture-secret");
    expect(card?.outputPreview).not.toContain("private-ref");
    expect(card?.outputPreview).not.toContain("private user prompt");
  });

  it("tool_end for an unknown id returns state unchanged", () => {
    const before = foldRuntimeEvents([
      { type: "tool_start", id: "t1", name: "X", input_preview: "" },
    ]);
    const after = foldRuntimeEvent(before, { type: "tool_end", id: "unknown-id", status: "ok" });
    // tools map is unchanged
    expect(after.tools.size).toBe(before.tools.size);
    expect(after.tools.get("t1")?.status).toBe("running");
  });

  it("renders TodoWrite tool_start as a todo card with the preview string", () => {
    const state = foldRuntimeEvents([
      { type: "tool_start", id: "td1", name: "TodoWrite", input_preview: "[ ] step one" },
    ]);
    const card = state.tools.get("td1");
    expect(card?.kind).toBe("todo");
    expect(card?.inputPreview).toBe("[ ] step one");
  });

  it("sets controlRequest with string arguments, and turn_result clears it", () => {
    const withRequest = foldRuntimeEvents([
      {
        type: "control_request",
        request_id: "r1",
        tool_name: "Bash",
        arguments: "rm -rf /tmp/x",
        reason: "destructive",
      },
    ]);
    expect(withRequest.controlRequest).toEqual({
      request_id: "r1",
      tool_name: "Bash",
      arguments: "rm -rf /tmp/x",
      reason: "destructive",
    });
    const cleared = foldRuntimeEvent(withRequest, {
      type: "turn_result",
      terminal: "completed",
    });
    expect(cleared.controlRequest).toBeNull();
  });

  it("control_request with null/missing arguments is dropped (no controlRequest set)", () => {
    const state = foldRuntimeEvents([
      { type: "control_request", request_id: "r1", tool_name: "Bash" /* no arguments */ },
    ]);
    expect(state.controlRequest).toBeNull();
  });

  it("sets phase from turn_phase and reads both turnId and turn_id", () => {
    const camel = foldRuntimeEvents([
      { type: "turn_phase", turnId: "T-camel", phase: "planning", label: "Planning" },
    ]);
    expect(camel.phase?.phase).toBe("planning");
    expect(camel.turnId).toBe("T-camel");

    const snake = foldRuntimeEvents([
      { type: "text_delta", delta: "x", turn_id: "T-snake" },
    ]);
    expect(snake.turnId).toBe("T-snake");
  });

  it("treats turn_phase as live progress before the first text or tool event", () => {
    const state = foldRuntimeEvents([
      { type: "turn_phase", turnId: "T-prep", phase: "preparing", label: "Preparing" },
    ]);

    expect(state.streaming).toBe(true);
    expect(state.phase?.phase).toBe("preparing");
  });

  it("turn_end clears streaming and records status", () => {
    const state = foldRuntimeEvents([
      { type: "text_delta", delta: "hi" },
      { type: "turn_end", turnId: "T1", status: "committed" },
    ]);
    expect(state.streaming).toBe(false);
  });

  it("turn_result terminal sets terminal + streaming false; aborted+error captured", () => {
    const state = foldRuntimeEvents([
      { type: "text_delta", delta: "partial" },
      {
        type: "turn_result",
        terminal: "aborted",
        error: "user cancelled",
        usage: { input_tokens: 1 },
        cost_usd: 0.0001,
      },
    ]);
    expect(state.streaming).toBe(false);
    expect(state.terminal?.terminal).toBe("aborted");
    expect(state.terminal?.error).toBe("user cancelled");
    expect(state.terminal?.costUsd).toBe(0.0001);
  });

  it("ORDERING: flushes assistant text before opening the first tool card", () => {
    const state = foldRuntimeEvents([
      { type: "text_delta", delta: "I will read the file." },
      { type: "tool_start", id: "t1", name: "FileRead", input_preview: "{}" },
    ]);
    // Invariant: the in-flight assistant text is committed (textCommitted) the
    // moment a non-text event arrives, so the transcript renders text BEFORE the tool.
    expect(state.textCommitted).toBe(true);
    expect(state.assistantText).toBe("I will read the file.");
    expect(state.tools.has("t1")).toBe(true);

    // A later text_delta opens a fresh in-flight bubble (un-commits).
    const resumed = foldRuntimeEvent(state, { type: "text_delta", delta: " Done." });
    expect(resumed.textCommitted).toBe(false);
    expect(resumed.assistantText).toBe("I will read the file. Done.");
  });

  it("folds public work-panel event types into structured live state", () => {
    const state = foldRuntimeEvents([
      {
        type: "task_board",
        tasks: [
          {
            id: "task-1",
            title: "Verify sources",
            description: "Check public reports",
            status: "in_progress",
            parallelGroup: "evidence",
            dependsOn: ["task-0"],
          },
        ],
      },
      {
        type: "source_inspected",
        source: {
          sourceId: "src-1",
          kind: "web_fetch",
          uri: "https://example.com/report",
          inspectedAt: 1_779_206_400_000,
          title: "Example report",
        },
      },
      {
        type: "rule_check",
        ruleId: "claim-citation-gate",
        verdict: "ok",
        checkedAt: 1_779_206_401_000,
      },
      {
        type: "runtime_trace",
        turnId: "turn-1",
        phase: "retry_scheduled",
        severity: "warning",
        title: "Verifier retry",
        detail: "Need source evidence.",
      },
      {
        type: "control_event",
        event: {
          type: "runtime_trace",
          turnId: "turn-1",
          phase: "verifier_blocked",
          severity: "warning",
          title: "Verifier blocked answer",
        },
      },
      { type: "child_started", taskId: "child-1", role: "research", detail: "Checking sources" },
      { type: "child_progress", taskId: "child-1", detail: "Reading report" },
      { type: "child_tool_request", taskId: "child-1", toolName: "WebFetch" },
      { type: "child_completed", taskId: "child-1", detail: "Sources verified" },
    ]);

    expect(state.taskBoard?.tasks).toEqual([
      {
        id: "task-1",
        title: "Verify sources",
        description: "Check public reports",
        status: "in_progress",
        parallelGroup: "evidence",
        dependsOn: ["task-0"],
      },
    ]);
    expect(state.inspectedSources).toHaveLength(1);
    expect(state.inspectedSources[0]).toMatchObject({
      sourceId: "src-1",
      uri: "https://example.com/report",
      title: "Example report",
    });
    expect(state.citationGate).toMatchObject({
      ruleId: "claim-citation-gate",
      verdict: "ok",
    });
    expect(state.runtimeTraces.map((trace) => trace.phase)).toEqual([
      "retry_scheduled",
      "verifier_blocked",
    ]);
    expect(state.subagents.get("child-1")).toMatchObject({
      taskId: "child-1",
      role: "research",
      status: "done",
      detail: "Sources verified",
    });
    expect(state.streaming).toBe(true);
  });

  it("folds child_started enriched fields (agentName, model, taskTitle) onto the subagent activity", () => {
    const state = foldRuntimeEvents([
      {
        type: "child_started",
        taskId: "child-7",
        parentTurnId: "turn-1",
        childReceiptRef: "receipt:sha256:abc",
        agentName: "Halley",
        model: "anthropic:claude-opus-4-8",
        taskTitle: "Cross-validate 1+1 across 3 SOTA models",
        detail: "Delegated child started",
      },
    ]);
    expect(state.subagents.get("child-7")).toMatchObject({
      taskId: "child-7",
      status: "running",
      agentName: "Halley",
      model: "anthropic:claude-opus-4-8",
      taskTitle: "Cross-validate 1+1 across 3 SOTA models",
    });
  });

  it("preserves enriched subagent fields across subsequent child_progress events that omit them", () => {
    const state = foldRuntimeEvents([
      {
        type: "child_started",
        taskId: "child-7",
        agentName: "Halley",
        model: "anthropic:claude-opus-4-8",
        taskTitle: "Cross-validate 1+1",
        detail: "Delegated child started",
      },
      { type: "child_progress", taskId: "child-7", detail: "Running tool" },
    ]);
    expect(state.subagents.get("child-7")).toMatchObject({
      agentName: "Halley",
      model: "anthropic:claude-opus-4-8",
      taskTitle: "Cross-validate 1+1",
      detail: "Running tool",
    });
  });

  it("pushes unknown / low-priority events as activities", () => {
    const state = foldRuntimeEvents([
      { type: "browser_frame", url: "https://example.com" },
      { type: "background_task", taskId: "background-1", detail: "Drafting" },
    ]);
    expect(state.activities.length).toBe(2);
    expect(state.activities[0]?.type).toBe("browser_frame");
  });

  it("foldRuntimeEvent with a non-object payload (null, number, string) returns state unchanged", () => {
    const initial = initialStreamChatState();
    expect(foldRuntimeEvent(initial, null)).toBe(initial);
    expect(foldRuntimeEvent(initial, 42)).toBe(initial);
    expect(foldRuntimeEvent(initial, "text_delta")).toBe(initial);
  });

  it("folds the full public fixture array without throwing and yields exact activity count and sane state", () => {
    // tsStylePublicEvents is readonly; cast once to satisfy the `readonly unknown[]` signature.
    const state = foldRuntimeEvents(tsStylePublicEvents as readonly unknown[]);
    expect(state.assistantText).toBe("Done.");
    expect(state.thinkingText).toBe("Checking.");
    expect(state.tools.get("tool-1")?.status).toBe("done");
    expect(state.phase?.phase).toBe("planning");
    expect(state.taskBoard?.tasks[0]?.title).toBe("Verify sources");
    expect(state.inspectedSources[0]?.uri).toBe("https://example.com/report");
    expect(state.runtimeTraces.map((trace) => trace.phase)).toEqual([
      "retry_scheduled",
      "retry_scheduled",
    ]);
    expect(state.subagents.get("child-1")?.status).toBe("done");
    // Spot-check remaining activity entries.
    expect(state.activities[0]?.type).toBe("browser_frame");
    expect(state.activities[1]?.type).toBe("document_draft");
  });
});
