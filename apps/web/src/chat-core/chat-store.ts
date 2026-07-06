"use client";

import { create } from "zustand";
import type {
  ChatMessage,
  Channel,
  ChannelState,
  QueuedMessage,
  ControlEvent,
  ControlRequestRecord,
  MissionActivity,
  SubagentActivity,
  ToolActivity,
} from "./types";
import { INTERRUPTED_SUFFIX, MAX_QUEUED_MESSAGES } from "./queue-constants";
import { compareChatMessages } from "./message-order";
import { hasNonTextTurnWork } from "./empty-response";
import { researchEvidenceFromChannelState } from "./research-evidence";
import {
  assistantMessagesExactlyMatch,
  mergeAssistantMessageCopies,
  shouldMergeAssistantMessageCopies,
} from "./assistant-dedupe";
import { finalizedSegmentsForMessage } from "./transcript-segments";

const DEFAULT_CHANNEL_STATE: ChannelState = {
  streaming: false,
  streamingText: "",
  thinkingText: "",
  error: null,
  hasTextContent: false,
  thinkingStartedAt: null,
  turnPhase: null,
  heartbeatElapsedMs: null,
  currentGoal: null,
  pendingInjectionCount: 0,
  activeTools: [],
  browserFrame: null,
  documentDraft: null,
  subagents: [],
  subagentProgress: {},
  taskBoard: null,
  missions: [],
  activeGoalMissionId: null,
  missionRefreshSeq: 0,
  lastMissionEventMissionId: null,
  pendingGoalMissionTitle: null,
  inspectedSources: [],
  citationGate: null,
  runtimeTraces: [],
  determinism: undefined,
  recipeSelection: undefined,
  turnUsage: undefined,
  liveTranscriptItems: [],
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
  currentGoal: null,
  pendingInjectionCount: 0,
  activeTools: [],
  browserFrame: null,
  documentDraft: null,
  subagents: [],
  subagentProgress: {},
  taskBoard: null,
  pendingGoalMissionTitle: null,
  inspectedSources: [],
  citationGate: null,
  runtimeTraces: [],
  determinism: undefined,
  recipeSelection: undefined,
  turnUsage: undefined,
  liveTranscriptItems: [],
  fileProcessing: false,
  reconnecting: false,
};

const MAX_LOCAL_MESSAGES = 200;
// Generous cap on how many completed/errored/cancelled child chips persist in
// the AGENTS strip past turn end. Kevin: "generous". Bounds the strip so a
// runaway spawn count cannot grow the retained list without limit; the entries
// are cleared at the next turn-start reset regardless.
const MAX_RETAINED_COMPLETED_SUBAGENTS = 16;
const SERVER_READABLE_USER_TURN_MARKER_RE =
  /^\s*<!-- openmagi:server-readable-user-turn:v1:[A-Za-z0-9_-]+ -->\s*$/;
const TRANSIENT_CONNECTION_ERROR_RE = /^Connecting to bot\.\.\. \(\d+\/\d+\)$/;
const SILENT_VERIFIER_META_ERROR_RE =
  /source-verified final answer|inspected-source context|claim[-_\s]?citation|research proof|runtime verifier stopped|promised work without completing|GOAL_PROGRESS_EXECUTE_NEXT|INTERACTIVE_TOOL_REQUIRED/i;
const MESSAGES_CACHE_KEY = (botId: string) => `clawy:messages:${botId}`;
const RESET_COUNTERS_KEY = (botId: string) => `clawy:resetCounters:${botId}`;
const LAST_READ_KEY = (botId: string) => `clawy:lastRead:${botId}`;

interface ResetCounterEntry {
  count: number;
  updatedAt?: number;
}

