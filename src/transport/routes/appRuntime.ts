import type { IncomingMessage, ServerResponse } from "node:http";
import type { BackgroundTaskStatus } from "../../tasks/BackgroundTaskRegistry.js";
import type { CronRecord } from "../../cron/CronScheduler.js";
import type { ArtifactMeta } from "../../artifacts/ArtifactManager.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { Tool } from "../../Tool.js";
import {
  authorizeBearer,
  clampLimit,
  parseUrl,
  route,
  writeJson,
  type HttpServerCtx,
  type RouteHandler,
} from "./_helpers.js";

const TASK_STATUSES: readonly BackgroundTaskStatus[] = [
  "running",
  "completed",
  "aborted",
  "failed",
];

function preview(value: unknown, maxChars = 400): string | undefined {
  if (value === undefined || value === null) return undefined;
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (!text) return undefined;
  return text.length > maxChars ? `${text.slice(0, maxChars - 3)}...` : text;
}

function readTaskStatus(raw: string | null): BackgroundTaskStatus | undefined {
  if (!raw) return undefined;
  return TASK_STATUSES.includes(raw as BackgroundTaskStatus)
    ? (raw as BackgroundTaskStatus)
    : undefined;
}

function sessionSnapshot(session: ReturnType<HttpServerCtx["agent"]["listSessions"]>[number]) {
  const permissionMode = session.getPermissionMode();
  const prePlanMode = session.getPrePlanMode();
  return {
    sessionKey: session.meta.sessionKey,
    botId: session.meta.botId,
    channel: session.meta.channel,
    ...(session.meta.persona ? { persona: session.meta.persona } : {}),
    ...(session.meta.role ? { role: session.meta.role } : {}),
    createdAt: session.meta.createdAt,
    lastActivityAt: session.meta.lastActivityAt,
    permissionMode,
    ...(prePlanMode ? { prePlanMode } : {}),
    crons: session.meta.crons ?? [],
    budget: session.budgetStats(),
    maxTurns: session.maxTurns,
    maxCostUsd: session.maxCostUsd,
  };
}

function toolSnapshot(tool: Tool) {
  return {
    name: tool.name,
    permission: tool.permission,
    kind: tool.kind ?? "core",
    dangerous: tool.dangerous === true,
  };
}

function taskSnapshot(task: Record<string, unknown>) {
  return {
    taskId: String(task.taskId ?? ""),
    sessionKey: String(task.sessionKey ?? ""),
    parentTurnId: typeof task.parentTurnId === "string" ? task.parentTurnId : undefined,
    persona: typeof task.persona === "string" ? task.persona : undefined,
    status: String(task.status ?? "unknown"),
    startedAt: typeof task.startedAt === "number" ? task.startedAt : undefined,
    finishedAt: typeof task.finishedAt === "number" ? task.finishedAt : undefined,
    promptPreview: preview(task.prompt, 240) ?? "",
    resultPreview: preview(task.resultText, 400),
    error: typeof task.error === "string" ? task.error : undefined,
    toolCallCount:
      typeof task.toolCallCount === "number" ? task.toolCallCount : undefined,
    attempts: typeof task.attempts === "number" ? task.attempts : undefined,
    progress: Array.isArray(task.progress) ? task.progress.slice(-10) : [],
    artifacts:
      task.artifacts && typeof task.artifacts === "object"
        ? task.artifacts
        : undefined,
  };
}

function cronSnapshot(cron: CronRecord) {
  return {
    cronId: cron.cronId,
    expression: cron.expression,
    enabled: cron.enabled,
    durable: cron.durable,
    internal: cron.internal === true,
    nextFireAt: cron.nextFireAt,
    ...(cron.lastFiredAt ? { lastFiredAt: cron.lastFiredAt } : {}),
    consecutiveFailures: cron.consecutiveFailures,
    deliveryChannel: cron.deliveryChannel,
    ...(cron.description ? { description: cron.description } : {}),
    ...(cron.sessionKey ? { sessionKey: cron.sessionKey } : {}),
    promptPreview: preview(cron.prompt, 240) ?? "",
  };
}

