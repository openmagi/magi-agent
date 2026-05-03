/**
 * SseWriter — serialises AgentEvent stream onto an SSE response.
 * Design reference: §7.9.
 *
 * Emits TWO interleaved streams on the same SSE body:
 *
 *  1. Legacy OpenAI-compatible `data: {...}` lines (choices[].delta.*)
 *     so chat-proxy's existing legacy parsing pipeline keeps
 *     working for this bot during the migration.
 *
 *  2. `event: agent\ndata: {...}` lines carrying structured
 *     `AgentEvent` payloads for the new ThinkingBlock UX.
 *
 * A client opting into the new UX sniffs for `event: agent` lines; one
 * that doesn't still sees a perfectly valid OpenAI chat-completion
 * stream.
 *
 * Phase 1a only implements the event types used by the echo-turn:
 *   turn_start, text_delta, turn_end. Later phases append to the
 *   AgentEvent union (§7.9) without changing the wire shape.
 */

import type { ServerResponse } from "node:http";
import { safeAgentEvent } from "./safeAgentEvent.js";
import type { TurnStatus, TurnStopReason } from "../turn/types.js";
import type { ControlEvent } from "../control/ControlEvents.js";
import { UserVisibleRouteMetaFilter } from "../turn/visibleText.js";

export type TurnRoute = "direct" | "subagent" | "pipeline";

/** Subset of §7.9 AgentEvent implemented so far; full union lives in
 * the design doc and will land incrementally. */
export type AgentEvent =
  | { type: "turn_start"; turnId: string; declaredRoute: TurnRoute }
  | { type: "turn_phase"; turnId: string; phase: TurnStatus }
  | {
      type: "turn_end";
      turnId: string;
      status: "committed" | "aborted";
      stopReason: TurnStopReason;
      reason?: string;
    }
  | { type: "control_event"; seq: number; event: ControlEvent }
  | { type: "control_replay_complete"; lastSeq: number }
  | { type: "text_delta"; delta: string }
  | { type: "response_clear" }
  | { type: "thinking_delta"; delta: string }
  | { type: "tool_start"; id: string; name: string; input_preview?: string }
  | { type: "tool_progress"; id: string; label: string }
  | { type: "tool_end"; id: string; status: string; output_preview?: string; durationMs: number }
  | { type: "context_end" }
  | {
      type: "task_board";
      tasks: Array<{
        id: string;
        title: string;
        description: string;
        status: "pending" | "in_progress" | "completed" | "cancelled";
        parallelGroup?: string;
        dependsOn?: string[];
      }>;
    }
  | {
      type: "rule_check";
      ruleId: string;
      verdict: "pending" | "ok" | "violation";
      detail?: string;
    }
  | {
      type: "retry";
      reason: string;
      retryNo: number;
      toolUseId?: string;
      toolName?: string;
    }
  | {
      type: "turn_interrupted";
      turnId: string;
      handoffRequested: boolean;
      source: string;
    }
  | {
      type: "structured_output";
      status: "valid" | "invalid" | "retry_exhausted";
      schemaName?: string;
      reason?: string;
    }
  | {
      /** SpawnAgent (§7.12.d) — child turn launched. */
      type: "spawn_started";
      taskId: string;
      persona: string;
      prompt: string;
      deliver: "return" | "background";
    }
  | {
      /** SpawnAgent (§7.12.d) — background child completed. */
      type: "spawn_result";
      taskId: string;
      status: "ok" | "error" | "aborted";
      finalText: string;
      toolCallCount: number;
      errorMessage?: string;
    }
  | {
      /** SpawnAgent child lifecycle — live mirror of durable control events. */
      type: "child_started";
      taskId: string;
      parentTurnId?: string;
      prompt?: string;
    }
  | {
      type: "child_progress";
      taskId: string;
      detail: string;
    }
  | {
      type: "child_tool_request";
      taskId: string;
      requestId: string;
      toolName: string;
    }
  | {
      type: "child_permission_decision";
      taskId: string;
      decision: "allow" | "deny" | "ask";
      reason?: string;
    }
  | {
      type: "child_cancelled";
      taskId: string;
      reason: string;
    }
  | {
      type: "child_failed";
      taskId: string;
      errorMessage: string;
    }
  | {
      type: "child_completed";
      taskId: string;
      summary?: unknown;
    }
  | {
      /** SpawnAgent tournament mode (T3-16, OMC Port A) — final ranked variants. */
      type: "tournament_result";
      variants: Array<{
        variantIndex: number;
        score: number;
        finalText: string;
        spawnDir: string;
      }>;
      winnerIndex: number;
    }
  | {
      /** AskUserQuestion (§7.5) — blocks turn pending client answer. */
      type: "ask_user";
      questionId: string;
      question: string;
      choices: Array<{ id: string; label: string; description?: string }>;
      allowFreeText?: boolean;
    }
  | {
      /** Plan mode (§7.2) — plan produced and ready for client-side approval. */
      type: "plan_ready";
      planId?: string;
      requestId?: string;
      state?: "awaiting_approval";
      plan: string;
    }
  | {
      /** Plan lifecycle state transition. */
      type: "plan_lifecycle";
      state: string;
      previousMode?: string;
    }
  | {
      /**
       * Stop-condition hook (§5 / T3-14, OMC Port E) — long-running
       * iteration loop hit a runtime stop condition (user_stop |
       * circuit_breaker | max_iter | target_met | plateau). Emitted
       * per task that met a condition. The companion `iterationState`
       * for the task is marked `step = "stopped"` atomically.
       */
      type: "session_stop";
      taskId: string;
      reason:
        | "user_stop"
        | "circuit_breaker"
        | "max_iter"
        | "target_met"
        | "plateau";
      round: number;
      lastScore?: number;
    }
  | {
      /**
       * T4-19 §7.10 multi-context — emitted at turn start naming the
       * active context so the client (web/app) can render a
       * context-aware chat thread.
       */
      type: "context_activated";
      contextId: string;
      title: string;
    }
  | {
      /**
       * Gap §11.6 — compaction reserve-token floor capped to the
       * model's context window. If the routed model's window is so
       * small that even a fully-compacted transcript leaves < the
       * minimum viable live budget, the turn aborts with this event so
       * the UI can prompt the user to switch to a larger-window model.
       *
       * Fields mirror Open-source legacy runtime's upstream `compaction_impossible`
       * telemetry so dashboards can cross-check.
       */
      type: "compaction_impossible";
      model: string;
      contextWindow: number;
      effectiveReserveTokens: number;
      effectiveBudgetTokens: number;
      minViableBudgetTokens: number;
    }
  | {
      type: "injection_queued";
      injectionId: string;
      text: string;
      queuedCount: number;
    }
  | {
      type: "injection_drained";
      count: number;
      iteration: number;
    }
  | {
      /**
       * B5 pipeline heartbeat — emitted by Turn.ts when an iteration
       * goes silent for > HEARTBEAT_SILENCE_MS (20s). Subsequent
       * heartbeats fire every HEARTBEAT_INTERVAL_MS (30s) until the
       * iteration emits something else or the turn ends. Lets the
       * frontend distinguish "agent alive but thinking" from "agent
       * wedged" on long-running tool calls / LLM streams.
       */
      type: "heartbeat";
      turnId: string;
      iter: number;
      elapsedMs: number;
      lastEventAt: number;
    }
  | { type: "error"; code: string; message: string };

