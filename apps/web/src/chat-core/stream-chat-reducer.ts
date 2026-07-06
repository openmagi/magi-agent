// Pure, transport-agnostic reducer that folds a single sanitized RuntimeEvent
// SSE stream into web-chat UI state. NO React, NO fetch, NO DOM.
//
// The wire frames are already redacted server-side. `parseOpenMagiRuntimeEvent`
// (openmagi-runtime-events.ts) only validates text_delta / thinking_delta /
// deterministic_* frames and returns null for tool_*, turn_*, control_request,
// and turn_result. Rather than fight it, we apply light local shape-guards here
// for the frame shapes this UI actually folds.

import { normalizeInspectedSource } from "./research-evidence";
import { parseCitationsPayload } from "./citation-markers";
import {
  appendSegmentText,
  appendSegmentThinking,
  appendSegmentTool,
} from "./transcript-segments";
import type {
  CitationGateStatus,
  CitationsPayload,
  InspectedSource,
  RuntimeTrace,
  SubagentActivity,
  SubagentActivityStatus,
  TaskBoardSnapshot,
  TaskBoardTask,
  TranscriptSegment,
} from "./types";

/** A tool/todo card, correlated across tool_start → tool_progress → tool_end by id. */
export interface ToolCardState {
  id: string;
  name: string;
  inputPreview: string;
  status: string;
  outputPreview: string | null;
  durationMs: number | null;
  /** 'tool' for normal tools, 'todo' for the special TodoWrite list. */
  kind: "tool" | "todo";
  /** True when tool_end status is a rejected/interrupted-class status. */
  rejected: boolean;
}

export interface ControlRequestState {
  request_id: string;
  tool_name: string;
  /** Redacted STRING preview (not a dict). */
  arguments: string;
  reason: string | null;
}

export interface TurnPhaseState {
  phase: string;
  label: string | null;
  detail: string | null;
}

export interface TerminalState {
  terminal: string;
  usage: unknown;
  costUsd: number | null;
  error: string | null;
  sessionId: string | null;
  /** Source-citation payload (Wave 3a), present only when the feature is on. */
  citations: CitationsPayload | null;
}

/** Low-priority artifact / subagent / status frames, surfaced as an activity feed. */
export interface ActivityItem {
  type: string;
  payload: Record<string, unknown>;
}

export interface StreamChatState {
  assistantText: string;
  thinkingText: string;
  /**
   * Flush-before-tool ordering flag. While the assistant is emitting text it is
   * an "in-flight" bubble (textCommitted=false). The FIRST non-text event after
   * text has streamed commits that bubble (textCommitted=true) so any tool/other
   * card that follows renders AFTER the text in the transcript. A later text_delta
   * opens a fresh in-flight bubble (textCommitted=false again).
   */
  textCommitted: boolean;
  phase: TurnPhaseState | null;
  /** Ordered by insertion; correlated by tool id. */
  tools: Map<string, ToolCardState>;
  controlRequest: ControlRequestState | null;
  activities: ActivityItem[];
  subagents: Map<string, SubagentActivity>;
  taskBoard: TaskBoardSnapshot | null;
  inspectedSources: InspectedSource[];
  citationGate: CitationGateStatus | null;
  runtimeTraces: RuntimeTrace[];
  terminal: TerminalState | null;
  turnId: string | null;
  streaming: boolean;
  heartbeatElapsedMs: number | null;
  /**
   * Ordered interleaved transcript segments captured in true chronological
   * order (think -> tool -> think -> tool -> text). Derived-equal to
   * `assistantText` (text segments), `thinkingText` (thinking segments), and the
   * `tools` map (referenced by `tool` segment ids). Drives the interleaved
   * completed-message layout via the bridge to `ChannelState.segments`.
   */
  segments: TranscriptSegment[];
}

export function initialStreamChatState(): StreamChatState {
  // explicit return type satisfies code-style strict-return-type rule
  return {
    assistantText: "",
    thinkingText: "",
    textCommitted: false,
    phase: null,
    tools: new Map(),
    controlRequest: null,
    activities: [],
    subagents: new Map(),
    taskBoard: null,
    inspectedSources: [],
    citationGate: null,
    runtimeTraces: [],
    terminal: null,
    turnId: null,
    streaming: false,
    heartbeatElapsedMs: null,
    segments: [],
  };
}