type ResetCounterStorageValue = number | ResetCounterEntry;

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
  const startsFreshAttempt =
    current.streaming === true &&
    partial.turnPhase === "pending" &&
    partial.determinism === undefined &&
    partial.hasTextContent === false &&
    partial.streamingText === "" &&
    partial.thinkingText === "";
  const endsRun = partial.streaming === false;
  const reset = startsFreshRun || startsFreshAttempt
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

function previewRecord(value?: string): Record<string, unknown> | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function backgroundTaskIdFromTool(activity: ToolActivity): string | null {
  const output = previewRecord(activity.outputPreview);
  if (output?.background !== true) return null;
  const taskId = output.backgroundTaskId;
  return typeof taskId === "string" && taskId.trim() ? taskId.trim() : null;
}

function backgroundSubagentFromTool(activity: ToolActivity): SubagentActivity | null {
  const taskId = backgroundTaskIdFromTool(activity);
  if (!taskId) return null;
  const now = Date.now();
  const role = activity.label.toLowerCase().includes("bash") ? "bash" : "background";
  return {
    taskId,
    role,
    status: "running",
    detail: role === "bash" ? "Background command running" : "Background task running",
    startedAt: activity.startedAt,
    updatedAt: now,
  };
}

function activeBackgroundSubagents(channelState: ChannelState): SubagentActivity[] {
  const active = (channelState.subagents ?? []).filter(
    (subagent) => subagent.status === "running" || subagent.status === "waiting",
  );
  const seen = new Set(active.map((subagent) => subagent.taskId));
  for (const activity of channelState.activeTools ?? []) {
    const background = backgroundSubagentFromTool(activity);
    if (!background || seen.has(background.taskId)) continue;
    active.push(background);
    seen.add(background.taskId);
  }
  return active;
}

function isExplicitBackgroundSubagent(subagent: SubagentActivity): boolean {
  const role = subagent.role.trim().toLowerCase();
  return role === "bash" || role === "background";
}

function activeSubagentsAfterLocalCancel(channelState: ChannelState): SubagentActivity[] {
  const active = (channelState.subagents ?? []).filter(
    (subagent) =>
      (subagent.status === "running" || subagent.status === "waiting") &&
      isExplicitBackgroundSubagent(subagent),
  );
  const seen = new Set(active.map((subagent) => subagent.taskId));
  for (const activity of channelState.activeTools ?? []) {
    const background = backgroundSubagentFromTool(activity);
    if (!background || seen.has(background.taskId)) continue;
    active.push(background);
    seen.add(background.taskId);
  }
  return active;
}

function isTerminalSubagentStatus(status: SubagentActivity["status"]): boolean {
  return status === "done" || status === "error" || status === "cancelled";
}

// Completed / errored / cancelled NON-background child chips to keep past turn
// end. The live background-detach path (activeBackgroundSubagents) already
// retains still-running/waiting subagents; this adds the terminal children so a
// finished spawn stays visible as a completed chip instead of vanishing at
// turn end. Capped at the most recent MAX_RETAINED_COMPLETED_SUBAGENTS by
// updatedAt to bound the strip. `alreadyRetained` are the taskIds kept by the
// background-detach pass so we never duplicate one.
function retainedCompletedSubagents(
  channelState: ChannelState,
  alreadyRetained: Set<string>,
): SubagentActivity[] {
  const completed = (channelState.subagents ?? []).filter(
    (subagent) =>
      isTerminalSubagentStatus(subagent.status) &&
      !isExplicitBackgroundSubagent(subagent) &&
      !alreadyRetained.has(subagent.taskId),
  );
  completed.sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0));
  return completed.slice(0, MAX_RETAINED_COMPLETED_SUBAGENTS);
}

function shouldMergePushedAssistant(existing: ChatMessage, incoming: ChatMessage): boolean {
  if (existing.role !== "assistant" || incoming.role !== "assistant") return false;
  if (existing.serverId || !incoming.serverId) return false;
  return shouldMergeAssistantMessageCopies(existing, incoming);
}

