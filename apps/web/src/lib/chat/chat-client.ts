"use client";

import type {
  BrowserFrame,
  Channel,
  ChannelState,
  ChatMessage,
  DocumentDraftPreview,
  ServerMessage,
  ReorderEntry,
  ReplyTo,
  ChannelMemoryMode,
  ToolActivity,
  PatchPreview,
  PatchPreviewFile,
  SubagentActivity,
  SubagentActivityStatus,
  TaskBoardSnapshot,
  TaskBoardTask,
  CitationGateStatus,
  ControlEvent,
  ControlRequestRecord,
  ControlRequestResponse,
  InspectedSource,
  InspectedSourceKind,
  ResponseUsage,
  RuntimeTrace,
} from "./types";
import type { ActiveSnapshot } from "./active-snapshot";
import { getResetCounter } from "./chat-store";

type LiveTurnPhase = NonNullable<ChannelState["turnPhase"]>;

const DEFAULT_CHAT_PROXY_URL = "https://chat.openmagi.ai";
const LEGACY_CHAT_PROXY_URL = "https://chat.clawy.pro";
const CHAT_PROXY_URL = process.env.NEXT_PUBLIC_CHAT_PROXY_URL || DEFAULT_CHAT_PROXY_URL;
const CHAT_PROXY_URLS = Array.from(
  new Set([CHAT_PROXY_URL, LEGACY_CHAT_PROXY_URL, DEFAULT_CHAT_PROXY_URL]),
);
/**
 * Phase 1 — time allotted for HTTP headers to arrive. Cleared once
 * fetch() resolves. §11.12 web watchdog (2026-04-20). Matches
 * `STREAM_CONNECT_TIMEOUT_MS` in apps/mobile/src/lib/chat-api.ts.
 */
const STREAM_CONNECT_TIMEOUT_MS = 120_000;
/**
 * Phase 2 — rolling idle timer armed AFTER fetch() resolves. Reset on
 * every chunk from the reader. chat-proxy injects `: heartbeat <ts>\n\n`
 * SSE comments every 15s so this only fires when the stream is truly
 * dead. Matches `STREAM_IDLE_TIMEOUT_MS` in mobile chat-api.ts.
 */
// Large rolling window — chat-proxy emits `: heartbeat` every 15s, so any
// live connection resets this well before expiry. Only fires when the
// connection is genuinely dead (network partition, proxy crash, etc.) so
// generous tolerance for multi-hour agent runs is harmless.
const STREAM_IDLE_TIMEOUT_MS = 600_000;

const DEFAULT_SMOOTHER_BURST_THRESHOLD_CHARS = 96;
const DEFAULT_SMOOTHER_INITIAL_CHARS = 2;
const DEFAULT_SMOOTHER_CHARS_PER_TICK = 3;
const DEFAULT_SMOOTHER_TICK_MS = 24;
const DEFAULT_SMOOTHER_MAX_TICKS = 60;
const REPLACEMENT_CHAR = "\uFFFD";
const REPLACEMENT_CHAR_RE = /\uFFFD+/g;
const LIVE_SNAPSHOT_REPAIR_DELAY_MS = 1_200;
const LIVE_SNAPSHOT_REPAIR_MAX_ATTEMPTS = 3;
const INTERNAL_VERIFIER_ERROR_MESSAGE =
  "응답 검증 중 내부 오류가 발생했습니다. 다시 시도해 주세요.";
const INTERNAL_VERIFIER_ERROR_PATTERNS = [
  /^beforeCommit blocked:/iu,
  /^hook:[^\s]+(?:\s+threw:|\s+.*?(?:timeout|timed out))/iu,
  /\bhook timeout:?\s+builtin:/iu,
];
const SILENT_VERIFIER_META_ERROR_RE =
  /source-verified final answer|inspected-source context|claim[-_\s]?citation|research proof|runtime verifier stopped|promised work without completing|GOAL_PROGRESS_EXECUTE_NEXT|INTERACTIVE_TOOL_REQUIRED/iu;
const RESEARCH_PROOF_PUBLIC_FAILURE_TEXT_RE =
  /I could not complete a source-verified final answer for this request\. Please retry with a narrower scope or ask me to continue from the inspected-source context\.?/giu;
const RUNTIME_VERIFIER_PUBLIC_FAILURE_TEXT_RE =
  /(?:⚠️\s*)?The runtime verifier stopped this run because the assistant promised work without completing it\. No final answer was produced\. Retry the request; the runtime will steer the agent to call the required tools before answering\.?/giu;
const PROVIDER_BUSY_ERROR_MESSAGE =
  "The model is temporarily busy. Please try again in a moment.";
const PROVIDER_BUSY_TEXT_RE = /overloaded|capacity|service.?busy|temporarily busy/iu;
const KOREAN_TEXT_RE = /[가-힣]/u;
const ACTIVITY_PROGRESS_PATH_KEYS = [
  "path",
  "file_path",
  "filepath",
  "file",
  "filename",
  "workspacePath",
  "workspace_path",
  "target",
] as const;

export interface StreamingTextSmootherOptions {
  burstThresholdChars?: number;
  initialChars?: number;
  charsPerTick?: number;
  tickMs?: number;
  maxTicks?: number;
}

export interface StreamingTextSmoother {
  push(text: string): void;
  flush(): Promise<void>;
  clear(): void;
}