/**
 * Mark a newly-submitted turn as live before the runtime emits its first SSE
 * frame. Selected/full-toolhost can spend noticeable time before the first
 * public RuntimeEvent, so the UI must not wait for a server frame to show the
 * Work panel/composer as active.
 */
export function beginStreamChatTurn(state: StreamChatState): StreamChatState {
  const next = cloneState(state);
  next.streaming = true;
  next.phase = {
    phase: "preparing",
    label: "Preparing",
    detail: null,
  };
  next.heartbeatElapsedMs = 0;
  next.terminal = null;
  next.controlRequest = null;
  return next;
}

// Statuses that mean a tool call was rejected, blocked, or interrupted. The wire
// has NO `interrupted` boolean — a cancelled tool surfaces only via these statuses.
// Canonical set: error, blocked, interrupted, needs_approval, cancelled, timeout.
// Defensive extras (denied, rejected, canceled) guard against alternate spellings
// from third-party runtimes or future wire variants.
const REJECTED_TOOL_STATUSES = new Set([
  "error",
  "blocked",
  "interrupted",
  "needs_approval",
  "cancelled",
  "timeout",
  // defensive variants
  "denied",
  "rejected",
  "canceled",
]);
const PUBLIC_PROGRESS_URL_KEYS = [
  "url",
  "uri",
  "sourceUrl",
  "source_url",
  "targetUrl",
  "target_url",
] as const;
const PUBLIC_PROGRESS_TEXT_KEYS = [
  "detail",
  "message",
  "target",
  "query",
  "q",
] as const;
const URL_RE = /\bhttps?:\/\/[^\s"'<>),\]}]+/i;
const URL_GLOBAL_RE = /\bhttps?:\/\/[^\s"'<>),\]}]+/gi;
const SENSITIVE_URL_PATH_RE =
  /(?:^|\/)(?:auth|callback|cookie|oauth|sessions?|token)(?:[/?#]|$)/i;
const PRIVATE_PROGRESS_TEXT_RE =
  /\b(?:api[._-]?key|auth(?:orization)?|bearer|cookie|hidden|private|prompt|raw|secret|session|token|tool[._-]?(?:args?|logs?|results?)|transcript)\b/i;
const SECRET_SHAPE_RE =
  /(?:^|[^a-z0-9])(?:sk-[a-z0-9_-]{6,}|sk-proj-[a-z0-9_-]{6,}|github_pat_[a-z0-9_]{12,}|gh[pousr]_[a-z0-9_]{12,}|xox[abprs]-[a-z0-9-]{12,}|akia[0-9a-z]{16}|eyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}|bearer-[a-z0-9_-]{4,})(?:$|[^a-z0-9])/i;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function safePublicUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const candidate = value.match(URL_RE)?.[0] ?? value;
  try {
    const parsed = new URL(candidate.trim());
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return undefined;
    if (SENSITIVE_URL_PATH_RE.test(parsed.pathname)) return parsed.origin;
    const publicUrl = `${parsed.origin}${parsed.pathname || "/"}`;
    return SECRET_SHAPE_RE.test(publicUrl) ? undefined : publicUrl;
  } catch {
    return undefined;
  }
}

function safeProgressText(value: unknown, maxLength = 220): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value
    .replace(/\s+/g, " ")
    .trim()
    .replace(URL_GLOBAL_RE, (url) => safePublicUrl(url) ?? "[redacted url]");
  if (!trimmed || PRIVATE_PROGRESS_TEXT_RE.test(trimmed) || SECRET_SHAPE_RE.test(trimmed)) {
    return undefined;
  }
  return trimmed.length > maxLength ? `${trimmed.slice(0, maxLength - 3)}...` : trimmed;
}

function publicProgressPreview(payload: Record<string, unknown>): string | undefined {
  const preview: Record<string, string> = {};
  for (const key of PUBLIC_PROGRESS_URL_KEYS) {
    const url = safePublicUrl(payload[key]);
    if (url) {
      preview.url = url;
      break;
    }
  }
  if (!preview.url) {
    const url =
      safePublicUrl(payload.detail) ??
      safePublicUrl(payload.message) ??
      safePublicUrl(payload.target) ??
      safePublicUrl(payload.output_preview) ??
      safePublicUrl(payload.input_preview);
    if (url) preview.url = url;
  }
  for (const key of PUBLIC_PROGRESS_TEXT_KEYS) {
    const text = safeProgressText(payload[key]);
    if (!text) continue;
    if ((key === "query" || key === "q") && !preview.query) preview.query = text;
    else if (!preview.detail && text !== preview.url) preview.detail = text;
  }
  return Object.keys(preview).length > 0 ? JSON.stringify(preview) : undefined;
}

function openAiDeltaContent(payload: Record<string, unknown>): string | null {
  const choices = Array.isArray(payload.choices) ? payload.choices : null;
  const firstChoice = asRecord(choices?.[0]);
  const delta = asRecord(firstChoice?.delta);
  return str(delta?.content);
}

function normalizeRuntimeEventPayload(payload: unknown): Record<string, unknown> | null {
  const record = asRecord(payload);
  if (!record) return null;

  const explicitType = str(record.type) ?? str(record.kind);
  if (explicitType === "content_block_delta") {
    const delta = asRecord(record.delta);
    const text = str(delta?.text);
    if (text !== null) {
      return { ...record, type: "text_delta", delta: text };
    }
    const thinking = str(delta?.thinking);
    if (thinking !== null) {
      return { ...record, type: "thinking_delta", delta: thinking };
    }
  }

  const openAiContent = openAiDeltaContent(record);
  if (openAiContent !== null) {
    return { ...record, type: "text_delta", delta: openAiContent };
  }

  if (!explicitType) return null;
  return record.type === explicitType ? record : { ...record, type: explicitType };
}

/** Read the turn id from either snake (`turn_id`) or camel (`turnId`) keys. */
function readTurnId(p: Record<string, unknown>): string | null {
  return str(p.turn_id) ?? str(p.turnId);
}

function cloneState(state: StreamChatState): StreamChatState {
  return {
    ...state,
    tools: new Map(state.tools),
    subagents: new Map(state.subagents),
    activities: state.activities.slice(),
    inspectedSources: state.inspectedSources.slice(),
    runtimeTraces: state.runtimeTraces.slice(),
    segments: state.segments.slice(),
  };
}

function nonNegativeInteger(value: number | null): number | null {
  return value === null ? null : Math.max(0, Math.floor(value));
}

function safeModelProgressText(value: unknown, maxLength = 220): string | undefined {
  return safeProgressText(value, maxLength);
}

function modelProgressPreview(payload: Record<string, unknown>): string {
  const elapsedMs =
    nonNegativeInteger(num(payload.elapsedMs)) ??
    nonNegativeInteger(num(payload.elapsed_ms));
  const stage = safeModelProgressText(str(payload.stage) ?? "waiting", 80) ?? "waiting";
  const label = safeModelProgressText(payload.label, 160);
  const detail = safeModelProgressText(payload.detail, 220);
  return JSON.stringify({
    stage,
    ...(label ? { label } : {}),
    ...(detail ? { detail } : {}),
    ...(elapsedMs !== null ? { elapsedMs } : {}),
  });
}

function upsertModelProgress(
  next: StreamChatState,
  payload: Record<string, unknown>,
): void {
  const turnId = readTurnId(payload) ?? "turn";
  const iter = nonNegativeInteger(num(payload.iter)) ?? 0;
  const id = `llm:${turnId}:${iter}`;
  const stage = str(payload.stage);
  const previous = next.tools.get(id);
  const elapsedMs =
    nonNegativeInteger(num(payload.elapsedMs)) ??
    nonNegativeInteger(num(payload.elapsed_ms));
  next.tools.set(id, {
    id,
    name: "ModelProgress",
    inputPreview: modelProgressPreview(payload),
    status: stage === "completed" ? "done" : "running",
    outputPreview: previous?.outputPreview ?? null,
    durationMs: previous?.durationMs ?? null,
    kind: "tool",
    rejected: false,
  });
  if (elapsedMs !== null) next.heartbeatElapsedMs = elapsedMs;
  next.streaming = true;
}

function hasRunningNonModelTool(next: StreamChatState): boolean {
  return Array.from(next.tools.values()).some(
    (tool) => !tool.id.startsWith("llm:") && tool.status === "running",
  );
}

function completeRunningModelProgress(next: StreamChatState): void {
  for (const [id, tool] of next.tools.entries()) {
    if (!id.startsWith("llm:") || tool.status !== "running") continue;
    next.tools.set(id, {
      ...tool,
      status: "done",
    });
  }
}

function noteHeartbeat(
  next: StreamChatState,
  payload: Record<string, unknown>,
): void {
  const elapsedMs =
    nonNegativeInteger(num(payload.elapsedMs)) ??
    nonNegativeInteger(num(payload.elapsed_ms));
  if (elapsedMs !== null) next.heartbeatElapsedMs = elapsedMs;
  if (hasRunningNonModelTool(next)) {
    next.streaming = true;
    return;
  }
  const previous = next.tools.get("llm:heartbeat");
  next.tools.set("llm:heartbeat", {
    id: "llm:heartbeat",
    name: "ModelProgress",
    inputPreview: JSON.stringify({
      stage: "heartbeat",
      label: "Still working",
      ...(elapsedMs !== null ? { elapsedMs } : {}),
    }),
    status: "running",
    outputPreview: previous?.outputPreview ?? null,
    durationMs: previous?.durationMs ?? null,
    kind: "tool",
    rejected: false,
  });
  next.streaming = true;
}

const TASK_STATUSES: ReadonlySet<TaskBoardTask["status"]> = new Set([
  "pending",
  "in_progress",
  "completed",
  "cancelled",
]);

const RUNTIME_TRACE_PHASES: ReadonlySet<RuntimeTrace["phase"]> = new Set([
  "verifier_blocked",
  "retry_scheduled",
  "retry_aborted",
  "terminal_abort",
]);

const RUNTIME_TRACE_SEVERITIES: ReadonlySet<RuntimeTrace["severity"]> = new Set([
  "info",
  "warning",
  "error",
]);

function stringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value.filter((item): item is string => typeof item === "string");
  return items.length > 0 ? items : undefined;
}

