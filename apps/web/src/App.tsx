"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type ReactNode,
} from "react";
import { ChatSidebar } from "@/components/chat/chat-sidebar";
import { ChatMessages, type ChatMessagesHandle } from "@/components/chat/chat-messages";
import {
  ChatInput,
  type ChatInputHandle,
  type ChatInputCustomSkill,
  type ChatInputSendOptions,
} from "@/components/chat/chat-input";
import { ChatModelPicker } from "@/components/chat/chat-model-picker";
import { KbContextBar } from "@/components/chat/kb-context-bar";
import { KbSidePanel } from "@/components/chat/kb-side-panel";
import { RunInspectorDock } from "@/components/chat/run-inspector-dock";
import { useChatStore } from "@/lib/chat/chat-store";
import { buildReplyPreview } from "@/lib/chat/attachment-marker";
import {
  buildMessageContentWithKbContext,
  mergeKbDocReferences,
} from "@/lib/chat/kb-send";
import { detectMessageResponseLanguage } from "@/lib/chat/message-language";
import { MAX_QUEUED_MESSAGES } from "@/lib/chat/queue-constants";
import {
  buildEscCancelDecision,
  cancelActiveTurnWithQueueHandoff,
} from "@/lib/chat/interrupt-handoff";
import {
  canSteerMidTurn,
  getStreamingSendMode,
  type StreamingComposerMode,
} from "@/lib/chat/send-policy";
import {
  buildWorkspaceFileContentUrl,
  getWorkspaceFilePreviewKind,
  normalizeWorkspaceFileList,
  type WorkspaceFileEntry,
} from "@/lib/workspace/workspace-files";
import {
  KB_UPLOAD_EXTENSIONS,
  resolveKnowledgeUploadMimeType,
} from "@/lib/knowledge/upload-mime";
import { localizeChannel } from "@/lib/chat/channel-i18n";
import {
  buildMemoryModeChannelIdentity,
  type ChannelMemoryModeOption,
} from "@/lib/chat/channel-memory-mode";
import {
  buildChatExportFilename,
  buildChatExportMarkdown,
  normalizeSelectedChatExportMessages,
} from "@/lib/chat/export";
import type {
  BrowserFrame,
  Channel,
  ChannelState,
  ChatMessage,
  ControlEvent,
  ControlRequestRecord,
  CitationGateStatus,
  DocumentDraftPreview,
  InspectedSource,
  KbDocReference,
  MissionActivity,
  PatchPreview,
  QueuedMessage,
  ReplyTo,
  RuntimeTrace,
  SubagentActivity,
  TaskBoardTask,
  ToolActivity,
} from "@/lib/chat/types";
import type { PendingKbUpload } from "@/lib/chat/kb-uploads";
import type { KbCollectionWithDocs, KbDocEntry } from "@/hooks/use-kb-docs";
import {
  DashboardSidebar,
  OverviewDashboard,
  SettingsDashboard,
  KnowledgeDashboard,
  WorkspaceDashboard,
  MemoryDashboard,
  SkillsDashboard,
  UsageDashboard,
  ButtonLike,
  runtimeStatusLabel,
} from "@/components/dashboard";

const BOT_ID = "local";
const BOT_NAME = "Magi_Local";
const DEFAULT_CHANNEL = "general";
const DEFAULT_MODEL = "auto";
const DEFAULT_ROUTER = "standard";
const WORKSPACE_SCAN_LIMIT = 220;
const EDITABLE_WORKSPACE_ROOTS = new Set([
  ".magi",
  ".hipocampus",
  "compaction",
  "compactions",
  "contracts",
  "harness",
  "harness-rules",
  "harnesses",
  "hooks",
  "memory",
  "prompts",
  "system-prompts",
]);

const storage = {
  agentUrl: "magi.agent.app.agentUrl",
  token: "magi.agent.app.token",
  sessionKey: "magi.agent.app.sessionKey",
  modelOverride: "magi.agent.app.modelOverride",
};