function artifactSnapshot(artifact: ArtifactMeta) {
  return {
    artifactId: artifact.artifactId,
    kind: artifact.kind,
    title: artifact.title,
    slug: artifact.slug,
    path: artifact.path,
    sizeBytes: artifact.sizeBytes,
    ...(artifact.producedBy ? { producedBy: artifact.producedBy } : {}),
    ...(artifact.sources ? { sources: artifact.sources } : {}),
    createdAt: artifact.createdAt,
    updatedAt: artifact.updatedAt,
    ...(artifact.spawnTaskId ? { spawnTaskId: artifact.spawnTaskId } : {}),
    ...(artifact.importedFromArtifactId
      ? { importedFromArtifactId: artifact.importedFromArtifactId }
      : {}),
  };
}

function compactTranscriptEntry(entry: TranscriptEntry) {
  const base = {
    kind: entry.kind,
    ts: entry.ts,
    ...("turnId" in entry ? { turnId: entry.turnId } : {}),
  };
  switch (entry.kind) {
    case "user_message":
    case "assistant_text":
      return { ...base, text: preview(entry.text, 4_000) ?? "" };
    case "tool_call":
      return {
        ...base,
        toolUseId: entry.toolUseId,
        name: entry.name,
        inputPreview: preview(entry.input, 2_000),
      };
    case "tool_result":
      return {
        ...base,
        toolUseId: entry.toolUseId,
        status: entry.status,
        outputPreview: preview(entry.output, 2_000),
        isError: entry.isError === true,
      };
    case "turn_started":
      return { ...base, declaredRoute: entry.declaredRoute };
    case "turn_committed":
      return {
        ...base,
        inputTokens: entry.inputTokens,
        outputTokens: entry.outputTokens,
      };
    case "turn_aborted":
      return { ...base, reason: entry.reason };
    case "compaction_boundary":
      return {
        ...base,
        boundaryId: entry.boundaryId,
        beforeTokenCount: entry.beforeTokenCount,
        afterTokenCount: entry.afterTokenCount,
        summaryHash: entry.summaryHash,
        summaryPreview: preview(entry.summaryText, 1_000),
        createdAt: entry.createdAt,
      };
    case "canonical_message":
      return {
        ...base,
        messageId: entry.messageId,
        parentId: entry.parentId,
        role: entry.role,
        contentPreview: preview(entry.content, 2_000),
      };
    case "control_event":
      return {
        ...base,
        seq: entry.seq,
        eventId: entry.eventId,
        eventType: entry.eventType,
      };
  }
}

async function buildSessions(ctx: HttpServerCtx) {
  const items = ctx.agent.listSessions().map(sessionSnapshot);
  return { count: items.length, items };
}

async function buildTasks(ctx: HttpServerCtx, req: IncomingMessage) {
  const url = parseUrl(req.url);
  const limit = clampLimit(url.searchParams.get("limit"), 1, 100, 25);
  const status = readTaskStatus(url.searchParams.get("status"));
  const sessionKey = url.searchParams.get("sessionKey") ?? undefined;
  const page = await ctx.agent.backgroundTasks.list({
    limit,
    ...(status ? { status } : {}),
    ...(sessionKey ? { sessionKey } : {}),
  });
  const items = page.tasks.map((task) =>
    taskSnapshot(task as unknown as Record<string, unknown>),
  );
  return {
    count: items.length,
    items,
    ...(page.nextCursor ? { nextCursor: page.nextCursor } : {}),
  };
}

async function buildCrons(ctx: HttpServerCtx, req: IncomingMessage) {
  const url = parseUrl(req.url);
  const limit = clampLimit(url.searchParams.get("limit"), 1, 100, 50);
  const enabledRaw = url.searchParams.get("enabled");
  const enabled =
    enabledRaw === "true" ? true : enabledRaw === "false" ? false : undefined;
  const all = ctx.agent.crons.list({
    includeInternal: true,
    ...(enabled !== undefined ? { enabled } : {}),
  });
  const items = all.slice(0, limit).map(cronSnapshot);
  return {
    count: all.length,
    internalCount: all.filter((cron) => cron.internal === true).length,
    items,
  };
}