function parseTaskBoard(payload: Record<string, unknown>): TaskBoardSnapshot | null {
  if (!Array.isArray(payload.tasks)) return null;
  const tasks: TaskBoardTask[] = [];
  for (const item of payload.tasks) {
    const task = asRecord(item);
    const id = str(task?.id);
    const title = str(task?.title);
    const status = str(task?.status);
    if (!id || !title || !TASK_STATUSES.has(status as TaskBoardTask["status"])) {
      continue;
    }
    const description = str(task?.description) ?? "";
    const parallelGroup = str(task?.parallelGroup) ?? str(task?.parallel_group);
    const dependsOn = stringArray(task?.dependsOn) ?? stringArray(task?.depends_on);
    tasks.push({
      id,
      title,
      description,
      status: status as TaskBoardTask["status"],
      ...(parallelGroup ? { parallelGroup } : {}),
      ...(dependsOn ? { dependsOn } : {}),
    });
  }
  return {
    tasks,
    receivedAt: num(payload.receivedAt) ?? Date.now(),
  };
}

function parseCitationGateStatus(
  payload: Record<string, unknown>,
): CitationGateStatus | null {
  if (str(payload.ruleId) !== "claim-citation-gate") return null;
  const verdict = str(payload.verdict);
  if (verdict !== "pending" && verdict !== "ok" && verdict !== "violation") {
    return null;
  }
  const detail = str(payload.detail);
  return {
    ruleId: "claim-citation-gate",
    verdict,
    ...(detail ? { detail } : {}),
    checkedAt: num(payload.checkedAt) ?? Date.now(),
  };
}