export class SseWriter {
  private ended = false;
  private readonly visibleTextFilter = new UserVisibleRouteMetaFilter();

  constructor(private readonly res: ServerResponse) {}

  /** MUST be called before any event/delta. Sets SSE headers. */
  start(): void {
    this.res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no", // bypass any upstream proxy buffering
    });
    // Flush headers immediately so chat-proxy sees the first byte.
    // (Node auto-flushes on first write, but a comment line forces it.)
    this.write(":ok\n\n");
  }

  /** Emit a structured agent event on the `agent` SSE event channel. */
  agent(event: AgentEvent): void {
    if (this.ended) return;
    if (event.type === "turn_start" || event.type === "response_clear") {
      this.visibleTextFilter.reset();
      this.writeAgentEvent(event);
      return;
    }
    if (event.type === "text_delta") {
      const delta = this.visibleTextFilter.filter(event.delta);
      if (delta.length === 0) return;
      this.writeAgentEvent({ ...event, delta });
      return;
    }
    if (event.type === "turn_end") {
      const flushed = this.visibleTextFilter.flush();
      if (flushed.length > 0) {
        this.writeAgentEvent({ type: "text_delta", delta: flushed });
      }
    }
    this.writeAgentEvent(event);
  }

  private writeAgentEvent(event: AgentEvent): void {
    const safeEvent = safeAgentEvent(event);
    if (!safeEvent) return;
    this.write(`event: agent\ndata: ${JSON.stringify(safeEvent)}\n\n`);
  }

  /**
   * Emit an OpenAI-compatible text delta on the default (unnamed) SSE
   * event channel. This is what legacy chat-proxy + non-aware clients
   * consume.
   */
  legacyDelta(content: string): void {
    if (this.ended) return;
    const payload = {
      choices: [{ index: 0, delta: { role: "assistant", content } }],
    };
    this.write(`data: ${JSON.stringify(payload)}\n\n`);
  }

  /** Emit the OpenAI finish_reason + DONE marker the legacy pipeline expects. */
  legacyFinish(): void {
    if (this.ended) return;
    const stop = { choices: [{ index: 0, delta: {}, finish_reason: "stop" }] };
    this.write(`data: ${JSON.stringify(stop)}\n\n`);
    this.write("data: [DONE]\n\n");
  }

  end(): void {
    if (this.ended) return;
    this.ended = true;
    if (this.res.destroyed || this.res.writableEnded) return;
    try {
      this.res.end();
    } catch {
      // Client refresh/app background can close the socket before the runtime
      // finishes. Recovery uses chat-proxy snapshots and persisted channel rows.
    }
  }

  private write(chunk: string): void {
    if (this.ended) return;
    if (this.res.destroyed || this.res.writableEnded) {
      this.ended = true;
      return;
    }
    try {
      this.res.write(chunk);
    } catch {
      this.ended = true;
    }
  }
}

/**
 * Silent SseWriter variant for out-of-band turns (cron fires, background
 * scheduled work) where there's no live HTTP client to stream to. Logs
 * events to console for observability but does not attempt to write to
 * a response stream.
 */
export class StubSseWriter extends SseWriter {
  constructor() {
    // Cast through unknown — the stub ignores the response anyway, and
    // passing a no-op object lets us subclass without exposing a new
    // constructor signature.
    super({
      writeHead: () => {},
      write: () => true,
      end: () => {},
    } as unknown as ServerResponse);
  }
  override start(): void { /* no-op */ }
  override agent(_event: AgentEvent): void { /* no-op — cron out-of-band */ }
  override legacyDelta(_content: string): void { /* no-op */ }
  override legacyFinish(): void { /* no-op */ }
  override end(): void { /* no-op */ }
}
