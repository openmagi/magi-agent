"use client";

import { create } from "zustand";
import type {
  ChatMessage,
  Channel,
  ChannelState,
  QueuedMessage,
  ControlEvent,
  ControlRequestRecord,
  SubagentActivity,
} from "./types";
import { INTERRUPTED_SUFFIX, MAX_QUEUED_MESSAGES } from "./queue-constants";
import { compareChatMessages } from "./message-order";

const DEFAULT_CHANNEL_STATE: ChannelState = {
  streaming: false,
  streamingText: "",
  thinkingText: "",
  error: null,
  hasTextContent: false,
  thinkingStartedAt: null,
  turnPhase: null,
  heartbeatElapsedMs: null,
  pendingInjectionCount: 0,
  activeTools: [],
  subagents: [],
  taskBoard: null,
  fileProcessing: false,
};

const RESET_LIVE_RUN_STATE: Partial<ChannelState> = {
  streamingText: "",
  thinkingText: "",
  error: null,
  hasTextContent: false,
  thinkingStartedAt: null,
  turnPhase: null,
  heartbeatElapsedMs: null,
  pendingInjectionCount: 0,
  activeTools: [],
  subagents: [],
  taskBoard: null,
  fileProcessing: false,
  reconnecting: false,
};

const MAX_LOCAL_MESSAGES = 200;
const TRANSIENT_CONNECTION_ERROR_RE = /^Connecting to bot\.\.\. \(\d+\/\d+\)$/;
const MESSAGES_CACHE_KEY = (botId: string) => `magi:messages:${botId}`;
const RESET_COUNTERS_KEY = (botId: string) => `magi:resetCounters:${botId}`;
const LAST_READ_KEY = (botId: string) => `magi:lastRead:${botId}`;

interface BotScope {
  botId?: string | null;
}

function matchesBotScope(currentBotId: string | null, scope?: BotScope): boolean {
  return !scope?.botId || currentBotId === scope.botId;
}

function mergeChannelState(
  current: ChannelState,
  partial: Partial<ChannelState>,
): ChannelState {
  const startsFreshRun = partial.streaming === true && current.streaming === false;
  const endsRun = partial.streaming === false;
  const reset = startsFreshRun
    ? RESET_LIVE_RUN_STATE
    : endsRun
      ? terminalLiveRunReset(current, partial)
      : {};
  const next = { ...current, ...reset, ...partial };
  if (shouldClearTransientConnectionError(current, partial)) {
    next.error = null;
  }
  if (shouldClearReconnecting(current, partial)) {
    next.reconnecting = false;
  }
  return next;
}

function activeBackgroundSubagents(channelState: ChannelState): SubagentActivity[] {
  return (channelState.subagents ?? []).filter(
    (subagent) => subagent.status === "running" || subagent.status === "waiting",
  );
}

function streamErrorFallback(language: ChannelState["responseLanguage"]): string {
  return language === "ko"
    ? "⚠️ 응답 생성 중 오류가 발생했습니다. 다시 시도해 주세요."
    : "⚠️ Something went wrong while generating the response. Please try again.";
}

function terminalLiveRunReset(
  current: ChannelState,
  partial: Partial<ChannelState>,
): Partial<ChannelState> {
  const reset = { ...RESET_LIVE_RUN_STATE };
  if (partial.subagents === undefined) {
    const detachedSubagents = activeBackgroundSubagents(current);
    if (detachedSubagents.length > 0) {
      reset.subagents = detachedSubagents;
    }
  }
  return reset;
}

function shouldClearTransientConnectionError(
  current: ChannelState,
  partial: Partial<ChannelState>,
): boolean {
  if (partial.error !== undefined) return false;
  if (!current.error || !TRANSIENT_CONNECTION_ERROR_RE.test(current.error)) return false;
  return isLiveStreamProgress(partial);
}

function shouldClearReconnecting(
  current: ChannelState,
  partial: Partial<ChannelState>,
): boolean {
  if (!current.reconnecting || partial.reconnecting !== undefined) return false;
  return isLiveStreamProgress(partial);
}

function isLiveStreamProgress(partial: Partial<ChannelState>): boolean {
  return (
    partial.hasTextContent === true ||
    !!partial.streamingText ||
    !!partial.thinkingText ||
    (partial.activeTools?.length ?? 0) > 0 ||
    (partial.subagents?.length ?? 0) > 0 ||
    !!partial.taskBoard?.tasks.length ||
    partial.heartbeatElapsedMs !== undefined ||
    (partial.pendingInjectionCount ?? 0) > 0 ||
    (partial.turnPhase !== undefined && partial.turnPhase !== null && partial.turnPhase !== "pending")
  );
}