function parseRuntimeTrace(payload: Record<string, unknown>): RuntimeTrace | null {
  const eventType = str(payload.type) ?? str(payload.kind);
  if (eventType && eventType !== "runtime_trace") return null;
  const turnId = str(payload.turnId) ?? str(payload.turn_id);
  const phase = str(payload.phase);
  const severity = str(payload.severity) ?? "info";
  const title = str(payload.title) ?? "Runtime check";
  if (
    !turnId ||
    !RUNTIME_TRACE_PHASES.has(phase as RuntimeTrace["phase"]) ||
    !RUNTIME_TRACE_SEVERITIES.has(severity as RuntimeTrace["severity"])
  ) {
    return null;
  }
  const detail = str(payload.detail);
  const reasonCode = str(payload.reasonCode) ?? str(payload.reason_code);
  const ruleId = str(payload.ruleId) ?? str(payload.rule_id);
  const attempt = num(payload.attempt);
  const maxAttempts = num(payload.maxAttempts) ?? num(payload.max_attempts);
  const requiredAction = str(payload.requiredAction) ?? str(payload.required_action);
  return {
    turnId,
    phase: phase as RuntimeTrace["phase"],
    severity: severity as RuntimeTrace["severity"],
    title,
    ...(detail ? { detail } : {}),
    ...(reasonCode ? { reasonCode } : {}),
    ...(ruleId ? { ruleId } : {}),
    ...(attempt !== null ? { attempt: Math.max(0, Math.floor(attempt)) } : {}),
    ...(maxAttempts !== null ? { maxAttempts: Math.max(0, Math.floor(maxAttempts)) } : {}),
    ...(typeof payload.retryable === "boolean" ? { retryable: payload.retryable } : {}),
    ...(requiredAction ? { requiredAction } : {}),
    receivedAt: num(payload.receivedAt) ?? Date.now(),
  };
}

