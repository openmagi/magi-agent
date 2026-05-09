import fs from "node:fs/promises";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import type { BackgroundTaskStatus } from "../../tasks/BackgroundTaskRegistry.js";
import type { CronRecord } from "../../cron/CronScheduler.js";
import type { ArtifactMeta } from "../../artifacts/ArtifactManager.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { Tool } from "../../Tool.js";
import { isFsSafeEscape, isUnderRoot, writeSafe } from "../../util/fsSafe.js";
import type { ChannelRef, ChannelType } from "../../util/types.js";
import {
  classifyEvidence,
  transcriptEvidenceForTurn,
  type EvidenceItem,
} from "../../verification/VerificationEvidence.js";
import {
  listLocalKnowledgeCollections,
  listLocalKnowledgeDocuments,
  readLocalKnowledgeFile,
  searchLocalKnowledge,
  writeLocalKnowledgeFile,
} from "../../knowledge/LocalKnowledgeBase.js";
import {
  authorizeBearer,
  clampLimit,
  parseUrl,
  readJsonBody,
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

const CHANNEL_TYPES: readonly ChannelType[] = [
  "app",
  "telegram",
  "discord",
  "internal",
];
const DEFAULT_FILE_READ_BYTES = 256 * 1024;
const MAX_FILE_READ_BYTES = 1024 * 1024;
const MAX_FILE_WRITE_BYTES = 1024 * 1024;

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

function readObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function decodePathPart(raw: string): string | null {
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

function normalizeRelativePath(raw: string | null | undefined, fallback = "."): string | null {
  const value = (raw ?? fallback).trim() || fallback;
  if (value.includes("\0") || path.isAbsolute(value)) return null;
  const normalized = path.posix.normalize(value.replace(/\\/g, "/"));
  if (
    normalized === ".." ||
    normalized.startsWith("../") ||
    normalized.startsWith("/")
  ) {
    return null;
  }
  return normalized === "." ? "." : normalized.replace(/^\.\//, "");
}

function resolveWorkspacePath(
  ctx: HttpServerCtx,
  raw: string | null | undefined,
  fallback = ".",
): { rel: string; full: string } | null {
  const rel = normalizeRelativePath(raw, fallback);
  if (!rel) return null;
  const root = path.resolve(ctx.agent.config.workspaceRoot);
  const full = path.resolve(root, rel === "." ? "" : rel);
  if (full !== root && !full.startsWith(`${root}${path.sep}`)) return null;
  return { rel, full };
}

function isMemoryPath(rel: string): boolean {
  return rel === "MEMORY.md" || rel === "memory" || rel.startsWith("memory/");
}

function readChannel(value: unknown): ChannelRef | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const type = raw.type;
  const channelId = raw.channelId;
  if (
    typeof type !== "string" ||
    !CHANNEL_TYPES.includes(type as ChannelType) ||
    typeof channelId !== "string"
  ) {
    return null;
  }
  return { type: type as ChannelType, channelId };
}

function readArtifactTier(raw: string | null): "l0" | "l1" | "l2" {
  return raw === "l1" || raw === "l2" ? raw : "l0";
}

function safeDownloadName(value: string): string {
  const base = value
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
  return base || "artifact.md";
}

function workspaceDownloadMimeType(filePath: string): string {
  const ext = path.extname(filePath).toLowerCase();
  const types: Record<string, string> = {
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".tsv": "text/tab-separated-values; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".hwpx": "application/hwp+zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".zip": "application/zip",
  };
  return types[ext] ?? "application/octet-stream";
}

function taskOutputSnapshot(task: Record<string, unknown>) {
  const startedAt = typeof task.startedAt === "number" ? task.startedAt : Date.now();
  const finishedAt = typeof task.finishedAt === "number" ? task.finishedAt : Date.now();
  return {
    taskId: String(task.taskId ?? ""),
    status: String(task.status ?? "unknown"),
    durationMs: Math.max(0, finishedAt - startedAt),
    ...(typeof task.resultText === "string" ? { resultText: task.resultText } : {}),
    ...(typeof task.error === "string" ? { error: task.error } : {}),
  };
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
    ...(cron.mode ? { mode: cron.mode } : {}),
    ...(cron.scriptPath ? { scriptPath: cron.scriptPath } : {}),
    ...(cron.timeoutMs !== undefined ? { timeoutMs: cron.timeoutMs } : {}),
    ...(cron.quietOnEmptyStdout !== undefined
      ? { quietOnEmptyStdout: cron.quietOnEmptyStdout }
      : {}),
    ...(cron.deliveryPolicy ? { deliveryPolicy: cron.deliveryPolicy } : {}),
    prompt: cron.prompt,
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

function parseJsonRecord(text: string | undefined): Record<string, unknown> | null {
  if (!text) return null;
  try {
    const parsed = JSON.parse(text) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function commandFromEvidence(item: EvidenceItem): string | undefined {
  if (!item.input || typeof item.input !== "object" || Array.isArray(item.input)) {
    return undefined;
  }
  return stringValue((item.input as Record<string, unknown>).command);
}

function isSuccessfulEvidence(item: EvidenceItem): boolean {
  if (item.isError === true) return false;
  return !item.status || item.status === "ok" || item.status === "success" || item.status === "completed";
}

function resultForToolUse(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): Map<string, Extract<TranscriptEntry, { kind: "tool_result" }>> {
  const out = new Map<string, Extract<TranscriptEntry, { kind: "tool_result" }>>();
  for (const entry of transcript) {
    if (entry.kind === "tool_result" && entry.turnId === turnId) {
      out.set(entry.toolUseId, entry);
    }
  }
  return out;
}

function deliveryEvidenceFromResult(
  output: string | undefined,
): Array<{
  target?: string;
  status?: string;
  externalId?: string;
  marker?: string;
  providerMessageId?: string;
  attemptCount?: number;
}> {
  const parsed = parseJsonRecord(output);
  const deliveries = parsed?.deliveries;
  if (!Array.isArray(deliveries)) return [];
  return deliveries.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const record = item as Record<string, unknown>;
    return [{
      target: stringValue(record.target),
      status: stringValue(record.status),
      externalId: stringValue(record.externalId),
      marker: stringValue(record.marker),
      providerMessageId: stringValue(record.providerMessageId),
      attemptCount:
        typeof record.attemptCount === "number" ? record.attemptCount : undefined,
    }];
  });
}

function artifactEvidenceFromResult(
  output: string | undefined,
): {
  artifactId?: string;
  filename?: string;
  workspacePath?: string;
  path?: string;
} | null {
  const parsed = parseJsonRecord(output);
  if (!parsed) return null;
  const meta = parsed.meta && typeof parsed.meta === "object" && !Array.isArray(parsed.meta)
    ? parsed.meta as Record<string, unknown>
    : {};
  const artifactId = stringValue(parsed.artifactId) ?? stringValue(meta.artifactId);
  const filename = stringValue(parsed.filename) ?? stringValue(meta.filename);
  const workspacePath =
    stringValue(parsed.workspacePath) ?? stringValue(parsed.path) ?? stringValue(meta.path);
  if (!artifactId && !filename && !workspacePath) return null;
  return {
    ...(artifactId ? { artifactId } : {}),
    ...(filename ? { filename } : {}),
    ...(workspacePath ? { workspacePath, path: workspacePath } : {}),
  };
}

function buildEvidenceProjection(
  transcript: ReadonlyArray<TranscriptEntry>,
  limit: number,
) {
  const turnIds: string[] = [];
  for (const entry of transcript) {
    if (!("turnId" in entry) || !entry.turnId) continue;
    if (!turnIds.includes(entry.turnId)) turnIds.push(entry.turnId);
  }

  return turnIds.slice(-limit).map((turnId) => {
    const entries = transcript.filter((entry) => "turnId" in entry && entry.turnId === turnId);
    const results = resultForToolUse(transcript, turnId);
    const evidence = transcriptEvidenceForTurn(transcript, turnId);
    const classification = classifyEvidence(evidence);
    const tools: Array<{
      name: string;
      toolUseId: string;
      status?: string;
      inputPreview?: string;
      outputPreview?: string;
    }> = [];
    const deliveries: ReturnType<typeof deliveryEvidenceFromResult> = [];
    const artifacts: Array<NonNullable<ReturnType<typeof artifactEvidenceFromResult>> & { tool: string }> = [];

    for (const entry of entries) {
      if (entry.kind !== "tool_call") continue;
      const result = results.get(entry.toolUseId);
      tools.push({
        name: entry.name,
        toolUseId: entry.toolUseId,
        ...(result?.status ? { status: result.status } : {}),
        inputPreview: preview(entry.input, 500),
        outputPreview: preview(result?.output, 500),
      });
      if (entry.name === "FileDeliver") {
        deliveries.push(...deliveryEvidenceFromResult(result?.output));
      }
      if (
        entry.name === "DocumentWrite" ||
        entry.name === "SpreadsheetWrite" ||
        entry.name === "ArtifactCreate" ||
        entry.name === "ArtifactUpdate"
      ) {
        const artifact = artifactEvidenceFromResult(result?.output);
        if (artifact) artifacts.push({ ...artifact, tool: entry.name });
      }
    }

    const verification = evidence
      .filter((item) => isSuccessfulEvidence(item) && classifyEvidence([item]).verification)
      .map((item) => ({
        tool: item.tool,
        status: item.status,
        command: commandFromEvidence(item),
        outputPreview: preview(item.output, 500),
      }));

    return {
      turnId,
      startedAt: entries[0]?.ts,
      endedAt: entries[entries.length - 1]?.ts,
      userPreview: preview(
        entries.find((entry) => entry.kind === "user_message") &&
          (entries.find((entry) => entry.kind === "user_message") as Extract<TranscriptEntry, { kind: "user_message" }>).text,
        500,
      ),
      assistantPreview: preview(
        entries.filter((entry) => entry.kind === "assistant_text").at(-1) &&
          (entries.filter((entry) => entry.kind === "assistant_text").at(-1) as Extract<TranscriptEntry, { kind: "assistant_text" }>).text,
        500,
      ),
      classification,
      tools,
      verification,
      deliveries,
      artifacts,
      committed: entries.some((entry) => entry.kind === "turn_committed"),
      aborted: entries.some((entry) => entry.kind === "turn_aborted"),
    };
  });
}

function knowledgeDocumentSnapshot(document: {
  collection: string;
  filename: string;
  title: string;
  path: string;
  objectKey: string;
  sizeBytes: number;
  mtimeMs: number;
}) {
  return {
    collection: document.collection,
    filename: document.filename,
    title: document.title,
    path: document.path,
    objectKey: document.objectKey,
    sizeBytes: document.sizeBytes,
    mtimeMs: document.mtimeMs,
  };
}

function knowledgeResultSnapshot(result: {
  collection: string;
  filename: string;
  title: string;
  path: string;
  objectKey: string;
  score: number;
  snippet: string;
}) {
  return {
    collection: result.collection,
    filename: result.filename,
    title: result.title,
    path: result.path,
    objectKey: result.objectKey,
    score: result.score,
    snippet: result.snippet,
  };
}

async function listWorkspaceEntries(ctx: HttpServerCtx, rawPath: string | null) {
  const resolved = resolveWorkspacePath(ctx, rawPath);
  if (!resolved) return null;
  const stat = await fs.stat(resolved.full);
  if (!stat.isDirectory()) {
    return {
      path: resolved.rel,
      entries: [],
      file: {
        type: "file",
        sizeBytes: stat.size,
        mtimeMs: stat.mtimeMs,
      },
    };
  }
  const dirents = await fs.readdir(resolved.full, { withFileTypes: true });
  const entries = await Promise.all(
    dirents.map(async (dirent) => {
      const childRel =
        resolved.rel === "."
          ? dirent.name
          : path.posix.join(resolved.rel, dirent.name);
      const full = path.join(resolved.full, dirent.name);
      const childStat = await fs.stat(full);
      return {
        name: dirent.name,
        path: childRel,
        type: dirent.isDirectory()
          ? "directory"
          : dirent.isFile()
            ? "file"
            : "other",
        sizeBytes: childStat.size,
        mtimeMs: childStat.mtimeMs,
      };
    }),
  );
  entries.sort((a, b) => {
    if (a.type !== b.type) return a.type === "directory" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  return { path: resolved.rel, entries };
}

async function readWorkspaceFile(
  ctx: HttpServerCtx,
  rawPath: string | null,
  maxBytes: number,
) {
  const resolved = resolveWorkspacePath(ctx, rawPath);
  if (!resolved) return null;
  const stat = await fs.stat(resolved.full);
  if (!stat.isFile()) {
    return { error: "not_file" as const };
  }
  const bytesToRead = Math.min(stat.size, maxBytes);
  const handle = await fs.open(resolved.full, "r");
  try {
    const buffer = Buffer.alloc(bytesToRead);
    const result =
      bytesToRead > 0
        ? await handle.read(buffer, 0, bytesToRead, 0)
        : { bytesRead: 0 };
    return {
      path: resolved.rel,
      sizeBytes: stat.size,
      mtimeMs: stat.mtimeMs,
      content: buffer.subarray(0, result.bytesRead).toString("utf8"),
      truncated: stat.size > result.bytesRead,
    };
  } finally {
    await handle.close();
  }
}

async function writeWorkspaceFile(
  ctx: HttpServerCtx,
  rawPath: string | null,
  content: string,
) {
  const resolved = resolveWorkspacePath(ctx, rawPath);
  if (!resolved || resolved.rel === ".") return null;
  const sizeBytes = Buffer.byteLength(content, "utf8");
  if (sizeBytes > MAX_FILE_WRITE_BYTES) {
    return { error: "file_too_large" as const };
  }

  await fs.mkdir(path.dirname(resolved.full), { recursive: true });
  const root = path.resolve(ctx.agent.config.workspaceRoot);
  const rootReal = await fs.realpath(root).catch(() => root);
  const parentReal = await fs.realpath(path.dirname(resolved.full)).catch(() => null);
  if (!parentReal || !isUnderRoot(parentReal, rootReal)) {
    return { error: "invalid_path" as const };
  }

  await writeSafe(resolved.rel, content, ctx.agent.config.workspaceRoot);
  const stat = await fs.stat(resolved.full);
  return {
    path: resolved.rel,
    sizeBytes: stat.size,
    mtimeMs: stat.mtimeMs,
  };
}

async function collectMemoryFiles(ctx: HttpServerCtx) {
  const root = path.resolve(ctx.agent.config.workspaceRoot);
  const out: Array<{ path: string; sizeBytes: number; mtimeMs: number }> = [];

  async function addIfFile(rel: string): Promise<void> {
    const resolved = resolveWorkspacePath(ctx, rel);
    if (!resolved || !isMemoryPath(resolved.rel)) return;
    try {
      const stat = await fs.stat(resolved.full);
      if (stat.isFile()) {
        out.push({
          path: resolved.rel,
          sizeBytes: stat.size,
          mtimeMs: stat.mtimeMs,
        });
      }
    } catch {
      /* ignore missing optional memory files */
    }
  }

  async function walk(rel: string): Promise<void> {
    const resolved = resolveWorkspacePath(ctx, rel);
    if (!resolved || !isMemoryPath(resolved.rel)) return;
    let dirents: Array<import("node:fs").Dirent>;
    try {
      dirents = await fs.readdir(resolved.full, { withFileTypes: true });
    } catch {
      return;
    }
    await Promise.all(
      dirents.map(async (dirent) => {
        const childRel = path.posix.join(rel, dirent.name);
        const childFull = path.resolve(root, childRel);
        if (childFull !== root && !childFull.startsWith(`${root}${path.sep}`)) {
          return;
        }
        if (dirent.isDirectory()) {
          await walk(childRel);
          return;
        }
        if (!dirent.isFile() || !dirent.name.endsWith(".md")) return;
        const stat = await fs.stat(childFull);
        out.push({
          path: childRel,
          sizeBytes: stat.size,
          mtimeMs: stat.mtimeMs,
        });
      }),
    );
  }

  await Promise.all([addIfFile("MEMORY.md"), walk("memory")]);
  out.sort((a, b) => a.path.localeCompare(b.path));
  return out;
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

async function handleEvidence(
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
  const limit = clampLimit(url.searchParams.get("limit"), 1, 50, 20);
  const transcript = await session.transcript.readCommitted();
  const turns = buildEvidenceProjection(transcript, limit);
  writeJson(res, 200, {
    ok: true,
    sessionKey,
    count: turns.length,
    turns,
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

async function readArtifactContent(
  ctx: HttpServerCtx,
  artifactId: string,
  tier: "l0" | "l1" | "l2",
): Promise<{ meta: ArtifactMeta; content: string }> {
  const meta = await ctx.agent.artifacts.getMeta(artifactId);
  const content =
    tier === "l1"
      ? await ctx.agent.artifacts.readL1(artifactId)
      : tier === "l2"
        ? await ctx.agent.artifacts.readL2(artifactId)
        : await ctx.agent.artifacts.readL0(artifactId);
  return { meta, content };
}

async function handleArtifactContent(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const artifactId = decodePathPart(match[1] ?? "");
  if (!artifactId) {
    writeJson(res, 400, { error: "invalid_artifact_id" });
    return;
  }
  const tier = readArtifactTier(parseUrl(req.url).searchParams.get("tier"));
  try {
    const { meta, content } = await readArtifactContent(ctx, artifactId, tier);
    writeJson(res, 200, {
      ok: true,
      artifact: artifactSnapshot(meta),
      tier,
      content,
    });
  } catch {
    writeJson(res, 404, { error: "not_found" });
  }
}

async function handleArtifactDownload(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const artifactId = decodePathPart(match[1] ?? "");
  if (!artifactId) {
    writeJson(res, 400, { error: "invalid_artifact_id" });
    return;
  }
  try {
    const { meta, content } = await readArtifactContent(ctx, artifactId, "l0");
    const filename = safeDownloadName(`${meta.slug || meta.artifactId}.md`);
    res.writeHead(200, {
      "Content-Type": "text/markdown; charset=utf-8",
      "Content-Disposition": `attachment; filename="${filename}"`,
      "Cache-Control": "no-cache",
    });
    res.end(content);
  } catch {
    writeJson(res, 404, { error: "not_found" });
  }
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

async function handleWorkspaceList(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const url = parseUrl(req.url);
  try {
    const listing = await listWorkspaceEntries(ctx, url.searchParams.get("path"));
    if (!listing) {
      writeJson(res, 400, { error: "invalid_path" });
      return;
    }
    writeJson(res, 200, { ok: true, ...listing });
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      writeJson(res, 404, { error: "not_found" });
      return;
    }
    throw err;
  }
}

async function handleWorkspaceFile(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const url = parseUrl(req.url);
  const maxBytes = clampLimit(
    url.searchParams.get("maxBytes"),
    1,
    MAX_FILE_READ_BYTES,
    DEFAULT_FILE_READ_BYTES,
  );
  try {
    const file = await readWorkspaceFile(ctx, url.searchParams.get("path"), maxBytes);
    if (!file) {
      writeJson(res, 400, { error: "invalid_path" });
      return;
    }
    if ("error" in file) {
      writeJson(res, 400, { error: file.error });
      return;
    }
    writeJson(res, 200, { ok: true, ...file });
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      writeJson(res, 404, { error: "not_found" });
      return;
    }
    throw err;
  }
}

async function handleWorkspaceFilePut(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const body = readObject(await readJsonBody(req));
  const rawPath = typeof body.path === "string" ? body.path : "";
  const content = typeof body.content === "string" ? body.content : null;
  if (!rawPath || content === null) {
    writeJson(res, 400, { error: "missing_workspace_file_fields" });
    return;
  }
  try {
    const written = await writeWorkspaceFile(ctx, rawPath, content);
    if (!written) {
      writeJson(res, 400, { error: "invalid_path" });
      return;
    }
    if ("error" in written) {
      writeJson(res, written.error === "file_too_large" ? 413 : 400, {
        error: written.error,
      });
      return;
    }
    if (isMemoryPath(written.path)) {
      await ctx.agent.hipocampus.getQmdManager().reindex().catch(() => {});
    }
    writeJson(res, 200, { ok: true, ...written });
  } catch (err) {
    if (isFsSafeEscape(err)) {
      writeJson(res, 400, { error: "invalid_path" });
      return;
    }
    throw err;
  }
}

async function handleWorkspaceDownload(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const url = parseUrl(req.url);
  const resolved = resolveWorkspacePath(ctx, url.searchParams.get("path"));
  if (!resolved) {
    writeJson(res, 400, { error: "invalid_path" });
    return;
  }
  try {
    const stat = await fs.stat(resolved.full);
    if (!stat.isFile()) {
      writeJson(res, 400, { error: "not_file" });
      return;
    }
    const body = await fs.readFile(resolved.full);
    const filename = safeDownloadName(path.basename(resolved.rel));
    res.writeHead(200, {
      "Content-Type": workspaceDownloadMimeType(resolved.full),
      "Content-Disposition": `attachment; filename="${filename}"`,
      "Cache-Control": "no-cache",
    });
    res.end(body);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      writeJson(res, 404, { error: "not_found" });
      return;
    }
    throw err;
  }
}

async function handleMemoryList(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const [files, status] = await Promise.all([
    collectMemoryFiles(ctx),
    ctx.agent.hipocampus.status().catch(() => null),
  ]);
  writeJson(res, 200, { ok: true, status, files });
}

async function handleMemoryFile(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const url = parseUrl(req.url);
  const rawPath = url.searchParams.get("path");
  const rel = normalizeRelativePath(rawPath);
  if (!rel || !isMemoryPath(rel)) {
    writeJson(res, 400, { error: "invalid_path" });
    return;
  }
  const maxBytes = clampLimit(
    url.searchParams.get("maxBytes"),
    1,
    MAX_FILE_READ_BYTES,
    DEFAULT_FILE_READ_BYTES,
  );
  try {
    const file = await readWorkspaceFile(ctx, rel, maxBytes);
    if (!file) {
      writeJson(res, 400, { error: "invalid_path" });
      return;
    }
    if ("error" in file) {
      writeJson(res, 400, { error: file.error });
      return;
    }
    writeJson(res, 200, { ok: true, ...file });
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      writeJson(res, 404, { error: "not_found" });
      return;
    }
    throw err;
  }
}

async function handleMemorySearch(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const url = parseUrl(req.url);
  const query = (url.searchParams.get("q") ?? "").trim();
  if (!query) {
    writeJson(res, 400, { error: "missing_query" });
    return;
  }
  const limit = clampLimit(url.searchParams.get("limit"), 1, 25, 5);
  const recall = await ctx.agent.hipocampus.recall(query, { limit });
  writeJson(res, 200, {
    ok: true,
    query,
    root: recall.root
      ? {
          path: recall.root.path,
          bytes: recall.root.bytes,
          contentPreview: preview(recall.root.content, 1_000),
        }
      : null,
    results: recall.results.map((result) => ({
      path: result.path,
      score: result.score,
      contentPreview: preview(result.content, 1_000),
      ...(result.context ? { context: result.context } : {}),
    })),
  });
}

async function handleMemoryCompact(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const body = readObject(await readJsonBody(req));
  const result = await ctx.agent.hipocampus.compact(body.force === true);
  writeJson(res, 200, { ok: true, result });
}

async function handleMemoryReindex(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  await ctx.agent.hipocampus.getQmdManager().reindex();
  writeJson(res, 200, { ok: true });
}

async function handleKnowledgeList(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const url = parseUrl(req.url);
  const collection = url.searchParams.get("collection")?.trim() || undefined;
  const [collections, documents] = await Promise.all([
    listLocalKnowledgeCollections(ctx.agent.config.workspaceRoot),
    listLocalKnowledgeDocuments(ctx.agent.config.workspaceRoot, collection),
  ]);
  writeJson(res, 200, {
    ok: true,
    root: "knowledge",
    collections,
    documents: documents.map(knowledgeDocumentSnapshot),
  });
}

async function handleKnowledgeSearch(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const url = parseUrl(req.url);
  const query = (url.searchParams.get("q") ?? "").trim();
  if (!query) {
    writeJson(res, 400, { error: "missing_query" });
    return;
  }
  const collection = url.searchParams.get("collection")?.trim() || undefined;
  const limit = clampLimit(url.searchParams.get("limit"), 1, 50, 10);
  const results = await searchLocalKnowledge(ctx.agent.config.workspaceRoot, query, {
    collection,
    limit,
  });
  writeJson(res, 200, {
    ok: true,
    query,
    collection: collection ?? null,
    results: results.map(knowledgeResultSnapshot),
  });
}

async function handleKnowledgeFile(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const url = parseUrl(req.url);
  const rawPath = url.searchParams.get("path");
  if (!rawPath) {
    writeJson(res, 400, { error: "missing_path" });
    return;
  }
  const maxBytes = clampLimit(
    url.searchParams.get("maxBytes"),
    1,
    MAX_FILE_READ_BYTES,
    DEFAULT_FILE_READ_BYTES,
  );
  try {
    const file = await readLocalKnowledgeFile(
      ctx.agent.config.workspaceRoot,
      rawPath,
      maxBytes,
    );
    if (!file) {
      writeJson(res, 400, { error: "invalid_path" });
      return;
    }
    writeJson(res, 200, { ok: true, ...file });
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      writeJson(res, 404, { error: "not_found" });
      return;
    }
    throw err;
  }
}

async function handleKnowledgeFilePut(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const body = readObject(await readJsonBody(req));
  const rawPath = typeof body.path === "string" ? body.path : "";
  const content = typeof body.content === "string" ? body.content : null;
  if (!rawPath || content === null) {
    writeJson(res, 400, { error: "missing_knowledge_file_fields" });
    return;
  }
  const written = await writeLocalKnowledgeFile(
    ctx.agent.config.workspaceRoot,
    rawPath,
    content,
  );
  if (!written) {
    writeJson(res, 400, { error: "invalid_path" });
    return;
  }
  await ctx.agent.hipocampus.getQmdManager().reindex().catch(() => {});
  writeJson(res, 200, { ok: true, ...written });
}

async function handleTaskGet(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const taskId = decodePathPart(match[1] ?? "");
  if (!taskId) {
    writeJson(res, 400, { error: "invalid_task_id" });
    return;
  }
  const task = await ctx.agent.backgroundTasks.get(taskId);
  if (!task) {
    writeJson(res, 404, { error: "not_found" });
    return;
  }
  writeJson(res, 200, {
    ok: true,
    task: taskSnapshot(task as unknown as Record<string, unknown>),
  });
}

async function handleTaskOutput(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const taskId = decodePathPart(match[1] ?? "");
  if (!taskId) {
    writeJson(res, 400, { error: "invalid_task_id" });
    return;
  }
  const task = await ctx.agent.backgroundTasks.get(taskId);
  if (!task) {
    writeJson(res, 404, { error: "not_found" });
    return;
  }
  writeJson(res, 200, {
    ok: true,
    ...taskOutputSnapshot(task as unknown as Record<string, unknown>),
  });
}

async function handleTaskStop(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const taskId = decodePathPart(match[1] ?? "");
  if (!taskId) {
    writeJson(res, 400, { error: "invalid_task_id" });
    return;
  }
  const body = readObject(await readJsonBody(req));
  const reason = typeof body.reason === "string" ? body.reason : undefined;
  const stopped = await ctx.agent.backgroundTasks.stop(taskId, reason);
  const task = await ctx.agent.backgroundTasks.get(taskId);
  if (!task) {
    writeJson(res, 404, { error: "not_found" });
    return;
  }
  writeJson(res, 200, {
    ok: true,
    taskId,
    stopped,
    task: taskSnapshot(task as unknown as Record<string, unknown>),
  });
}

async function handleCronCreate(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const body = readObject(await readJsonBody(req));
  const expression = typeof body.expression === "string" ? body.expression.trim() : "";
  const prompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
  if (!expression || !prompt) {
    writeJson(res, 400, { error: "missing_cron_fields" });
    return;
  }
  const deliveryChannel = readChannel(body.deliveryChannel) ?? {
    type: "app" as const,
    channelId: "web",
  };
  try {
    const sessionKey =
      typeof body.sessionKey === "string" && body.sessionKey.trim()
        ? body.sessionKey.trim()
        : undefined;
    const durable = body.durable === true;
    const mode =
      body.mode === "script" || body.mode === "agent" ? body.mode : undefined;
    const cron = await ctx.agent.crons.create({
      botId: ctx.agent.config.botId,
      userId: ctx.agent.config.userId,
      expression,
      prompt,
      deliveryChannel,
      durable,
      ...(typeof body.description === "string" && body.description.trim()
        ? { description: body.description.trim() }
        : {}),
      ...(mode ? { mode } : {}),
      ...(typeof body.scriptPath === "string" && body.scriptPath.trim()
        ? { scriptPath: body.scriptPath.trim() }
        : {}),
      ...(typeof body.timeoutMs === "number" && Number.isFinite(body.timeoutMs)
        ? { timeoutMs: Math.max(1, Math.floor(body.timeoutMs)) }
        : {}),
      ...(typeof body.quietOnEmptyStdout === "boolean"
        ? { quietOnEmptyStdout: body.quietOnEmptyStdout }
        : {}),
      ...(body.deliveryPolicy === "stdout_non_empty" ||
      body.deliveryPolicy === "always" ||
      body.deliveryPolicy === "never"
        ? { deliveryPolicy: body.deliveryPolicy }
        : {}),
      ...(sessionKey ? { sessionKey } : {}),
    });
    if (!durable && sessionKey) {
      ctx.agent.getSession(sessionKey)?.registerSessionCron(cron.cronId);
    }
    writeJson(res, 200, { ok: true, cron: cronSnapshot(cron) });
  } catch (err) {
    writeJson(res, 400, {
      error: "cron_create_failed",
      message: err instanceof Error ? err.message : String(err),
    });
  }
}

async function handleCronUpdate(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const cronId = decodePathPart(match[1] ?? "");
  if (!cronId) {
    writeJson(res, 400, { error: "invalid_cron_id" });
    return;
  }
  const current = ctx.agent.crons.get(cronId);
  if (!current) {
    writeJson(res, 404, { error: "not_found" });
    return;
  }
  if (current.internal) {
    writeJson(res, 403, { error: "internal_cron_locked" });
    return;
  }
  const body = readObject(await readJsonBody(req));
  const patch = {
    ...(typeof body.expression === "string" && body.expression.trim()
      ? { expression: body.expression.trim() }
      : {}),
    ...(typeof body.prompt === "string" ? { prompt: body.prompt } : {}),
    ...(typeof body.enabled === "boolean" ? { enabled: body.enabled } : {}),
    ...(typeof body.description === "string"
      ? { description: body.description }
      : {}),
  };
  try {
    const cron = await ctx.agent.crons.update(cronId, patch);
    writeJson(res, 200, { ok: true, cron: cronSnapshot(cron) });
  } catch (err) {
    writeJson(res, 400, {
      error: "cron_update_failed",
      message: err instanceof Error ? err.message : String(err),
    });
  }
}

async function handleCronDelete(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const cronId = decodePathPart(match[1] ?? "");
  if (!cronId) {
    writeJson(res, 400, { error: "invalid_cron_id" });
    return;
  }
  const current = ctx.agent.crons.get(cronId);
  if (current?.internal) {
    writeJson(res, 403, { error: "internal_cron_locked" });
    return;
  }
  try {
    const deleted = await ctx.agent.crons.delete(cronId);
    writeJson(res, 200, { ok: true, cronId, deleted });
  } catch (err) {
    writeJson(res, 400, {
      error: "cron_delete_failed",
      message: err instanceof Error ? err.message : String(err),
    });
  }
}

async function handleSkillsReload(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const result = await ctx.agent.reloadWorkspaceSkills();
  writeJson(res, 200, {
    ok: true,
    loaded: result.loaded,
    issues: result.issues,
    runtimeHooks: result.runtimeHooks,
  });
}

export const appRuntimeRoutes: RouteHandler[] = [
  route("GET", /^\/v1\/app\/runtime(?:\?.*)?$/, handleRuntime),
  route("GET", /^\/v1\/app\/sessions(?:\?.*)?$/, handleSessions),
  route("GET", /^\/v1\/app\/transcript(?:\?.*)?$/, handleTranscript),
  route("GET", /^\/v1\/app\/evidence(?:\?.*)?$/, handleEvidence),
  route("GET", /^\/v1\/app\/tasks(?:\?.*)?$/, handleTasks),
  route("GET", /^\/v1\/app\/tasks\/([^/?]+)(?:\?.*)?$/, handleTaskGet),
  route("GET", /^\/v1\/app\/tasks\/([^/?]+)\/output(?:\?.*)?$/, handleTaskOutput),
  route("POST", /^\/v1\/app\/tasks\/([^/?]+)\/stop(?:\?.*)?$/, handleTaskStop),
  route("GET", /^\/v1\/app\/crons(?:\?.*)?$/, handleCrons),
  route("POST", /^\/v1\/app\/crons(?:\?.*)?$/, handleCronCreate),
  route("PUT", /^\/v1\/app\/crons\/([^/?]+)(?:\?.*)?$/, handleCronUpdate),
  route("DELETE", /^\/v1\/app\/crons\/([^/?]+)(?:\?.*)?$/, handleCronDelete),
  route("GET", /^\/v1\/app\/artifacts(?:\?.*)?$/, handleArtifacts),
  route("GET", /^\/v1\/app\/artifacts\/([^/?]+)\/content(?:\?.*)?$/, handleArtifactContent),
  route("GET", /^\/v1\/app\/artifacts\/([^/?]+)\/download(?:\?.*)?$/, handleArtifactDownload),
  route("GET", /^\/v1\/app\/skills(?:\?.*)?$/, handleSkills),
  route("POST", /^\/v1\/app\/skills\/reload(?:\?.*)?$/, handleSkillsReload),
  route("GET", /^\/v1\/app\/workspace(?:\?.*)?$/, handleWorkspaceList),
  route("GET", /^\/v1\/app\/workspace\/file(?:\?.*)?$/, handleWorkspaceFile),
  route("PUT", /^\/v1\/app\/workspace\/file(?:\?.*)?$/, handleWorkspaceFilePut),
  route("GET", /^\/v1\/app\/workspace\/download(?:\?.*)?$/, handleWorkspaceDownload),
  route("GET", /^\/v1\/app\/memory(?:\?.*)?$/, handleMemoryList),
  route("GET", /^\/v1\/app\/memory\/file(?:\?.*)?$/, handleMemoryFile),
  route("GET", /^\/v1\/app\/memory\/search(?:\?.*)?$/, handleMemorySearch),
  route("POST", /^\/v1\/app\/memory\/compact(?:\?.*)?$/, handleMemoryCompact),
  route("POST", /^\/v1\/app\/memory\/reindex(?:\?.*)?$/, handleMemoryReindex),
  route("GET", /^\/v1\/app\/knowledge(?:\?.*)?$/, handleKnowledgeList),
  route("GET", /^\/v1\/app\/knowledge\/search(?:\?.*)?$/, handleKnowledgeSearch),
  route("GET", /^\/v1\/app\/knowledge\/file(?:\?.*)?$/, handleKnowledgeFile),
  route("PUT", /^\/v1\/app\/knowledge\/file(?:\?.*)?$/, handleKnowledgeFilePut),
];