function recordFromUnknown(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringField(record: Record<string, unknown> | null, key: string): string | null {
  const value = record?.[key];
  return typeof value === "string" ? value : null;
}

function numberField(record: Record<string, unknown> | null, key: string): number | null {
  const value = record?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringArrayField(record: Record<string, unknown> | null, key: string): string[] | undefined {
  const value = record?.[key];
  if (!Array.isArray(value)) return undefined;
  const items = value.filter((item): item is string => typeof item === "string");
  return items.length > 0 ? items : undefined;
}

function latestUserRequestText(messages: Pick<ChatMessage, "role" | "content">[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role === "user" && message.content.trim()) {
      return message.content.trim();
    }
  }
  return "";
}

function isKoreanText(value: string): boolean {
  return KOREAN_TEXT_RE.test(value);
}

function genericWaitStageLabels(requestText: string): string[] {
  return isKoreanText(requestText)
    ? ["요청 처리 중", "다음 단계 준비 중", "응답 구조 잡는 중"]
    : ["Processing request", "Preparing next step", "Structuring response"];
}

function publicModelWaitDetail(requestText: string): string {
  return isKoreanText(requestText)
    ? "공개 진행 로그를 갱신하고 있습니다"
    : "Updating the public progress log";
}

function normalizeActivityToolLabel(value: string): string {
  return value.replace(/[^a-z0-9]/gi, "").toLowerCase();
}

function publicActivityHeartbeatLabel(activityLabel: string, requestText: string): string {
  const tool = normalizeActivityToolLabel(activityLabel);
  const korean = isKoreanText(requestText);
  if (
    tool === "fileread" ||
    tool === "read" ||
    tool === "artifactread" ||
    tool === "documentread" ||
    tool === "reviewingdocument"
  ) {
    return korean ? "자료 읽는 중" : "Reading materials";
  }
  if (
    tool === "spawnagent" ||
    tool === "taskget" ||
    tool === "taskread" ||
    tool === "taskstatus" ||
    tool === "assigninghelper" ||
    tool === "subagent"
  ) {
    return korean ? "서브에이전트 작업 중" : "Running sub-agent";
  }
  if (
    tool === "filewrite" ||
    tool === "write" ||
    tool === "fileedit" ||
    tool === "edit" ||
    tool === "documentwrite"
  ) {
    return korean ? "결과 작성 중" : "Writing output";
  }
  if (tool === "time" || tool === "datetime" || tool === "checkedcurrenttime" || tool === "checkingcurrenttime") {
    return korean ? "현재 시간 확인 중" : "Checking current time";
  }
  if (tool === "webfetch" || tool === "websearch" || tool === "search" || tool === "fetch") {
    return korean ? "자료 조사 중" : "Researching materials";
  }
  return korean ? "작업 진행 중" : "Working through current step";
}

function previewObject(value?: string): Record<string, unknown> | null {
  if (!value) return null;
  try {
    return recordFromUnknown(JSON.parse(value));
  } catch {
    return null;
  }
}

function activityProgressTarget(activity: ToolActivity): string | undefined {
  const input = previewObject(activity.inputPreview);
  const output = previewObject(activity.outputPreview);
  for (const key of ACTIVITY_PROGRESS_PATH_KEYS) {
    const fromInput = stringField(input, key);
    if (fromInput) return fromInput;
    const fromOutput = stringField(output, key);
    if (fromOutput) return fromOutput;
  }
  return undefined;
}

const INSPECTED_SOURCE_KINDS: readonly InspectedSourceKind[] = [
  "web_search",
  "web_fetch",
  "browser",
  "kb",
  "file",
  "external_repo",
  "external_doc",
  "subagent_result",
];

function parseInspectedSource(value: unknown): InspectedSource | null {
  const source = recordFromUnknown(value);
  const sourceId = stringField(source, "sourceId");
  const uri = stringField(source, "uri");
  if (!sourceId || !uri) return null;
  const rawKind = stringField(source, "kind");
  const kind = INSPECTED_SOURCE_KINDS.includes(rawKind as InspectedSourceKind)
    ? rawKind as InspectedSourceKind
    : "web_fetch";
  const inspectedAt = numberField(source, "inspectedAt") ?? Date.now();
  const parsed: InspectedSource = {
    sourceId,
    kind,
    uri,
    inspectedAt,
  };
  const turnId = stringField(source, "turnId");
  const toolName = stringField(source, "toolName");
  const toolUseId = stringField(source, "toolUseId");
  const title = stringField(source, "title");
  const contentHash = stringField(source, "contentHash");
  const contentType = stringField(source, "contentType");
  const trustTier = stringField(source, "trustTier");
  const snippets = stringArrayField(source, "snippets");
  if (turnId) parsed.turnId = turnId;
  if (toolName) parsed.toolName = toolName;
  if (toolUseId) parsed.toolUseId = toolUseId;
  if (title) parsed.title = title;
  if (contentHash) parsed.contentHash = contentHash;
  if (contentType) parsed.contentType = contentType;
  if (
    trustTier === "primary" ||
    trustTier === "official" ||
    trustTier === "secondary" ||
    trustTier === "unknown"
  ) {
    parsed.trustTier = trustTier;
  }
  if (snippets) parsed.snippets = snippets;
  return parsed;
}

function patchOperation(value: unknown): PatchPreviewFile["operation"] | null {
  return value === "create" || value === "update" || value === "delete" ? value : null;
}

function nonNegativeInteger(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(0, Math.floor(value))
    : 0;
}

function parsePatchPreviewFile(value: unknown): PatchPreviewFile | null {
  const record = recordFromUnknown(value);
  const path = stringField(record, "path");
  const operation = patchOperation(record?.operation);
  if (!path || !operation) return null;
  const oldSha256 = stringField(record, "oldSha256");
  const newSha256 = stringField(record, "newSha256");
  return {
    path,
    operation,
    hunks: nonNegativeInteger(record?.hunks),
    addedLines: nonNegativeInteger(record?.addedLines),
    removedLines: nonNegativeInteger(record?.removedLines),
    ...(oldSha256 ? { oldSha256 } : {}),
    ...(newSha256 ? { newSha256 } : {}),
  };
}

function parsePatchPreview(value: unknown): PatchPreview | null {
  const record = recordFromUnknown(value);
  if (!record) return null;
  const files = Array.isArray(record.files)
    ? record.files
      .map(parsePatchPreviewFile)
      .filter((file): file is PatchPreviewFile => file !== null)
    : [];
  const changedFiles = stringArrayField(record, "changedFiles") ?? files.map((file) => file.path);
  if (changedFiles.length === 0 && files.length === 0) return null;
  return {
    dryRun: record.dryRun === true,
    changedFiles,
    createdFiles: stringArrayField(record, "createdFiles") ?? [],
    deletedFiles: stringArrayField(record, "deletedFiles") ?? [],
    files,
  };
}

function parseCitationGateStatus(
  ev: Record<string, unknown>,
): CitationGateStatus | null {
  if (stringField(ev, "ruleId") !== "claim-citation-gate") return null;
  const verdict = ev.verdict;
  if (verdict !== "pending" && verdict !== "ok" && verdict !== "violation") {
    return null;
  }
  const detail = stringField(ev, "detail");
  return {
    ruleId: "claim-citation-gate",
    verdict,
    ...(detail ? { detail } : {}),
    checkedAt: Date.now(),
  };
}

function parseRuntimeTrace(ev: Record<string, unknown>): RuntimeTrace | null {
  const turnId = stringField(ev, "turnId");
  const title = stringField(ev, "title");
  const rawPhase = stringField(ev, "phase");
  const rawSeverity = stringField(ev, "severity");
  if (!turnId || !title) return null;
  if (
    rawPhase !== "verifier_blocked" &&
    rawPhase !== "retry_scheduled" &&
    rawPhase !== "retry_aborted" &&
    rawPhase !== "terminal_abort"
  ) return null;
  if (
    rawSeverity !== "info" &&
    rawSeverity !== "warning" &&
    rawSeverity !== "error"
  ) return null;
  const trace: RuntimeTrace = {
    turnId,
    phase: rawPhase,
    severity: rawSeverity,
    title,
    receivedAt: Date.now(),
  };
  const detail = stringField(ev, "detail");
  const reasonCode = stringField(ev, "reasonCode");
  const ruleId = stringField(ev, "ruleId");
  const requiredAction = stringField(ev, "requiredAction");
  const attempt = numberField(ev, "attempt");
  const maxAttempts = numberField(ev, "maxAttempts");
  if (detail) trace.detail = detail;
  if (reasonCode) trace.reasonCode = reasonCode;
  if (ruleId) trace.ruleId = ruleId;
  if (attempt !== null) trace.attempt = attempt;
  if (maxAttempts !== null) trace.maxAttempts = maxAttempts;
  if (typeof ev.retryable === "boolean") trace.retryable = ev.retryable;
  if (requiredAction) trace.requiredAction = requiredAction;
  return trace;
}

function parseEmbeddedJsonObject(message: string): Record<string, unknown> | null {
  const firstBrace = message.indexOf("{");
  if (firstBrace < 0) return null;
  try {
    return recordFromUnknown(JSON.parse(message.slice(firstBrace)));
  } catch {
    return null;
  }
}

function overloadedProviderMessage(message: string): string | null {
  const payload = parseEmbeddedJsonObject(message);
  const nestedError = recordFromUnknown(payload?.error);
  const errorRecord = nestedError ?? payload;
  const errorType = stringField(errorRecord, "type") ?? stringField(payload, "type");
  const errorMessage = stringField(errorRecord, "message") ?? stringField(payload, "message");

  if (errorType === "overloaded_error") return PROVIDER_BUSY_ERROR_MESSAGE;
  if (errorMessage && PROVIDER_BUSY_TEXT_RE.test(errorMessage)) {
    return PROVIDER_BUSY_ERROR_MESSAGE;
  }

  if (/\boverloaded_error\b/iu.test(message)) return PROVIDER_BUSY_ERROR_MESSAGE;
  if (/\b(?:api error|upstream|provider|http 529)\b/iu.test(message) && PROVIDER_BUSY_TEXT_RE.test(message)) {
    return PROVIDER_BUSY_ERROR_MESSAGE;
  }

  return null;
}

function userVisibleTerminalAgentError(message: string): string {
  const trimmed = message.trim();
  if (!trimmed) return trimmed;
  const providerMessage = overloadedProviderMessage(trimmed);
  if (providerMessage) return providerMessage;
  return INTERNAL_VERIFIER_ERROR_PATTERNS.some((pattern) => pattern.test(trimmed))
    ? INTERNAL_VERIFIER_ERROR_MESSAGE
    : trimmed;
}

function isSilentVerifierMetaMessage(message: string): boolean {
  return SILENT_VERIFIER_META_ERROR_RE.test(message);
}

function stripVerifierMetaText(text: string): string {
  const stripped = text
    .replace(RESEARCH_PROOF_PUBLIC_FAILURE_TEXT_RE, "")
    .replace(RUNTIME_VERIFIER_PUBLIC_FAILURE_TEXT_RE, "");
  return isSilentVerifierMetaMessage(stripped.trim()) ? "" : stripped;
}

/**
 * Smooths SSE text deltas before they hit React state.
 *
 * The stream often arrives as uneven token or sentence bursts. Reveal the
 * first character immediately, then drain the rest on short ticks so the UI
 * reads like fast typing while still catching up quickly after large bursts.
 */
export function createStreamingTextSmoother(
  emit: (delta: string) => void,
  options: StreamingTextSmootherOptions = {},
): StreamingTextSmoother {
  const burstThresholdChars =
    options.burstThresholdChars ?? DEFAULT_SMOOTHER_BURST_THRESHOLD_CHARS;
  const initialChars = Math.max(
    1,
    options.initialChars ?? DEFAULT_SMOOTHER_INITIAL_CHARS,
  );
  const charsPerTick = Math.max(
    1,
    options.charsPerTick ?? DEFAULT_SMOOTHER_CHARS_PER_TICK,
  );
  const tickMs = Math.max(0, options.tickMs ?? DEFAULT_SMOOTHER_TICK_MS);
  const maxTicks = Math.max(1, options.maxTicks ?? DEFAULT_SMOOTHER_MAX_TICKS);

  let pending: string[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;
  let flushResolvers: Array<() => void> = [];

  const resolveFlushers = (): void => {
    if (pending.length > 0 || timer) return;
    const resolvers = flushResolvers;
    flushResolvers = [];
    for (const resolve of resolvers) resolve();
  };

  const take = (count: number): string => {
    const chunk = pending.slice(0, count).join("");
    pending = pending.slice(count);
    return chunk;
  };

  const schedule = (): void => {
    if (timer || pending.length === 0) {
      resolveFlushers();
      return;
    }
    timer = setTimeout(() => {
      timer = null;
      const acceleratedChunkSize =
        pending.length > burstThresholdChars ? Math.ceil(pending.length / maxTicks) : 0;
      const chunkSize = Math.max(charsPerTick, acceleratedChunkSize);
      const chunk = take(chunkSize);
      if (chunk) emit(chunk);
      schedule();
    }, tickMs);
  };

  return {
    push(text: string): void {
      if (!text) return;
      const chars = Array.from(text);

      if (pending.length === 0 && !timer) {
        const first = chars.splice(0, initialChars).join("");
        if (first) emit(first);
      }
      pending.push(...chars);
      schedule();
    },
    flush(): Promise<void> {
      if (pending.length === 0 && !timer) return Promise.resolve();
      return new Promise((resolve) => {
        flushResolvers.push(resolve);
        schedule();
      });
    },
    clear(): void {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      pending = [];
      resolveFlushers();
    },
  };
}

type TokenGetter = () => Promise<string | null>;

let _getToken: TokenGetter = async () => null;

/** Must be called once at app init with Privy's getAccessToken */
export function setChatTokenGetter(getter: TokenGetter): void {
  _getToken = getter;
}

class AuthExpiredError extends Error {
  constructor() {
    super("Auth expired");
    this.name = "AuthExpiredError";
  }
}

async function getToken(): Promise<string> {
  // Privy may need a moment after ready=true before tokens are available
  for (let i = 0; i < 5; i++) {
    const token = await _getToken();
    if (token) return token;
    if (i < 4) await new Promise((r) => setTimeout(r, 500));
  }
  throw new AuthExpiredError();
}

async function chatFetch(path: string, options?: RequestInit): Promise<Response> {
  let token: string;
  try {
    token = await getToken();
  } catch (err) {
    console.error("[chat-client] Token acquisition failed:", err);
    throw err;
  }
  const res = await chatProxyFetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...options?.headers,
    },
  });
  if (res.status === 401) throw new AuthExpiredError();
  return res;
}

function shouldRetryChatProxyResponse(status: number): boolean {
  return [502, 503, 504, 521, 522, 523, 524].includes(status);
}