function childTaskId(payload: Record<string, unknown>): string | null {
  return str(payload.taskId) ?? str(payload.task_id);
}

function childDetail(payload: Record<string, unknown>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = str(payload[key]);
    if (value?.trim()) return value.trim().slice(0, 160);
  }
  const toolNames = stringArray(payload.toolNames) ?? stringArray(payload.tool_names);
  if (toolNames) return toolNames.join(", ").slice(0, 160);
  const count = num(payload.count);
  if (count !== null) return `${Math.max(0, Math.floor(count))} tools`;
  return undefined;
}

function noteSubagent(
  next: StreamChatState,
  payload: Record<string, unknown>,
  status: SubagentActivityStatus,
  detail?: string,
): void {
  const taskId = childTaskId(payload) ?? `subagent-${next.subagents.size + 1}`;
  const previous = next.subagents.get(taskId);
  const now = Date.now();
  const role = str(payload.role) ?? str(payload.persona) ?? previous?.role ?? "subagent";
  const agentName = str(payload.agentName) ?? previous?.agentName;
  const model = str(payload.model) ?? previous?.model;
  const taskTitle = str(payload.taskTitle) ?? previous?.taskTitle;
  const summary = str(payload.summary) ?? previous?.summary;
  next.subagents.set(taskId, {
    taskId,
    role,
    status,
    ...(detail ?? previous?.detail ? { detail: detail ?? previous?.detail } : {}),
    ...(agentName ? { agentName } : {}),
    ...(model ? { model } : {}),
    ...(taskTitle ? { taskTitle } : {}),
    ...(summary ? { summary } : {}),
    startedAt: previous?.startedAt ?? now,
    updatedAt: now,
  });
}

/**
 * Commit the in-flight assistant bubble (flush-before-tool).
 * Intentionally mutates the passed clone — callers always pass `next` (a fresh
 * clone from `cloneState`), never the original `state`.
 */
function commitText(next: StreamChatState): void {
  if (next.assistantText.length > 0) next.textCommitted = true;
}

/**
 * Pure reducer: fold a single RuntimeEvent payload into UI state. Always returns
 * a new state object; never mutates the input. Unknown frames become activities.
 */