/** Get the reset counter for a channel (used by chat-client for session key) */
export function getResetCounter(botId: string, channel: string): number {
  try {
    const raw = localStorage.getItem(RESET_COUNTERS_KEY(botId));
    if (raw) {
      const counters = JSON.parse(raw) as Record<string, number>;
      return counters[channel] ?? 0;
    }
  } catch { /* ignore */ }
  return 0;
}

function setLocalResetCounter(botId: string, channel: string, value: number): void {
  try {
    const key = RESET_COUNTERS_KEY(botId);
    const raw = localStorage.getItem(key);
    const counters = raw ? (JSON.parse(raw) as Record<string, number>) : {};
    counters[channel] = value;
    localStorage.setItem(key, JSON.stringify(counters));
  } catch { /* ignore */ }
}

/** Sync reset counters from server — merges with local (take max) */
export async function syncResetCounters(
  botId: string,
  getToken: () => Promise<string | null>,
): Promise<void> {
  try {
    const token = await getToken();
    if (!token) return;
    const res = await fetch(`/api/chat/reset-counters?botId=${botId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return;
    const { counters: serverCounters } = (await res.json()) as {
      counters: Record<string, number>;
    };
    // Merge: take max of local and server for each channel
    const key = RESET_COUNTERS_KEY(botId);
    const raw = localStorage.getItem(key);
    const local = raw ? (JSON.parse(raw) as Record<string, number>) : {};
    let changed = false;
    for (const [ch, serverVal] of Object.entries(serverCounters)) {
      const localVal = local[ch] ?? 0;
      if (serverVal > localVal) {
        local[ch] = serverVal;
        changed = true;
      }
    }
    if (changed) {
      localStorage.setItem(key, JSON.stringify(local));
    }
  } catch { /* ignore — local counters remain authoritative */ }
}

/** Increment reset counter locally and on server */
async function incrementResetCounter(
  botId: string,
  channel: string,
  getToken: () => Promise<string | null>,
): Promise<void> {
  // Optimistic local increment
  const current = getResetCounter(botId, channel);
  setLocalResetCounter(botId, channel, current + 1);

  // Sync to server (fire-and-forget)
  try {
    const token = await getToken();
    if (!token) return;
    const res = await fetch("/api/chat/reset-counters", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ botId, channelName: channel }),
    });
    if (res.ok) {
      const { resetCount } = (await res.json()) as { resetCount: number };
      // Server is authoritative — update local if server is higher
      if (resetCount > current + 1) {
        setLocalResetCounter(botId, channel, resetCount);
      }
    }
  } catch { /* ignore */ }
}

/** Persist messages to localStorage (debounced via microtask) */
let _persistQueued = false;
function persistMessages(botId: string | null, messages: Record<string, ChatMessage[]>): void {
  if (!botId || _persistQueued) return;
  _persistQueued = true;
  queueMicrotask(() => {
    _persistQueued = false;
    try {
      localStorage.setItem(MESSAGES_CACHE_KEY(botId), JSON.stringify(messages));
    } catch { /* quota exceeded — ignore */ }
  });
}

/** Load cached messages from localStorage — only the latest N per channel for fast initial render */
const INITIAL_RENDER_LIMIT = 50;
function loadCachedMessages(botId: string): Record<string, ChatMessage[]> {
  try {
    const raw = localStorage.getItem(MESSAGES_CACHE_KEY(botId));
    if (raw) {
      const all = JSON.parse(raw) as Record<string, ChatMessage[]>;
      // Trim each channel to latest N so the initial render is snappy
      for (const ch of Object.keys(all)) {
        if (all[ch].length > INITIAL_RENDER_LIMIT) {
          all[ch] = all[ch].slice(-INITIAL_RENDER_LIMIT);
        }
      }
      return all;
    }
  } catch { /* ignore */ }
  return {};
}

function dedupeControlRequests(
  requests: ControlRequestRecord[],
): ControlRequestRecord[] {
  const byId = new Map<string, ControlRequestRecord>();
  for (const request of requests) byId.set(request.requestId, request);
  return [...byId.values()].sort((a, b) => a.createdAt - b.createdAt);
}

function upsertControlRequestList(
  requests: ControlRequestRecord[],
  request: ControlRequestRecord,
): ControlRequestRecord[] {
  return dedupeControlRequests([
    ...requests.filter((item) => item.requestId !== request.requestId),
    request,
  ]);
}

function queuedPriorityRank(message: QueuedMessage): number {
  if (message.priority === "now") return 0;
  if (message.priority === "later") return 2;
  return 1;
}

function nextQueuedIndex(messages: QueuedMessage[]): number {
  if (messages.length === 0) return -1;
  let bestIndex = 0;
  let bestRank = queuedPriorityRank(messages[0]);
  for (let index = 1; index < messages.length; index += 1) {
    const rank = queuedPriorityRank(messages[index]);
    if (rank < bestRank) {
      bestIndex = index;
      bestRank = rank;
    }
  }
  return bestIndex;
}

interface ChatState {
  botId: string | null;
  channels: Channel[];
  activeChannel: string;
  messages: Record<string, ChatMessage[]>;
  channelStates: Record<string, ChannelState>;
  serverMessages: Record<string, ChatMessage[]>;
  lastServerFetch: Record<string, string>;
  abortControllers: Record<string, AbortController>;

  /** Selection mode for message deletion */
  selectionMode: boolean;
  selectedMessages: Record<string, Set<string>>;

  /** Deleted message IDs — prevents server poll from restoring them */
  deletedIds: Record<string, Set<string>>;

  /**
   * Client-side outbound queue — messages the user hit Enter on while a
   * stream was in flight. Drained FIFO by the view-client's `onDone`
   * handler; never persisted. See `src/lib/chat/queue-constants.ts`.
   */
  queuedMessages: Record<string, QueuedMessage[]>;
  controlRequests: Record<string, ControlRequestRecord[]>;

  setBotId: (botId: string) => void;
  setChannels: (channels: Channel[], scope?: BotScope) => void;
  setActiveChannel: (name: string) => void;
  getChannelState: (channel: string) => ChannelState;
  addMessage: (channel: string, message: ChatMessage, scope?: BotScope) => void;
  setChannelState: (channel: string, state: Partial<ChannelState>, scope?: BotScope) => void;
  setServerMessages: (channel: string, messages: ChatMessage[], scope?: BotScope) => void;
  setLastServerFetch: (channel: string, timestamp: string, scope?: BotScope) => void;
  cancelStream: (channel?: string, options?: { preserveQueue?: boolean; botId?: string | null }) => void;
  setAbortController: (channel: string, controller: AbortController, scope?: BotScope) => void;
  finalizeStream: (channel: string, msgId?: string, scope?: BotScope) => void;
  resetSession: (channel: string, getToken?: () => Promise<string | null>) => void;
  markChannelRead: (channel: string) => void;
  hasUnread: (channel: string) => boolean;

  /** Selection mode actions */
  enterSelectionMode: (channel: string, msgId: string) => void;
  exitSelectionMode: () => void;
  toggleMessageSelection: (channel: string, msgId: string) => void;
  selectAllMessages: (channel: string) => void;
  deselectAllMessages: (channel: string) => void;
  removeMessages: (channel: string, msgIds: Set<string>, scope?: BotScope) => void;
  isDeleted: (channel: string, msgId: string) => boolean;
  clearDeletedIds: (channel: string) => void;

  /** Outbound queue actions — see `queuedMessages`. */
  enqueueMessage: (channel: string, msg: QueuedMessage, scope?: BotScope) => boolean;
  dequeueFirst: (channel: string, scope?: BotScope) => QueuedMessage | null;
  promoteNextQueuedMessage: (channel: string, scope?: BotScope) => boolean;
  removeFromQueue: (channel: string, id: string, scope?: BotScope) => void;
  clearQueue: (channel: string, scope?: BotScope) => void;

  hydrateControlRequests: (channel: string, requests: ControlRequestRecord[]) => void;
  applyControlEvent: (channel: string, event: ControlEvent) => void;
  upsertControlRequest: (channel: string, request: ControlRequestRecord) => void;

  /**
   * §7.15 — append a message delivered via Supabase Realtime push
   * (core-agent → chat-proxy → push_messages table). Dedupes by
   * serverId against both the local `messages` and `serverMessages`
   * buckets; mirrors the polling dedupe used by the existing
   * app_channel_messages flow.
   */
  receivePushMessage: (
    channel: string,
    row: {
      id: string;
      role: "assistant" | "system";
      content: string;
      server_id: string;
      created_at: string;
    },
    scope?: BotScope,
  ) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  botId: null,
  channels: [],
  activeChannel: "general",
  messages: {},
  channelStates: {},
  serverMessages: {},
  lastServerFetch: {},
  abortControllers: {},
  selectionMode: false,
  selectedMessages: {},
  deletedIds: {},
  queuedMessages: {},
  controlRequests: {},

  setBotId: (botId) => {
    // Only reset if botId actually changed
    if (get().botId === botId) return;
    for (const controller of Object.values(get().abortControllers)) {
      controller.abort();
    }
    const cachedMessages = loadCachedMessages(botId);
    set({
      botId,
      channels: [],
      activeChannel: "general",
      messages: cachedMessages,
      channelStates: {},
      serverMessages: {},
      lastServerFetch: {},
      abortControllers: {},
      deletedIds: {},
      queuedMessages: {},
      selectionMode: false,
      selectedMessages: {},
      controlRequests: {},
    });
  },

  setChannels: (channels, scope) =>
    set((state) => (matchesBotScope(state.botId, scope) ? { channels } : {})),

  setActiveChannel: (name) => set({ activeChannel: name }),

  getChannelState: (channel) => get().channelStates[channel] ?? DEFAULT_CHANNEL_STATE,

  addMessage: (channel, message, scope) =>
    set((state) => {
      if (!matchesBotScope(state.botId, scope)) return {};
      const existing = state.messages[channel] ?? [];
      const idx = existing.findIndex((candidate) => (
        (!!candidate.id && !!message.id && candidate.id === message.id) ||
        (!!candidate.serverId && !!message.serverId && candidate.serverId === message.serverId)
      ));
      const merged = idx >= 0
        ? existing.map((candidate, i) => (i === idx ? { ...candidate, ...message } : candidate))
        : [...existing, message];
      const updated = merged.sort(compareChatMessages).slice(-MAX_LOCAL_MESSAGES);
      const newMessages = { ...state.messages, [channel]: updated };
      persistMessages(state.botId, newMessages);
      return { messages: newMessages };
    }),

  setChannelState: (channel, partial, scope) =>
    set((state) => {
      if (!matchesBotScope(state.botId, scope)) return {};
      const current = state.channelStates[channel] ?? DEFAULT_CHANNEL_STATE;
      return {
        channelStates: {
          ...state.channelStates,
          [channel]: mergeChannelState(current, partial),
        },
      };
    }),

  setServerMessages: (channel, messages, scope) =>
    set((state) => (
      matchesBotScope(state.botId, scope)
        ? { serverMessages: { ...state.serverMessages, [channel]: messages } }
        : {}
    )),

  setLastServerFetch: (channel, timestamp, scope) =>
    set((state) => (
      matchesBotScope(state.botId, scope)
        ? { lastServerFetch: { ...state.lastServerFetch, [channel]: timestamp } }
        : {}
    )),

  cancelStream: (channel, options) => {
    if (!matchesBotScope(get().botId, options)) return;
    const ch = channel ?? get().activeChannel;
    const controller = get().abortControllers[ch];
    if (controller) controller.abort();

    const state = get().channelStates[ch] ?? DEFAULT_CHANNEL_STATE;
    const activities = state.activeTools ?? [];
    const taskBoard = state.taskBoard ?? null;
    if (state.streamingText || state.thinkingText || activities.length > 0 || (taskBoard && taskBoard.tasks.length > 0)) {
      const hasThinking = !!state.thinkingText;
      const thinkingDuration = state.thinkingStartedAt
        ? Math.round((Date.now() - state.thinkingStartedAt) / 1000)
        : undefined;
      // Tag the message with the interrupted suffix. Applied once here —
      // not on every ESC keypress — because this is the single sink for
      // all cancel paths (ESC key, stop button, programmatic cancel).
      const baseContent = state.streamingText || "";
      const content = baseContent
        ? `${baseContent}${INTERRUPTED_SUFFIX}`
        : INTERRUPTED_SUFFIX.trimStart();
      get().addMessage(ch, {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content,
        timestamp: Date.now(),
        thinkingContent: hasThinking ? state.thinkingText : undefined,
        thinkingDuration,
        activities: activities.length > 0 ? activities : undefined,
        taskBoard: taskBoard && taskBoard.tasks.length > 0 ? taskBoard : undefined,
      });
    }
    set((s) => {
      const nextQueued = { ...s.queuedMessages };
      if (!options?.preserveQueue) {
        delete nextQueued[ch];
      }
      return {
        channelStates: {
          ...s.channelStates,
          [ch]: { ...DEFAULT_CHANNEL_STATE },
        },
        queuedMessages: nextQueued,
      };
    });
  },

  setAbortController: (channel, controller, scope) =>
    set((state) => {
      if (!matchesBotScope(state.botId, scope)) {
        controller.abort();
        return {};
      }
      return {
        abortControllers: { ...state.abortControllers, [channel]: controller },
      };
    }),

  finalizeStream: (channel, msgId, scope) => {
    if (!matchesBotScope(get().botId, scope)) return;
    const state = get().channelStates[channel] ?? DEFAULT_CHANNEL_STATE;
    const content = state.streamingText;
    const hasVisibleText = !!state.hasTextContent && content.trim().length > 0;
    const hasThinking = !!state.thinkingText;
    const activities = state.activeTools ?? [];
    const taskBoard = state.taskBoard ?? null;
    const detachedSubagents = activeBackgroundSubagents(state);
    {
      const thinkingDuration = state.thinkingStartedAt
        ? Math.round((Date.now() - state.thinkingStartedAt) / 1000)
        : undefined;
      // If no visible text but the stream was active (streaming started),
      // show a fallback so the user doesn't see a vanished response.
      const hadStreamActivity = state.streaming || hasThinking || activities.length > 0;
      const finalContent = hasVisibleText
        ? (content || "")
        : hadStreamActivity
          ? streamErrorFallback(state.responseLanguage)
          : "";
      if (finalContent) {
        get().addMessage(channel, {
          id: msgId ?? `assistant-${Date.now()}`,
          role: "assistant",
          content: finalContent,
          timestamp: Date.now(),
          thinkingContent: hasThinking ? state.thinkingText : undefined,
          thinkingDuration,
          activities: activities.length > 0 ? activities : undefined,
          taskBoard: taskBoard && taskBoard.tasks.length > 0 ? taskBoard : undefined,
        });
      }
    }
    set((s) => ({
      channelStates: {
        ...s.channelStates,
        [channel]: detachedSubagents.length > 0
          ? { ...DEFAULT_CHANNEL_STATE, subagents: detachedSubagents }
          : { ...DEFAULT_CHANNEL_STATE },
      },
    }));
  },

  resetSession: (channel, getToken) => {
    // Abort any in-flight stream
    const controller = get().abortControllers[channel];
    if (controller) controller.abort();

    const state = get();

    // Increment reset counter so next request uses a new session key
    if (state.botId) {
      incrementResetCounter(state.botId, channel, getToken ?? (() => Promise.resolve(null)));
    }

    // Insert a system divider message instead of clearing history
    const existing = state.messages[channel] ?? [];
    const divider: ChatMessage = {
      id: `system-reset-${Date.now()}`,
      role: "system",
      content: "Session ended — new conversation started",
      timestamp: Date.now(),
    };
    const updated = [...existing, divider];
    const newMessages = { ...state.messages, [channel]: updated };
    persistMessages(state.botId, newMessages);

    set({
      messages: newMessages,
      channelStates: {
        ...state.channelStates,
        [channel]: { ...DEFAULT_CHANNEL_STATE },
      },
      controlRequests: {
        ...state.controlRequests,
        [channel]: [],
      },
    });
  },

  markChannelRead: (channel) => {
    try {
      const botId = get().botId;
      const key = botId ? LAST_READ_KEY(botId) : "magi:lastRead";
      const raw = localStorage.getItem(key);
      const data = raw ? (JSON.parse(raw) as Record<string, number>) : {};
      data[channel] = Date.now();
      localStorage.setItem(key, JSON.stringify(data));
    } catch { /* ignore */ }
  },

  hasUnread: (channel) => {
    let lastRead = 0;
    try {
      const botId = get().botId;
      const raw = botId
        ? (localStorage.getItem(LAST_READ_KEY(botId)) ?? localStorage.getItem("magi:lastRead"))
        : localStorage.getItem("magi:lastRead");
      if (raw) {
        const data = JSON.parse(raw) as Record<string, number>;
        lastRead = data[channel] ?? 0;
      }
    } catch { /* ignore */ }
    const { messages, serverMessages } = get();
    const localMsgs = messages[channel] ?? [];
    if (localMsgs.some((m) => m.role === "assistant" && (m.timestamp ?? 0) > lastRead)) return true;
    const srvMsgs = serverMessages[channel] ?? [];
    return srvMsgs.some((m) => m.role === "assistant" && (m.timestamp ?? 0) > lastRead);
  },

  enterSelectionMode: (channel, msgId) =>
    set({
      selectionMode: true,
      selectedMessages: { [channel]: new Set([msgId]) },
    }),

  exitSelectionMode: () =>
    set({ selectionMode: false, selectedMessages: {} }),

  toggleMessageSelection: (channel, msgId) =>
    set((state) => {
      const current = new Set(state.selectedMessages[channel] ?? []);
      if (current.has(msgId)) current.delete(msgId);
      else current.add(msgId);
      // Exit selection mode if nothing selected
      if (current.size === 0) return { selectionMode: false, selectedMessages: {} };
      return { selectedMessages: { ...state.selectedMessages, [channel]: current } };
    }),

  selectAllMessages: (channel) =>
    set((state) => {
      const allMsgs = state.messages[channel] ?? [];
      const serverMsgs = state.serverMessages[channel] ?? [];
      const allIds = new Set([
        ...allMsgs.filter((m) => m.role !== "system").map((m) => m.id),
        ...serverMsgs.map((m) => m.id),
      ]);
      return { selectedMessages: { ...state.selectedMessages, [channel]: allIds } };
    }),

  deselectAllMessages: (channel) =>
    set((state) => ({
      selectedMessages: { ...state.selectedMessages, [channel]: new Set() },
    })),

  removeMessages: (channel, msgIds, scope) =>
    set((state) => {
      if (!matchesBotScope(state.botId, scope)) return {};
      const localMsgs = (state.messages[channel] ?? []).filter((m) => !msgIds.has(m.id));
      const serverMsgs = (state.serverMessages[channel] ?? []).filter((m) => !msgIds.has(m.id) && !(m.serverId && msgIds.has(m.serverId)));
      const newMessages = { ...state.messages, [channel]: localMsgs };
      persistMessages(state.botId, newMessages);
      // Track deleted IDs so server poll doesn't restore them
      const existingDeleted = state.deletedIds[channel] ?? new Set();
      const mergedDeleted = new Set([...existingDeleted, ...msgIds]);
      // Prune deleted IDs from selectedMessages (don't reset selectionMode —
      // E2EE polling also calls removeMessages and would clear user's in-progress selection)
      const currentSelected = state.selectedMessages[channel];
      let newSelectedMessages = state.selectedMessages;
      let newSelectionMode = state.selectionMode;
      if (currentSelected) {
        const pruned = new Set([...currentSelected].filter((id) => !msgIds.has(id)));
        newSelectedMessages = { ...state.selectedMessages, [channel]: pruned };
        if (pruned.size === 0 && state.selectionMode) {
          newSelectionMode = false;
          newSelectedMessages = {};
        }
      }
      return {
        messages: newMessages,
        serverMessages: { ...state.serverMessages, [channel]: serverMsgs },
        deletedIds: { ...state.deletedIds, [channel]: mergedDeleted },
        selectionMode: newSelectionMode,
        selectedMessages: newSelectedMessages,
      };
    }),

  isDeleted: (channel, msgId) => {
    const deleted = get().deletedIds[channel];
    return deleted ? deleted.has(msgId) : false;
  },

  clearDeletedIds: (channel) =>
    set((state) => {
      const newDeletedIds = { ...state.deletedIds };
      delete newDeletedIds[channel];
      return { deletedIds: newDeletedIds };
    }),

  enqueueMessage: (channel, msg, scope) => {
    if (!matchesBotScope(get().botId, scope)) return false;
    const current = get().queuedMessages[channel] ?? [];
    if (current.length >= MAX_QUEUED_MESSAGES) return false;
    set((state) => ({
      queuedMessages: {
        ...state.queuedMessages,
        [channel]: [...(state.queuedMessages[channel] ?? []), msg],
      },
    }));
    return true;
  },

  dequeueFirst: (channel, scope) => {
    if (!matchesBotScope(get().botId, scope)) return null;
    const current = get().queuedMessages[channel] ?? [];
    if (current.length === 0) return null;
    const index = nextQueuedIndex(current);
    const first = current[index];
    const rest = current.filter((_, i) => i !== index);
    set((state) => ({
      queuedMessages: { ...state.queuedMessages, [channel]: rest },
    }));
    return first ?? null;
  },

  promoteNextQueuedMessage: (channel, scope) => {
    if (!matchesBotScope(get().botId, scope)) return false;
    const current = get().queuedMessages[channel] ?? [];
    if (current.length === 0) return false;
    set((state) => {
      const queue = state.queuedMessages[channel] ?? [];
      if (queue.length === 0) return {};
      return {
        queuedMessages: {
          ...state.queuedMessages,
          [channel]: queue.map((message, index) => (
            index === 0 ? { ...message, priority: "now" } : message
          )),
        },
      };
    });
    return true;
  },

  removeFromQueue: (channel, id, scope) =>
    set((state) => {
      if (!matchesBotScope(state.botId, scope)) return {};
      const current = state.queuedMessages[channel] ?? [];
      return {
        queuedMessages: {
          ...state.queuedMessages,
          [channel]: current.filter((m) => m.id !== id),
        },
      };
    }),

  clearQueue: (channel, scope) =>
    set((state) => {
      if (!matchesBotScope(state.botId, scope)) return {};
      const next = { ...state.queuedMessages };
      delete next[channel];
      return { queuedMessages: next };
    }),

  hydrateControlRequests: (channel, requests) =>
    set((state) => ({
      controlRequests: {
        ...state.controlRequests,
        [channel]: dedupeControlRequests(requests),
      },
    })),

  upsertControlRequest: (channel, request) =>
    set((state) => ({
      controlRequests: {
        ...state.controlRequests,
        [channel]: upsertControlRequestList(
          state.controlRequests[channel] ?? [],
          request,
        ),
      },
    })),

  applyControlEvent: (channel, event) =>
    set((state) => {
      const current = state.controlRequests[channel] ?? [];
      let next = current;
      if (event.type === "control_request_created") {
        next = upsertControlRequestList(current, event.request);
      } else if (event.type === "control_request_resolved") {
        next = current.map((request) =>
          request.requestId === event.requestId
            ? {
                ...request,
                state: event.decision,
                decision: event.decision,
                resolvedAt: Date.now(),
                ...(event.feedback !== undefined ? { feedback: event.feedback } : {}),
                ...(event.updatedInput !== undefined ? { updatedInput: event.updatedInput } : {}),
                ...(event.answer !== undefined ? { answer: event.answer } : {}),
              }
            : request,
        );
      } else if (event.type === "control_request_cancelled") {
        next = current.map((request) =>
          request.requestId === event.requestId
            ? {
                ...request,
                state: "cancelled",
                resolvedAt: Date.now(),
                feedback: event.reason,
              }
            : request,
        );
      } else if (event.type === "control_request_timed_out") {
        next = current.map((request) =>
          request.requestId === event.requestId
            ? { ...request, state: "timed_out", resolvedAt: Date.now() }
            : request,
        );
      }
      return {
        controlRequests: {
          ...state.controlRequests,
          [channel]: dedupeControlRequests(next),
        },
      };
    }),

  receivePushMessage: (channel, row, scope) =>
    set((state) => {
      if (!matchesBotScope(state.botId, scope)) return {};
      const serverId = row.server_id;
      if (!serverId) return {};
      // Dedupe against local + server buckets. Also suppress if the
      // user already deleted this id — matches setServerMessages
      // behaviour so push can't resurrect deleted content.
      const deleted = state.deletedIds[channel];
      if (deleted && deleted.has(serverId)) return {};
      const existingLocal = state.messages[channel] ?? [];
      if (existingLocal.some((m) => m.serverId === serverId || m.id === serverId)) {
        return {};
      }
      const existingServer = state.serverMessages[channel] ?? [];
      if (existingServer.some((m) => m.serverId === serverId || m.id === serverId)) {
        return {};
      }
      const ts = Date.parse(row.created_at);
      const msg: ChatMessage = {
        id: row.id || serverId,
        role: row.role === "system" ? "system" : "assistant",
        content: row.content,
        timestamp: Number.isFinite(ts) ? ts : Date.now(),
        serverId,
      };
      const updated = [...existingLocal, msg].slice(-MAX_LOCAL_MESSAGES);
      const newMessages = { ...state.messages, [channel]: updated };
      persistMessages(state.botId, newMessages);
      return { messages: newMessages };
    }),
}));