async function chatProxyFetch(path: string, options?: RequestInit): Promise<Response> {
  let lastError: unknown;

  for (const [idx, baseUrl] of CHAT_PROXY_URLS.entries()) {
    try {
      const res = await fetch(`${baseUrl}${path}`, options);
      if (idx < CHAT_PROXY_URLS.length - 1 && shouldRetryChatProxyResponse(res.status)) {
        continue;
      }
      return res;
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") throw err;
      lastError = err;
    }
  }

  throw lastError instanceof Error ? lastError : new Error("chat proxy request failed");
}

// --- Channel CRUD ---

export async function fetchChannels(botId: string): Promise<Channel[]> {
  const res = await chatFetch(`/v1/chat/${botId}/channels`);
  if (!res.ok) throw new Error(`Failed to fetch channels: ${res.status}`);
  const data = await res.json();
  return data.channels ?? [];
}

export async function createChannel(
  botId: string,
  name: string,
  displayName?: string,
  category?: string,
  memoryMode?: ChannelMemoryMode,
): Promise<Channel> {
  const res = await chatFetch(`/v1/chat/${botId}/channels`, {
    method: "POST",
    body: JSON.stringify({ name, displayName, category, memoryMode }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    throw new Error(err.error || `Failed to create channel: ${res.status}`);
  }
  const data = await res.json();
  return data.channel;
}

export async function deleteChannel(botId: string, channelName: string): Promise<void> {
  const res = await chatFetch(`/v1/chat/${botId}/channels/${encodeURIComponent(channelName)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    throw new Error(err.error || `Failed to delete channel: ${res.status}`);
  }
}

export async function reorderChannels(botId: string, channels: ReorderEntry[]): Promise<void> {
  await chatFetch(`/v1/chat/${botId}/channels/reorder`, {
    method: "POST",
    body: JSON.stringify({ channels }),
  });
}

export async function updateChannel(
  botId: string,
  channelName: string,
  updates: {
    display_name?: string;
    category?: string;
    position?: number;
    memory_mode?: ChannelMemoryMode;
    model_selection?: string | null;
    router_type?: string | null;
  },
): Promise<void> {
  await chatFetch(`/v1/chat/${botId}/channels/${encodeURIComponent(channelName)}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
}

export async function fetchChannelMessages(
  botId: string,
  channelName: string,
  since?: string,
  limit = 50,
): Promise<ServerMessage[]> {
  const params = new URLSearchParams();
  if (since) params.set("since", since);
  params.set("limit", String(limit));
  const res = await chatFetch(
    `/v1/chat/${botId}/channels/${encodeURIComponent(channelName)}/messages?${params}`,
  );
  const data = await res.json();
  return data.messages ?? [];
}

// --- Mid-turn injection (#86) ---

/**
 * Result of `injectMessage`.
 *
 * - `injected: true`  — chat-proxy returned 200 and the message was queued
 *                        into the running turn. Client should render the
 *                        user bubble with an `injected` marker.
 * - `injected: false` — chat-proxy returned 4xx (most commonly 409
 *                        `no_active_turn`) or the request failed. Client
 *                        should fall back to the existing "queue and flush
 *                        on onDone" behavior via `enqueueMessage`.
 */
export interface InjectMessageResult {
  injected: boolean;
  /** Present when injected=true: server-assigned injection id. */
  injectionId?: string;
  /** Populated when injected=false: upstream status (or 0 for network err). */
  status?: number;
  /** Short machine-readable reason (e.g. "no_active_turn"). */
  reason?: string;
}

export interface InterruptTurnResult {
  accepted: boolean;
  handoffRequested: boolean;
  status?: number;
  reason?: string;
}

/**
 * Post a mid-turn user message to `/v1/chat/:botId/inject` (core-agent
 * 0.15.2+). See docs/plans/2026-04-20-message-queue-mid-turn-injection-design.md.
 *
 * Never throws: a 4xx / network failure is surfaced as `injected: false`
 * so the caller can transparently fall back to the normal queue path.
 */
export async function injectMessage(
  botId: string,
  sessionKey: string,
  text: string,
  source: "web" | "mobile" = "web",
): Promise<InjectMessageResult> {
  try {
    const token = await getToken();
    const res = await chatProxyFetch(`/v1/chat/${botId}/inject`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ sessionKey, text, source }),
    });
    if (res.ok) {
      const data = (await res.json().catch(() => ({}))) as { injectionId?: string };
      return { injected: true, injectionId: data.injectionId, status: res.status };
    }
    const body = await res.json().catch(() => ({}));
    return {
      injected: false,
      status: res.status,
      reason: (body as { error?: string }).error ?? `http_${res.status}`,
    };
  } catch (err) {
    return {
      injected: false,
      status: 0,
      reason: err instanceof Error ? err.message : "network_error",
    };
  }
}

export async function interruptTurn(
  botId: string,
  sessionKey: string,
  handoffRequested = false,
  source: "web" | "mobile" = "web",
): Promise<InterruptTurnResult> {
  try {
    const token = await getToken();
    const res = await chatProxyFetch(`/v1/chat/${botId}/interrupt`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ sessionKey, handoffRequested, source }),
    });
    if (res.ok) {
      const data = (await res.json().catch(() => ({}))) as {
        status?: string;
        handoffRequested?: boolean;
      };
      return {
        accepted: data.status === "accepted",
        handoffRequested: data.handoffRequested === true,
        status: res.status,
      };
    }
    const body = await res.json().catch(() => ({}));
    return {
      accepted: false,
      handoffRequested,
      status: res.status,
      reason: (body as { error?: string }).error ?? `http_${res.status}`,
    };
  } catch (err) {
    return {
      accepted: false,
      handoffRequested,
      status: 0,
      reason: err instanceof Error ? err.message : "network_error",
    };
  }
}

// --- Active snapshot (#111 resume-on-refresh) ---

/**
 * Fetch an in-flight assistant message, if any, for the given (botId, channel).
 *
 * Returns null in all "nothing to resume" paths — missing snapshot, TTL
 * expired, server error, auth error — because the UI has a sensible fallback
 * (show only committed messages from Supabase). Never throws.
 */