function latestUserTimestampBeforeOrAt(messages: ChatMessage[], timestamp: number): number | null {
  let latest: number | null = null;
  for (const message of messages) {
    if (message.role !== "user") continue;
    const messageTimestamp = message.timestamp ?? 0;
    if (messageTimestamp > timestamp) continue;
    if (latest === null || messageTimestamp > latest) latest = messageTimestamp;
  }
  return latest;
}

function assistantMessagesShareLatestUserTurn(
  messages: ChatMessage[],
  first: ChatMessage,
  second: ChatMessage,
): boolean {
  const firstTimestamp = first.timestamp ?? 0;
  const secondTimestamp = second.timestamp ?? 0;
  const boundary = latestUserTimestampBeforeOrAt(
    messages,
    Math.max(firstTimestamp, secondTimestamp),
  );
  if (boundary === null) return false;
  return firstTimestamp >= boundary && secondTimestamp >= boundary;
}

function shouldMergeAssistantMessageInChannel(
  messages: ChatMessage[],
  existing: ChatMessage,
  incoming: ChatMessage,
): boolean {
  if (shouldMergeAssistantMessageCopies(existing, incoming)) return true;
  if (existing.role !== "assistant" || incoming.role !== "assistant") return false;
  if (existing.serverId && incoming.serverId) return false;
  if (!assistantMessagesExactlyMatch(existing, incoming)) return false;
  return assistantMessagesShareLatestUserTurn(messages, existing, incoming);
}

function compactErrorDetail(message: string): string {
  const compact = message.replace(/\s+/g, " ").trim();
  return compact.length > 160 ? `${compact.slice(0, 157)}...` : compact;
}

function streamErrorFallback(state: ChannelState): string {
  const language = state.responseLanguage;
  const errorDetail = state.error ? compactErrorDetail(state.error) : null;
  if (errorDetail && isSilentVerifierMetaError(errorDetail)) return "";
  if (language === "ko") {
    if (errorDetail) {
      return `⚠️ 응답 생성이 중단되었습니다: ${errorDetail}. 다시 시도해 주세요.`;
    }
    if (state.turnPhase === "aborted") {
      return "⚠️ 응답 생성이 중단되었습니다. 최종 답변 텍스트가 도착하지 않았습니다. 다시 시도해 주세요.";
    }
    if (hasNonTextTurnWork(state)) {
      return "⚠️ 작업은 진행됐지만 최종 답변 텍스트가 도착하지 않았습니다. 다시 시도해 주세요.";
    }
    return "⚠️ 빈 응답이 도착했습니다. 다시 시도해 주세요.";
  }

  if (errorDetail) {
    return `⚠️ Response generation stopped: ${errorDetail}. Please try again.`;
  }
  if (state.turnPhase === "aborted") {
    return "⚠️ Response generation stopped before final answer text arrived. Please try again.";
  }
  if (hasNonTextTurnWork(state)) {
    return "⚠️ Work started, but no final answer text arrived. Please try again.";
  }
  return "⚠️ The response ended without visible answer text. Please try again.";
}

function isSilentVerifierMetaError(message: string | null | undefined): boolean {
  return !!message && SILENT_VERIFIER_META_ERROR_RE.test(message);
}

function finalVisibleStreamContent(state: ChannelState, content: string): string {
  if (!state.error && state.turnPhase !== "aborted") return content;
  if (isSilentVerifierMetaError(state.error)) return content.trimEnd();
  const suffix = streamErrorFallback({
    ...state,
    streamingText: "",
    hasTextContent: false,
  });
  return `${content.trimEnd()}\n\n${suffix}`;
}

function isTerminalMission(mission: MissionActivity): boolean {
  return (
    mission.status === "completed" ||
    mission.status === "failed" ||
    mission.status === "cancelled"
  );
}