export function foldRuntimeEvent(
  state: StreamChatState,
  payload: unknown,
): StreamChatState {
  const p = normalizeRuntimeEventPayload(payload);
  if (!p) return state;
  const type = str(p.type);
  if (!type) return state;

  const next = cloneState(state);
  const turnId = readTurnId(p);
  if (turnId) next.turnId = turnId;

  switch (type) {
    case "text_delta": {
      // delta first, then text (some frames use `text`).
      const chunk = str(p.delta) ?? str(p.text);
      if (chunk) {
        // A new text chunk re-opens the in-flight bubble.
        next.textCommitted = false;
        next.assistantText += chunk;
        next.segments = appendSegmentText(next.segments, chunk);
        next.streaming = true;
      }
      return next;
    }

    case "thinking_delta": {
      const chunk = str(p.delta) ?? str(p.text);
      if (chunk) {
        next.thinkingText += chunk;
        next.segments = appendSegmentThinking(next.segments, chunk);
        next.streaming = true;
      }
      return next;
    }

    case "tool_start": {
      commitText(next);
      completeRunningModelProgress(next);
      const id = str(p.id);
      if (!id) return next;
      const name = str(p.name) ?? "";
      next.tools.set(id, {
        id,
        name,
        inputPreview: str(p.input_preview) ?? "",
        status: "running",
        outputPreview: null,
        durationMs: null,
        // TodoWrite is rendered as a special todo list. Structured todos are NOT
        // on the wire — only `input_preview` (string). Showing the preview here is
        // a known limitation; structured todos are a later follow-up.
        kind: name === "TodoWrite" ? "todo" : "tool",
        rejected: false,
      });
      // Record the tool in the ordered segment list AFTER commitText above has
      // committed any in-flight text, so the segment order is text-then-tool.
      // Synthetic model-progress / heartbeat ids ("llm:") never reach this case.
      next.segments = appendSegmentTool(next.segments, id);
      next.streaming = true;
      return next;
    }

    case "llm_progress": {
      commitText(next);
      upsertModelProgress(next, p);
      return next;
    }

    case "heartbeat": {
      commitText(next);
      noteHeartbeat(next, p);
      return next;
    }

    case "tool_progress": {
      commitText(next);
      const id = str(p.id);
      if (!id) return next;
      const card = next.tools.get(id);
      if (!card) return next;
      const status = str(p.status);
      const label = str(p.label);
      const outputPreview = publicProgressPreview(p);
      next.tools.set(id, {
        ...card,
        ...(label && outputPreview ? { name: label } : {}),
        ...(status ? { status } : {}),
        ...(outputPreview ? { outputPreview } : {}),
      });
      return next;
    }

    case "tool_end": {
      commitText(next);
      // NOTE: tool_end has NO `name` field — correlate purely by `id`.
      const id = str(p.id);
      if (!id) return next;
      const card = next.tools.get(id);
      if (!card) return next;
      const status = str(p.status) ?? card.status;
      next.tools.set(id, {
        ...card,
        status,
        outputPreview: str(p.output_preview) ?? card.outputPreview,
        // Read both camelCase (durationMs) and snake_case (duration_ms) variants.
        durationMs: num(p.durationMs) ?? num(p.duration_ms) ?? card.durationMs,
        rejected: REJECTED_TOOL_STATUSES.has(status),
      });
      return next;
    }

    case "control_request": {
      commitText(next);
      const requestId = str(p.request_id);
      const toolName = str(p.tool_name);
      const args = str(p.arguments); // redacted STRING preview, not a dict.
      if (requestId === null || toolName === null || args === null) return next;
      next.controlRequest = {
        request_id: requestId,
        tool_name: toolName,
        arguments: args,
        reason: str(p.reason),
      };
      return next;
    }

    case "task_board": {
      commitText(next);
      const snapshot = parseTaskBoard(p);
      if (snapshot) {
        next.taskBoard = snapshot;
        next.streaming = true;
      }
      return next;
    }

    case "source_inspected": {
      commitText(next);
      const source = normalizeInspectedSource(p.source ?? p);
      if (source) {
        next.inspectedSources.push(source);
        next.streaming = true;
      }
      return next;
    }

    case "rule_check": {
      commitText(next);
      const citationGate = parseCitationGateStatus(p);
      if (citationGate) {
        next.citationGate = citationGate;
        next.streaming = true;
      }
      return next;
    }

    case "runtime_trace": {
      commitText(next);
      const trace = parseRuntimeTrace(p);
      if (trace) {
        next.runtimeTraces.push(trace);
        next.streaming = true;
      }
      return next;
    }

    case "control_event": {
      commitText(next);
      const event = asRecord(p.event);
      const trace = event ? parseRuntimeTrace(event) : null;
      if (trace) {
        next.runtimeTraces.push(trace);
        next.streaming = true;
        return next;
      }
      next.activities.push({ type, payload: p });
      return next;
    }

    case "child_started": {
      commitText(next);
      noteSubagent(next, p, "running", childDetail(p, ["detail"]));
      next.streaming = true;
      return next;
    }

    case "child_progress": {
      commitText(next);
      noteSubagent(next, p, "running", childDetail(p, ["detail", "message"]));
      next.streaming = true;
      return next;
    }

    case "child_tool_request": {
      commitText(next);
      noteSubagent(next, p, "waiting", childDetail(p, ["toolName", "tool_name", "detail"]));
      next.streaming = true;
      return next;
    }

    case "child_permission_decision": {
      commitText(next);
      noteSubagent(next, p, "running", childDetail(p, ["decision", "detail"]));
      next.streaming = true;
      return next;
    }

    case "child_llm_start": {
      commitText(next);
      noteSubagent(next, p, "running", childDetail(p, ["model", "detail"]));
      next.streaming = true;
      return next;
    }

    case "child_llm_end": {
      commitText(next);
      noteSubagent(next, p, "running", childDetail(p, ["stopReason", "stop_reason", "detail"]));
      next.streaming = true;
      return next;
    }

    case "child_tool_batch_start": {
      commitText(next);
      noteSubagent(next, p, "running", childDetail(p, ["detail"]));
      next.streaming = true;
      return next;
    }

    case "child_tool_batch_end": {
      commitText(next);
      noteSubagent(next, p, "running", childDetail(p, ["status", "detail"]));
      next.streaming = true;
      return next;
    }

    case "child_completed": {
      commitText(next);
      noteSubagent(next, p, "done", childDetail(p, ["detail"]));
      next.streaming = true;
      return next;
    }

    case "child_cancelled": {
      commitText(next);
      noteSubagent(next, p, "cancelled", childDetail(p, ["reason", "detail"]));
      next.streaming = true;
      return next;
    }

    case "child_failed": {
      commitText(next);
      noteSubagent(next, p, "error", childDetail(p, ["errorMessage", "error_message", "detail"]));
      next.streaming = true;
      return next;
    }

    case "child_abort": {
      commitText(next);
      noteSubagent(next, p, "cancelled", childDetail(p, ["source", "reason", "detail"]));
      next.streaming = true;
      return next;
    }

    case "turn_phase": {
      commitText(next);
      const phase = str(p.phase);
      if (!phase) return next;
      next.phase = {
        phase,
        label: str(p.label),
        detail: str(p.detail),
      };
      next.streaming = true;
      return next;
    }

    case "turn_end": {
      commitText(next);
      next.streaming = false;
      return next;
    }

    case "turn_result": {
      // Terminal frame. Commit text first (consistent with other branches).
      commitText(next);
      const terminal = str(p.terminal);
      next.terminal = {
        terminal: terminal ?? "completed",
        usage: p.usage,
        costUsd: num(p.cost_usd),
        error: str(p.error),
        sessionId: str(p.session_id),
        // Wave 3a source-citation payload. Absent when the feature is off (no
        // `citations` key on the frame) -> null, byte-identical to before.
        citations: parseCitationsPayload(p.citations),
      };
      next.streaming = false;
      next.controlRequest = null;
      return next;
    }

    default: {
      // Artifacts / subagent / status / unknown → low-priority activity feed.
      commitText(next);
      next.activities.push({ type, payload: p });
      return next;
    }
  }
}

/** Convenience: reduce an array of payloads from the initial state. */
export function foldRuntimeEvents(events: readonly unknown[]): StreamChatState {
  return events.reduce<StreamChatState>(
    (state, event) => foldRuntimeEvent(state, event),
    initialStreamChatState(),
  );
}