export async function getActiveSnapshot(
  botId: string,
  channelName: string,
): Promise<ActiveSnapshot | null> {
  try {
    const token = await getToken();
    const res = await fetch(
      `${CHAT_PROXY_URL}/v1/chat/${botId}/active-snapshot/${encodeURIComponent(channelName)}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!res.ok) return null;
    const data = (await res.json().catch(() => null)) as {
      snapshot?: ActiveSnapshot | null;
    } | null;
    return data?.snapshot ?? null;
  } catch {
    return null;
  }
}

export async function fetchControlRequests(
  botId: string,
  sessionKey: string,
  channelName?: string,
): Promise<ControlRequestRecord[]> {
  const params = new URLSearchParams({ sessionKey });
  if (channelName) params.set("channelName", channelName);
  const res = await chatFetch(
    `/v1/chat/${botId}/control-requests?${params.toString()}`,
  );
  if (!res.ok) throw new Error(`Failed to fetch control requests: ${res.status}`);
  const data = (await res.json()) as { requests?: ControlRequestRecord[] };
  return Array.isArray(data.requests) ? data.requests : [];
}

export async function fetchControlEvents(
  botId: string,
  sessionKey: string,
  channelName?: string,
  lastSeq = 0,
): Promise<{ events: ControlEvent[]; lastSeq: number }> {
  const params = new URLSearchParams({ sessionKey, lastSeq: String(lastSeq) });
  if (channelName) params.set("channelName", channelName);
  const res = await chatFetch(
    `/v1/chat/${botId}/control-events?${params.toString()}`,
  );
  if (!res.ok) throw new Error(`Failed to fetch control events: ${res.status}`);
  const data = (await res.json()) as {
    events?: unknown[];
    lastSeq?: number;
  };
  return {
    events: Array.isArray(data.events)
      ? data.events
          .map((event) => parseControlEvent(event))
          .filter((event): event is ControlEvent => event !== null)
      : [],
    lastSeq: typeof data.lastSeq === "number" ? data.lastSeq : lastSeq,
  };
}

export async function respondToControlRequest(
  botId: string,
  request: ControlRequestRecord,
  response: ControlRequestResponse,
): Promise<ControlRequestRecord> {
  const params = new URLSearchParams({ sessionKey: request.sessionKey });
  if (request.channelName) params.set("channelName", request.channelName);
  const payload = controlRequestResponsePayload(request, response);
  const res = await chatFetch(
    `/v1/chat/${botId}/control-requests/${encodeURIComponent(request.requestId)}/response?${params.toString()}`,
    {
      method: "POST",
      body: JSON.stringify({ ...payload, sessionKey: request.sessionKey }),
    },
  );
  const data = (await res.json().catch(() => ({}))) as {
    ok?: boolean;
    error?: string;
    request?: ControlRequestRecord;
  };
  if (!res.ok && !data.request) {
    throw new Error(data.error || `Failed to respond to control request: ${res.status}`);
  }
  if (!data.request) {
    const local = locallyResolvedControlRequest(request, response, data.ok === true);
    if (local) return local;
    throw new Error("control request response missing request");
  }
  return data.request;
}

function controlRequestChoiceIds(value: unknown): Set<string> {
  if (!value || typeof value !== "object") return new Set();
  const choices = (value as { choices?: unknown }).choices;
  if (!Array.isArray(choices)) return new Set();
  return new Set(
    choices
      .map((choice) => {
        if (!choice || typeof choice !== "object") return null;
        const id = (choice as { id?: unknown }).id;
        return typeof id === "string" ? id : null;
      })
      .filter((id): id is string => id !== null),
  );
}

function controlRequestResponsePayload(
  request: ControlRequestRecord,
  response: ControlRequestResponse,
): ControlRequestResponse & { selectedId?: string; freeText?: string } {
  if (
    request.kind !== "user_question" ||
    response.decision !== "answered" ||
    typeof response.answer !== "string"
  ) {
    return response;
  }
  const answer = response.answer.trim();
  if (!answer) return response;
  const choiceIds = controlRequestChoiceIds(request.proposedInput);
  return {
    ...response,
    ...(choiceIds.has(answer) ? { selectedId: answer } : { freeText: answer }),
  };
}

function locallyResolvedControlRequest(
  request: ControlRequestRecord,
  response: ControlRequestResponse,
  ok: boolean,
): ControlRequestRecord | null {
  if (!ok || request.kind !== "user_question" || response.decision !== "answered") {
    return null;
  }
  return {
    ...request,
    state: "answered",
    decision: "answered",
    resolvedAt: Date.now(),
    ...(response.feedback !== undefined ? { feedback: response.feedback } : {}),
    ...(response.updatedInput !== undefined ? { updatedInput: response.updatedInput } : {}),
    ...(response.answer !== undefined ? { answer: response.answer } : {}),
  };
}

/** Build the sessionKey the inject route expects — mirrors the one used by
 * sendMessage so both endpoints address the same core-agent session. */
export function buildSessionKey(botId: string, channelName: string): string {
  const rc = getResetCounter(botId, channelName);
  return rc > 0
    ? `agent:main:app:${channelName}:${rc}`
    : `agent:main:app:${channelName}`;
}

// --- SSE Streaming ---

export interface SendMessageOptions {
  model?: string;
  /** Create a persistent goal mission and let runtime continue until done. */
  goalMode?: boolean;
  /** If set, the newest user message is a reply to this target. */
  replyTo?: ReplyTo;
  onDelta: (text: string) => void;
  onThinkingDelta?: (text: string) => void;
  onResponseClear?: () => void;
  /** Replaces streamingText atomically (no empty-string intermediate state).
   *  Used when the agent channel takes over from the legacy SSE path so the
   *  UI never renders an empty bubble between clear-and-first-delta. */
  onContentReplace?: (text: string) => void;
  /** Live tool activity feed — fires whenever the activity list changes */
  onToolActivity?: (activities: ToolActivity[]) => void;
  /** Live spawned subagent roster — fires whenever child-agent state changes. */
  onSubagentActivity?: (subagents: SubagentActivity[]) => void;
  /** Live TaskBoard snapshot — fires on every `task_board` AgentEvent. */
  onTaskBoard?: (snapshot: TaskBoardSnapshot) => void;
  /** Latest safe browser preview frame from parent or child browser work. */
  onBrowserFrame?: (frame: BrowserFrame) => void;
  /** Latest safe markdown/text draft preview from an in-flight document write. */
  onDocumentDraft?: (draft: DocumentDraftPreview | null) => void;
  /** Durable mission state events emitted by core-agent MissionLedger. */
  onMissionEvent?: (event: Record<string, unknown>) => void;
  /** Live source ledger evidence from source_inspected AgentEvents. */
  onSourceInspected?: (source: InspectedSource) => void;
  /** Latest claim-citation gate status from rule_check AgentEvents. */
  onCitationGate?: (status: CitationGateStatus) => void;
  /** Public runtime verifier/contract traces from the current turn. */
  onRuntimeTrace?: (trace: RuntimeTrace) => void;
  onControlEvent?: (event: ControlEvent) => void;
  onControlReplayComplete?: (lastSeq: number) => void;
  onTurnPhase?: (phase: LiveTurnPhase) => void;
  onHeartbeat?: (elapsedMs: number) => void;
  onPendingInjectionCount?: (queuedCount: number) => void;
  onUsage?: (usage: ResponseUsage) => void;
  onDone: () => void;
  onError: (error: Error) => void;
  signal?: AbortSignal;
}

/** Match `agent-run.sh skill-name` or `claude-agent.sh skill-name` in raw text */
const SKILL_INVOCATION_RE = /\b(?:agent-run|claude-agent)\.sh\s+([a-z][a-z0-9-]{2,})/g;

/** Strip leading path / wrappers so the user sees a clean tool label */
function prettifyToolName(raw: string): string {
  if (!raw) return "tool";
  // strip "functions." prefix and trailing parens
  return raw.replace(/^functions\./, "").replace(/\(.*\)$/, "").trim() || "tool";
}

function isControlRequestRecord(value: unknown): value is ControlRequestRecord {
  if (!value || typeof value !== "object") return false;
  const rec = value as Partial<ControlRequestRecord>;
  return (
    typeof rec.requestId === "string" &&
    typeof rec.kind === "string" &&
    typeof rec.state === "string" &&
    typeof rec.sessionKey === "string" &&
    typeof rec.prompt === "string" &&
    typeof rec.createdAt === "number" &&
    typeof rec.expiresAt === "number"
  );
}

function parseDocumentDraft(value: Record<string, unknown>): DocumentDraftPreview | null {
  if (value.type !== "document_draft") return null;
  const id = typeof value.id === "string" && value.id ? value.id : null;
  const format = value.format === "txt" || value.format === "md" ? value.format : null;
  const contentPreview =
    typeof value.contentPreview === "string" ? value.contentPreview : null;
  const contentLength =
    typeof value.contentLength === "number" && Number.isFinite(value.contentLength)
      ? Math.max(0, Math.floor(value.contentLength))
      : null;
  if (!id || !format || contentPreview === null || contentLength === null) return null;
  return {
    id,
    format,
    status: "streaming",
    contentPreview,
    contentLength,
    truncated: value.truncated === true,
    updatedAt: Date.now(),
    ...(typeof value.filename === "string" && value.filename
      ? { filename: value.filename }
      : {}),
  };
}

function parseResponseUsage(value: unknown): ResponseUsage | null {
  const record = recordFromUnknown(value);
  if (!record) return null;
  const inputTokens = record.inputTokens;
  const outputTokens = record.outputTokens;
  const costUsd = record.costUsd;
  if (
    typeof inputTokens !== "number" ||
    typeof outputTokens !== "number" ||
    typeof costUsd !== "number" ||
    !Number.isFinite(inputTokens) ||
    !Number.isFinite(outputTokens) ||
    !Number.isFinite(costUsd)
  ) {
    return null;
  }
  return {
    inputTokens: Math.max(0, Math.floor(inputTokens)),
    outputTokens: Math.max(0, Math.floor(outputTokens)),
    costUsd: Math.max(0, costUsd),
  };
}

function parseControlEvent(value: unknown): ControlEvent | null {
  if (!value || typeof value !== "object") return null;
  const event = value as Record<string, unknown>;
  if (event.type === "control_request_created" && isControlRequestRecord(event.request)) {
    return { type: "control_request_created", request: event.request };
  }
  if (
    event.type === "control_request_resolved" &&
    typeof event.requestId === "string" &&
    (event.decision === "approved" ||
      event.decision === "denied" ||
      event.decision === "answered")
  ) {
    return {
      type: "control_request_resolved",
      requestId: event.requestId,
      decision: event.decision,
      ...(typeof event.feedback === "string" ? { feedback: event.feedback } : {}),
      ...(event.updatedInput !== undefined ? { updatedInput: event.updatedInput } : {}),
      ...(typeof event.answer === "string" ? { answer: event.answer } : {}),
    };
  }
  if (event.type === "control_request_cancelled" && typeof event.requestId === "string") {
    return {
      type: "control_request_cancelled",
      requestId: event.requestId,
      reason: typeof event.reason === "string" ? event.reason : "cancelled",
    };
  }
  if (event.type === "control_request_timed_out" && typeof event.requestId === "string") {
    return { type: "control_request_timed_out", requestId: event.requestId };
  }
  return null;
}

function legacyAskUserToControlRequest(
  ev: Record<string, unknown>,
  sessionKey: string,
  channelName: string,
): ControlRequestRecord | null {
  if (typeof ev.questionId !== "string" || typeof ev.question !== "string") {
    return null;
  }
  const turnId = ev.questionId.split(":ask:")[0] || undefined;
  return {
    requestId: ev.questionId,
    kind: "user_question",
    state: "pending",
    sessionKey,
    ...(turnId ? { turnId } : {}),
    channelName,
    source: "turn",
    prompt: ev.question,
    proposedInput: {
      choices: Array.isArray(ev.choices) ? ev.choices : [],
      allowFreeText: ev.allowFreeText === true,
    },
    createdAt: Date.now(),
    expiresAt: Date.now() + 10 * 60_000,
  };
}

function planReadyToControlRequest(
  ev: Record<string, unknown>,
  sessionKey: string,
  channelName: string,
): ControlRequestRecord | null {
  if (
    typeof ev.requestId !== "string" ||
    typeof ev.planId !== "string" ||
    typeof ev.plan !== "string"
  ) {
    return null;
  }
  return {
    requestId: ev.requestId,
    kind: "plan_approval",
    state: "pending",
    sessionKey,
    channelName,
    source: "plan",
    prompt: "Approve this plan before execution tools are unlocked.",
    proposedInput: { planId: ev.planId, plan: ev.plan },
    createdAt: Date.now(),
    expiresAt: Date.now() + 30 * 60_000,
  };
}

export async function sendMessage(
  botId: string,
  channelName: string,
  messages: Pick<ChatMessage, "role" | "content">[],
  options: SendMessageOptions,
): Promise<void> {
  const {
    model = "auto",
    goalMode,
    replyTo,
    onDelta,
    onThinkingDelta,
    onResponseClear,
    onContentReplace,
    onToolActivity,
    onSubagentActivity,
    onTaskBoard,
    onBrowserFrame,
    onDocumentDraft,
    onMissionEvent,
    onSourceInspected,
    onCitationGate,
    onRuntimeTrace,
    onControlEvent,
    onControlReplayComplete,
    onTurnPhase,
    onHeartbeat,
    onPendingInjectionCount,
    onUsage,
    onDone,
    onError,
    signal,
  } = options;

  // Tool activity tracker — preserved across SSE chunks via closure.
  // `gotContent` lives at sendMessage scope so `handleAgentEvent` (defined
  // below, uses it from the `text_delta` case) shares the same cell as the
  // main OpenAI-compat stream loop. Previously declared inside the try
  // block → handleAgentEvent's reference was out of scope → ReferenceError
  // at runtime every time core-agent emitted `event: agent` text_delta.
  const activities = new Map<string, ToolActivity>();
  const subagents = new Map<string, SubagentActivity>();
  const toolCallIdsByIndex = new Map<number, string>();
  const seenSkills = new Set<string>();
  let modelProgressId: string | null = null;
  let modelProgressBaseId: string | null = null;
  let gotContent = false;
  let terminalAgentError: string | null = null;
  let terminalAgentErrorIsSilent = false;
  let legacyContentEmitted = false;
  let clearedLegacyForAgentChannel = false;
  let latestDocumentDraft: DocumentDraftPreview | null = null;
  let emittedVisibleText = "";
  let sawLiveReplacementChar = false;
  let liveSnapshotRepairTimer: ReturnType<typeof setTimeout> | null = null;
  let liveSnapshotRepairInFlight = false;
  let liveSnapshotRepairAttempts = 0;
  let liveSnapshotRepairGeneration = 0;

  function emitVisibleDelta(delta: string): void {
    if (!delta) return;
    emittedVisibleText += delta;
    onDelta(delta);
  }

  const visibleText = createStreamingTextSmoother(emitVisibleDelta);
  const userRequestText = latestUserRequestText(messages);
  const publicModelWaitStages = genericWaitStageLabels(userRequestText);
  const publicModelHeartbeatDetail = publicModelWaitDetail(userRequestText);

  function resetLiveSnapshotRepairState(): void {
    liveSnapshotRepairGeneration += 1;
    if (liveSnapshotRepairTimer) {
      clearTimeout(liveSnapshotRepairTimer);
      liveSnapshotRepairTimer = null;
    }
    liveSnapshotRepairInFlight = false;
    liveSnapshotRepairAttempts = 0;
    sawLiveReplacementChar = false;
  }

  function cancelLiveSnapshotRepair(): void {
    resetLiveSnapshotRepairState();
    liveSnapshotRepairAttempts = LIVE_SNAPSHOT_REPAIR_MAX_ATTEMPTS;
  }

  function clearVisibleText(): void {
    visibleText.clear();
    emittedVisibleText = "";
    resetLiveSnapshotRepairState();
    onResponseClear?.();
  }

  function replaceVisibleTextFromSnapshot(content: string): void {
    visibleText.clear();
    emittedVisibleText = content;
    sawLiveReplacementChar = false;
    liveSnapshotRepairAttempts = 0;
    if (onContentReplace) {
      onContentReplace(content);
    } else {
      onResponseClear?.();
      emitVisibleDelta(content);
    }
  }

  function shouldPatchLiveTextFromSnapshot(snapshot: ActiveSnapshot | null): snapshot is ActiveSnapshot {
    const content = snapshot?.content ?? "";
    return (
      sawLiveReplacementChar &&
      content.length > 0 &&
      !content.includes(REPLACEMENT_CHAR) &&
      content !== emittedVisibleText
    );
  }

  function scheduleLiveSnapshotRepair(): void {
    if (
      liveSnapshotRepairTimer ||
      liveSnapshotRepairInFlight ||
      liveSnapshotRepairAttempts >= LIVE_SNAPSHOT_REPAIR_MAX_ATTEMPTS
    ) {
      return;
    }
    liveSnapshotRepairAttempts += 1;
    const generation = liveSnapshotRepairGeneration;
    liveSnapshotRepairTimer = setTimeout(() => {
      liveSnapshotRepairTimer = null;
      liveSnapshotRepairInFlight = true;
      void (async () => {
        let repaired = false;
        try {
          const snapshot = await getActiveSnapshot(botId, channelName);
          if (generation !== liveSnapshotRepairGeneration) return;
          if (shouldPatchLiveTextFromSnapshot(snapshot)) {
            replaceVisibleTextFromSnapshot(snapshot.content);
            repaired = true;
          }
        } finally {
          if (generation === liveSnapshotRepairGeneration) {
            liveSnapshotRepairInFlight = false;
            if (
              !repaired &&
              sawLiveReplacementChar &&
              liveSnapshotRepairAttempts < LIVE_SNAPSHOT_REPAIR_MAX_ATTEMPTS
            ) {
              scheduleLiveSnapshotRepair();
            }
          }
        }
      })();
    }, LIVE_SNAPSHOT_REPAIR_DELAY_MS);
  }

  function pushVisibleText(text: string): void {
    if (!text) return;
    let visible = stripVerifierMetaText(text);
    if (!visible) return;
    if (visible.includes(REPLACEMENT_CHAR)) {
      sawLiveReplacementChar = true;
      scheduleLiveSnapshotRepair();
      visible = visible.replace(REPLACEMENT_CHAR_RE, "");
    }
    if (visible) visibleText.push(visible);
  }

  function emitActivities(): void {
    if (onToolActivity) onToolActivity([...activities.values()]);
  }
  function emitSubagents(): void {
    if (onSubagentActivity) onSubagentActivity([...subagents.values()]);
  }
  function noteTerminalAgentError(message: unknown): void {
    if (typeof message !== "string") return;
    const trimmed = message.trim();
    if (!trimmed) return;
    if (isSilentVerifierMetaMessage(trimmed)) {
      terminalAgentError = null;
      terminalAgentErrorIsSilent = true;
      return;
    }
    terminalAgentError = userVisibleTerminalAgentError(trimmed);
    terminalAgentErrorIsSilent = false;
  }
  function noteStreamedProviderError(text: string): boolean {
    const message = overloadedProviderMessage(text.trim());
    if (!message) return false;
    terminalAgentError = message;
    markAllDone();
    return true;
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
  function safeActivityDetail(value: string | undefined, maxLength = 160): string | undefined {
    if (!value) return undefined;
    const trimmed = redactPreview(value.trim());
    if (!trimmed) return undefined;
    return trimmed.length > maxLength ? `${trimmed.slice(0, maxLength - 3)}...` : trimmed;
  }
  function formatActivityElapsed(ms: number): string {
    const seconds = Math.max(1, Math.round(ms / 1000));
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    return rest === 0 ? `${minutes}m` : `${minutes}m ${rest}s`;
  }
  function heartbeatStageLabel(baseId: string): string {
    const existingHeartbeatCount = Array.from(activities.keys())
      .filter((id) => id.startsWith(`${baseId}:heartbeat:`))
      .length;
    return publicModelWaitStages[existingHeartbeatCount % publicModelWaitStages.length] ?? "Still working";
  }
  function safeSubagentRole(value: unknown, fallback = "subagent"): string {
    if (typeof value !== "string") return fallback;
    const trimmed = value.trim();
    return trimmed ? trimmed.slice(0, 64) : fallback;
  }
  function isLowSignalSubagentDetail(value: string | undefined): boolean {
    if (!value) return false;
    return /^iteration\s+\d+$/i.test(value.trim());
  }
  function noteSubagent(
    taskId: unknown,
    updates: {
      role?: unknown;
      status?: SubagentActivityStatus;
      detail?: unknown;
    },
  ): void {
    if (typeof taskId !== "string" || !taskId) return;
    const now = Date.now();
    const existing = subagents.get(taskId);
    const incomingDetail = safeActivityDetail(
      typeof updates.detail === "string" ? updates.detail : undefined,
    );
    const detail = incomingDetail && isLowSignalSubagentDetail(incomingDetail) && existing?.detail
      ? existing.detail
      : incomingDetail;
    const next: SubagentActivity = {
      taskId,
      role: safeSubagentRole(updates.role, existing?.role ?? "subagent"),
      status: updates.status ?? existing?.status ?? "running",
      startedAt: existing?.startedAt ?? now,
      updatedAt: now,
      ...(detail !== undefined
        ? { detail }
        : existing?.detail !== undefined
          ? { detail: existing.detail }
          : {}),
    };
    subagents.set(taskId, next);
    emitSubagents();
  }
  function statusFromBackgroundTask(value: unknown): SubagentActivityStatus {
    switch (value) {
      case "completed":
        return "done";
      case "failed":
        return "error";
      case "aborted":
        return "cancelled";
      case "running":
      default:
        return "running";
    }
  }
  function statusFromSpawnResult(value: unknown): SubagentActivityStatus {
    switch (value) {
      case "ok":
        return "done";
      case "aborted":
        return "cancelled";
      case "error":
      default:
        return "error";
    }
  }
  function noteToolStart(id: string, label: string, inputPreview?: string): void {
    // Filter noise: META-tag artifacts like "intent:random", "domain:x" that
    // leak through chat-proxy's skills tracker look like pseudo-tools in the
    // activity list. Skip anything matching `<word>:<word>` that isn't a
    // real tool id (tools use `toolu_xxx` / `ag-N` id shape).
    if (/^[a-z_]+:[a-z_]+$/i.test(label)) return;
    if (!id.startsWith("llm:") && modelProgressId) {
      const modelActivity = activities.get(modelProgressId);
      if (modelActivity?.status === "running") {
        modelActivity.status = "done";
      }
      modelProgressId = null;
      modelProgressBaseId = null;
    }
    if (activities.has(id)) {
      const existing = activities.get(id);
      const preview = safeActivityDetail(inputPreview, 400);
      if (existing && preview) {
        existing.inputPreview = preview;
        emitActivities();
      }
      return;
    }
    const preview = safeActivityDetail(inputPreview, 400);
    activities.set(id, {
      id,
      label: prettifyToolName(label),
      status: "running",
      startedAt: Date.now(),
      ...(preview ? { inputPreview: preview } : {}),
    });
    emitActivities();
  }
  function noteModelProgress(ev: Record<string, unknown>): void {
    const turnId = typeof ev.turnId === "string" && ev.turnId ? ev.turnId : "turn";
    const iter = typeof ev.iter === "number" && Number.isFinite(ev.iter)
      ? Math.max(0, Math.floor(ev.iter))
      : 0;
    const id = `llm:${turnId}:${iter}`;
    modelProgressBaseId = id;
    modelProgressId = id;
    const label = typeof ev.label === "string" && ev.label.trim()
      ? ev.label.trim()
      : "Thinking through next step";
    const detail = typeof ev.detail === "string" ? ev.detail : undefined;
    const stage = typeof ev.stage === "string" ? ev.stage : "waiting";
    const elapsedMs = typeof ev.elapsedMs === "number" && Number.isFinite(ev.elapsedMs)
      ? Math.max(0, Math.floor(ev.elapsedMs))
      : undefined;
    const preview = safeActivityDetail(
      JSON.stringify({
        stage,
        label,
        ...(detail ? { detail } : {}),
        ...(elapsedMs !== undefined ? { elapsedMs } : {}),
      }),
      400,
    );
    const existing = activities.get(id);
    if (existing) {
      existing.status = "running";
      if (preview) existing.inputPreview = preview;
    } else {
      activities.set(id, {
        id,
        label: "ModelProgress",
        status: "running",
        startedAt: Date.now(),
        ...(preview ? { inputPreview: preview } : {}),
      });
    }
    emitActivities();
  }
  function noteHeartbeatActivity(elapsedMs: number): void {
    const elapsed = formatActivityElapsed(elapsedMs);
    const activityList = Array.from(activities.values());
    const runningRealActivities = activityList.filter(
      (activity) => activity.status === "running" && !activity.id.startsWith("llm:"),
    );
    if (runningRealActivities.length > 0) {
      const heartbeatSeconds = Math.max(1, Math.round(elapsedMs / 1000));
      const outputPreview = safeActivityDetail(`Still running (${elapsed} elapsed)`);
      for (const activity of runningRealActivities) {
        activity.outputPreview = outputPreview;
        const heartbeatId = `${activity.id}:heartbeat:${heartbeatSeconds}`;
        const heartbeatLabel = publicActivityHeartbeatLabel(activity.label, userRequestText);
        const target = activityProgressTarget(activity);
        const inputPreview = safeActivityDetail(
          JSON.stringify({
            stage: "heartbeat",
            label: heartbeatLabel,
            ...(target ? { target } : {}),
            detail: activity.label,
            elapsedMs,
          }),
          400,
        );
        const existing = activities.get(heartbeatId);
        if (existing) {
          existing.status = "done";
          if (inputPreview) existing.inputPreview = inputPreview;
          if (outputPreview) existing.outputPreview = outputPreview;
        } else {
          activities.set(heartbeatId, {
            id: heartbeatId,
            label: "ActivityProgress",
            status: "done",
            startedAt: Date.now(),
            ...(inputPreview ? { inputPreview } : {}),
            ...(outputPreview ? { outputPreview } : {}),
          });
        }
      }
      emitActivities();
      return;
    }
    if (modelProgressId) {
      const activity = activities.get(modelProgressId);
      if (activity) {
        activity.outputPreview = safeActivityDetail(`Still thinking (${elapsed} elapsed)`);
        if (activity.status === "running") {
          activity.status = "done";
        }
        const baseId = modelProgressBaseId ?? modelProgressId;
        const heartbeatSeconds = Math.max(1, Math.round(elapsedMs / 1000));
        const heartbeatId = `${baseId}:heartbeat:${heartbeatSeconds}`;
        const heartbeatLabel = heartbeatStageLabel(baseId);
        const inputPreview = safeActivityDetail(
          JSON.stringify({
            stage: "heartbeat",
            label: heartbeatLabel,
            detail: publicModelHeartbeatDetail,
            elapsedMs,
          }),
          400,
        );
        const outputPreview = safeActivityDetail(`Still thinking (${elapsed} elapsed)`);
        const existing = activities.get(heartbeatId);
        if (existing) {
          existing.status = "running";
          if (inputPreview) existing.inputPreview = inputPreview;
          if (outputPreview) existing.outputPreview = outputPreview;
        } else {
          activities.set(heartbeatId, {
            id: heartbeatId,
            label: "ModelProgress",
            status: "running",
            startedAt: Date.now(),
            ...(inputPreview ? { inputPreview } : {}),
            ...(outputPreview ? { outputPreview } : {}),
          });
        }
        modelProgressId = heartbeatId;
        emitActivities();
        return;
      }
    }
    noteModelProgress({
      type: "llm_progress",
      turnId: "heartbeat",
      iter: 0,
      stage: "waiting",
      label: publicModelWaitStages[0] ?? "Still working",
      detail: publicModelHeartbeatDetail,
      elapsedMs,
    });
  }
  function noteToolInputDelta(id: string, inputDelta: string | undefined): void {
    if (!inputDelta) return;
    const activity = activities.get(id);
    if (!activity) return;
    const preview = safeActivityDetail(`${activity.inputPreview ?? ""}${inputDelta}`, 400);
    if (!preview || preview === activity.inputPreview) return;
    activity.inputPreview = preview;
    emitActivities();
  }
  function noteSkillFromText(text: string): void {
    SKILL_INVOCATION_RE.lastIndex = 0;
    let m;
    while ((m = SKILL_INVOCATION_RE.exec(text)) !== null) {
      const skill = m[1];
      if (seenSkills.has(skill)) continue;
      seenSkills.add(skill);
      noteToolStart(`skill-${skill}`, skill);
    }
  }
  function markAllDone(): void {
    let changed = false;
    for (const a of activities.values()) {
      if (a.status === "running") { a.status = "done"; changed = true; }
    }
    if (changed) emitActivities();
  }
  function noteToolEnd(
    id: string,
    status: string,
    durationMs?: number,
    outputPreview?: string,
  ): void {
    const markDocumentDraftDone = (): void => {
      if (latestDocumentDraft?.id !== id) return;
      latestDocumentDraft = {
        ...latestDocumentDraft,
        status: "done",
        updatedAt: Date.now(),
      };
      onDocumentDraft?.(latestDocumentDraft);
    };
    const a = activities.get(id);
    if (!a || (a.status !== "running")) {
      markDocumentDraftDone();
      return;
    }
    // Map server status values to UI status.
    a.status =
      status === "ok"
        ? "done"
        : status === "permission_denied"
          ? "denied"
            : status === "error" || status === "unknown_tool" || status === "aborted"
            ? "error"
            : "done";
    delete a.outputPreview;
    const preview = safeActivityDetail(outputPreview, 400);
    if (preview) a.outputPreview = preview;
    if (typeof durationMs === "number") a.durationMs = durationMs;
    emitActivities();
    markDocumentDraftDone();
  }
  function notePatchPreview(id: string, preview: PatchPreview): void {
    if (!activities.has(id)) {
      noteToolStart(id, "PatchApply");
    }
    const activity = activities.get(id);
    if (!activity) return;
    activity.patchPreview = preview;
    emitActivities();
  }
  function noteToolRetry(
    id: string,
    label: string | undefined,
    retryNo: number,
    reason?: string,
  ): void {
    const preview = safeActivityDetail(`Retry ${retryNo}: ${reason || "transient failure"}`);
    const existing = activities.get(id);
    if (existing) {
      existing.status = "running";
      if (preview) existing.outputPreview = preview;
      emitActivities();
      return;
    }
    activities.set(id, {
      id,
      label: `Retrying ${prettifyToolName(label || "tool")}`,
      status: "running",
      startedAt: Date.now(),
      ...(preview ? { outputPreview: preview } : {}),
    });
    emitActivities();
  }
  function noteChildProgress(taskId: unknown, label: string, detail?: unknown): void {
    const id = typeof taskId === "string" && taskId ? `child:${taskId}` : `child:${activities.size}`;
    if (!activities.has(id)) {
      noteToolStart(id, label);
    }
    const activity = activities.get(id);
    if (!activity) return;
    activity.label = label;
    const preview = safeActivityDetail(typeof detail === "string" ? detail : undefined);
    if (preview) activity.outputPreview = preview;
    emitActivities();
  }
  function noteChildDone(taskId: unknown, status: "done" | "error", detail?: unknown): void {
    const id = typeof taskId === "string" && taskId ? `child:${taskId}` : "";
    if (!id) return;
    if (!activities.has(id)) {
      noteToolStart(id, "Subagent");
    }
    const activity = activities.get(id);
    if (!activity) return;
    activity.status = status;
    const preview = safeActivityDetail(typeof detail === "string" ? detail : undefined);
    if (preview) activity.outputPreview = preview;
    emitActivities();
  }

  /**
   * Dispatch a core-agent `event: agent` frame. Phase 1a handles the
   * turn-lifecycle + basic tool-call shapes; later phases extend.
   * See `infra/docker/clawy-core-agent/src/transport/SseWriter.ts` for
   * the emitter side.
   */
  function handleAgentEvent(ev: { type?: string } & Record<string, unknown>): void {
    if (!ev || typeof ev.type !== "string") return;
    switch (ev.type) {
      case "llm_progress": {
        noteModelProgress(ev);
        break;
      }
      case "tool_start": {
        const id = typeof ev.id === "string" ? ev.id : `ag-${activities.size}`;
        const name = typeof ev.name === "string" ? ev.name : "tool";
        const inputPreview = typeof ev.input_preview === "string" ? ev.input_preview : undefined;
        noteToolStart(id, name, inputPreview);
        break;
      }
      case "tool_progress": {
        const id = typeof ev.id === "string" ? ev.id : "";
        const label = typeof ev.label === "string" ? ev.label : "";
        // Same META-tag noise filter as noteToolStart.
        if (/^[a-z_]+:[a-z_]+$/i.test(label)) break;
        const a = id ? activities.get(id) : undefined;
        if (a && label) {
          a.label = prettifyToolName(label);
          emitActivities();
        }
        break;
      }
      case "tool_end": {
        const id = typeof ev.id === "string" ? ev.id : "";
        const status = typeof ev.status === "string" ? ev.status : "done";
        const durationMs = typeof ev.durationMs === "number" ? ev.durationMs : undefined;
        const outputPreview = typeof ev.output_preview === "string" ? ev.output_preview : undefined;
        if (id) noteToolEnd(id, status, durationMs, outputPreview);
        break;
      }
      case "patch_preview": {
        const toolUseId =
          typeof ev.toolUseId === "string"
            ? ev.toolUseId
            : typeof ev.id === "string"
              ? ev.id
              : "";
        const preview = parsePatchPreview(ev);
        if (toolUseId && preview) notePatchPreview(toolUseId, preview);
        break;
      }
      case "browser_frame": {
        if (!onBrowserFrame) break;
        const action = typeof ev.action === "string" ? ev.action : "browser";
        const imageBase64 = typeof ev.imageBase64 === "string" ? ev.imageBase64 : "";
        if (!imageBase64) break;
        const contentType =
          ev.contentType === "image/jpeg" || ev.contentType === "image/png"
            ? ev.contentType
            : "image/png";
        const capturedAt = typeof ev.capturedAt === "number" ? ev.capturedAt : Date.now();
        onBrowserFrame({
          action,
          imageBase64,
          contentType,
          capturedAt,
          ...(typeof ev.url === "string" && ev.url ? { url: ev.url } : {}),
        });
        break;
      }
      case "document_draft": {
        if (!onDocumentDraft) break;
        const draft = parseDocumentDraft(ev);
        if (!draft) break;
        latestDocumentDraft = draft;
        onDocumentDraft(draft);
        break;
      }
      case "source_inspected": {
        const source = parseInspectedSource(ev.source);
        if (source) onSourceInspected?.(source);
        break;
      }
      case "rule_check": {
        const status = parseCitationGateStatus(ev);
        if (status) onCitationGate?.(status);
        break;
      }
      case "runtime_trace": {
        const trace = parseRuntimeTrace(ev);
        if (trace) onRuntimeTrace?.(trace);
        break;
      }
      case "turn_end":
        if (ev.status === "committed") {
          const usage = parseResponseUsage(ev.usage);
          if (usage) onUsage?.(usage);
        }
        if (ev.status === "aborted") {
          noteTerminalAgentError(ev.reason);
          onTurnPhase?.("aborted");
        } else if (ev.status === "committed") {
          onTurnPhase?.("committed");
        }
        markAllDone();
        break;
      case "context_end":
        markAllDone();
        break;
      case "turn_phase": {
        const phase = ev.phase;
        if (
          phase === "pending" ||
          phase === "planning" ||
          phase === "executing" ||
          phase === "verifying" ||
          phase === "committing" ||
          phase === "compacting" ||
          phase === "committed" ||
          phase === "aborted"
        ) {
          onTurnPhase?.(phase);
        }
        break;
      }
      case "heartbeat": {
        const elapsedMs = typeof ev.elapsedMs === "number" ? ev.elapsedMs : null;
        if (elapsedMs !== null) {
          onHeartbeat?.(elapsedMs);
          noteHeartbeatActivity(elapsedMs);
        }
        break;
      }
      case "retry": {
        const retryNo = typeof ev.retryNo === "number" ? ev.retryNo : 1;
        const toolUseId =
          typeof ev.toolUseId === "string"
            ? ev.toolUseId
            : typeof ev.id === "string"
              ? ev.id
              : "";
        const toolName =
          typeof ev.toolName === "string"
            ? ev.toolName
            : typeof ev.name === "string"
              ? ev.name
              : undefined;
        const reason = typeof ev.reason === "string" ? ev.reason : undefined;
        if (toolUseId) noteToolRetry(toolUseId, toolName, retryNo, reason);
        break;
      }
      case "injection_queued": {
        const queuedCount = typeof ev.queuedCount === "number" ? ev.queuedCount : null;
        if (queuedCount !== null) onPendingInjectionCount?.(queuedCount);
        break;
      }
      case "injection_drained":
        onPendingInjectionCount?.(0);
        break;
      case "turn_interrupted":
        onTurnPhase?.("aborted");
        break;
      case "control_event": {
        const trace = parseRuntimeTrace(recordFromUnknown(ev.event) ?? {});
        if (trace) onRuntimeTrace?.(trace);
        const event = parseControlEvent(ev.event);
        if (event) onControlEvent?.(event);
        break;
      }
      case "control_replay_complete": {
        const lastSeq = typeof ev.lastSeq === "number" ? ev.lastSeq : 0;
        onControlReplayComplete?.(lastSeq);
        break;
      }
      case "ask_user": {
        const request = legacyAskUserToControlRequest(ev, sessionKey, channelName);
        if (request) onControlEvent?.({ type: "control_request_created", request });
        break;
      }
      case "plan_ready": {
        const request = planReadyToControlRequest(ev, sessionKey, channelName);
        if (request) onControlEvent?.({ type: "control_request_created", request });
        break;
      }
      case "spawn_started":
        noteSubagent(ev.taskId, {
          role: ev.persona,
          status: "running",
          detail: ev.detail,
        });
        break;
      case "background_task":
        noteSubagent(ev.taskId, {
          role: ev.persona,
          status: statusFromBackgroundTask(ev.status),
          detail: ev.detail,
        });
        break;
      case "spawn_result": {
        const status = statusFromSpawnResult(ev.status);
        const detail = status === "done" ? undefined : ev.errorMessage;
        noteSubagent(ev.taskId, {
          status,
          detail,
        });
        noteChildDone(ev.taskId, status === "done" ? "done" : "error", detail);
        break;
      }
      case "child_started":
        noteSubagent(ev.taskId, { status: "running", detail: ev.detail });
        noteChildProgress(ev.taskId, "Subagent running");
        break;
      case "child_progress":
        noteSubagent(ev.taskId, { status: "running", detail: ev.detail });
        noteChildProgress(ev.taskId, "Subagent running", ev.detail);
        break;
      case "child_tool_request":
        noteSubagent(ev.taskId, { status: "waiting", detail: ev.toolName });
        noteChildProgress(ev.taskId, "Subagent waiting for tool approval", ev.toolName);
        break;
      case "child_permission_decision":
        noteSubagent(ev.taskId, { status: "running", detail: ev.decision });
        noteChildProgress(ev.taskId, "Subagent tool decision", ev.decision);
        break;
      case "child_completed":
        noteSubagent(ev.taskId, { status: "done" });
        noteChildDone(ev.taskId, "done");
        break;
      case "child_cancelled":
        noteSubagent(ev.taskId, { status: "cancelled", detail: ev.reason });
        noteChildDone(ev.taskId, "error", ev.reason);
        break;
      case "child_failed":
        noteSubagent(ev.taskId, { status: "error", detail: ev.errorMessage });
        noteChildDone(ev.taskId, "error", ev.errorMessage);
        break;
      case "error":
        noteTerminalAgentError(ev.message);
        markAllDone();
        break;
      case "task_board": {
        // Full-board snapshot (server always sends the complete board —
        // client overwrites rather than merges). See §7.1.
        if (!onTaskBoard) break;
        const rawTasks = Array.isArray(ev.tasks) ? ev.tasks : null;
        if (!rawTasks) break;
        const tasks: TaskBoardTask[] = [];
        for (const t of rawTasks) {
          if (!t || typeof t !== "object") continue;
          const rec = t as Record<string, unknown>;
          const id = typeof rec.id === "string" ? rec.id : null;
          const title = typeof rec.title === "string" ? rec.title : null;
          const status = rec.status;
          if (!id || !title) continue;
          if (
            status !== "pending" &&
            status !== "in_progress" &&
            status !== "completed" &&
            status !== "cancelled"
          ) continue;
          const description = typeof rec.description === "string" ? rec.description : "";
          const parallelGroup = typeof rec.parallelGroup === "string" ? rec.parallelGroup : undefined;
          const dependsOn = Array.isArray(rec.dependsOn)
            ? rec.dependsOn.filter((d): d is string => typeof d === "string")
            : undefined;
          tasks.push({
            id,
            title,
            description,
            status,
            ...(parallelGroup ? { parallelGroup } : {}),
            ...(dependsOn && dependsOn.length > 0 ? { dependsOn } : {}),
          });
        }
        onTaskBoard({ tasks, receivedAt: Date.now() });
        break;
      }
      case "mission_created":
      case "mission_event":
      case "mission_updated":
      case "mission_run":
      case "goal_status":
      case "goal_continue":
      case "cron_run":
        onMissionEvent?.(ev);
        break;
      // 2026-04-20: `thinking_delta` and `text_delta` were previously
      // no-op ("reserved for later phases") — core-agent Opus 4.7 emits
      // thinking exclusively via this `event: agent` channel (not the
      // legacy OpenAI-compat path), so the ThinkingBlock stayed frozen
      // at the FALLBACK_STEPS ("Routing request...") even though the
      // bot was actively streaming reasoning. Wire both now.
      case "thinking_delta": {
        const delta = typeof ev.delta === "string" ? ev.delta : "";
        if (delta) onThinkingDelta?.(delta);
        break;
      }
      case "response_clear":
        gotContent = false;
        legacyContentEmitted = false;
        latestDocumentDraft = null;
        clearVisibleText();
        onDocumentDraft?.(null);
        break;
      case "text_delta": {
        const delta = typeof ev.delta === "string" ? ev.delta : "";
        if (delta) {
          if (noteStreamedProviderError(delta)) break;
          if (legacyContentEmitted && !clearedLegacyForAgentChannel) {
            gotContent = false;
            legacyContentEmitted = false;
            clearedLegacyForAgentChannel = true;
            visibleText.clear();
            emittedVisibleText = "";
            resetLiveSnapshotRepairState();
            if (onContentReplace) {
              const visible = stripVerifierMetaText(delta);
              if (visible) {
                emittedVisibleText = visible;
                onContentReplace(visible);
                gotContent = true;
                noteSkillFromText(delta);
                break;
              }
            }
            onResponseClear?.();
          }
          gotContent = true;
          pushVisibleText(delta);
          noteSkillFromText(delta);
        }
        break;
      }
      // turn_start / other context + plan + subagent events: reserved for
      // later phases; no-op.
      default:
        break;
    }
  }

  const token = await getToken();
  const resetCount = getResetCounter(botId, channelName);
  const sessionKey = resetCount > 0
    ? `agent:main:app:${channelName}:${resetCount}`
    : `agent:main:app:${channelName}`;
  const requestBody = {
    model,
    messages: messages.map((m) => ({ role: m.role, content: m.content })),
    stream: true,
    ...(goalMode ? { goalMode: true } : {}),
    ...(replyTo ? { replyTo } : {}),
  };
  const baseHeaders = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "x-openclaw-session-key": sessionKey,
  };

  // Two-phase timeout (§11.12 web watchdog, 2026-04-20):
  //   connect — fires if HTTP headers don't arrive in STREAM_CONNECT_TIMEOUT_MS
  //   idle    — armed after fetch() resolves; reset on every chunk read.
  //             chat-proxy injects `: heartbeat <ts>\n\n` every 15s, so
  //             this only fires when the stream is genuinely dead.
  // A local AbortController merges timeout-driven aborts with the
  // user-provided `signal` (stop button). Both phases share it.
  const timeoutController = new AbortController();
  let phase: "connect" | "idle" = "connect";
  let timeoutId: ReturnType<typeof setTimeout> = setTimeout(
    () => timeoutController.abort(),
    STREAM_CONNECT_TIMEOUT_MS,
  );
  const resetIdle = (): void => {
    if (phase !== "idle") return;
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => timeoutController.abort(), STREAM_IDLE_TIMEOUT_MS);
  };
  if (signal) {
    if (signal.aborted) {
      clearTimeout(timeoutId);
      timeoutController.abort();
    } else {
      signal.addEventListener(
        "abort",
        () => {
          clearTimeout(timeoutId);
          timeoutController.abort();
        },
        { once: true },
      );
    }
  }

  try {
    const res = await chatProxyFetch(`/v1/chat/${botId}/completions`, {
      method: "POST",
      headers: {
        ...baseHeaders,
        Accept: "text/event-stream",
        "Cache-Control": "no-cache",
      },
      body: JSON.stringify(requestBody),
      signal: timeoutController.signal,
    });

    // Switch to idle phase — initial headers arrived, now rolling window.
    clearTimeout(timeoutId);
    phase = "idle";
    timeoutId = setTimeout(() => timeoutController.abort(), STREAM_IDLE_TIMEOUT_MS);

    if (!res.ok) {
      if (res.status === 401) throw new AuthExpiredError();
      const text = await res.text().catch(() => "");
      throw new Error(text || `HTTP ${res.status}`);
    }

    const reader = res.body?.getReader();
    if (!reader) {
      const text = await res.text();
      if (text) {
        pushVisibleText(text);
        await visibleText.flush();
      }
      clearTimeout(timeoutId);
      onDone();
      return;
    }

    const decoder = new TextDecoder();
    let buffer = "";
    // SSE custom event channel — resets to "message" between events
    // (blank line delimiter per spec). Used by core-agent runtime which
    // streams structured `event: agent\ndata: {...}` frames alongside
    // the OpenAI-compatible `data: {...}` frames.
    let currentEvent = "message";
    // Defensive dual-render gate: once we see any `event: agent` frame
    // we know the upstream is core-agent (not OpenClaw), whose text
    // now flows exclusively on the agent channel. Suppress the legacy
    // `choices[0].delta.content` / Anthropic `text_delta` text paths
    // so a stale server that still dual-emits doesn't duplicate every
    // character into the UI. Thinking / tool_calls / finish_reason
    // remain handled on the legacy path because Fireworks routing
    // (Kimi / MiniMax) carries reasoning_content + tool_calls there.
    let sawAgentChannel = false;

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      // §11.12 — reset idle timer on every chunk (including `: heartbeat`
      // SSE comments from chat-proxy every 15s). Only fires on real
      // stream death, not on long tool calls.
      resetIdle();

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) {
          currentEvent = "message"; // blank line = event boundary
          continue;
        }
        if (trimmed.startsWith(":")) continue; // SSE comment
        if (trimmed.startsWith("event:")) {
          currentEvent = trimmed.slice(6).trim() || "message";
          continue;
        }
        if (!trimmed.startsWith("data: ")) continue;
        const payload = trimmed.slice(6);

        // core-agent structured event channel — see §7.9 of design doc
        if (currentEvent === "agent") {
          sawAgentChannel = true;
          try {
            const ev = JSON.parse(payload);
            handleAgentEvent(ev);
          } catch { /* ignore malformed */ }
          continue;
        }
        if (payload === "[DONE]") {
          clearTimeout(timeoutId);
          if (terminalAgentErrorIsSilent) {
            if (gotContent) await visibleText.flush();
            markAllDone();
            onDone();
            return;
          }
          if (terminalAgentError) {
            if (gotContent) await visibleText.flush();
            onError(new Error(terminalAgentError));
            return;
          }
          if (!gotContent) {
            // Stream returned no content — fallback to non-streaming
            await sendMessageNonStreaming(botId, channelName, messages, options);
            return;
          }
          await visibleText.flush();
          markAllDone();
          onDone();
          return;
        }
        try {
          const event = JSON.parse(payload);
          // OpenAI format
          const delta = event.choices?.[0]?.delta;
          if (delta?.content && !sawAgentChannel) {
            if (noteStreamedProviderError(delta.content)) continue;
            gotContent = true;
            legacyContentEmitted = true;
            pushVisibleText(delta.content);
            noteSkillFromText(delta.content);
          }
          // OpenAI reasoning_content (thinking/CoT)
          if (delta?.reasoning_content && onThinkingDelta) {
            onThinkingDelta(delta.reasoning_content);
          }
          // OpenAI tool_calls (real-time tool invocations)
          if (Array.isArray(delta?.tool_calls)) {
            for (const tc of delta.tool_calls) {
              const index = typeof tc.index === "number" ? tc.index : undefined;
              const existingId = index !== undefined ? toolCallIdsByIndex.get(index) : undefined;
              const id = tc.id ?? existingId ?? `tc-${index ?? activities.size}`;
              if (index !== undefined) toolCallIdsByIndex.set(index, id);
              const name = tc.function?.name;
              const inputDelta =
                typeof tc.function?.arguments === "string" ? tc.function.arguments : undefined;
              if (name) noteToolStart(id, name);
              noteToolInputDelta(id, inputDelta);
            }
          }
          // OpenAI finish_reason
          if (event.choices?.[0]?.finish_reason === "tool_calls") markAllDone();
          // Anthropic format
          if (event.type === "content_block_delta" && event.delta) {
            if (event.delta.text && !sawAgentChannel) {
              if (noteStreamedProviderError(event.delta.text)) continue;
              gotContent = true;
              legacyContentEmitted = true;
              pushVisibleText(event.delta.text);
              noteSkillFromText(event.delta.text);
            }
            if (event.delta.thinking) onThinkingDelta?.(event.delta.thinking);
          }
          // Anthropic tool_use start
          if (event.type === "content_block_start" && event.content_block?.type === "tool_use") {
            const id = event.content_block.id ?? `cb-${event.index ?? activities.size}`;
            const name = event.content_block.name;
            const inputPreview =
              event.content_block.input !== undefined
                ? JSON.stringify(event.content_block.input)
                : undefined;
            if (name) noteToolStart(id, name, inputPreview);
          }
          if (event.type === "message_stop") markAllDone();
        } catch {
          // Skip unparseable lines
        }
      }
    }

    clearTimeout(timeoutId);
    if (signal?.aborted) return;
    if (terminalAgentErrorIsSilent) {
      if (gotContent) await visibleText.flush();
      markAllDone();
      onDone();
      return;
    }
    if (terminalAgentError) {
      onError(new Error(terminalAgentError));
      return;
    }
    if (!gotContent) {
      await sendMessageNonStreaming(botId, channelName, messages, options);
      return;
    }
    await visibleText.flush();
    onDone();
  } catch (err) {
    clearTimeout(timeoutId);
    if (signal?.aborted) return;
    // §11.12 — distinguish watchdog-driven aborts from other errors so
    // the UI can show "gateway timeout" rather than a generic network
    // error. The user-signal path returned above; if we got here via
    // `timeoutController.abort()`, the caught error is AbortError.
    if ((err as Error).name === "AbortError") {
      onError(new Error("gateway timeout"));
      return;
    }
    if (gotContent) await visibleText.flush();
    const rawMessage = err instanceof Error ? err.message : String(err);
    onError(new Error(userVisibleTerminalAgentError(rawMessage)));
  } finally {
    cancelLiveSnapshotRepair();
  }
}

/** Non-streaming fallback when SSE returns empty content */
async function sendMessageNonStreaming(
  botId: string,
  channelName: string,
  messages: Pick<ChatMessage, "role" | "content">[],
  options: SendMessageOptions,
): Promise<void> {
  const { model = "auto", goalMode, replyTo, onDelta, onDone, onError, signal } = options;
  const visibleText = createStreamingTextSmoother(onDelta);

  try {
    const token = await getToken();
    const rc = getResetCounter(botId, channelName);
    const sessionKey = rc > 0
      ? `agent:main:app:${channelName}:${rc}`
      : `agent:main:app:${channelName}`;
    const res = await chatProxyFetch(`/v1/chat/${botId}/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "x-openclaw-session-key": sessionKey,
      },
      body: JSON.stringify({
        model,
        messages: messages.map((m) => ({ role: m.role, content: m.content })),
        stream: false,
        ...(goalMode ? { goalMode: true } : {}),
        ...(replyTo ? { replyTo } : {}),
      }),
      signal,
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(text || `HTTP ${res.status}`);
    }

    // Chat-proxy always returns SSE format regardless of stream param.
    // Parse SSE lines to extract content, falling back to JSON if needed.
    const text = await res.text();
    let content = "";

    if (text.startsWith("data: ")) {
      // SSE format — extract content from data lines
      for (const line of text.split("\n")) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data: ") || trimmed === "data: [DONE]") continue;
        try {
          const event = JSON.parse(trimmed.slice(6));
          const delta = event.choices?.[0]?.delta?.content;
          const msg = event.choices?.[0]?.message?.content;
          if (delta) content += delta;
          else if (msg) content += msg;
        } catch { /* skip unparseable lines */ }
      }
    } else {
      // Plain JSON response
      try {
        const data = JSON.parse(text);
        content = data.choices?.[0]?.message?.content ?? "";
      } catch { /* ignore parse errors */ }
    }

    if (content) {
      const providerError = overloadedProviderMessage(content);
      if (providerError) {
        onError(new Error(providerError));
        return;
      }
      visibleText.push(content);
      await visibleText.flush();
    }
    if (!signal?.aborted) onDone();
  } catch (err) {
    if (signal?.aborted) return;
    const rawMessage = err instanceof Error ? err.message : String(err);
    onError(new Error(userVisibleTerminalAgentError(rawMessage)));
  }
}