function durableMissionState(
  current: ChannelState,
): Pick<ChannelState, "missions" | "activeGoalMissionId"> {
  const missions = (current.missions ?? []).filter((mission) => !isTerminalMission(mission));
  const currentActiveGoalId =
    current.activeGoalMissionId &&
    missions.some((mission) => mission.id === current.activeGoalMissionId && mission.kind === "goal")
      ? current.activeGoalMissionId
      : null;
  const fallbackActiveGoalId =
    currentActiveGoalId ?? missions.find((mission) => mission.kind === "goal")?.id ?? null;
  return { missions, activeGoalMissionId: fallbackActiveGoalId };
}

function terminalLiveRunReset(
  current: ChannelState,
  partial: Partial<ChannelState>,
): Partial<ChannelState> {
  const reset = { ...RESET_LIVE_RUN_STATE };
  if (partial.missions === undefined) {
    Object.assign(reset, durableMissionState(current));
  }
  if (partial.subagents === undefined) {
    const detachedSubagents = activeBackgroundSubagents(current);
    const detachedTaskIds = new Set(detachedSubagents.map((subagent) => subagent.taskId));
    // Also keep completed / errored / cancelled child chips so a finished
    // subagent stays visible past turn end (cleared at the next turn-start
    // reset via RESET_LIVE_RUN_STATE, which empties `subagents`).
    const completedSubagents = retainedCompletedSubagents(current, detachedTaskIds);
    const retained = [...detachedSubagents, ...completedSubagents];
    if (retained.length > 0) {
      reset.subagents = retained;
      const retainedTaskIds = new Set(retained.map((subagent) => subagent.taskId));
      reset.subagentProgress = Object.fromEntries(
        Object.entries(current.subagentProgress ?? {}).filter(([taskId]) =>
          retainedTaskIds.has(taskId),
        ),
      );
    }
  }
  if (
    partial.runtimeTraces === undefined &&
    (partial.error || partial.turnPhase === "aborted" || current.turnPhase === "aborted")
  ) {
    reset.runtimeTraces = current.runtimeTraces ?? [];
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
    !!partial.browserFrame ||
    !!partial.documentDraft ||
    (partial.subagents?.length ?? 0) > 0 ||
    Object.keys(partial.subagentProgress ?? {}).length > 0 ||
    (partial.missions?.length ?? 0) > 0 ||
    !!partial.taskBoard?.tasks.length ||
    (partial.inspectedSources?.length ?? 0) > 0 ||
    !!partial.citationGate ||
    (partial.runtimeTraces?.length ?? 0) > 0 ||
    !!partial.turnUsage ||
    (partial.liveTranscriptItems?.length ?? 0) > 0 ||
    partial.heartbeatElapsedMs !== undefined ||
    (partial.pendingInjectionCount ?? 0) > 0 ||
    (partial.turnPhase !== undefined && partial.turnPhase !== null && partial.turnPhase !== "pending")
  );
}

/** Get the reset counter for a channel (used by chat-client for session key) */
export function getResetCounter(botId: string, channel: string): number {
  return readLocalResetCounters(botId)[channel]?.count ?? 0;
}

export function getResetBoundaryTimestamp(botId: string, channel: string): number | null {
  return readLocalResetCounters(botId)[channel]?.updatedAt ?? null;
}

function normalizedResetCounterEntry(value: unknown): ResetCounterEntry {
  if (typeof value === "number" && Number.isFinite(value)) {
    return { count: Math.max(0, Math.floor(value)) };
  }
  if (value && typeof value === "object") {
    const record = value as Partial<ResetCounterEntry>;
    const count = typeof record.count === "number" && Number.isFinite(record.count)
      ? Math.max(0, Math.floor(record.count))
      : 0;
    const updatedAt = typeof record.updatedAt === "number" && Number.isFinite(record.updatedAt) && record.updatedAt > 0
      ? Math.floor(record.updatedAt)
      : undefined;
    return updatedAt === undefined ? { count } : { count, updatedAt };
  }
  return { count: 0 };
}