async function buildArtifacts(ctx: HttpServerCtx, req: IncomingMessage) {
  const url = parseUrl(req.url);
  const limit = clampLimit(url.searchParams.get("limit"), 1, 100, 50);
  const kind = url.searchParams.get("kind") ?? undefined;
  const all = await ctx.agent.artifacts.list(kind ? { kind } : undefined);
  const items = all
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, limit)
    .map(artifactSnapshot);
  return {
    count: all.length,
    items,
  };
}

function buildTools(ctx: HttpServerCtx) {
  const items = ctx.agent.tools.list().map(toolSnapshot);
  return {
    count: items.length,
    skillCount: items.filter((tool) => tool.kind === "skill").length,
    items,
  };
}

function buildSkills(ctx: HttpServerCtx) {
  const report = ctx.agent.tools.skillReport();
  const loaded = report?.loaded ?? [];
  const issues = report?.issues ?? [];
  const runtimeHooks = report?.runtimeHooks ?? [];
  return {
    loadedCount: loaded.length,
    issueCount: issues.length,
    runtimeHookCount: runtimeHooks.length,
    loaded,
    issues,
    runtimeHooks,
  };
}

async function handleRuntime(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const [sessions, tasks, crons, artifacts] = await Promise.all([
    buildSessions(ctx),
    buildTasks(ctx, req),
    buildCrons(ctx, req),
    buildArtifacts(ctx, req),
  ]);
  writeJson(res, 200, {
    ok: true,
    runtime: "core-agent",
    botId: ctx.agent.config.botId,
    generatedAt: Date.now(),
    sessions,
    tasks,
    crons,
    artifacts,
    tools: buildTools(ctx),
    skills: buildSkills(ctx),
  });
}

async function handleSessions(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const sessions = await buildSessions(ctx);
  writeJson(res, 200, {
    ok: true,
    count: sessions.count,
    sessions: sessions.items,
  });
}

async function handleTranscript(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const url = parseUrl(req.url);
  const sessionKey = url.searchParams.get("sessionKey");
  if (!sessionKey) {
    writeJson(res, 400, { error: "missing_session_key" });
    return;
  }
  const session = ctx.agent.getSession(sessionKey);
  if (!session) {
    writeJson(res, 404, { error: "not_found" });
    return;
  }
  const limit = clampLimit(url.searchParams.get("limit"), 1, 200, 50);
  const entries = (await session.transcript.readCommitted())
    .slice(-limit)
    .map(compactTranscriptEntry);
  writeJson(res, 200, {
    ok: true,
    sessionKey,
    count: entries.length,
    entries,
  });
}

async function handleTasks(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const tasks = await buildTasks(ctx, req);
  writeJson(res, 200, { ok: true, ...tasks });
}

async function handleCrons(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const crons = await buildCrons(ctx, req);
  writeJson(res, 200, { ok: true, ...crons });
}

async function handleArtifacts(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const artifacts = await buildArtifacts(ctx, req);
  writeJson(res, 200, { ok: true, ...artifacts });
}

async function handleSkills(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  writeJson(res, 200, { ok: true, ...buildSkills(ctx) });
}

export const appRuntimeRoutes: RouteHandler[] = [
  route("GET", /^\/v1\/app\/runtime(?:\?.*)?$/, handleRuntime),
  route("GET", /^\/v1\/app\/sessions(?:\?.*)?$/, handleSessions),
  route("GET", /^\/v1\/app\/transcript(?:\?.*)?$/, handleTranscript),
  route("GET", /^\/v1\/app\/tasks(?:\?.*)?$/, handleTasks),
  route("GET", /^\/v1\/app\/crons(?:\?.*)?$/, handleCrons),
  route("GET", /^\/v1\/app\/artifacts(?:\?.*)?$/, handleArtifacts),
  route("GET", /^\/v1\/app\/skills(?:\?.*)?$/, handleSkills),
];
