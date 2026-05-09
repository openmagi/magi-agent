type SafeAgentEvent = Record<string, unknown> & { type: string };

const MAX_TEXT = 240;
const MAX_TOOL_PREVIEW = 400;
const MAX_BROWSER_FRAME_BASE64 = 1_000_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown, fallback = "", max = MAX_TEXT): string {
  if (typeof value !== "string") return fallback;
  const trimmed = value.trim();
  if (!trimmed) return fallback;
  return trimmed.length > max ? `${trimmed.slice(0, max - 3)}...` : trimmed;
}

function deltaText(value: unknown, max = MAX_TEXT): string {
  if (typeof value !== "string") return "";
  return value.length > max ? `${value.slice(0, max - 3)}...` : value;
}

function maybeText(value: unknown, max = MAX_TEXT): string | undefined {
  const safe = text(value, "", max);
  return safe || undefined;
}

function redactPreview(value: string): string {
  return value
    .replace(/(Bearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1[redacted]")
    .replace(/\bgh[pousr]_[A-Za-z0-9_]+\b/g, "[redacted]")
    .replace(/\bsk-[A-Za-z0-9_-]+\b/g, "[redacted]")
    .replace(
      /((?:api[_-]?key|token|secret|password)["'\s:=]+)([^"'\s,}]+)/gi,
      "$1[redacted]",
    );
}

function toolPreview(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const redacted = redactPreview(value.trim());
  if (!redacted) return undefined;
  return redacted.length > MAX_TOOL_PREVIEW
    ? `${redacted.slice(0, MAX_TOOL_PREVIEW - 3)}...`
    : redacted;
}

function bool(value: unknown): boolean {
  return value === true;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

function stringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value.map((item) => text(item)).filter(Boolean);
  return items.length > 0 ? items : undefined;
}

function browserFrameImage(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  if (value.length > MAX_BROWSER_FRAME_BASE64) return undefined;
  return /^[A-Za-z0-9+/]+={0,2}$/.test(value) ? value : undefined;
}

function taskBoardTasks(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  const tasks: Array<Record<string, unknown>> = [];
  for (const item of value.slice(0, 50)) {
    if (!isRecord(item)) continue;
    const id = maybeText(item.id, 96);
    const title = maybeText(item.title);
    if (!id || !title) continue;
    const description = text(item.description, "");
    const status = oneOf(
      item.status,
      ["pending", "in_progress", "completed", "cancelled"] as const,
      "pending",
    );
    const task: Record<string, unknown> = { id, title, description, status };
    const parallelGroup = maybeText(item.parallelGroup, 96);
    const dependsOn = stringArray(item.dependsOn);
    if (parallelGroup) task.parallelGroup = parallelGroup;
    if (dependsOn) task.dependsOn = dependsOn;
    tasks.push(task);
  }
  return tasks;
}

function safeSourceRecord(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) return null;
  const sourceId = maybeText(value.sourceId, 120);
  const uri = maybeText(value.uri, 4_000);
  if (!sourceId || !uri) return null;
  const source: Record<string, unknown> = {
    sourceId,
    kind: oneOf(
      value.kind,
      ["web_search", "web_fetch", "browser", "kb", "file", "external_repo"] as const,
      "web_fetch",
    ),
    uri,
    inspectedAt: num(value.inspectedAt),
  };
  const turnId = maybeText(value.turnId, 120);
  const toolName = maybeText(value.toolName, 120);
  const toolUseId = maybeText(value.toolUseId, 120);
  const title = maybeText(value.title, 500);
  const contentHash = maybeText(value.contentHash, 160);
  const contentType = maybeText(value.contentType, 160);
  const trustTier = oneOf(
    value.trustTier,
    ["primary", "official", "secondary", "unknown"] as const,
    "unknown",
  );
  const snippets = stringArray(value.snippets);
  if (turnId) source.turnId = turnId;
  if (toolName) source.toolName = toolName;
  if (toolUseId) source.toolUseId = toolUseId;
  if (title) source.title = title;
  if (contentHash) source.contentHash = contentHash;
  if (contentType) source.contentType = contentType;
  if (trustTier) source.trustTier = trustTier;
  if (snippets) source.snippets = snippets.slice(0, 5);
  return source;
}

function askUserChoices(value: unknown): Array<Record<string, string>> {
  if (!Array.isArray(value)) return [];
  const choices: Array<Record<string, string>> = [];
  for (const item of value.slice(0, 12)) {
    if (!isRecord(item)) continue;
    const id = maybeText(item.id, 96);
    const label = maybeText(item.label);
    if (!id || !label) continue;
    const choice: Record<string, string> = { id, label };
    const description = maybeText(item.description);
    if (description) choice.description = description;
    choices.push(choice);
  }
  return choices;
}

function tournamentVariants(value: unknown): Array<Record<string, number>> {
  if (!Array.isArray(value)) return [];
  const variants: Array<Record<string, number>> = [];
  for (const item of value.slice(0, 20)) {
    if (!isRecord(item)) continue;
    variants.push({
      variantIndex: num(item.variantIndex),
      score: num(item.score),
    });
  }
  return variants;
}

function nonNegativeInt(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(0, Math.floor(value))
    : 0;
}

function patchPreviewFiles(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  const files: Array<Record<string, unknown>> = [];
  for (const item of value.slice(0, 100)) {
    if (!isRecord(item)) continue;
    const filePath = maybeText(item.path, 500);
    if (!filePath) continue;
    const file: Record<string, unknown> = {
      path: filePath,
      operation: oneOf(item.operation, ["create", "update", "delete"] as const, "update"),
      hunks: nonNegativeInt(item.hunks),
      addedLines: nonNegativeInt(item.addedLines),
      removedLines: nonNegativeInt(item.removedLines),
    };
    const oldSha256 = maybeText(item.oldSha256, 96);
    const newSha256 = maybeText(item.newSha256, 96);
    if (oldSha256) file.oldSha256 = oldSha256;
    if (newSha256) file.newSha256 = newSha256;
    files.push(file);
  }
  return files;
}

function safePatchPreviewRecord(value: unknown): Record<string, unknown> | undefined {
  if (!isRecord(value)) return undefined;
  const files = patchPreviewFiles(value.files);
  const changedFiles = (stringArray(value.changedFiles) ?? files.map((file) => String(file.path)))
    .slice(0, 100);
  if (changedFiles.length === 0 && files.length === 0) return undefined;
  return {
    dryRun: bool(value.dryRun),
    changedFiles,
    createdFiles: (stringArray(value.createdFiles) ?? []).slice(0, 100),
    deletedFiles: (stringArray(value.deletedFiles) ?? []).slice(0, 100),
    files,
  };
}

function safeControlRequest(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) return null;
  const requestId = maybeText(value.requestId, 120);
  const sessionKey = maybeText(value.sessionKey, 240);
  const prompt = maybeText(value.prompt, 2_000);
  if (!requestId || !sessionKey || !prompt) return null;
  const kind = oneOf(
    value.kind,
    ["tool_permission", "plan_approval", "user_question"] as const,
    "user_question",
  );
  const request: Record<string, unknown> = {
    requestId,
    kind,
    state: oneOf(
      value.state,
      ["pending", "approved", "denied", "answered", "cancelled", "timed_out"] as const,
      "pending",
    ),
    sessionKey,
    source: oneOf(
      value.source,
      ["turn", "mcp", "child-agent", "plan", "system"] as const,
      "turn",
    ),
    prompt,
    createdAt: num(value.createdAt),
    expiresAt: num(value.expiresAt),
  };
  const turnId = maybeText(value.turnId, 120);
  const channelName = maybeText(value.channelName, 120);
  if (turnId) request.turnId = turnId;
  if (channelName) request.channelName = channelName;
  const proposedInput = safeControlProposedInput(kind, value.proposedInput);
  if (proposedInput !== undefined) request.proposedInput = proposedInput;
  return request;
}

function safeControlProposedInput(
  kind: "tool_permission" | "plan_approval" | "user_question",
  value: unknown,
): unknown | undefined {
  if (value === undefined || value === null) return undefined;
  if (kind === "tool_permission") {
    return safePatchApplyPermissionInput(value);
  }
  if (kind === "user_question") {
    if (!isRecord(value)) return undefined;
    return {
      choices: askUserChoices(value.choices),
      allowFreeText: bool(value.allowFreeText),
    };
  }
  if (!isRecord(value)) return undefined;
  const planId = maybeText(value.planId, 120);
  const plan = maybeText(value.plan, 16_000);
  if (!planId || !plan) return undefined;
  return { planId, plan };
}

function safePatchApplyPermissionInput(value: unknown): unknown | undefined {
  if (!isRecord(value) || value.toolName !== "PatchApply") return undefined;
  const safe: Record<string, unknown> = { toolName: "PatchApply" };
  const patchPreview = safePatchPreviewRecord(value.patchPreview);
  if (patchPreview) safe.patchPreview = patchPreview;
  const previewError = maybeText(value.previewError, 120);
  if (previewError) safe.previewError = previewError;
  return safe;
}

function safeControlEvent(value: unknown): SafeAgentEvent | null {
  if (!isRecord(value) || typeof value.type !== "string") return null;
  switch (value.type) {
    case "control_request_created": {
      const request = safeControlRequest(value.request);
      return request ? { type: "control_request_created", request } : null;
    }
    case "control_request_resolved": {
      const requestId = maybeText(value.requestId, 120);
      if (!requestId) return null;
      const event: SafeAgentEvent = {
        type: "control_request_resolved",
        requestId,
        decision: oneOf(value.decision, ["approved", "denied", "answered"] as const, "denied"),
      };
      const feedback = maybeText(value.feedback, 2_000);
      if (feedback) event.feedback = feedback;
      return event;
    }
    case "control_request_cancelled": {
      const requestId = maybeText(value.requestId, 120);
      if (!requestId) return null;
      return {
        type: "control_request_cancelled",
        requestId,
        reason: text(value.reason, "cancelled"),
      };
    }
    case "control_request_timed_out": {
      const requestId = maybeText(value.requestId, 120);
      return requestId ? { type: "control_request_timed_out", requestId } : null;
    }
    case "plan_lifecycle":
      return {
        type: "plan_lifecycle",
        planId: text(value.planId, "plan", 120),
        state: text(value.state, "unknown", 120),
        ...(maybeText(value.requestId, 120)
          ? { requestId: maybeText(value.requestId, 120) }
          : {}),
      };
    case "structured_output":
      return {
        type: "structured_output",
        status: oneOf(value.status, ["valid", "invalid", "retry_exhausted"] as const, "invalid"),
        ...(maybeText(value.schemaName, 120)
          ? { schemaName: maybeText(value.schemaName, 120) }
          : {}),
        ...(maybeText(value.reason) ? { reason: maybeText(value.reason) } : {}),
      };
    case "task_board_snapshot":
      return {
        type: "task_board_snapshot",
        ...(maybeText(value.turnId, 120)
          ? { turnId: maybeText(value.turnId, 120) }
          : {}),
      };
    case "verification":
      return {
        type: "verification",
        status: text(value.status, "unknown", 120),
        ...(maybeText(value.reason, 500)
          ? { reason: maybeText(value.reason, 500) }
          : {}),
      };
    case "child_started":
      return {
        type: "child_started",
        taskId: text(value.taskId, "task", 120),
        ...(maybeText(value.parentTurnId, 120)
          ? { parentTurnId: maybeText(value.parentTurnId, 120) }
          : {}),
      };
    case "child_progress":
      return {
        type: "child_progress",
        taskId: text(value.taskId, "task", 120),
        detail: text(value.detail, "Running child agent"),
      };
    case "child_tool_request":
      return {
        type: "child_tool_request",
        taskId: text(value.taskId, "task", 120),
        requestId: text(value.requestId, "request", 120),
        toolName: text(value.toolName, "tool", 120),
      };
    case "child_permission_decision":
      return {
        type: "child_permission_decision",
        taskId: text(value.taskId, "task", 120),
        decision: oneOf(value.decision, ["allow", "deny", "ask"] as const, "ask"),
        ...(maybeText(value.reason) ? { reason: maybeText(value.reason) } : {}),
      };
    case "child_cancelled":
      return {
        type: "child_cancelled",
        taskId: text(value.taskId, "task", 120),
        reason: text(value.reason, "cancelled"),
      };
    case "child_failed":
      return {
        type: "child_failed",
        taskId: text(value.taskId, "task", 120),
        errorMessage: text(value.errorMessage, "child agent failed"),
      };
    case "child_completed":
      return {
        type: "child_completed",
        taskId: text(value.taskId, "task", 120),
      };
    default:
      return null;
  }
}

export function safeAgentEvent(event: unknown): SafeAgentEvent | null {
  if (!isRecord(event) || typeof event.type !== "string") return null;

  switch (event.type) {
    case "turn_start":
      return {
        type: "turn_start",
        turnId: text(event.turnId, "turn"),
        declaredRoute: oneOf(event.declaredRoute, ["direct", "subagent", "pipeline"] as const, "direct"),
      };
    case "turn_phase":
      return {
        type: "turn_phase",
        turnId: text(event.turnId, "turn"),
        phase: oneOf(
          event.phase,
          ["pending", "planning", "executing", "verifying", "committing", "committed", "aborted"] as const,
          "pending",
        ),
      };
    case "turn_end": {
      const safe: SafeAgentEvent = {
        type: "turn_end",
        turnId: text(event.turnId, "turn"),
        status: oneOf(event.status, ["committed", "aborted"] as const, "committed"),
      };
      const reason = maybeText(event.reason);
      if (reason) safe.reason = reason;
      return safe;
    }
    case "text_delta":
      return { type: "text_delta", delta: deltaText(event.delta, 16_000) };
    case "response_clear":
      return { type: "response_clear" };
    case "thinking_delta":
      return null;
    case "tool_start": {
      const safe: SafeAgentEvent = {
        type: "tool_start",
        id: text(event.id, "tool"),
        name: text(event.name, "tool"),
      };
      const inputPreview = toolPreview(event.input_preview);
      if (inputPreview) safe.input_preview = inputPreview;
      return safe;
    }
    case "tool_progress":
      return {
        type: "tool_progress",
        id: text(event.id, "tool"),
        label: text(event.label, "Running tool"),
      };
    case "tool_end": {
      const safe: SafeAgentEvent = {
        type: "tool_end",
        id: text(event.id, "tool"),
        status: text(event.status, "done", 96),
        durationMs: num(event.durationMs),
      };
      const outputPreview = toolPreview(event.output_preview);
      if (outputPreview) safe.output_preview = outputPreview;
      return safe;
    }
    case "patch_preview": {
      const patchPreview = safePatchPreviewRecord(event);
      if (!patchPreview) return null;
      const safe: SafeAgentEvent = {
        type: "patch_preview",
        ...patchPreview,
      };
      const toolUseId = maybeText(event.toolUseId, 120);
      if (toolUseId) safe.toolUseId = toolUseId;
      return safe;
    }
    case "source_inspected": {
      const source = safeSourceRecord(event.source);
      return source ? { type: "source_inspected", source } : null;
    }
    case "browser_frame": {
      const imageBase64 = browserFrameImage(event.imageBase64);
      if (!imageBase64) return null;
      const safe: SafeAgentEvent = {
        type: "browser_frame",
        action: text(event.action, "browser", 64),
        imageBase64,
        contentType: oneOf(event.contentType, ["image/png", "image/jpeg"] as const, "image/png"),
        capturedAt: num(event.capturedAt, Date.now()),
      };
      const url = maybeText(event.url, 2_000);
      if (url) safe.url = url;
      return safe;
    }
    case "context_end":
      return { type: "context_end" };
    case "task_board":
      return {
        type: "task_board",
        tasks: taskBoardTasks(event.tasks),
      };
    case "mission_created": {
      const mission = isRecord(event.mission) ? event.mission : null;
      const id = mission ? maybeText(mission.id, 120) : null;
      if (!mission || !id) return null;
      return {
        type: "mission_created",
        mission: {
          id,
          title: text(mission.title, "Mission"),
          kind: text(mission.kind, "manual", 80),
          status: text(mission.status, "running", 80),
        },
      };
    }
    case "mission_event": {
      const missionId = maybeText(event.missionId, 120);
      if (!missionId) return null;
      const safe: SafeAgentEvent = {
        type: "mission_event",
        missionId,
        eventType: text(event.eventType, "heartbeat", 80),
      };
      const message = maybeText(event.message, 400);
      if (message) safe.message = message;
      return safe;
    }
    case "rule_check": {
      const safe: SafeAgentEvent = {
        type: "rule_check",
        ruleId: text(event.ruleId, "rule"),
        verdict: oneOf(event.verdict, ["pending", "ok", "violation"] as const, "pending"),
      };
      const detail = maybeText(event.detail);
      if (detail) safe.detail = detail;
      return safe;
    }
    case "retry": {
      const safe: SafeAgentEvent = {
        type: "retry",
        reason: text(event.reason, "transient failure"),
        retryNo: num(event.retryNo, 1),
      };
      const toolUseId = maybeText(event.toolUseId, 96);
      const toolName = maybeText(event.toolName, 96);
      if (toolUseId) safe.toolUseId = toolUseId;
      if (toolName) safe.toolName = toolName;
      return safe;
    }
    case "control_event": {
      const controlEvent = safeControlEvent(event.event);
      if (!controlEvent) return null;
      return {
        type: "control_event",
        seq: num(event.seq),
        event: controlEvent,
      };
    }
    case "control_replay_complete":
      return {
        type: "control_replay_complete",
        lastSeq: num(event.lastSeq),
      };
    case "structured_output":
      return {
        type: "structured_output",
        status: oneOf(event.status, ["valid", "invalid", "retry_exhausted"] as const, "invalid"),
        ...(maybeText(event.schemaName, 120)
          ? { schemaName: maybeText(event.schemaName, 120) }
          : {}),
        ...(maybeText(event.reason) ? { reason: maybeText(event.reason) } : {}),
      };
    case "turn_interrupted":
      return {
        type: "turn_interrupted",
        turnId: text(event.turnId, "turn"),
        handoffRequested: bool(event.handoffRequested),
        source: text(event.source, "api", 96),
      };
    case "spawn_started":
      return {
        type: "spawn_started",
        taskId: text(event.taskId, "task"),
        persona: text(event.persona, "agent", 96),
        deliver: oneOf(event.deliver, ["return", "background"] as const, "return"),
      };
    case "spawn_result":
      return {
        type: "spawn_result",
        taskId: text(event.taskId, "task"),
        status: oneOf(event.status, ["ok", "error", "aborted"] as const, "error"),
        toolCallCount: num(event.toolCallCount),
      };
    case "background_task": {
      const safe: SafeAgentEvent = {
        type: "background_task",
        taskId: text(event.taskId, "task"),
        persona: text(event.persona, "agent", 96),
        status: oneOf(
          event.status,
          ["running", "completed", "failed", "aborted"] as const,
          "running",
        ),
      };
      const detail = maybeText(event.detail, 240);
      if (detail) safe.detail = detail;
      return safe;
    }
    case "child_started":
    case "child_progress":
    case "child_tool_request":
    case "child_permission_decision":
    case "child_cancelled":
    case "child_failed":
    case "child_completed":
      return safeControlEvent(event);
    case "tournament_result":
      return {
        type: "tournament_result",
        variants: tournamentVariants(event.variants),
        winnerIndex: num(event.winnerIndex),
      };
    case "ask_user":
      return {
        type: "ask_user",
        questionId: text(event.questionId, "question"),
        question: text(event.question),
        choices: askUserChoices(event.choices),
        allowFreeText: bool(event.allowFreeText),
      };
    case "plan_ready":
      return {
        type: "plan_ready",
        planId: text(event.planId, "plan", 120),
        requestId: text(event.requestId, "request", 120),
        state: text(event.state, "awaiting_approval", 120),
        plan: text(event.plan, "", 16_000),
      };
    case "plan_lifecycle":
      return {
        type: "plan_lifecycle",
        state: text(event.state, "unknown", 120),
        ...(maybeText(event.previousMode, 120)
          ? { previousMode: maybeText(event.previousMode, 120) }
          : {}),
      };
    case "session_stop":
      return {
        type: "session_stop",
        taskId: text(event.taskId, "task"),
        reason: oneOf(
          event.reason,
          ["user_stop", "circuit_breaker", "max_iter", "target_met", "plateau"] as const,
          "user_stop",
        ),
        round: num(event.round),
        lastScore: typeof event.lastScore === "number" && Number.isFinite(event.lastScore)
          ? event.lastScore
          : undefined,
      };
    case "context_activated":
      return {
        type: "context_activated",
        contextId: text(event.contextId, "context"),
        title: text(event.title, "Context"),
      };
    case "compaction_impossible":
      return {
        type: "compaction_impossible",
        model: text(event.model, "unknown", 96),
        contextWindow: num(event.contextWindow),
        effectiveReserveTokens: num(event.effectiveReserveTokens),
        effectiveBudgetTokens: num(event.effectiveBudgetTokens),
        minViableBudgetTokens: num(event.minViableBudgetTokens),
      };
    case "injection_queued":
      return {
        type: "injection_queued",
        injectionId: text(event.injectionId, "injection"),
        queuedCount: num(event.queuedCount),
      };
    case "injection_drained":
      return {
        type: "injection_drained",
        count: num(event.count),
        iteration: num(event.iteration),
      };
    case "heartbeat":
      return {
        type: "heartbeat",
        turnId: text(event.turnId, "turn"),
        iter: num(event.iter),
        elapsedMs: num(event.elapsedMs),
        lastEventAt: num(event.lastEventAt),
      };
    case "error":
      return {
        type: "error",
        code: text(event.code, "runtime_error", 96),
        message: text(event.message, "Runtime error"),
      };
    default:
      return null;
  }
}