function downloadMarkdownFile(filename: string, markdown: string): void {
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

type JsonRecord = Record<string, unknown>;
type RuntimePhase = NonNullable<ChannelState["turnPhase"]>;
type AppRoute =
  | "chat"
  | "overview"
  | "settings"
  | "usage"
  | "skills"
  | "workspace"
  | "knowledge"
  | "memory";
type DashboardRoute = Exclude<AppRoute, "chat">;
type ProviderName = "anthropic" | "openai" | "google" | "openai-compatible";
type RuntimeCheckStatus = "not_checked" | "checking" | "active" | "unavailable";
type SkillDirectoryFilter = "all" | "prompt" | "script" | "hooks" | "issues";

interface AppBootstrap {
  agentUrl?: string;
  token?: string;
}

interface KnowledgeDocumentRow {
  collection?: string;
  filename?: string;
  title?: string;
  path?: string;
  objectKey?: string;
  sizeBytes?: number;
  mtimeMs?: number;
}

interface WorkspaceEntryRow {
  name?: string;
  path?: string;
  type?: string;
  sizeBytes?: number;
  mtimeMs?: number;
}

interface MemoryFileEntry {
  path: string;
  sizeBytes: number;
  mtimeMs: number | null;
}

interface MemorySearchResult {
  path?: string;
  score?: number;
  contentPreview?: string;
  context?: string;
}

interface LocalConfigState {
  path: string;
  exists: boolean;
  provider: ProviderName;
  model: string;
  baseUrl: string;
  apiKeyEnvVar: string;
  gatewayTokenEnvVar: string;
  workspace: string;
  contextWindow: string;
  maxOutputTokens: string;
  supportsThinking: boolean;
  restartRequired: boolean;
  liveReloadSupported: boolean;
}

interface LocalConfigSaveInput {
  llm: {
    provider: ProviderName;
    model: string;
    baseUrl?: string;
    apiKeyEnvVar?: string;
    capabilities: {
      contextWindow?: number;
      maxOutputTokens?: number;
      supportsThinking: boolean;
    };
  };
  server?: {
    gatewayTokenEnvVar?: string;
  };
  workspace?: string;
}

interface SkillIssueDetail {
  key: string;
  title: string;
  reason: string;
  detail: string;
  path: string;
  lookupKeys: string[];
}

interface SkillDirectoryItem {
  name: string;
  path: string;
  tags: string[];
  promptOnly: boolean;
  scriptBacked: boolean;
  runtimeHooks: number;
  issues: SkillIssueDetail[];
}

function defaultChannel(): Channel {
  return {
    id: "local-general",
    name: DEFAULT_CHANNEL,
    display_name: "General",
    position: 0,
    category: "General",
    created_at: new Date(0).toISOString(),
  };
}

function nowId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function normalizeChannelName(value: string): string {
  return (
    value
      .trim()
      .toLowerCase()
      .replace(/\s+/g, "-")
      .replace(/[^a-z0-9-_]/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "") || `ch-${Date.now().toString(36)}`
  );
}

function normalizeAgentUrl(value: string): string {
  const trimmed = value.trim();
  return trimmed ? trimmed.replace(/\/+$/, "") : window.location.origin;
}

function defaultSessionKey(channel: string): string {
  return `agent:local:app:${channel}`;
}

function sessionKeyForChannel(channel: string): string {
  const raw = window.localStorage.getItem(storage.sessionKey)?.trim();
  return raw || defaultSessionKey(channel);
}

function decodePathPart(value: string | undefined): string | null {
  if (!value) return null;
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function isDashboardRoute(value: string | null): value is DashboardRoute {
  return (
    value === "overview" ||
    value === "settings" ||
    value === "usage" ||
    value === "skills" ||
    value === "workspace" ||
    value === "knowledge" ||
    value === "memory"
  );
}

function routeFromPathname(pathname: string): AppRoute {
  const parts = pathname.replace(/^\/app(?=\/)/, "").split("/").filter(Boolean);
  if (parts[0] !== "dashboard") return "chat";
  const section = decodePathPart(parts[2] ?? parts[1]);
  if (section === "chat") return "chat";
  return isDashboardRoute(section) ? section : "overview";
}

function channelFromPathname(pathname: string): string | null {
  const parts = pathname.replace(/^\/app(?=\/)/, "").split("/").filter(Boolean);
  if (parts[0] !== "dashboard" || parts[2] !== "chat") return null;
  const channel = decodePathPart(parts[3]);
  return channel ? normalizeChannelName(channel) : null;
}

function pathForRoute(route: AppRoute, channel = DEFAULT_CHANNEL): string {
  if (route === "chat") {
    return `/dashboard/${BOT_ID}/chat/${encodeURIComponent(channel)}`;
  }
  return `/dashboard/${BOT_ID}/${route}`;
}

function getStored(key: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return window.localStorage.getItem(key) || fallback;
}

function getConfiguredModelSelection(): string {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(storage.modelOverride);
  }
  return DEFAULT_MODEL;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

function asArray(value: unknown): JsonRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is JsonRecord => !!item && typeof item === "object")
    : [];
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function normalizedLookupKey(value: string): string {
  return value.trim().toLowerCase();
}

function compactUniqueStrings(values: string[]): string[] {
  return Array.from(
    new Set(values.map((value) => value.trim()).filter((value) => value.length > 0)),
  );
}

function normalizeSkillIssueDetails(issues: JsonRecord[]): SkillIssueDetail[] {
  return issues.map((issue, index) => {
    const skillName = asString(issue.skillName);
    const dir = asString(issue.dir);
    const path = asString(issue.path, dir);
    const title = skillName || dir || `Issue ${index + 1}`;
    const reason = asString(issue.reason, "unknown_issue");
    const detail = asString(issue.detail);
    const lookupKeys = compactUniqueStrings([skillName, dir, path, title]).map(normalizedLookupKey);
    return {
      key: `${title}-${reason}-${index}`,
      title,
      reason,
      detail,
      path,
      lookupKeys,
    };
  });
}

function normalizeSkillDirectoryItems(
  loaded: JsonRecord[],
  issues: JsonRecord[],
): SkillDirectoryItem[] {
  const issueDetails = normalizeSkillIssueDetails(issues);
  return loaded.map((skill, index) => {
    const name = asString(skill.name, `skill-${index + 1}`);
    const dir = asString(skill.dir);
    const path = asString(skill.path, dir);
    const lookupKeys = compactUniqueStrings([name, dir, path]).map(normalizedLookupKey);
    const matchedIssues = issueDetails.filter((issue) =>
      issue.lookupKeys.some((key) => lookupKeys.includes(key)),
    );
    return {
      name,
      path,
      tags: asStringArray(skill.tags),
      promptOnly: skill.promptOnly === true,
      scriptBacked: skill.scriptBacked === true,
      runtimeHooks: Math.max(0, Math.floor(asNumber(skill.runtimeHooks, 0))),
      issues: matchedIssues,
    };
  });
}

function skillSearchText(skill: SkillDirectoryItem): string {
  return [
    skill.name,
    skill.path,
    ...skill.tags,
    ...skill.issues.flatMap((issue) => [issue.title, issue.reason, issue.detail, issue.path]),
  ]
    .join(" ")
    .toLowerCase();
}

function filterSkillDirectoryItem(skill: SkillDirectoryItem, filter: SkillDirectoryFilter): boolean {
  if (filter === "prompt") return skill.promptOnly;
  if (filter === "script") return skill.scriptBacked;
  if (filter === "hooks") return skill.runtimeHooks > 0;
  if (filter === "issues") return skill.issues.length > 0;
  return true;
}

function skillTypeLabel(skill: SkillDirectoryItem): string {
  if (skill.scriptBacked) return "Script skill";
  if (skill.promptOnly) return "Prompt skill";
  return "Skill";
}

function asProviderName(value: unknown): ProviderName {
  return value === "anthropic" ||
    value === "openai" ||
    value === "google" ||
    value === "openai-compatible"
    ? value
    : "openai-compatible";
}

function localConfigFromPayload(payload: JsonRecord): LocalConfigState {
  const config = asRecord(payload.config);
  const llm = asRecord(config.llm);
  const server = asRecord(config.server);
  const capabilities = asRecord(llm.capabilities);
  return {
    path: asString(payload.path, asString(config.path, "magi-agent.yaml")),
    exists: payload.exists === true,
    provider: asProviderName(llm.provider),
    model: asString(llm.model, "llama3.1"),
    baseUrl: asString(llm.baseUrl),
    apiKeyEnvVar: asString(llm.apiKeyEnvVar),
    gatewayTokenEnvVar: asString(server.gatewayTokenEnvVar),
    workspace: asString(config.workspace, "./workspace"),
    contextWindow:
      typeof capabilities.contextWindow === "number"
        ? String(capabilities.contextWindow)
        : "",
    maxOutputTokens:
      typeof capabilities.maxOutputTokens === "number"
        ? String(capabilities.maxOutputTokens)
        : "",
    supportsThinking: capabilities.supportsThinking === true,
    restartRequired: payload.restartRequired === true,
    liveReloadSupported: payload.liveReloadSupported === true,
  };
}

function optionalNumber(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : undefined;
}

function configSavePayload(config: LocalConfigState): LocalConfigSaveInput {
  return {
    llm: {
      provider: config.provider,
      model: config.model.trim() || "llama3.1",
      ...(config.baseUrl.trim() ? { baseUrl: config.baseUrl.trim() } : {}),
      ...(config.apiKeyEnvVar.trim() ? { apiKeyEnvVar: config.apiKeyEnvVar.trim() } : {}),
      capabilities: {
        ...(optionalNumber(config.contextWindow) ? { contextWindow: optionalNumber(config.contextWindow) } : {}),
        ...(optionalNumber(config.maxOutputTokens) ? { maxOutputTokens: optionalNumber(config.maxOutputTokens) } : {}),
        supportsThinking: config.supportsThinking,
      },
    },
    ...(config.gatewayTokenEnvVar.trim()
      ? { server: { gatewayTokenEnvVar: config.gatewayTokenEnvVar.trim() } }
      : {}),
    ...(config.workspace.trim() ? { workspace: config.workspace.trim() } : {}),
  };
}

function preview(value: unknown, max = 400): string | undefined {
  if (value === undefined || value === null) return undefined;
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (!text) return undefined;
  return text.length > max ? `${text.slice(0, max - 3)}...` : text;
}

function isRuntimePhase(value: unknown): value is RuntimePhase {
  return (
    value === "pending" ||
    value === "planning" ||
    value === "executing" ||
    value === "verifying" ||
    value === "committing" ||
    value === "compacting" ||
    value === "committed" ||
    value === "aborted"
  );
}

function createSseParser(onEvent: (eventName: string, rawData: string) => void) {
  let buffer = "";
  return (chunk: string) => {
    buffer += chunk;
    const frames = buffer.split(/\n\n/);
    buffer = frames.pop() || "";
    for (const frame of frames) {
      const lines = frame.split(/\n/);
      let eventName = "message";
      const data: string[] = [];
      for (const line of lines) {
        if (line.startsWith(":")) continue;
        if (line.startsWith("event:")) eventName = line.slice("event:".length).trim();
        if (line.startsWith("data:")) data.push(line.slice("data:".length).trimStart());
      }
      if (data.length > 0) onEvent(eventName, data.join("\n"));
    }
  };
}

function makeControlRequest(raw: JsonRecord, fallbackSessionKey: string): ControlRequestRecord | null {
  const requestId = asString(raw.requestId);
  const prompt = asString(raw.prompt);
  if (!requestId || !prompt) return null;
  const kind =
    raw.kind === "plan_approval" || raw.kind === "user_question"
      ? raw.kind
      : "tool_permission";
  const state =
    raw.state === "approved" ||
    raw.state === "denied" ||
    raw.state === "answered" ||
    raw.state === "cancelled" ||
    raw.state === "timed_out"
      ? raw.state
      : "pending";
  const source =
    raw.source === "mcp" ||
    raw.source === "child-agent" ||
    raw.source === "plan" ||
    raw.source === "system"
      ? raw.source
      : "turn";
  return {
    requestId,
    kind,
    state,
    sessionKey: asString(raw.sessionKey, fallbackSessionKey),
    source,
    prompt,
    createdAt: asNumber(raw.createdAt, Date.now()),
    expiresAt: asNumber(raw.expiresAt, Date.now() + 10 * 60_000),
    ...(typeof raw.turnId === "string" ? { turnId: raw.turnId } : {}),
    ...(typeof raw.channelName === "string" ? { channelName: raw.channelName } : {}),
    ...(raw.proposedInput !== undefined ? { proposedInput: raw.proposedInput } : {}),
  };
}

function normalizeTaskBoard(payload: JsonRecord): TaskBoardTask[] {
  return asArray(payload.tasks).map((task, index) => {
    const status =
      task.status === "in_progress" ||
      task.status === "completed" ||
      task.status === "cancelled"
        ? task.status
        : "pending";
    return {
      id: asString(task.id, `task-${index + 1}`),
      title: asString(task.title, asString(task.name, `Task ${index + 1}`)),
      description: asString(task.description, asString(task.detail)),
      status,
      ...(Array.isArray(task.dependsOn)
        ? { dependsOn: task.dependsOn.filter((item): item is string => typeof item === "string") }
        : {}),
      ...(typeof task.parallelGroup === "string" ? { parallelGroup: task.parallelGroup } : {}),
    };
  });
}

function normalizeSubagentStatus(type: string, status: unknown): SubagentActivity["status"] {
  if (type === "child_completed" || type === "spawn_result" || status === "completed" || status === "ok") {
    return "done";
  }
  if (type === "child_cancelled" || status === "aborted") return "cancelled";
  if (type === "child_failed" || status === "failed" || status === "error") return "error";
  if (status === "waiting") return "waiting";
  return "running";
}

function normalizeBrowserFrame(payload: JsonRecord): BrowserFrame | null {
  const imageBase64 = asString(payload.imageBase64);
  if (!imageBase64) return null;
  const contentType =
    payload.contentType === "image/jpeg" || payload.contentType === "image/png"
      ? payload.contentType
      : "image/png";
  return {
    action: asString(payload.action, "browser"),
    imageBase64,
    contentType,
    capturedAt: asNumber(payload.capturedAt, Date.now()),
    ...(typeof payload.url === "string" && payload.url ? { url: payload.url } : {}),
  };
}

function normalizeDocumentDraft(payload: JsonRecord): DocumentDraftPreview | null {
  const id = asString(payload.id);
  const contentPreview = asString(payload.contentPreview);
  if (!id && !contentPreview) return null;
  const format = payload.format === "txt" ? "txt" : "md";
  return {
    id: id || nowId("document-draft"),
    ...(typeof payload.filename === "string" && payload.filename ? { filename: payload.filename } : {}),
    format,
    status: payload.status === "done" ? "done" : "streaming",
    contentPreview,
    contentLength: Math.max(0, Math.floor(asNumber(payload.contentLength, contentPreview.length))),
    truncated: payload.truncated === true,
    updatedAt: Date.now(),
  };
}

function normalizePatchPreview(payload: JsonRecord): PatchPreview | null {
  const files = asArray(payload.files)
    .map((file) => {
      const filePath = asString(file.path);
      if (!filePath) return null;
      const operation =
        file.operation === "create" || file.operation === "delete" || file.operation === "update"
          ? file.operation
          : "update";
      return {
        path: filePath,
        operation,
        hunks: Math.max(0, Math.floor(asNumber(file.hunks, 0))),
        addedLines: Math.max(0, Math.floor(asNumber(file.addedLines, 0))),
        removedLines: Math.max(0, Math.floor(asNumber(file.removedLines, 0))),
        ...(typeof file.oldSha256 === "string" ? { oldSha256: file.oldSha256 } : {}),
        ...(typeof file.newSha256 === "string" ? { newSha256: file.newSha256 } : {}),
      };
    })
    .filter((file): file is PatchPreview["files"][number] => file !== null);
  const changedFiles = asStringArray(payload.changedFiles);
  if (files.length === 0 && changedFiles.length === 0) return null;
  return {
    dryRun: payload.dryRun === true,
    changedFiles: changedFiles.length > 0 ? changedFiles : files.map((file) => file.path),
    createdFiles: asStringArray(payload.createdFiles),
    deletedFiles: asStringArray(payload.deletedFiles),
    files,
  };
}

function sourceKind(value: unknown): InspectedSource["kind"] {
  if (
    value === "web_search" ||
    value === "web_fetch" ||
    value === "browser" ||
    value === "kb" ||
    value === "file" ||
    value === "external_repo" ||
    value === "external_doc" ||
    value === "subagent_result"
  ) {
    return value;
  }
  return "file";
}

function normalizeInspectedSource(payload: JsonRecord): InspectedSource | null {
  const sourceId = asString(payload.sourceId);
  const uri = asString(payload.uri);
  if (!sourceId || !uri) return null;
  const trustTier =
    payload.trustTier === "primary" ||
    payload.trustTier === "official" ||
    payload.trustTier === "secondary" ||
    payload.trustTier === "unknown"
      ? payload.trustTier
      : undefined;
  return {
    sourceId,
    kind: sourceKind(payload.kind),
    uri,
    inspectedAt: asNumber(payload.inspectedAt, Date.now()),
    ...(typeof payload.turnId === "string" ? { turnId: payload.turnId } : {}),
    ...(typeof payload.toolName === "string" ? { toolName: payload.toolName } : {}),
    ...(typeof payload.toolUseId === "string" ? { toolUseId: payload.toolUseId } : {}),
    ...(typeof payload.title === "string" ? { title: payload.title } : {}),
    ...(typeof payload.contentHash === "string" ? { contentHash: payload.contentHash } : {}),
    ...(typeof payload.contentType === "string" ? { contentType: payload.contentType } : {}),
    ...(trustTier ? { trustTier } : {}),
    ...(Array.isArray(payload.snippets) ? { snippets: asStringArray(payload.snippets).slice(0, 4) } : {}),
  };
}

function appendInspectedSource(
  current: InspectedSource[] | undefined,
  source: InspectedSource,
): InspectedSource[] {
  const next = [...(current ?? []).filter((item) => item.sourceId !== source.sourceId), source];
  return next.slice(-40);
}

function normalizeCitationGate(payload: JsonRecord): CitationGateStatus | null {
  if (payload.ruleId !== "claim-citation-gate") return null;
  const verdict =
    payload.verdict === "ok" || payload.verdict === "violation" || payload.verdict === "pending"
      ? payload.verdict
      : "pending";
  return {
    ruleId: "claim-citation-gate",
    verdict,
    checkedAt: Date.now(),
    ...(typeof payload.detail === "string" ? { detail: payload.detail } : {}),
  };
}

function runtimeTracePhase(value: unknown): RuntimeTrace["phase"] {
  if (
    value === "retry_scheduled" ||
    value === "retry_aborted" ||
    value === "terminal_abort"
  ) {
    return value;
  }
  return "verifier_blocked";
}

function runtimeTraceSeverity(value: unknown): RuntimeTrace["severity"] {
  if (value === "warning" || value === "error") return value;
  return "info";
}

function normalizeRuntimeTrace(payload: JsonRecord): RuntimeTrace | null {
  const turnId = asString(payload.turnId);
  const title = asString(payload.title);
  if (!turnId || !title) return null;
  return {
    turnId,
    title,
    phase: runtimeTracePhase(payload.phase),
    severity: runtimeTraceSeverity(payload.severity),
    receivedAt: Date.now(),
    ...(typeof payload.detail === "string" ? { detail: payload.detail } : {}),
    ...(typeof payload.reasonCode === "string" ? { reasonCode: payload.reasonCode } : {}),
    ...(typeof payload.ruleId === "string" ? { ruleId: payload.ruleId } : {}),
    ...(typeof payload.attempt === "number" ? { attempt: payload.attempt } : {}),
    ...(typeof payload.maxAttempts === "number" ? { maxAttempts: payload.maxAttempts } : {}),
    ...(typeof payload.retryable === "boolean" ? { retryable: payload.retryable } : {}),
    ...(typeof payload.requiredAction === "string" ? { requiredAction: payload.requiredAction } : {}),
  };
}

function missionStatus(value: unknown): MissionActivity["status"] {
  if (
    value === "queued" ||
    value === "running" ||
    value === "blocked" ||
    value === "waiting" ||
    value === "completed" ||
    value === "failed" ||
    value === "cancelled" ||
    value === "paused"
  ) {
    return value;
  }
  return "running";
}

function missionStatusFromEvent(eventType: string): MissionActivity["status"] {
  const normalized = eventType.toLowerCase();
  if (normalized.includes("complete") || normalized.includes("done")) return "completed";
  if (normalized.includes("fail") || normalized.includes("error")) return "failed";
  if (normalized.includes("cancel")) return "cancelled";
  if (normalized.includes("block")) return "blocked";
  if (normalized.includes("pause")) return "paused";
  if (normalized.includes("wait")) return "waiting";
  return "running";
}

function appendMissionActivity(
  current: MissionActivity[] | undefined,
  patch: MissionActivity,
): MissionActivity[] {
  const next = [...(current ?? [])];
  const index = next.findIndex((item) => item.id === patch.id);
  if (index >= 0) next[index] = { ...next[index], ...patch };
  else next.push(patch);
  return next.slice(-32);
}

function appendToolActivity(current: ToolActivity[] | undefined, patch: ToolActivity): ToolActivity[] {
  const next = [...(current ?? [])];
  const index = next.findIndex((item) => item.id === patch.id);
  if (index >= 0) {
    next[index] = {
      ...next[index],
      ...patch,
      label: patch.label === patch.id ? next[index].label : patch.label,
      startedAt: next[index].startedAt,
    };
  } else {
    next.push(patch);
  }
  return next.slice(-24);
}

function appendSubagentActivity(
  current: SubagentActivity[] | undefined,
  patch: SubagentActivity,
): SubagentActivity[] {
  const next = [...(current ?? [])];
  const index = next.findIndex((item) => item.taskId === patch.taskId);
  if (index >= 0) next[index] = { ...next[index], ...patch };
  else next.push(patch);
  return next.slice(-32);
}

function toKbCollections(payload: JsonRecord): KbCollectionWithDocs[] {
  const documents = asArray(payload.documents) as KnowledgeDocumentRow[];
  if (documents.length === 0) return [];

  const grouped = new Map<string, KbDocEntry[]>();
  for (const [index, doc] of documents.entries()) {
    const collectionName = doc.collection || "knowledge";
    const collectionId = `local-${collectionName}`;
    const id = doc.objectKey || doc.path || doc.filename || `doc-${index}`;
    const entry: KbDocEntry = {
      id,
      filename: doc.title || doc.filename || id,
      status: "ready",
      scope: "personal",
      orgId: null,
      path: doc.path || doc.objectKey || id,
      sort_order: index,
      source_external_id: doc.objectKey || doc.path || id,
      source_parent_external_id: null,
      parent_document_id: null,
      collectionId,
      collectionName,
    };
    grouped.set(collectionName, [...(grouped.get(collectionName) ?? []), entry]);
  }

  return Array.from(grouped.entries()).map(([name, docs]) => ({
    id: `local-${name}`,
    name,
    scope: "personal",
    orgId: null,
    docs,
  }));
}

function toWorkspaceFiles(payload: JsonRecord): WorkspaceFileEntry[] {
  const entries = asArray(payload.entries) as WorkspaceEntryRow[];
  return normalizeWorkspaceFileList(
    entries
      .filter((entry) => entry.type === "file")
      .map((entry) => ({
        path: entry.path || entry.name || "",
        size: entry.sizeBytes ?? 0,
        modifiedAt:
          typeof entry.mtimeMs === "number"
            ? new Date(entry.mtimeMs).toISOString()
            : null,
      }))
      .filter((entry) => entry.path.length > 0),
  );
}

function shouldScanWorkspaceDirectory(entryPath: string, depth: number): boolean {
  if (depth >= 4) return false;
  const root = entryPath.split("/").filter(Boolean)[0] ?? "";
  return EDITABLE_WORKSPACE_ROOTS.has(root);
}


function LocalDashboardShell({
  route,
  runtimeSnapshot,
  runtimeStatus,
  skillsSnapshot,
  skillsLoading,
  config,
  configLoading,
  configSaving,
  configNotice,
  configError,
  agentUrl,
  token,
  kbCollections,
  kbLoading,
  kbRefreshing,
  workspaceFiles,
  workspaceLoading,
  workspaceRefreshing,
  memoryFiles,
  memoryStatus,
  memoryLoading,
  memoryRefreshing,
  setAgentUrl,
  setToken,
  onNavigate,
  onRefreshAll,
  onRefreshKnowledge,
  onRefreshWorkspace,
  onRefreshMemory,
  onRefreshSkills,
  onUploadKnowledge,
  onReadWorkspaceFile,
  onSaveWorkspaceFile,
  onMemorySearch,
  onMemoryReadFile,
  onMemorySaveFile,
  onMemoryDeleteFiles,
  onMemoryCompact,
  onMemoryReindex,
  onSaveConnection,
  onCheckRuntime,
  onSaveConfig,
  onReloadConfig,
  onRestartRuntime,
  getJson,
  sendJson,
  putJson,
  deleteJson,
}: {
  route: DashboardRoute;
  runtimeSnapshot: JsonRecord | null;
  runtimeStatus: RuntimeCheckStatus;
  skillsSnapshot: JsonRecord | null;
  skillsLoading: boolean;
  config: LocalConfigState | null;
  configLoading: boolean;
  configSaving: boolean;
  configNotice: string | null;
  configError: string | null;
  agentUrl: string;
  token: string;
  kbCollections: KbCollectionWithDocs[];
  kbLoading: boolean;
  kbRefreshing: boolean;
  workspaceFiles: WorkspaceFileEntry[];
  workspaceLoading: boolean;
  workspaceRefreshing: boolean;
  memoryFiles: MemoryFileEntry[];
  memoryStatus: JsonRecord | null;
  memoryLoading: boolean;
  memoryRefreshing: boolean;
  setAgentUrl: (value: string) => void;
  setToken: (value: string) => void;
  onNavigate: (route: AppRoute) => void;
  onRefreshAll: () => void;
  onRefreshKnowledge: () => void;
  onRefreshWorkspace: () => void;
  onRefreshMemory: () => void;
  onRefreshSkills: () => void;
  onUploadKnowledge: (files: FileList) => Promise<void>;
  onReadWorkspaceFile: (path: string) => Promise<string>;
  onSaveWorkspaceFile: (path: string, content: string) => Promise<void>;
  onMemorySearch: (query: string) => Promise<JsonRecord>;
  onMemoryReadFile: (path: string) => Promise<string>;
  onMemorySaveFile: (path: string, content: string) => Promise<void>;
  onMemoryDeleteFiles: (paths: string[]) => Promise<void>;
  onMemoryCompact: () => Promise<void>;
  onMemoryReindex: () => Promise<void>;
  onSaveConnection: () => void;
  onCheckRuntime: () => void;
  onSaveConfig: (config: LocalConfigState) => Promise<void>;
  onReloadConfig: () => Promise<void>;
  onRestartRuntime: () => Promise<void>;
  getJson: (path: string) => Promise<JsonRecord>;
  sendJson: (path: string, body: JsonRecord) => Promise<JsonRecord>;
  putJson: (path: string, body: JsonRecord) => Promise<JsonRecord>;
  deleteJson: (path: string, body: JsonRecord) => Promise<JsonRecord>;
}) {
  const mobileRoutes: Array<{ route: AppRoute; label: string }> = [
    { route: "chat", label: "Chat" },
    { route: "overview", label: "Overview" },
    { route: "settings", label: "Settings" },
    { route: "usage", label: "Usage" },
    { route: "skills", label: "Skills" },
    { route: "knowledge", label: "Knowledge" },
    { route: "memory", label: "Memory" },
    { route: "workspace", label: "Workspace" },
  ];

  return (
    <div className="flex h-full min-w-0 flex-1 bg-background">
      <DashboardSidebar
        activeRoute={route}
        runtimeStatus={runtimeStatus}
        onNavigate={onNavigate}
        onRefresh={onRefreshAll}
      />
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="border-b border-gray-200 bg-gray-50 px-4 py-3 md:hidden">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-foreground">{BOT_NAME}</div>
              <div className="mt-1 flex items-center gap-2 text-xs text-secondary">
                <span className={`h-2 w-2 rounded-full ${runtimeStatus === "active" ? "bg-emerald-400" : "bg-gray-300"}`} />
                {runtimeStatusLabel(runtimeStatus)}
              </div>
            </div>
            <ButtonLike variant="secondary" onClick={() => onNavigate("chat")} className="min-h-0 px-3 py-2">
              Chat
            </ButtonLike>
          </div>
          <select
            value={route}
            onChange={(event) => onNavigate(event.target.value as AppRoute)}
            className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-foreground"
            aria-label="Dashboard section"
          >
            {mobileRoutes.map((item) => (
              <option key={item.route} value={item.route}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
        <div className="min-w-0 p-4 sm:p-6 md:p-8">
          {route === "overview" && (
            <OverviewDashboard
              runtimeSnapshot={runtimeSnapshot}
              runtimeStatus={runtimeStatus}
              kbCollections={kbCollections}
              workspaceFiles={workspaceFiles}
              onNavigate={onNavigate}
            />
          )}
          {route === "settings" && (
            <SettingsDashboard
              agentUrl={agentUrl}
              token={token}
              runtimeStatus={runtimeStatus}
              config={config}
              configLoading={configLoading}
              configSaving={configSaving}
              configNotice={configNotice}
              configError={configError}
              setAgentUrl={setAgentUrl}
              setToken={setToken}
              onSaveConnection={onSaveConnection}
              onCheckRuntime={onCheckRuntime}
              onSaveConfig={onSaveConfig}
              onReloadConfig={onReloadConfig}
              onRestartRuntime={onRestartRuntime}
              getJson={getJson}
              sendJson={sendJson}
              putJson={putJson}
              deleteJson={deleteJson}
            />
          )}
          {route === "usage" && <UsageDashboard runtimeSnapshot={runtimeSnapshot} />}
          {route === "skills" && (
            <SkillsDashboard
              skillsSnapshot={skillsSnapshot}
              loading={skillsLoading}
              onRefresh={onRefreshSkills}
            />
          )}
          {route === "knowledge" && (
            <KnowledgeDashboard
              kbCollections={kbCollections}
              loading={kbLoading}
              refreshing={kbRefreshing}
              onRefresh={onRefreshKnowledge}
              onUpload={onUploadKnowledge}
            />
          )}
          {route === "workspace" && (
            <WorkspaceDashboard
              workspaceFiles={workspaceFiles}
              loading={workspaceLoading}
              refreshing={workspaceRefreshing}
              onRefresh={onRefreshWorkspace}
              onReadFile={onReadWorkspaceFile}
              onSaveFile={onSaveWorkspaceFile}
            />
          )}
          {route === "memory" && (
            <MemoryDashboard
              memoryFiles={memoryFiles}
              memoryStatus={memoryStatus}
              loading={memoryLoading}
              refreshing={memoryRefreshing}
              onRefresh={onRefreshMemory}
              onSearch={onMemorySearch}
              onReadFile={onMemoryReadFile}
              onSaveFile={onMemorySaveFile}
              onDeleteFiles={onMemoryDeleteFiles}
              onCompact={onMemoryCompact}
              onReindex={onMemoryReindex}
            />
          )}
        </div>
      </main>
    </div>
  );
}

export function App() {
  const store = useChatStore();
  const chatMessagesRef = useRef<ChatMessagesHandle>(null);
  const chatInputRef = useRef<ChatInputHandle>(null);
  const sawAgentEventRef = useRef(false);
  const interruptHandoffChannelsRef = useRef(new Set<string>());
  const [appRoute, setAppRoute] = useState<AppRoute>(() => routeFromPathname(window.location.pathname));
  const [agentUrl, setAgentUrl] = useState(() => getStored(storage.agentUrl, window.location.origin));
  const [token, setToken] = useState(() => getStored(storage.token, ""));
  const [editing, setEditing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeCheckStatus>("not_checked");
  const [runtimeSnapshot, setRuntimeSnapshot] = useState<JsonRecord | null>(null);
  const [skillsSnapshot, setSkillsSnapshot] = useState<JsonRecord | null>(null);
  const [skillsLoading, setSkillsLoading] = useState(true);
  const [localConfig, setLocalConfig] = useState<LocalConfigState | null>(null);
  const [configLoading, setConfigLoading] = useState(true);
  const [configSaving, setConfigSaving] = useState(false);
  const [configNotice, setConfigNotice] = useState<string | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [customCategories, setCustomCategories] = useState<string[]>([]);
  const [selectedKbDocs, setSelectedKbDocs] = useState<KbDocReference[]>([]);
  const [kbCollections, setKbCollections] = useState<KbCollectionWithDocs[]>([]);
  const [kbLoading, setKbLoading] = useState(true);
  const [kbRefreshing, setKbRefreshing] = useState(false);
  const [workspaceFiles, setWorkspaceFiles] = useState<WorkspaceFileEntry[]>([]);
  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [workspaceRefreshing, setWorkspaceRefreshing] = useState(false);
  const [memoryFiles, setMemoryFiles] = useState<MemoryFileEntry[]>([]);
  const [memoryStatus, setMemoryStatus] = useState<JsonRecord | null>(null);
  const [memoryLoading, setMemoryLoading] = useState(true);
  const [memoryRefreshing, setMemoryRefreshing] = useState(false);
  const [uploadStates, setUploadStates] = useState<Record<string, PendingKbUpload>>({});
  const [replyingTo, setReplyingTo] = useState<ReplyTo | null>(null);
  const [streamingComposerMode, setStreamingComposerMode] = useState<StreamingComposerMode>("queue");
  const [escArmedUntil, setEscArmedUntil] = useState<number | null>(null);
  const [rightWorkInspectorOpen, setRightWorkInspectorOpen] = useState(() => {
    try {
      return (
        localStorage.getItem("magi:kbPanelExpanded") !== "0" &&
        localStorage.getItem("magi:rightInspectorView") !== "knowledge"
      );
    } catch {
      return true;
    }
  });
  const [modelSelection, setModelSelection] = useState(getConfiguredModelSelection);
  const [routerType, setRouterType] = useState(DEFAULT_ROUTER);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const dragCounterRef = useRef(0);

  const normalizedBase = useMemo(() => normalizeAgentUrl(agentUrl), [agentUrl]);
  const activeChannel = store.activeChannel || DEFAULT_CHANNEL;
  const channelState = store.channelStates[activeChannel] ?? store.getChannelState(activeChannel);
  const queuedForChannel = store.queuedMessages[activeChannel] ?? [];
  const controlsForChannel = store.controlRequests[activeChannel] ?? [];
  const allKbDocs = useMemo(() => kbCollections.flatMap((collection) => collection.docs), [kbCollections]);
  const chatInputCustomSkills = useMemo<ChatInputCustomSkill[]>(() => {
    return normalizeSkillDirectoryItems(asArray(skillsSnapshot?.loaded), asArray(skillsSnapshot?.issues))
      .filter((skill) => skill.promptOnly || skill.scriptBacked || skill.runtimeHooks > 0)
      .map((skill) => ({
        name: skill.name,
        title: skill.name,
        tags: skill.tags,
        description: skill.path,
      }));
  }, [skillsSnapshot]);
  const anyStreaming = Object.values(store.channelStates).some((state) => state.streaming);

  const authHeaders = useCallback(
    (json = false): HeadersInit => ({
      ...(json ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "X-Magi-Session-Key": sessionKeyForChannel(activeChannel),
    }),
    [activeChannel, token],
  );

  const getAccessToken = useCallback(async () => token || null, [token]);

  const getJson = useCallback(
    async (path: string): Promise<JsonRecord> => {
      const response = await fetch(`${normalizedBase}${path}`, {
        headers: authHeaders(),
      });
      const payload = (await response.json().catch(() => ({}))) as JsonRecord;
      if (!response.ok) {
        throw new Error(asString(payload.error, response.statusText));
      }
      return payload;
    },
    [authHeaders, normalizedBase],
  );

  const sendJson = useCallback(
    async (path: string, body: JsonRecord): Promise<JsonRecord> => {
      const response = await fetch(`${normalizedBase}${path}`, {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify(body),
      });
      const payload = (await response.json().catch(() => ({}))) as JsonRecord;
      if (!response.ok) {
        throw new Error(asString(payload.error, response.statusText));
      }
      return payload;
    },
    [authHeaders, normalizedBase],
  );

  const putJson = useCallback(
    async (path: string, body: JsonRecord): Promise<JsonRecord> => {
      const response = await fetch(`${normalizedBase}${path}`, {
        method: "PUT",
        headers: authHeaders(true),
        body: JSON.stringify(body),
      });
      const payload = (await response.json().catch(() => ({}))) as JsonRecord;
      if (!response.ok) {
        throw new Error(asString(payload.error, response.statusText));
      }
      return payload;
    },
    [authHeaders, normalizedBase],
  );

  const deleteJson = useCallback(
    async (path: string, body: JsonRecord): Promise<JsonRecord> => {
      const response = await fetch(`${normalizedBase}${path}`, {
        method: "DELETE",
        headers: authHeaders(true),
        body: JSON.stringify(body),
      });
      const payload = (await response.json().catch(() => ({}))) as JsonRecord;
      if (!response.ok) {
        throw new Error(asString(payload.error, response.statusText));
      }
      return payload;
    },
    [authHeaders, normalizedBase],
  );

  const refreshRuntime = useCallback(async () => {
    setRuntimeStatus("checking");
    try {
      const payload = await getJson("/v1/app/runtime");
      setRuntimeSnapshot(payload);
      setRuntimeStatus("active");
    } catch {
      setRuntimeSnapshot(null);
      setRuntimeStatus("unavailable");
    }
  }, [getJson]);

  const refreshSkills = useCallback(async () => {
    setSkillsLoading(true);
    try {
      const payload = await getJson("/v1/app/skills");
      setSkillsSnapshot(payload);
    } catch {
      setSkillsSnapshot(null);
    } finally {
      setSkillsLoading(false);
    }
  }, [getJson]);

  const refreshConfig = useCallback(async () => {
    setConfigLoading(true);
    try {
      const payload = await getJson("/v1/app/config");
      setLocalConfig(localConfigFromPayload(payload));
      setConfigError(null);
    } catch (err) {
      setLocalConfig(null);
      setConfigError(err instanceof Error ? err.message : "Failed to load config");
    } finally {
      setConfigLoading(false);
    }
  }, [getJson]);

  const refreshKnowledge = useCallback(async () => {
    setKbRefreshing(true);
    try {
      const payload = await getJson("/v1/app/knowledge");
      setKbCollections(toKbCollections(payload));
    } catch {
      setKbCollections([]);
    } finally {
      setKbLoading(false);
      setKbRefreshing(false);
    }
  }, [getJson]);

  const uploadKnowledgeFiles = useCallback(
    async (files: FileList): Promise<void> => {
      for (const file of Array.from(files)) {
        const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
        if (!KB_UPLOAD_EXTENSIONS.has(extension)) {
          throw new Error(`Unsupported file type: ${file.name}`);
        }
        const contentType = resolveKnowledgeUploadMimeType(file);
        const isTextFile =
          contentType.startsWith("text/") ||
          ["application/json", "application/xml"].includes(contentType) ||
          /\.(md|markdown|txt|csv|tsv|json|yaml|yml|html|htm|xml)$/i.test(file.name);
        const content = isTextFile
          ? await file.text()
          : `Binary knowledge file saved from local dashboard: ${file.name} (${file.size} bytes, ${contentType})`;
        const safeName =
          file.name
            .replace(/[^A-Za-z0-9._-]+/g, "-")
            .replace(/^-+|-+$/g, "")
            .slice(0, 120) || "document.txt";
        const response = await fetch(`${normalizedBase}/v1/app/knowledge/file`, {
          method: "PUT",
          headers: authHeaders(true),
          body: JSON.stringify({ path: `dashboard/${safeName}`, content }),
        });
        const payload = (await response.json().catch(() => ({}))) as JsonRecord;
        if (!response.ok) throw new Error(asString(payload.error, response.statusText));
      }
      await refreshKnowledge();
    },
    [authHeaders, normalizedBase, refreshKnowledge],
  );

  const refreshWorkspace = useCallback(async () => {
    setWorkspaceRefreshing(true);
    try {
      const seen = new Map<string, { path: string; size: number; modifiedAt: string | null }>();
      const visit = async (path: string, depth: number): Promise<void> => {
        if (seen.size >= WORKSPACE_SCAN_LIMIT) return;
        const payload = await getJson(`/v1/app/workspace?path=${encodeURIComponent(path)}`);
        const entries = asArray(payload.entries) as WorkspaceEntryRow[];
        for (const entry of entries) {
          if (!entry.path) continue;
          if (entry.type === "file") {
            seen.set(entry.path, {
              path: entry.path,
              size: entry.sizeBytes ?? 0,
              modifiedAt:
                typeof entry.mtimeMs === "number"
                  ? new Date(entry.mtimeMs).toISOString()
                  : null,
            });
            if (seen.size >= WORKSPACE_SCAN_LIMIT) break;
            continue;
          }
          if (entry.type === "directory" && shouldScanWorkspaceDirectory(entry.path, depth)) {
            await visit(entry.path, depth + 1);
          }
        }
      };
      await visit(".", 0);
      setWorkspaceFiles(normalizeWorkspaceFileList(Array.from(seen.values())));
    } catch {
      setWorkspaceFiles([]);
    } finally {
      setWorkspaceLoading(false);
      setWorkspaceRefreshing(false);
    }
  }, [getJson]);

  const refreshMemory = useCallback(async () => {
    setMemoryRefreshing(true);
    try {
      const payload = await getJson("/v1/app/memory");
      setMemoryStatus(asRecord(payload.status));
      setMemoryFiles(
        asArray(payload.files)
          .map((file) => ({
            path: asString(file.path),
            sizeBytes: asNumber(file.sizeBytes, 0),
            mtimeMs:
              typeof file.mtimeMs === "number" && Number.isFinite(file.mtimeMs)
                ? file.mtimeMs
                : null,
          }))
          .filter((file) => file.path.length > 0),
      );
    } catch {
      setMemoryStatus(null);
      setMemoryFiles([]);
    } finally {
      setMemoryLoading(false);
      setMemoryRefreshing(false);
    }
  }, [getJson]);

  const saveWorkspaceFile = useCallback(
    async (path: string, content: string) => {
      await putJson("/v1/app/workspace/file", { path, content });
      void refreshWorkspace();
    },
    [putJson, refreshWorkspace],
  );

  const readWorkspaceFile = useCallback(
    async (path: string): Promise<string> => {
      const payload = await getJson(`/v1/app/workspace/file?path=${encodeURIComponent(path)}`);
      return asString(payload.content);
    },
    [getJson],
  );

  const saveConfig = useCallback(
    async (config: LocalConfigState): Promise<void> => {
      setConfigSaving(true);
      setConfigNotice(null);
      setConfigError(null);
      try {
        const payload = await putJson("/v1/app/config", configSavePayload(config) as unknown as JsonRecord);
        const next = { ...config, ...localConfigFromPayload({ ...payload, config: configSavePayload(config) as unknown as JsonRecord }) };
        setLocalConfig(next);
        setConfigNotice("Settings saved. Restart the runtime for provider or workspace changes to take effect.");
      } catch (err) {
        setConfigError(err instanceof Error ? err.message : "Failed to save config");
      } finally {
        setConfigSaving(false);
      }
    },
    [putJson],
  );

  const reloadConfig = useCallback(async (): Promise<void> => {
    setConfigNotice(null);
    setConfigError(null);
    try {
      const payload = await sendJson("/v1/app/config/reload", {});
      setLocalConfig(localConfigFromPayload(payload));
      setConfigNotice(asString(payload.message, "Config reloaded. Restart may still be required."));
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : "Failed to reload config");
    }
  }, [sendJson]);

  const restartRuntime = useCallback(async (): Promise<void> => {
    setConfigNotice(null);
    setConfigError(null);
    try {
      const payload = await sendJson("/v1/app/runtime/restart", {});
      setConfigNotice(asString(payload.message, payload.ok === true ? "Runtime restart scheduled." : "Runtime restart is not configured."));
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : "Failed to restart runtime");
    }
  }, [sendJson]);

  const readMemoryFile = useCallback(
    async (path: string): Promise<string> => {
      const payload = await getJson(`/v1/app/memory/file?path=${encodeURIComponent(path)}`);
      return asString(payload.content);
    },
    [getJson],
  );

  const searchMemory = useCallback(
    async (query: string): Promise<JsonRecord> =>
      getJson(`/v1/app/memory/search?q=${encodeURIComponent(query)}&limit=10`),
    [getJson],
  );

  const deleteMemoryFiles = useCallback(
    async (paths: string[]): Promise<void> => {
      await deleteJson("/v1/app/memory/files", { paths });
      await refreshMemory();
      await refreshWorkspace();
    },
    [deleteJson, refreshMemory, refreshWorkspace],
  );

  const compactMemory = useCallback(async (): Promise<void> => {
    await sendJson("/v1/app/memory/compact", { force: true });
    await refreshMemory();
  }, [refreshMemory, sendJson]);

  const reindexMemory = useCallback(async (): Promise<void> => {
    await sendJson("/v1/app/memory/reindex", {});
    await refreshMemory();
  }, [refreshMemory, sendJson]);

  const saveMemoryFile = useCallback(
    async (path: string, content: string): Promise<void> => {
      await saveWorkspaceFile(path, content);
      await refreshMemory();
    },
    [refreshMemory, saveWorkspaceFile],
  );

  const refreshChannels = useCallback(() => {
    setRefreshing(true);
    store.setChannels(store.channels.length > 0 ? store.channels : [defaultChannel()], { botId: BOT_ID });
    void Promise.allSettled([refreshRuntime(), refreshKnowledge(), refreshWorkspace(), refreshMemory(), refreshSkills(), refreshConfig()]).finally(() => {
      window.setTimeout(() => setRefreshing(false), 300);
    });
  }, [refreshConfig, refreshKnowledge, refreshMemory, refreshRuntime, refreshSkills, refreshWorkspace, store]);

  const refreshDashboardData = useCallback(() => {
    setRefreshing(true);
    void Promise.allSettled([refreshRuntime(), refreshKnowledge(), refreshWorkspace(), refreshMemory(), refreshSkills(), refreshConfig()]).finally(() => {
      window.setTimeout(() => setRefreshing(false), 300);
    });
  }, [refreshConfig, refreshKnowledge, refreshMemory, refreshRuntime, refreshSkills, refreshWorkspace]);

  const navigateToRoute = useCallback(
    (route: AppRoute, channel = useChatStore.getState().activeChannel || DEFAULT_CHANNEL) => {
      if (route === "chat") {
        store.setActiveChannel(channel);
      }
      window.history.pushState({}, "", pathForRoute(route, channel));
      window.dispatchEvent(new PopStateEvent("popstate"));
      setAppRoute(route);
    },
    [store],
  );

  const handleSaveConnection = useCallback(() => {
    const nextAgentUrl = normalizeAgentUrl(agentUrl);
    const nextToken = token.trim();
    setAgentUrl(nextAgentUrl);
    setToken(nextToken);
    window.localStorage.setItem(storage.agentUrl, nextAgentUrl);
    if (nextToken) window.localStorage.setItem(storage.token, nextToken);
    else window.localStorage.removeItem(storage.token);
    void refreshRuntime();
  }, [agentUrl, refreshRuntime, token]);

  useEffect(() => {
    store.setBotId(BOT_ID);
    store.setChannels([defaultChannel()], { botId: BOT_ID });
    store.setActiveChannel(DEFAULT_CHANNEL);
    void fetch(`${window.location.origin}/app/bootstrap.json`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((bootstrap: AppBootstrap | null) => {
        if (!bootstrap) return;
        if (bootstrap.agentUrl) {
          setAgentUrl(bootstrap.agentUrl);
          window.localStorage.setItem(storage.agentUrl, bootstrap.agentUrl);
        }
        if (bootstrap.token) {
          setToken(bootstrap.token);
          window.localStorage.setItem(storage.token, bootstrap.token);
        }
      })
      .catch(() => {});
    void Promise.allSettled([refreshRuntime(), refreshKnowledge(), refreshWorkspace(), refreshMemory(), refreshSkills(), refreshConfig()]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const syncRoute = () => {
      const nextRoute = routeFromPathname(window.location.pathname);
      const nextChannel = channelFromPathname(window.location.pathname);
      setAppRoute((current) => (current === nextRoute ? current : nextRoute));
      if (nextChannel && useChatStore.getState().activeChannel !== nextChannel) {
        useChatStore.getState().setActiveChannel(nextChannel);
      }
    };
    syncRoute();
    window.addEventListener("popstate", syncRoute);
    return () => window.removeEventListener("popstate", syncRoute);
  }, []);

  useEffect(() => {
    if (!channelState.streaming) setStreamingComposerMode("queue");
  }, [channelState.streaming]);

  const handleToggleKbDoc = useCallback((doc: KbDocReference) => {
    setSelectedKbDocs((prev) => {
      const exists = prev.some((item) => item.id === doc.id);
      return exists ? prev.filter((item) => item.id !== doc.id) : [...prev, doc];
    });
  }, []);

  const handleRemoveKbDoc = useCallback((docId: string) => {
    setSelectedKbDocs((prev) => prev.filter((doc) => doc.id !== docId));
  }, []);

  const updateActiveTools = useCallback(
    (channel: string, patch: ToolActivity) => {
      const current = useChatStore.getState().channelStates[channel];
      store.setChannelState(channel, {
        activeTools: appendToolActivity(current?.activeTools, patch),
      }, { botId: BOT_ID });
    },
    [store],
  );

  const updateSubagents = useCallback(
    (channel: string, patch: SubagentActivity) => {
      const current = useChatStore.getState().channelStates[channel];
      store.setChannelState(channel, {
        subagents: appendSubagentActivity(current?.subagents, patch),
      }, { botId: BOT_ID });
    },
    [store],
  );

  const applyControlEvent = useCallback(
    (channel: string, event: unknown) => {
      const record = asRecord(event);
      const type = asString(record.type);
      if (type === "control_request_created") {
        const request = makeControlRequest(asRecord(record.request), sessionKeyForChannel(channel));
        if (request) store.upsertControlRequest(channel, request);
        return;
      }
      if (
        type === "control_request_resolved" ||
        type === "control_request_cancelled" ||
        type === "control_request_timed_out"
      ) {
        store.applyControlEvent(channel, record as ControlEvent);
      }
    },
    [store],
  );

  const appendAssistantDelta = useCallback(
    (channel: string, delta: string) => {
      const state = useChatStore.getState().channelStates[channel];
      store.setChannelState(channel, {
        streamingText: `${state?.streamingText ?? ""}${delta}`,
        hasTextContent: true,
        fileProcessing: false,
      }, { botId: BOT_ID });
    },
    [store],
  );

  const handleAgentEvent = useCallback(
    (channel: string, payload: JsonRecord) => {
      const type = asString(payload.type, "agent");
      if (type === "turn_start") {
        store.setChannelState(channel, {
          streaming: true,
          turnPhase: "pending",
          error: null,
          currentGoal: typeof payload.goal === "string" ? payload.goal : null,
          activeTools: [],
          browserFrame: null,
          documentDraft: null,
          subagents: [],
          taskBoard: null,
          inspectedSources: [],
          citationGate: null,
          runtimeTraces: [],
          heartbeatElapsedMs: null,
        }, { botId: BOT_ID });
      }
      if (type === "turn_phase" && isRuntimePhase(payload.phase)) {
        store.setChannelState(channel, { turnPhase: payload.phase }, { botId: BOT_ID });
      }
      if (type === "response_clear") {
        store.setChannelState(channel, {
          streamingText: "",
          hasTextContent: false,
          heartbeatElapsedMs: null,
        }, { botId: BOT_ID });
      }
      if (type === "thinking_delta" && typeof payload.delta === "string") {
        const state = useChatStore.getState().channelStates[channel];
        store.setChannelState(channel, {
          streaming: true,
          thinkingText: `${state?.thinkingText ?? ""}${payload.delta}`,
          thinkingStartedAt: state?.thinkingStartedAt ?? Date.now(),
          fileProcessing: false,
        }, { botId: BOT_ID });
      }
      if (type === "text_delta" && typeof payload.delta === "string") {
        appendAssistantDelta(channel, payload.delta);
      }
      if (type === "llm_progress") {
        const turnId = asString(payload.turnId, channel);
        const iter = asNumber(payload.iter, 0);
        const stage = asString(payload.stage, "waiting");
        const label = asString(payload.label, "Thinking through next step");
        const detail = asString(payload.detail);
        const elapsedMs = asNumber(payload.elapsedMs);
        updateActiveTools(channel, {
          id: `llm:${turnId}:${iter}`,
          label: "ModelProgress",
          status: stage === "completed" ? "done" : "running",
          startedAt: Date.now(),
          inputPreview: JSON.stringify({ stage, label, detail, elapsedMs }),
          outputPreview: detail,
          durationMs: elapsedMs,
        });
      }
      if (type === "tool_start") {
        const id = asString(payload.id, nowId("tool"));
        updateActiveTools(channel, {
          id,
          label: asString(payload.name, "Working in workspace"),
          status: "running",
          startedAt: Date.now(),
          inputPreview: asString(payload.input_preview),
        });
      }
      if (type === "tool_progress") {
        const id = asString(payload.id);
        if (id) {
          updateActiveTools(channel, {
            id,
            label: asString(payload.label, "Working in workspace"),
            status: "running",
            startedAt: Date.now(),
          });
        }
      }
      if (type === "tool_end") {
        const id = asString(payload.id);
        if (id) {
          const current = useChatStore.getState().channelStates[channel];
          const existing = current?.activeTools?.find((item) => item.id === id);
          updateActiveTools(channel, {
            id,
            label: asString(payload.name, asString(payload.label, id)),
            status:
              payload.status === "error" || payload.status === "denied"
                ? payload.status
                : "done",
            startedAt: Date.now(),
            outputPreview: asString(payload.output_preview),
            durationMs: asNumber(payload.durationMs),
            ...(existing?.patchPreview ? { patchPreview: existing.patchPreview } : {}),
          });
          if (current?.documentDraft?.id === id) {
            store.setChannelState(channel, {
              documentDraft: {
                ...current.documentDraft,
                status: "done",
                updatedAt: Date.now(),
              },
            }, { botId: BOT_ID });
          }
        }
      }
      if (type === "document_draft") {
        const documentDraft = normalizeDocumentDraft(payload);
        if (documentDraft) {
          store.setChannelState(channel, { documentDraft }, { botId: BOT_ID });
        }
      }
      if (type === "patch_preview") {
        const patchPreview = normalizePatchPreview(payload);
        if (patchPreview) {
          const current = useChatStore.getState().channelStates[channel];
          const activeTools = current?.activeTools ?? [];
          const toolUseId = asString(payload.toolUseId);
          const patchTool = [...activeTools].reverse().find((item) =>
            item.label.toLowerCase().replace(/[^a-z0-9]/g, "") === "patchapply"
          );
          const id = toolUseId || patchTool?.id || nowId("patch");
          updateActiveTools(channel, {
            id,
            label: "PatchApply",
            status: patchTool?.status ?? "running",
            startedAt: Date.now(),
            patchPreview,
          });
        }
      }
      if (type === "browser_frame") {
        const browserFrame = normalizeBrowserFrame(payload);
        if (browserFrame) {
          store.setChannelState(channel, { browserFrame }, { botId: BOT_ID });
        }
      }
      if (type === "task_board") {
        store.setChannelState(channel, {
          taskBoard: { tasks: normalizeTaskBoard(payload), receivedAt: Date.now() },
        }, { botId: BOT_ID });
      }
      if (type === "source_inspected") {
        const source = normalizeInspectedSource(asRecord(payload.source));
        if (source) {
          const current = useChatStore.getState().channelStates[channel];
          store.setChannelState(channel, {
            inspectedSources: appendInspectedSource(current?.inspectedSources, source),
          }, { botId: BOT_ID });
        }
      }
      if (type === "rule_check") {
        const citationGate = normalizeCitationGate(payload);
        if (citationGate) {
          store.setChannelState(channel, { citationGate }, { botId: BOT_ID });
        }
      }
      if (type === "runtime_trace") {
        const trace = normalizeRuntimeTrace(payload);
        if (trace) {
          store.applyControlEvent(channel, {
            type: "runtime_trace",
            ...trace,
          });
        }
      }
      if (type === "mission_created") {
        const mission = asRecord(payload.mission);
        const id = asString(mission.id);
        if (id) {
          const current = useChatStore.getState().channelStates[channel];
          const activity: MissionActivity = {
            id,
            title: asString(mission.title, "Mission"),
            kind: asString(mission.kind, "manual"),
            status: missionStatus(mission.status),
            updatedAt: Date.now(),
          };
          store.setChannelState(channel, {
            missions: appendMissionActivity(current?.missions, activity),
            ...(activity.kind === "goal" ? { activeGoalMissionId: activity.id } : {}),
          }, { botId: BOT_ID });
        }
      }
      if (type === "mission_event") {
        const missionId = asString(payload.missionId);
        if (missionId) {
          const current = useChatStore.getState().channelStates[channel];
          const existing = current?.missions?.find((mission) => mission.id === missionId);
          const eventType = asString(payload.eventType, "heartbeat");
          store.setChannelState(channel, {
            missions: appendMissionActivity(current?.missions, {
              id: missionId,
              title: existing?.title ?? "Mission",
              kind: existing?.kind ?? "manual",
              status: missionStatusFromEvent(eventType),
              detail: asString(payload.message) || existing?.detail,
              updatedAt: Date.now(),
            }),
          }, { botId: BOT_ID });
        }
      }
      if (
        type === "spawn_started" ||
        type === "child_started" ||
        type === "background_task" ||
        type === "spawn_result" ||
        type === "child_completed" ||
        type === "child_failed" ||
        type === "child_cancelled"
      ) {
        const taskId = asString(payload.taskId, nowId("child"));
        updateSubagents(channel, {
          taskId,
          role: asString(payload.persona, "worker"),
          status: normalizeSubagentStatus(type, payload.status),
          detail:
            asString(payload.prompt) ||
            asString(payload.detail) ||
            asString(payload.finalText) ||
            asString(payload.errorMessage) ||
            preview(payload.summary),
          startedAt: Date.now(),
          updatedAt: Date.now(),
        });
      }
      if (type === "child_progress") {
        const taskId = asString(payload.taskId);
        if (taskId) {
          updateSubagents(channel, {
            taskId,
            role: "worker",
            status: "running",
            detail: asString(payload.detail),
            startedAt: Date.now(),
            updatedAt: Date.now(),
          });
        }
      }
      if (type === "heartbeat") {
        const current = useChatStore.getState().channelStates[channel];
        store.setChannelState(channel, {
          heartbeatElapsedMs: asNumber(payload.elapsedMs, current?.heartbeatElapsedMs ?? 0),
        }, { botId: BOT_ID });
      }
      if (type === "turn_interrupted") {
        store.setChannelState(channel, { turnPhase: "aborted" }, { botId: BOT_ID });
      }
      if (type === "control_event") {
        applyControlEvent(channel, payload.event);
      }
      if (type === "turn_end") {
        store.setChannelState(channel, {
          turnPhase: payload.status === "aborted" ? "aborted" : "committed",
        }, { botId: BOT_ID });
        store.finalizeStream(channel, undefined, { botId: BOT_ID });
      }
    },
    [appendAssistantDelta, applyControlEvent, store, updateActiveTools, updateSubagents],
  );

  const handleSseEvent = useCallback(
    (channel: string, eventName: string, rawData: string) => {
      if (rawData === "[DONE]") {
        store.finalizeStream(channel, undefined, { botId: BOT_ID });
        return;
      }
      let payload: JsonRecord;
      try {
        payload = JSON.parse(rawData) as JsonRecord;
      } catch {
        return;
      }
      if (eventName === "agent") {
        sawAgentEventRef.current = true;
        handleAgentEvent(channel, payload);
        return;
      }
      if (sawAgentEventRef.current) return;
      const choice = asRecord(asArray(payload.choices)[0]);
      const delta = asString(asRecord(choice.delta).content);
      if (delta) appendAssistantDelta(channel, delta);
      if (choice.finish_reason) {
        store.finalizeStream(channel, undefined, { botId: BOT_ID });
      }
    },
    [appendAssistantDelta, handleAgentEvent, store],
  );

  const resolveKbDocsForFiles = useCallback(
    async (files?: File[]): Promise<KbDocReference[]> => {
      if (!files?.length) return [];
      const refs: KbDocReference[] = [];
      for (const file of files) {
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        setUploadStates((prev) => ({
          ...prev,
          [key]: { key, filename: file.name, phase: "uploading" },
        }));
        const path = `uploads/${Date.now()}-${file.name.replace(/[^A-Za-z0-9._-]+/g, "-")}`;
        const content =
          getWorkspaceFilePreviewKind(file.name) === "download"
            ? `Binary file attached through local web UI: ${file.name} (${file.size} bytes)`
            : await file.text();
        await fetch(`${normalizedBase}/v1/app/knowledge/file`, {
          method: "PUT",
          headers: authHeaders(true),
          body: JSON.stringify({ path, content }),
        }).then(async (response) => {
          if (!response.ok) {
            const body = (await response.json().catch(() => ({}))) as JsonRecord;
            throw new Error(asString(body.error, response.statusText));
          }
        });
        const ref = {
          id: path,
          filename: file.name,
          collectionId: "local-uploads",
          collectionName: "uploads",
          source: "chat_upload" as const,
        };
        refs.push(ref);
        setUploadStates((prev) => ({
          ...prev,
          [key]: { key, filename: file.name, phase: "ready", ref },
        }));
      }
      void refreshKnowledge();
      return refs;
    },
    [authHeaders, normalizedBase, refreshKnowledge],
  );

  const performSend = useCallback(
    async (
      text: string,
      explicitReply: ReplyTo | null,
      kbDocs: KbDocReference[],
      modelOverride?: string,
      sendOptions?: ChatInputSendOptions,
    ) => {
      const channel = useChatStore.getState().activeChannel || DEFAULT_CHANNEL;
      const messageText = buildMessageContentWithKbContext(text, kbDocs);
      if (!messageText.trim()) return;
      const goalMode = sendOptions?.goalMode === true;
      const userMsg: ChatMessage = {
        id: nowId("user"),
        role: "user",
        content: messageText,
        timestamp: Date.now(),
        ...(explicitReply ? { replyTo: explicitReply } : {}),
      };
      store.addMessage(channel, userMsg, { botId: BOT_ID });
      chatMessagesRef.current?.scrollToBottom();
      sawAgentEventRef.current = false;
      const controller = new AbortController();
      store.setAbortController(channel, controller, { botId: BOT_ID });
      store.setChannelState(channel, {
        streaming: true,
        streamingText: "",
        thinkingText: "",
        hasTextContent: false,
        error: null,
        thinkingStartedAt: Date.now(),
        fileProcessing: false,
        turnPhase: "pending",
        heartbeatElapsedMs: null,
        pendingInjectionCount: 0,
        activeTools: [],
        browserFrame: null,
        subagents: [],
        taskBoard: null,
        inspectedSources: [],
        citationGate: null,
        currentGoal: goalMode ? text.trim() : null,
        pendingGoalMissionTitle: goalMode ? text.trim() : null,
        responseLanguage: detectMessageResponseLanguage(messageText),
      }, { botId: BOT_ID });

      try {
        const response = await fetch(`${normalizedBase}/v1/chat/completions`, {
          method: "POST",
          headers: authHeaders(true),
          signal: controller.signal,
          body: JSON.stringify({
            stream: true,
            ...(modelOverride && modelOverride !== "auto" ? { model: modelOverride } : {}),
            ...(goalMode ? { goalMode: true } : {}),
            ...(explicitReply ? { replyTo: explicitReply } : {}),
            messages: [{ role: "user", content: messageText }],
          }),
        });
        if (!response.ok || !response.body) {
          const payload = (await response.json().catch(() => ({}))) as JsonRecord;
          throw new Error(asString(payload.error, response.statusText));
        }
        const parser = createSseParser((eventName, rawData) =>
          handleSseEvent(channel, eventName, rawData),
        );
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          parser(decoder.decode(value, { stream: true }));
        }
        parser(decoder.decode());
        store.finalizeStream(channel, undefined, { botId: BOT_ID });
        void Promise.allSettled([refreshKnowledge(), refreshWorkspace()]);
      } catch (err) {
        if (controller.signal.aborted) return;
        const hasText = useChatStore.getState().channelStates[channel]?.hasTextContent;
        if (hasText) {
          store.finalizeStream(channel, undefined, { botId: BOT_ID });
        } else {
          store.setChannelState(channel, {
            streaming: false,
            streamingText: "",
            thinkingText: "",
            hasTextContent: false,
            turnPhase: null,
            error: err instanceof Error ? err.message : "Unknown error",
          }, { botId: BOT_ID });
        }
      }
    },
    [authHeaders, handleSseEvent, normalizedBase, refreshKnowledge, refreshWorkspace, store],
  );

  const drainQueue = useCallback(
    (channel: string) => {
      const next = useChatStore.getState().dequeueFirst(channel, { botId: BOT_ID });
      if (!next) return;
      window.setTimeout(() => {
        void performSend(
          next.content,
          next.replyTo ?? null,
          next.kbDocs ?? [],
          next.modelOverride,
          next.goalMode ? { goalMode: true } : undefined,
        );
      }, 0);
    },
    [performSend],
  );

  useEffect(() => {
    if (channelState.streaming || queuedForChannel.length === 0) return;
    drainQueue(activeChannel);
  }, [activeChannel, channelState.streaming, drainQueue, queuedForChannel.length]);

  const handleSend = useCallback(
    async (text: string, files?: File[], options?: ChatInputSendOptions) => {
      const channel = useChatStore.getState().activeChannel || DEFAULT_CHANNEL;
      const activeReply = replyingTo;
      const goalMode = options?.goalMode === true;
      setReplyingTo(null);
      let uploadedRefs: KbDocReference[] = [];
      try {
        uploadedRefs = await resolveKbDocsForFiles(files);
      } catch (err) {
        if (activeReply) setReplyingTo(activeReply);
        store.setChannelState(channel, {
          error: err instanceof Error ? err.message : "Failed to prepare files",
        }, { botId: BOT_ID });
        return false;
      }
      const messageKbDocs = mergeKbDocReferences(selectedKbDocs, uploadedRefs);
      const isStreaming = !!useChatStore.getState().channelStates[channel]?.streaming;
      if (isStreaming) {
        const sendMode = getStreamingSendMode({
          hasFiles: !!files?.length,
          hasKbContext: messageKbDocs.length > 0,
          requestedMode: streamingComposerMode,
        });
        if (!goalMode && sendMode === "inject") {
          try {
            const injectedAfterChars =
              useChatStore.getState().channelStates[channel]?.streamingText?.length ?? 0;
            const result = await sendJson("/v1/chat/inject", {
              sessionKey: sessionKeyForChannel(channel),
              text,
              source: "web",
            });
            if (result.injectionId) {
              store.addMessage(channel, {
                id: nowId("injected"),
                role: "user",
                content: text,
                timestamp: Date.now(),
                injected: true,
                injectedAfterChars,
                ...(activeReply ? { replyTo: activeReply } : {}),
              }, { botId: BOT_ID });
              const current = useChatStore.getState().channelStates[channel];
              store.setChannelState(channel, {
                pendingInjectionCount: (current?.pendingInjectionCount ?? 0) + 1,
              }, { botId: BOT_ID });
              setSelectedKbDocs([]);
              return true;
            }
          } catch {
            // Runtime may be between LLM iterations; queue is the fallback.
          }
        }
        const queued: QueuedMessage = {
          id: nowId("queued"),
          content: text,
          queuedAt: Date.now(),
          modelOverride: modelSelection,
          ...(goalMode ? { goalMode: true } : {}),
          ...(activeReply ? { replyTo: activeReply } : {}),
          ...(messageKbDocs.length > 0 ? { kbDocs: messageKbDocs } : {}),
        };
        const ok = store.enqueueMessage(channel, queued, { botId: BOT_ID });
        if (!ok) {
          if (activeReply) setReplyingTo(activeReply);
          store.setChannelState(channel, {
            error: `Queue full (max ${MAX_QUEUED_MESSAGES}). Wait for the agent to finish.`,
          }, { botId: BOT_ID });
          return false;
        }
        setSelectedKbDocs([]);
        return true;
      }

      void performSend(text, activeReply, messageKbDocs, modelSelection, options);
      setSelectedKbDocs([]);
      return true;
    },
    [
      modelSelection,
      performSend,
      replyingTo,
      resolveKbDocsForFiles,
      selectedKbDocs,
      sendJson,
      store,
      streamingComposerMode,
    ],
  );

  const cancelChannelTurn = useCallback((channel: string) => {
    if (interruptHandoffChannelsRef.current.has(channel)) return;
    interruptHandoffChannelsRef.current.add(channel);
    void cancelActiveTurnWithQueueHandoff({
      hasQueued: () => (useChatStore.getState().queuedMessages[channel] ?? []).length > 0,
      promoteQueuedForHandoff: () => {
        useChatStore.getState().promoteNextQueuedMessage(channel, { botId: BOT_ID });
      },
      cancelStream: (options) => {
        store.cancelStream(channel, { ...options, botId: BOT_ID });
      },
      interrupt: async (handoffRequested) => {
        const response = await fetch(`${normalizedBase}/v1/chat/interrupt`, {
          method: "POST",
          headers: authHeaders(true),
          body: JSON.stringify({
            sessionKey: sessionKeyForChannel(channel),
            handoffRequested,
            source: "web",
          }),
        });
        const payload = (await response.json().catch(() => ({}))) as JsonRecord;
        return {
          accepted: response.ok && asString(payload.status) === "accepted",
          handoffRequested: payload.handoffRequested === true,
          status: response.status,
          reason: asString(payload.error),
        };
      },
      drainQueue: () => {
        drainQueue(channel);
      },
    })
      .then((result) => {
        setEscArmedUntil(null);
        if (result.handoffRequested && !result.drained) {
          store.setChannelState(channel, {
            error: "Interrupted current turn, but could not hand off the queued message yet. Please send again.",
          }, { botId: BOT_ID });
        }
      })
      .catch((err) => {
        console.warn("[chat] runtime interrupt failed:", err);
      })
      .finally(() => {
        interruptHandoffChannelsRef.current.delete(channel);
      });
  }, [authHeaders, drainQueue, normalizedBase, store]);

  const handleCancel = useCallback(() => {
    const channel = useChatStore.getState().activeChannel || DEFAULT_CHANNEL;
    cancelChannelTurn(channel);
  }, [cancelChannelTurn]);

  const handleCancelQueue = useCallback(() => {
    const channel = useChatStore.getState().activeChannel || DEFAULT_CHANNEL;
    store.clearQueue(channel, { botId: BOT_ID });
  }, [store]);

  useEffect(() => {
    if (!anyStreaming) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const active = document.activeElement as HTMLElement | null;
      if (active?.closest('[role="dialog"], [aria-modal="true"]')) return;
      if ((event as KeyboardEvent & { isComposing?: boolean }).isComposing) return;
      event.preventDefault();
      const channel = useChatStore.getState().activeChannel || DEFAULT_CHANNEL;
      const hasQueued = (useChatStore.getState().queuedMessages[channel] ?? []).length > 0;
      const decision = buildEscCancelDecision({
        hasQueued,
        armedUntil: escArmedUntil,
        now: Date.now(),
      });
      if (decision.action === "arm") {
        setEscArmedUntil(decision.nextArmedUntil);
        return;
      }
      setEscArmedUntil(null);
      cancelChannelTurn(channel);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [anyStreaming, cancelChannelTurn, escArmedUntil]);

  useEffect(() => {
    if (!anyStreaming && escArmedUntil !== null) {
      setEscArmedUntil(null);
    }
  }, [anyStreaming, escArmedUntil]);

  useEffect(() => {
    setEscArmedUntil(null);
  }, [activeChannel]);

  useEffect(() => {
    if (escArmedUntil === null) return;
    const delay = Math.max(0, escArmedUntil - Date.now());
    const timer = window.setTimeout(() => setEscArmedUntil(null), delay);
    return () => window.clearTimeout(timer);
  }, [escArmedUntil]);

  const handleReplyTo = useCallback((message: ChatMessage) => {
    if (message.role !== "user" && message.role !== "assistant") return;
    const previewText = buildReplyPreview(message.content);
    if (!previewText) return;
    setReplyingTo({
      messageId: message.serverId ?? message.id,
      preview: previewText,
      role: message.role,
    });
    chatInputRef.current?.focus();
  }, []);

  const handleCreateChannel = useCallback(
    (name: string, memoryMode: ChannelMemoryModeOption = "normal") => {
      const identity = buildMemoryModeChannelIdentity(name, memoryMode);
      const channelName = identity.name || normalizeChannelName(name);
      const existing = useChatStore.getState().channels;
      if (existing.some((channel) => channel.name === channelName)) {
        store.setActiveChannel(channelName);
        return;
      }
      const channel: Channel = {
        id: `local-${channelName}`,
        name: channelName,
        display_name: identity.displayName ?? (name === channelName ? null : name),
        category: "General",
        memory_mode: identity.memoryMode,
        position: existing.length,
        created_at: new Date().toISOString(),
      };
      store.setChannels([...existing, channel], { botId: BOT_ID });
      store.setActiveChannel(channel.name);
    },
    [store],
  );

  const handleDeleteChannel = useCallback(
    (name: string) => {
      if (name === DEFAULT_CHANNEL) return;
      const remaining = useChatStore.getState().channels.filter((channel) => channel.name !== name);
      store.setChannels(remaining, { botId: BOT_ID });
      if (useChatStore.getState().activeChannel === name) {
        store.setActiveChannel(remaining[0]?.name ?? DEFAULT_CHANNEL);
      }
    },
    [store],
  );

  const handleCreateCategory = useCallback((name: string) => {
    setCustomCategories((prev) => (prev.includes(name) ? prev : [...prev, name]));
  }, []);

  const handleDragEnter = useCallback((event: DragEvent) => {
    event.preventDefault();
    dragCounterRef.current += 1;
    if (event.dataTransfer.types.includes("Files")) setIsDraggingOver(true);
  }, []);

  const handleDragLeave = useCallback((event: DragEvent) => {
    event.preventDefault();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current <= 0) setIsDraggingOver(false);
  }, []);

  const handleDrop = useCallback((event: DragEvent) => {
    event.preventDefault();
    dragCounterRef.current = 0;
    setIsDraggingOver(false);
    if (event.dataTransfer.files.length > 0) {
      chatInputRef.current?.addFiles(event.dataTransfer.files);
    }
  }, []);

  const handleReset = useCallback(() => {
    const channel = useChatStore.getState().activeChannel || DEFAULT_CHANNEL;
    store.resetSession(channel, getAccessToken);
  }, [getAccessToken, store]);

  const handleStartExportSelection = useCallback(() => {
    const channel = useChatStore.getState().activeChannel || DEFAULT_CHANNEL;
    store.startSelectionMode(channel);
  }, [store]);

  const handleExportSelected = useCallback(() => {
    const {
      activeChannel: channel,
      messages: localMessages,
      serverMessages,
      selectedMessages,
    } = useChatStore.getState();
    const selected = selectedMessages[channel];
    if (!channel || !selected || selected.size === 0) {
      store.setChannelState(channel || DEFAULT_CHANNEL, {
        error: "Select at least one user or assistant message to export.",
      }, { botId: BOT_ID });
      return;
    }

    const combined = [
      ...(localMessages[channel] ?? []),
      ...(serverMessages[channel] ?? []),
    ];
    const normalized = normalizeSelectedChatExportMessages(combined, selected);
    const unique = Array.from(
      new Map(
        normalized.map((message) => [
          `${message.role}:${message.timestamp}:${message.content}`,
          message,
        ]),
      ).values(),
    );

    if (unique.length === 0) {
      store.setChannelState(channel, {
        error: "Select at least one user or assistant message to export.",
      }, { botId: BOT_ID });
      return;
    }

    const exportedAt = new Date();
    downloadMarkdownFile(
      buildChatExportFilename({
        botName: BOT_NAME,
        channelName: channel,
        exportedAt,
      }),
      buildChatExportMarkdown({
        botName: BOT_NAME,
        channelName: channel,
        exportedAt,
        messages: unique,
      }),
    );
    store.exitSelectionMode();
  }, [store]);

  const handleModelSelectionChange = useCallback((_nextModel: string, _nextRouter: string) => {
    setModelSelection(DEFAULT_MODEL);
    setRouterType(DEFAULT_ROUTER);
    window.localStorage.removeItem(storage.modelOverride);
  }, []);

  const cancelHint =
    escArmedUntil !== null
      ? channelState.responseLanguage === "ko"
        ? "다시 ESC로 중지"
        : "ESC again to stop"
      : undefined;

  const composerAccessory = (
    <ChatModelPicker
      botId={BOT_ID}
      modelSelection={modelSelection}
      routerType={routerType}
      apiKeyMode="platform_credits"
      subscriptionPlan="max"
      persistMode="local"
      menuPlacement="top"
      onModelSelectionChange={handleModelSelectionChange}
    />
  );

  if (appRoute !== "chat") {
    return (
      <LocalDashboardShell
        route={appRoute}
        runtimeSnapshot={runtimeSnapshot}
        runtimeStatus={runtimeStatus}
        skillsSnapshot={skillsSnapshot}
        skillsLoading={skillsLoading}
        config={localConfig}
        configLoading={configLoading}
        configSaving={configSaving}
        configNotice={configNotice}
        configError={configError}
        agentUrl={agentUrl}
        token={token}
        kbCollections={kbCollections}
        kbLoading={kbLoading}
        kbRefreshing={kbRefreshing}
        workspaceFiles={workspaceFiles}
        workspaceLoading={workspaceLoading}
        workspaceRefreshing={workspaceRefreshing}
        memoryFiles={memoryFiles}
        memoryStatus={memoryStatus}
        memoryLoading={memoryLoading}
        memoryRefreshing={memoryRefreshing}
        setAgentUrl={setAgentUrl}
        setToken={setToken}
        onNavigate={navigateToRoute}
        onRefreshAll={refreshDashboardData}
        onRefreshKnowledge={() => void refreshKnowledge()}
        onRefreshWorkspace={() => void refreshWorkspace()}
        onRefreshMemory={() => void refreshMemory()}
        onRefreshSkills={() => void refreshSkills()}
        onUploadKnowledge={uploadKnowledgeFiles}
        onReadWorkspaceFile={readWorkspaceFile}
        onSaveWorkspaceFile={saveWorkspaceFile}
        onMemorySearch={searchMemory}
        onMemoryReadFile={readMemoryFile}
        onMemorySaveFile={saveMemoryFile}
        onMemoryDeleteFiles={deleteMemoryFiles}
        onMemoryCompact={compactMemory}
        onMemoryReindex={reindexMemory}
        onSaveConnection={handleSaveConnection}
        onCheckRuntime={() => void refreshRuntime()}
        onSaveConfig={saveConfig}
        onReloadConfig={reloadConfig}
        onRestartRuntime={restartRuntime}
        getJson={getJson}
        sendJson={sendJson}
        putJson={putJson}
        deleteJson={deleteJson}
      />
    );
  }

  return (
    <div className="flex h-full bg-background">
      <ChatSidebar
        channels={store.channels.length > 0 ? store.channels : [defaultChannel()]}
        activeChannel={activeChannel}
        currentBotId={BOT_ID}
        botName={BOT_NAME}
        botStatus="active"
        bots={[{ id: BOT_ID, name: BOT_NAME, status: "active" }]}
        maxBots={1}
        editing={editing}
        customCategories={customCategories}
        refreshing={refreshing}
        mobileOpen={sidebarOpen}
        onChannelSelect={(name) => navigateToRoute("chat", name)}
        onDeleteChannel={handleDeleteChannel}
        onCreateChannel={handleCreateChannel}
        onCreateCategory={handleCreateCategory}
        onDeleteCategory={(name) => setCustomCategories((prev) => prev.filter((item) => item !== name))}
        onRefreshChannels={refreshChannels}
        onToggleEdit={() => setEditing((prev) => !prev)}
        onCancelEdit={() => setEditing(false)}
        onMobileClose={() => setSidebarOpen(false)}
        onReorderChannels={(channels) => store.setChannels(channels, { botId: BOT_ID })}
        onRenameChannel={(channelName, newDisplayName) => {
          store.setChannels(
            useChatStore.getState().channels.map((channel) =>
              channel.name === channelName
                ? { ...channel, display_name: newDisplayName }
                : channel,
            ),
            { botId: BOT_ID },
          );
        }}
        onRenameCategory={(oldName, newName) => {
          store.setChannels(
            useChatStore.getState().channels.map((channel) =>
              channel.category === oldName ? { ...channel, category: newName } : channel,
            ),
            { botId: BOT_ID },
          );
          setCustomCategories((prev) => prev.map((item) => (item === oldName ? newName : item)));
        }}
      />

      <div
        className="relative flex min-w-0 flex-1 flex-col"
        onDrop={handleDrop}
        onDragOver={(event) => event.preventDefault()}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
      >
        {isDraggingOver && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-primary/[0.04] border-2 border-dashed border-primary/30 rounded-2xl pointer-events-none">
            <div className="flex items-center gap-2 text-sm text-primary/70 font-medium bg-white/90 backdrop-blur-sm px-5 py-3 rounded-xl border border-primary/20 shadow-sm">
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
              Drop files to attach
            </div>
          </div>
        )}
        <div className="px-4 md:px-6 py-3 flex items-center gap-3 border-b border-black/[0.06]">
          <button
            onClick={() => setSidebarOpen(true)}
            className="md:hidden p-1.5 -ml-1 text-secondary/60 hover:text-foreground rounded-xl hover:bg-black/[0.04] transition-all duration-200"
            aria-label="Open channels"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </button>
          <h1 className="text-sm font-medium text-foreground/80 flex-1 min-w-0 truncate">
            {localizeChannel(
              activeChannel,
              store.channels.find((channel) => channel.name === activeChannel)?.display_name ?? null,
              "en",
            )}
          </h1>
          <button
            type="button"
            onClick={handleStartExportSelection}
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] text-secondary/55 transition-all duration-200 hover:bg-black/[0.04] hover:text-foreground/75"
            aria-label="Export conversation"
            title="Export conversation"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="18" cy="5" r="3" />
              <circle cx="6" cy="12" r="3" />
              <circle cx="18" cy="19" r="3" />
              <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
              <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
            </svg>
            <span>Export</span>
          </button>
          <button
            onClick={handleReset}
            className="px-2.5 py-1 text-[11px] text-secondary/50 hover:text-foreground/70 rounded-lg hover:bg-black/[0.04] transition-all duration-200"
          >
            Reset
          </button>
          <button
            type="button"
            onClick={() => navigateToRoute("overview")}
            className="md:hidden p-1.5 text-secondary/60 hover:text-foreground rounded-xl hover:bg-black/[0.04] transition-all duration-200"
            aria-label="Dashboard"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" />
            </svg>
          </button>
        </div>

        <ChatMessages
          ref={chatMessagesRef}
          key={activeChannel}
          messages={store.messages[activeChannel] ?? []}
          serverMessages={store.serverMessages[activeChannel] ?? []}
          channelState={channelState}
          uiLanguage={channelState.responseLanguage}
          loading={false}
          botId={BOT_ID}
          selectionMode={store.selectionMode}
          selectedMessages={store.selectedMessages[activeChannel]}
          onToggleSelect={(msgId) => store.toggleMessageSelection(activeChannel, msgId)}
          onEnterSelectionMode={(msgId) => store.enterSelectionMode(activeChannel, msgId)}
          onSelectAll={() => store.selectAllMessages(activeChannel)}
          onDeselectAll={() => store.deselectAllMessages(activeChannel)}
          onExportSelected={handleExportSelected}
          onDeleteSelected={() => {
            const selected = store.selectedMessages[activeChannel];
            if (selected) store.removeMessages(activeChannel, selected, { botId: BOT_ID });
            store.exitSelectionMode();
          }}
          onExitSelectionMode={() => store.exitSelectionMode()}
          onReplyTo={handleReplyTo}
          queuedMessages={queuedForChannel}
          onCancelQueued={(id) => store.removeFromQueue(activeChannel, id, { botId: BOT_ID })}
          controlRequests={controlsForChannel}
          onRespondControlRequest={async (request, response) => {
            store.applyControlEvent(activeChannel, {
              type: "control_request_resolved",
              requestId: request.requestId,
              decision: response.decision,
              feedback: response.feedback,
              updatedInput: response.updatedInput,
              answer: response.answer,
            });
          }}
        />

        {channelState.error && (
          <div className="px-4 pb-1">
            <div className="mx-auto max-w-3xl rounded-xl bg-red-500/[0.06] px-3 py-2 text-xs text-red-400/80">
              {channelState.error}
            </div>
          </div>
        )}

        {selectedKbDocs.length > 0 && (
          <div className="px-4 md:px-8 lg:px-12">
            <div className="mx-auto max-w-3xl">
              <KbContextBar docs={selectedKbDocs} onRemove={handleRemoveKbDoc} />
            </div>
          </div>
        )}

        <RunInspectorDock
          channelState={channelState}
          queuedMessages={queuedForChannel}
          controlRequests={controlsForChannel}
          compactDetails={rightWorkInspectorOpen}
        />

        <ChatInput
          ref={chatInputRef}
          onSend={handleSend}
          uiLanguage={channelState.responseLanguage}
          onReset={handleReset}
          streaming={channelState.streaming}
          onCancel={handleCancel}
          cancelHint={cancelHint}
          replyingTo={replyingTo}
          onCancelReply={() => setReplyingTo(null)}
          queuedCount={queuedForChannel.length}
          onCancelQueue={handleCancelQueue}
          queueFull={queuedForChannel.length >= MAX_QUEUED_MESSAGES}
          streamingMode={streamingComposerMode}
          onStreamingModeChange={setStreamingComposerMode}
          steeringDisabled={!canSteerMidTurn({
            hasFiles: false,
            hasKbContext: selectedKbDocs.length > 0,
          })}
          steeringDisabledReason={
            channelState.responseLanguage === "ko"
              ? "선택한 지식은 현재 실행이 끝난 뒤 전송됩니다."
              : "Selected knowledge will send after the current run."
          }
          kbDocs={allKbDocs}
          onSelectKbDoc={handleToggleKbDoc}
          uploadStates={uploadStates}
          composerAccessory={composerAccessory}
          customSkills={chatInputCustomSkills}
        />
      </div>

      <KbSidePanel
        botId={BOT_ID}
        collections={kbCollections}
        loading={kbLoading}
        refreshing={kbRefreshing}
        workspaceFiles={workspaceFiles}
        workspaceLoading={workspaceLoading}
        workspaceRefreshing={workspaceRefreshing}
        selectedDocs={selectedKbDocs}
        onToggleDoc={handleToggleKbDoc}
        onRefresh={() => void refreshKnowledge()}
        onWorkspaceRefresh={() => void refreshWorkspace()}
        onWorkspaceFileSave={saveWorkspaceFile}
        getAccessToken={getAccessToken}
        channelState={channelState}
        queuedMessages={queuedForChannel}
        controlRequests={controlsForChannel}
        onWorkOpenChange={setRightWorkInspectorOpen}
      />
    </div>
  );
}