function readLocalResetCounters(botId: string): Record<string, ResetCounterEntry> {
  try {
    const raw = localStorage.getItem(RESET_COUNTERS_KEY(botId));
    if (raw) {
      const counters = JSON.parse(raw) as Record<string, ResetCounterStorageValue>;
      return Object.fromEntries(
        Object.entries(counters).map(([name, value]) => [
          name,
          normalizedResetCounterEntry(value),
        ]),
      );
    }
  } catch { /* ignore */ }
  return {};
}

function writeLocalResetCounters(
  botId: string,
  counters: Record<string, ResetCounterEntry>,
): void {
  try {
    localStorage.setItem(RESET_COUNTERS_KEY(botId), JSON.stringify(counters));
  } catch { /* ignore */ }
}

function setLocalResetCounter(
  botId: string,
  channel: string,
  value: number,
  updatedAt?: number | null,
): void {
  const counters = readLocalResetCounters(botId);
  const existing = counters[channel];
  const normalizedUpdatedAt =
    typeof updatedAt === "number" && Number.isFinite(updatedAt) && updatedAt > 0
      ? Math.floor(updatedAt)
      : existing?.updatedAt;
  counters[channel] = normalizedUpdatedAt === undefined
    ? { count: Math.max(0, Math.floor(value)) }
    : { count: Math.max(0, Math.floor(value)), updatedAt: normalizedUpdatedAt };
  writeLocalResetCounters(botId, counters);
}

function mergeResetCounterFromServer(
  local: Record<string, ResetCounterEntry>,
  channel: string,
  serverCount: number,
  serverUpdatedAt?: number,
): boolean {
  if (!Number.isFinite(serverCount)) return false;
  const localEntry = local[channel] ?? { count: 0 };
  const normalizedServerCount = Math.max(0, Math.floor(serverCount));
  const normalizedServerUpdatedAt =
    typeof serverUpdatedAt === "number" && Number.isFinite(serverUpdatedAt) && serverUpdatedAt > 0
      ? Math.floor(serverUpdatedAt)
      : undefined;
  const shouldAdopt =
    normalizedServerCount > localEntry.count ||
    (
      normalizedServerCount === localEntry.count &&
      normalizedServerUpdatedAt !== undefined &&
      normalizedServerUpdatedAt > (localEntry.updatedAt ?? 0)
    );
  if (!shouldAdopt) return false;
  local[channel] = normalizedServerUpdatedAt === undefined
    ? { count: normalizedServerCount, updatedAt: localEntry.updatedAt }
    : { count: normalizedServerCount, updatedAt: normalizedServerUpdatedAt };
  return true;
}

