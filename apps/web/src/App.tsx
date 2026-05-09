import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
} from "react";
import { ChatSidebar } from "@/components/chat/chat-sidebar";
import { ChatMessages, type ChatMessagesHandle } from "@/components/chat/chat-messages";
import { ChatInput, type ChatInputHandle } from "@/components/chat/chat-input";
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
import { localizeChannel } from "@/lib/chat/channel-i18n";
import type {
  BrowserFrame,
  Channel,
  ChannelState,
  ChatMessage,
  ControlEvent,
  ControlRequestRecord,
  KbDocReference,
  QueuedMessage,
  ReplyTo,
  SubagentActivity,
  TaskBoardTask,
  ToolActivity,
} from "@/lib/chat/types";
import type { PendingKbUpload } from "@/lib/chat/kb-uploads";
import type { KbCollectionWithDocs, KbDocEntry } from "@/hooks/use-kb-docs";

const BOT_ID = "local";
const BOT_NAME = "Magi_Local";
const DEFAULT_CHANNEL = "general";
const DEFAULT_MODEL = "magi_smart_routing";
const DEFAULT_ROUTER = "big_dic";
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

type JsonRecord = Record<string, unknown>;
type RuntimePhase = NonNullable<ChannelState["turnPhase"]>;

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

function getStored(key: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return window.localStorage.getItem(key) || fallback;
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

export function App() {
  const store = useChatStore();
  const chatMessagesRef = useRef<ChatMessagesHandle>(null);
  const chatInputRef = useRef<ChatInputHandle>(null);
  const sawAgentEventRef = useRef(false);
  const [agentUrl, setAgentUrl] = useState(() => getStored(storage.agentUrl, window.location.origin));
  const [token, setToken] = useState(() => getStored(storage.token, ""));
  const [editing, setEditing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [customCategories, setCustomCategories] = useState<string[]>([]);
  const [selectedKbDocs, setSelectedKbDocs] = useState<KbDocReference[]>([]);
  const [kbCollections, setKbCollections] = useState<KbCollectionWithDocs[]>([]);
  const [kbLoading, setKbLoading] = useState(true);
  const [kbRefreshing, setKbRefreshing] = useState(false);
  const [workspaceFiles, setWorkspaceFiles] = useState<WorkspaceFileEntry[]>([]);
  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [workspaceRefreshing, setWorkspaceRefreshing] = useState(false);
  const [uploadStates, setUploadStates] = useState<Record<string, PendingKbUpload>>({});
  const [replyingTo, setReplyingTo] = useState<ReplyTo | null>(null);
  const [streamingComposerMode, setStreamingComposerMode] = useState<StreamingComposerMode>("queue");
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
  const [modelSelection, setModelSelection] = useState(() =>
    getStored(storage.modelOverride, DEFAULT_MODEL),
  );
  const [routerType, setRouterType] = useState(DEFAULT_ROUTER);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const dragCounterRef = useRef(0);

  const normalizedBase = useMemo(() => normalizeAgentUrl(agentUrl), [agentUrl]);
  const activeChannel = store.activeChannel || DEFAULT_CHANNEL;
  const channelState = store.channelStates[activeChannel] ?? store.getChannelState(activeChannel);
  const queuedForChannel = store.queuedMessages[activeChannel] ?? [];
  const controlsForChannel = store.controlRequests[activeChannel] ?? [];
  const allKbDocs = useMemo(() => kbCollections.flatMap((collection) => collection.docs), [kbCollections]);

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

  const saveWorkspaceFile = useCallback(
    async (path: string, content: string) => {
      await putJson("/v1/app/workspace/file", { path, content });
      void refreshWorkspace();
    },
    [putJson, refreshWorkspace],
  );

  const refreshChannels = useCallback(() => {
    setRefreshing(true);
    store.setChannels(store.channels.length > 0 ? store.channels : [defaultChannel()], { botId: BOT_ID });
    void Promise.allSettled([refreshKnowledge(), refreshWorkspace()]).finally(() => {
      window.setTimeout(() => setRefreshing(false), 300);
    });
  }, [refreshKnowledge, refreshWorkspace, store]);

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
    void Promise.allSettled([refreshKnowledge(), refreshWorkspace()]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
          activeTools: [],
          browserFrame: null,
          subagents: [],
          taskBoard: null,
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
    ) => {
      const channel = useChatStore.getState().activeChannel || DEFAULT_CHANNEL;
      const messageText = buildMessageContentWithKbContext(text, kbDocs);
      if (!messageText.trim()) return;
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
    async (text: string, files?: File[]) => {
      const channel = useChatStore.getState().activeChannel || DEFAULT_CHANNEL;
      const activeReply = replyingTo;
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
        if (sendMode === "inject") {
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

      void performSend(text, activeReply, messageKbDocs, modelSelection);
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

  const handleCancel = useCallback(() => {
    const channel = useChatStore.getState().activeChannel || DEFAULT_CHANNEL;
    const controller = useChatStore.getState().abortControllers[channel];
    controller?.abort();
    void sendJson("/v1/chat/interrupt", {
      sessionKey: sessionKeyForChannel(channel),
      handoffRequested: (useChatStore.getState().queuedMessages[channel] ?? []).length > 0,
      source: "web",
    }).catch(() => {});
    store.cancelStream(channel, { preserveQueue: true, botId: BOT_ID });
  }, [sendJson, store]);

  const handleCancelQueue = useCallback(() => {
    const channel = useChatStore.getState().activeChannel || DEFAULT_CHANNEL;
    store.clearQueue(channel, { botId: BOT_ID });
  }, [store]);

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
    (name: string) => {
      const channelName = normalizeChannelName(name);
      const existing = useChatStore.getState().channels;
      if (existing.some((channel) => channel.name === channelName)) {
        store.setActiveChannel(channelName);
        return;
      }
      const channel: Channel = {
        id: `local-${channelName}`,
        name: channelName,
        display_name: name === channelName ? null : name,
        category: "General",
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

  const handleModelSelectionChange = useCallback((nextModel: string, nextRouter: string) => {
    setModelSelection(nextModel);
    setRouterType(nextRouter);
    window.localStorage.setItem(storage.modelOverride, nextModel);
  }, []);

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
        onChannelSelect={(name) => store.setActiveChannel(name)}
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
            onClick={handleReset}
            className="px-2.5 py-1 text-[11px] text-secondary/50 hover:text-foreground/70 rounded-lg hover:bg-black/[0.04] transition-all duration-200"
          >
            Reset
          </button>
          <a
            href="/dashboard"
            className="md:hidden p-1.5 text-secondary/60 hover:text-foreground rounded-xl hover:bg-black/[0.04] transition-all duration-200"
            aria-label="Dashboard"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" />
            </svg>
          </a>
        </div>

        <ChatMessages
          ref={chatMessagesRef}
          key={activeChannel}
          messages={store.messages[activeChannel] ?? []}
          serverMessages={store.serverMessages[activeChannel] ?? []}
          channelState={channelState}
          loading={false}
          botId={BOT_ID}
          selectionMode={store.selectionMode}
          selectedMessages={store.selectedMessages[activeChannel]}
          onToggleSelect={(msgId) => store.toggleMessageSelection(activeChannel, msgId)}
          onEnterSelectionMode={(msgId) => store.enterSelectionMode(activeChannel, msgId)}
          onSelectAll={() => store.selectAllMessages(activeChannel)}
          onDeselectAll={() => store.deselectAllMessages(activeChannel)}
          onExportSelected={() => {}}
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
          onReset={handleReset}
          streaming={channelState.streaming}
          onCancel={handleCancel}
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
          steeringDisabledReason="Selected knowledge will send after the current run."
          kbDocs={allKbDocs}
          onSelectKbDoc={handleToggleKbDoc}
          uploadStates={uploadStates}
          composerAccessory={composerAccessory}
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