/** Sync reset counters from server — merges with local (take max) */
export async function syncResetCounters(
  botId: string,
  getToken: () => Promise<string | null>,
): Promise<void> {
  try {
    const token = await getToken();
    if (!token) return;
    const res = await fetch(
      `/api/chat/reset-counters?botId=${encodeURIComponent(botId)}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!res.ok) return;
    const { counters: serverCounters, resetAt } = (await res.json()) as {
      counters: Record<string, number>;
      resetAt?: Record<string, number>;
    };
    // Merge: take max of local and server for each channel
    const local = readLocalResetCounters(botId);
    let changed = false;
    for (const [ch, serverVal] of Object.entries(serverCounters)) {
      changed = mergeResetCounterFromServer(local, ch, serverVal, resetAt?.[ch]) || changed;
    }
    if (changed) {
      writeLocalResetCounters(botId, local);
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
  const optimisticResetAt = Date.now();
  setLocalResetCounter(botId, channel, current + 1, optimisticResetAt);

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
      const { resetCount, resetAt } = (await res.json()) as {
        resetCount: number;
        resetAt?: number;
      };
      // Server is authoritative — update local if server is higher
      const local = readLocalResetCounters(botId);
      if (mergeResetCounterFromServer(local, channel, resetCount, resetAt)) {
        writeLocalResetCounters(botId, local);
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
   * handler; never persisted. See `src/lib/chat-core/queue-constants.ts`.
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
  clearSession: (channel: string, getToken?: () => Promise<string | null>) => void;
  markChannelRead: (channel: string) => void;
  hasUnread: (channel: string) => boolean;

  /** Selection mode actions */
  startSelectionMode: (channel: string) => void;
  enterSelectionMode: (channel: string, msgId: string) => void;
  exitSelectionMode: () => void;
  toggleMessageSelection: (channel: string, msgId: string) => void;
  selectAllMessages: (channel: string) => void;
  deselectAllMessages: (channel: string) => void;
  removeMessages: (channel: string, msgIds: Set<string>, scope?: BotScope) => void;
  removeLocalMessages: (channel: string, msgIds: Set<string>, scope?: BotScope) => void;
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
      const assistantCopyIndex = idx >= 0
        ? -1
        : existing.findIndex((candidate) =>
            shouldMergeAssistantMessageInChannel(existing, candidate, message),
          );
      const merged = idx >= 0
        ? existing.map((candidate, i) => (i === idx ? { ...candidate, ...message } : candidate))
        : assistantCopyIndex >= 0
          ? existing.map((candidate, i) =>
              i === assistantCopyIndex ? mergeAssistantMessageCopies(candidate, message) : candidate,
            )
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
    const researchEvidence = researchEvidenceFromChannelState(state);
    const usage = state.turnUsage;
    if (
      state.streamingText ||
      state.thinkingText ||
      activities.length > 0 ||
      (taskBoard && taskBoard.tasks.length > 0) ||
      researchEvidence
    ) {
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
        researchEvidence,
        citations: state.turnCitations ?? undefined,
        usage,
      });
    }
    set((s) => {
      const nextQueued = { ...s.queuedMessages };
      if (!options?.preserveQueue) {
        delete nextQueued[ch];
      }
      const detachedSubagents = activeSubagentsAfterLocalCancel(state);
      const missions = durableMissionState(state);
      return {
        channelStates: {
          ...s.channelStates,
          [ch]: detachedSubagents.length > 0
            ? { ...DEFAULT_CHANNEL_STATE, ...missions, subagents: detachedSubagents }
            : { ...DEFAULT_CHANNEL_STATE, ...missions },
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
    const researchEvidence = researchEvidenceFromChannelState(state);
    const usage = state.turnUsage;
    const detachedSubagents = activeBackgroundSubagents(state);
    // Keep completed / errored / cancelled child chips visible past turn end
    // (T2 retention), in addition to the still-running background subagents.
    const detachedTaskIds = new Set(detachedSubagents.map((subagent) => subagent.taskId));
    const completedSubagents = retainedCompletedSubagents(state, detachedTaskIds);
    const retainedSubagents = [...detachedSubagents, ...completedSubagents];
    const retainedSubagentProgress = (() => {
      if (retainedSubagents.length === 0) return undefined;
      const retainedTaskIds = new Set(retainedSubagents.map((subagent) => subagent.taskId));
      return Object.fromEntries(
        Object.entries(state.subagentProgress ?? {}).filter(([taskId]) =>
          retainedTaskIds.has(taskId),
        ),
      );
    })();
    const missions = durableMissionState(state);
    {
      const thinkingDuration = state.thinkingStartedAt
        ? Math.round((Date.now() - state.thinkingStartedAt) / 1000)
        : undefined;
      // If no visible text but the stream was active (streaming started),
      // show a fallback so the user doesn't see a vanished response.
      const hadStreamActivity = state.streaming || hasThinking || activities.length > 0;
      const finalContent = hasVisibleText
        ? finalVisibleStreamContent(state, content || "")
        : hadStreamActivity
          ? streamErrorFallback(state)
          : "";
      if (finalContent) {
        // Attach the ordered interleaved segments only when they are a faithful
        // decomposition of finalContent (content-authority check). If a
        // catch-up/error path mutated the visible text, this yields undefined
        // and the message renders via the flat fallback.
        const segments = finalizedSegmentsForMessage(state.segments, finalContent);
        get().addMessage(channel, {
          id: msgId ?? `assistant-${Date.now()}`,
          role: "assistant",
          content: finalContent,
          timestamp: Date.now(),
          thinkingContent: hasThinking ? state.thinkingText : undefined,
          thinkingDuration,
          activities: activities.length > 0 ? activities : undefined,
          ...(segments ? { segments } : {}),
          taskBoard: taskBoard && taskBoard.tasks.length > 0 ? taskBoard : undefined,
          researchEvidence,
          citations: state.turnCitations ?? undefined,
          usage,
        });
      }
    }
    set((s) => ({
      channelStates: {
        ...s.channelStates,
        [channel]: retainedSubagents.length > 0
          ? {
              ...DEFAULT_CHANNEL_STATE,
              ...missions,
              subagents: retainedSubagents,
              ...(retainedSubagentProgress !== undefined
                ? { subagentProgress: retainedSubagentProgress }
                : {}),
            }
          : { ...DEFAULT_CHANNEL_STATE, ...missions },
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
      serverMessages: {
        ...state.serverMessages,
        [channel]: [],
      },
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

  clearSession: (channel, getToken) => {
    // Abort any in-flight stream
    const controller = get().abortControllers[channel];
    if (controller) controller.abort();

    const state = get();

    // New session key so the next turn starts a fresh conversation
    if (state.botId) {
      incrementResetCounter(state.botId, channel, getToken ?? (() => Promise.resolve(null)));
    }

    // Unlike resetSession (which keeps history behind a divider), this fully
    // wipes the channel transcript for callers that want a blank slate.
    const newMessages = { ...state.messages, [channel]: [] };
    persistMessages(state.botId, newMessages);

    set({
      messages: newMessages,
      serverMessages: { ...state.serverMessages, [channel]: [] },
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
      const key = botId ? LAST_READ_KEY(botId) : "clawy:lastRead";
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
        ? (localStorage.getItem(LAST_READ_KEY(botId)) ?? localStorage.getItem("clawy:lastRead"))
        : localStorage.getItem("clawy:lastRead");
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

  startSelectionMode: (channel) =>
    set({
      selectionMode: true,
      selectedMessages: { [channel]: new Set() },
    }),

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

  removeLocalMessages: (channel, msgIds, scope) =>
    set((state) => {
      if (!matchesBotScope(state.botId, scope)) return {};
      const localMsgs = (state.messages[channel] ?? []).filter((m) => !msgIds.has(m.id));
      const newMessages = { ...state.messages, [channel]: localMsgs };
      persistMessages(state.botId, newMessages);
      return { messages: newMessages };
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
      if (SERVER_READABLE_USER_TURN_MARKER_RE.test(row.content)) return {};
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
      const timestamp = Number.isFinite(ts) ? ts : Date.now();
      const msg: ChatMessage = {
        id: row.id || serverId,
        role: row.role === "system" ? "system" : "assistant",
        content: row.content,
        timestamp,
        serverId,
      };
      const optimisticIndex = existingLocal.findIndex((candidate) =>
        shouldMergePushedAssistant(candidate, msg),
      );
      const updated = (optimisticIndex >= 0
        ? existingLocal.map((candidate, index) =>
            index === optimisticIndex ? mergeAssistantMessageCopies(candidate, msg) : candidate,
          )
        : [...existingLocal, msg]
      ).slice(-MAX_LOCAL_MESSAGES);
      const newMessages = { ...state.messages, [channel]: updated };
      persistMessages(state.botId, newMessages);
      return { messages: newMessages };
    }),
}));
