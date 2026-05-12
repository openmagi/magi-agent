"use client";

import { useRef, useEffect, useLayoutEffect, useMemo, useImperativeHandle, forwardRef, useState, useCallback } from "react";
import { MessageBubble } from "./message-bubble";
import { TypingIndicator } from "./typing-indicator";
import { ControlRequestCard } from "./control-request";
import { compareChatMessages } from "@/lib/chat/message-order";
import { shouldPreferServerAssistantMessage } from "@/lib/chat/server-reconcile";
import { stripAssistantMetadataPreamble } from "@/lib/chat/visible-content";
import { deriveWorkConsoleRows, type WorkConsoleRow, type WorkConsoleRowStatus } from "@/lib/chat/work-console";
import { deriveWorkStateSummary } from "@/lib/chat/work-state";
import { dispatchOpenMissionLedgerEvent } from "@/lib/chat/mission-ledger-events";
import type { ReactNode } from "react";
import type {
  ChatMessage,
  ChannelState,
  QueuedMessage,
  ControlRequestRecord,
  ControlRequestResponse,
  ChatResponseLanguage,
  BrowserFrame,
  MissionActivity,
} from "@/lib/chat/types";

export interface ChatMessagesHandle {
  scrollToBottom: () => void;
}

interface ChatMessagesProps {
  messages: ChatMessage[];
  serverMessages: ChatMessage[];
  channelState: ChannelState;
  loading?: boolean;
  botId?: string;
  /** Selection mode */
  selectionMode?: boolean;
  selectedMessages?: Set<string>;
  onToggleSelect?: (msgId: string) => void;
  onEnterSelectionMode?: (msgId: string) => void;
  onSelectAll?: () => void;
  onDeselectAll?: () => void;
  onExportSelected?: () => void;
  onDeleteSelected?: () => void;
  onExitSelectionMode?: () => void;
  /** Load older messages (scroll-up pagination) */
  onLoadOlder?: () => Promise<void>;
  hasOlderMessages?: boolean;
  loadingOlder?: boolean;
  /** Called when the user picks "Reply" from a message's context menu. */
  onReplyTo?: (msg: ChatMessage) => void;
  /** Messages the user has typed while streaming, not yet sent. Greyed bubbles. */
  queuedMessages?: QueuedMessage[];
  /** Cancel a specific queued message. */
  onCancelQueued?: (id: string) => void;
  controlRequests?: ControlRequestRecord[];
  onRespondControlRequest?: (
    request: ControlRequestRecord,
    response: ControlRequestResponse,
  ) => Promise<void> | void;
  uiLanguage?: ChatResponseLanguage;
}

function writingAnswerLabel(language?: ChatResponseLanguage): string {
  return language === "ko" ? "답변 작성 중..." : "Writing answer...";
}

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function t(language: ChatResponseLanguage | undefined, en: string, ko: string): string {
  return isKorean(language) ? ko : en;
}

function waitingCountLabel(count: number, language?: ChatResponseLanguage): string {
  return isKorean(language) ? `${count}개 대기` : `${count} waiting`;
}

function queuedIndexLabel(index: number, language?: ChatResponseLanguage): string {
  return isKorean(language) ? `대기 #${index}` : `Queued #${index}`;
}

function MessageSkeleton() {
  return (
    <div className="space-y-5 py-2">
      {/* Assistant skeleton */}
      <div className="flex justify-start">
        <div className="space-y-2 max-w-[65%]">
          <div className="chat-skeleton-line h-4 w-48" />
          <div className="chat-skeleton-line h-4 w-36" />
        </div>
      </div>
      {/* User skeleton */}
      <div className="flex justify-end">
        <div className="chat-skeleton-line h-4 w-32" />
      </div>
      {/* Assistant skeleton */}
      <div className="flex justify-start">
        <div className="space-y-2 max-w-[65%]">
          <div className="chat-skeleton-line h-4 w-56" />
          <div className="chat-skeleton-line h-4 w-44" />
          <div className="chat-skeleton-line h-4 w-28" />
        </div>
      </div>
    </div>
  );
}

function statusDotClass(status: WorkConsoleRowStatus): string {
  switch (status) {
    case "running":
      return "bg-[#7C3AED]";
    case "done":
      return "bg-emerald-500";
    case "waiting":
      return "bg-amber-500";
    case "error":
      return "bg-red-500";
    case "info":
    default:
      return "bg-secondary/30";
  }
}

function browserActionLabel(action: string, language?: ChatResponseLanguage): string {
  switch (action) {
    case "open":
      return t(language, "Opening page", "페이지 여는 중");
    case "click":
    case "mouse_click":
      return t(language, "Clicking", "클릭 중");
    case "fill":
    case "keyboard_type":
    case "press":
      return t(language, "Typing", "입력 중");
    case "scroll":
      return t(language, "Scrolling", "스크롤 중");
    case "screenshot":
    case "snapshot":
      return t(language, "Inspecting page", "페이지 확인 중");
    case "scrape":
      return t(language, "Reading page", "페이지 읽는 중");
    default:
      return t(language, "Using browser", "브라우저 사용 중");
  }
}

function InlineBrowserFramePreview({
  frame,
  language,
}: {
  frame: BrowserFrame;
  language?: ChatResponseLanguage;
}) {
  const imageSrc = `data:${frame.contentType};base64,${frame.imageBase64}`;
  return (
    <div
      className="mt-2 overflow-hidden rounded-md border border-black/[0.08] bg-white"
      data-chat-inline-browser-frame="true"
    >
      <div className="flex min-w-0 items-center justify-between gap-2 border-b border-black/[0.06] px-2.5 py-1.5">
        <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-secondary/45">
          {t(language, "Live browser", "실시간 브라우저")}
        </span>
        <span className="min-w-0 truncate text-[10.5px] text-secondary/55">
          {browserActionLabel(frame.action, language)}
          {frame.url ? ` · ${frame.url}` : ""}
        </span>
      </div>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={imageSrc}
        alt={t(language, "Browser preview", "브라우저 미리보기")}
        className="block aspect-video w-full max-h-56 bg-black/[0.03] object-contain"
      />
    </div>
  );
}

function hasOpenTaskState(channelState: ChannelState): boolean {
  return !!channelState.taskBoard?.tasks.some(
    (task) => task.status === "pending" || task.status === "in_progress",
  );
}

function isTerminalMission(mission: MissionActivity): boolean {
  return (
    mission.status === "completed" ||
    mission.status === "failed" ||
    mission.status === "cancelled"
  );
}

function currentRunMission(channelState: ChannelState): MissionActivity | null {
  const missions = channelState.missions ?? [];
  const activeGoal = channelState.activeGoalMissionId
    ? missions.find((mission) => mission.id === channelState.activeGoalMissionId)
    : null;
  if (activeGoal && !isTerminalMission(activeGoal)) return activeGoal;
  return missions.find((mission) => !isTerminalMission(mission)) ?? null;
}

function hasInlineRunStatus(
  channelState: ChannelState,
  queuedMessages: QueuedMessage[],
  pendingRequests: ControlRequestRecord[],
): boolean {
  const hasLiveWork =
    (channelState.activeTools ?? []).some((tool) => tool.status === "running") ||
    (channelState.subagents ?? []).some(
      (subagent) => subagent.status === "running" || subagent.status === "waiting",
    ) ||
    hasOpenTaskState(channelState) ||
    (channelState.runtimeTraces ?? []).some((trace) => trace.severity !== "info") ||
    !!channelState.browserFrame ||
    queuedMessages.length > 0 ||
    pendingRequests.length > 0 ||
    channelState.fileProcessing ||
    channelState.reconnecting;

  return hasLiveWork || (channelState.streaming && !channelState.streamingText);
}

const INLINE_WORK_ROW_LIMIT = 4;

function isUsefulSubagentRow(row: WorkConsoleRow): boolean {
  return !row.detail || !/^iteration\s+\d+$/i.test(row.detail.trim());
}

function inlineWorkRows(
  channelState: ChannelState,
  queuedMessages: QueuedMessage[],
  pendingRequests: ControlRequestRecord[],
  language?: ChatResponseLanguage,
): WorkConsoleRow[] {
  const rows = deriveWorkConsoleRows({
    channelState,
    queuedMessages,
    controlRequests: pendingRequests,
    uiLanguage: language,
  });
  const selected: WorkConsoleRow[] = [];
  const appendRows = (items: WorkConsoleRow[]) => {
    for (const item of items) {
      if (selected.length >= INLINE_WORK_ROW_LIMIT) break;
      selected.push(item);
    }
  };

  appendRows(rows.filter((row) => row.group === "control" && row.status === "waiting"));

  if (channelState.browserFrame && selected.length < INLINE_WORK_ROW_LIMIT) {
    appendRows([{
      id: "browser-frame",
      group: "status",
      label: t(language, "Live browser", "실시간 브라우저"),
      detail: [
        browserActionLabel(channelState.browserFrame.action, language),
        channelState.browserFrame.url,
      ].filter(Boolean).join(" - "),
      status: "running",
    }]);
  }

  const toolRows = rows.filter((row) => row.group === "tool").slice(-INLINE_WORK_ROW_LIMIT);
  const traceRows = rows
    .filter((row) => row.group === "trace" && row.status !== "info")
    .slice(-2);
  const taskRows = rows
    .filter((row) => row.group === "task" && (row.status === "running" || row.status === "done"))
    .slice(-2);
  const subagentRows = rows
    .filter(
      (row) =>
        row.group === "subagent" &&
        (row.status === "running" || row.status === "waiting") &&
        isUsefulSubagentRow(row),
    )
    .slice(-2);

  appendRows(subagentRows);
  appendRows(traceRows);
  appendRows(toolRows);
  appendRows(taskRows);

  if (selected.length === 0) {
    appendRows(rows.filter((row) => row.group === "status" && row.id !== "idle"));
  }

  if (selected.length === 0 && queuedMessages.length > 0) {
    appendRows(rows.filter((row) => row.group === "queue").slice(0, 1));
  }

  return selected.slice(0, INLINE_WORK_ROW_LIMIT);
}

function InlineRunStatus({
  channelState,
  queuedMessages,
  pendingRequests,
  uiLanguage,
}: {
  channelState: ChannelState;
  queuedMessages: QueuedMessage[];
  pendingRequests: ControlRequestRecord[];
  uiLanguage?: ChatResponseLanguage;
}) {
  if (!hasInlineRunStatus(channelState, queuedMessages, pendingRequests)) return null;

  const language = uiLanguage ?? channelState.responseLanguage;
  const summary = deriveWorkStateSummary({
    channelState,
    queuedMessages,
    controlRequests: pendingRequests,
    uiLanguage: language,
  });
  const rows = inlineWorkRows(channelState, queuedMessages, pendingRequests, language);
  const genericGoal = t(language, "Working on your request", "요청 처리 중");
  const displayGoal = summary.goal !== genericGoal ? summary.goal : null;
  const mission = currentRunMission(channelState);

  return (
    <div className="chat-msg-in mb-4 flex justify-start" data-chat-inline-run-status="true">
      <div className="w-full max-w-[92%] rounded-lg border border-black/[0.08] bg-white/90 px-3 py-2.5 shadow-sm backdrop-blur sm:max-w-[82%]">
        <div className="flex min-w-0 items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
              {summary.title}
            </div>
            {displayGoal && (
              <div className="mt-1 line-clamp-2 break-words text-[12px] leading-snug text-foreground/70">
                {displayGoal}
              </div>
            )}
            <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-secondary/70">
              <span className="font-medium text-foreground/75">{summary.status}</span>
              {summary.progress && <span>{summary.progress}</span>}
              {queuedMessages.length > 0 && (
                <span>
                  {isKorean(language)
                    ? `${queuedMessages.length}개 대기`
                    : `${queuedMessages.length} queued`}
                </span>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {mission && (
              <button
                type="button"
                onClick={() => dispatchOpenMissionLedgerEvent(mission.id)}
                className="rounded-md border border-[#7C3AED]/15 bg-[#7C3AED]/10 px-2 py-1 text-[10px] font-semibold text-[#6D28D9] transition-colors hover:border-[#7C3AED]/25 hover:bg-[#7C3AED]/15"
                aria-label={`Open Mission Ledger for ${mission.title}`}
                title={t(language, "Open mission ledger", "미션 원장 열기")}
                data-chat-open-mission-ledger={mission.id}
              >
                {t(language, "Open mission ledger", "미션 원장 열기")}
              </button>
            )}
            <span className="rounded-full bg-[#7C3AED]/10 px-2 py-0.5 text-[10px] font-semibold text-[#7C3AED]">
              {t(language, "Live", "실시간")}
            </span>
          </div>
        </div>

        {channelState.browserFrame && (
          <InlineBrowserFramePreview frame={channelState.browserFrame} language={language} />
        )}

        {rows.length > 0 && (
          <ul className="mt-2 space-y-1" aria-label={t(language, "Current work updates", "현재 작업 업데이트")}>
            {rows.map((row) => (
              <li
                key={row.id}
                className="flex min-w-0 items-start gap-2 rounded-md bg-black/[0.025] px-2 py-1.5"
                data-chat-inline-run-row="true"
                data-chat-inline-run-row-status={row.status}
              >
                <span
                  className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${statusDotClass(row.status)}`}
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1">
                  <span className="flex min-w-0 items-baseline gap-2">
                    <span className="min-w-0 truncate text-[12px] font-medium text-foreground/80">
                      {row.label}
                    </span>
                    {row.meta && (
                      <span className="shrink-0 text-[10px] text-secondary/40">{row.meta}</span>
                    )}
                  </span>
                  {row.detail && (
                    <span className="mt-0.5 block truncate text-[11.5px] leading-snug text-secondary/60">
                      {row.detail}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

const TIMESTAMP_DEDUP_WINDOW_MS = 10_000;
const OPTIMISTIC_CONTENT_DEDUP_WINDOW_MS = 5 * 60_000;
const OPTIMISTIC_CONTENT_DEDUP_MIN_CHARS = 80;

function normalizedDuplicateContent(message: ChatMessage): string | null {
  if (message.role === "system") return null;
  const content = message.role === "assistant"
    ? stripAssistantMetadataPreamble(message.content)
    : message.content;
  const normalized = content.replace(/\s+/g, " ").trim();
  if (normalized.length < OPTIMISTIC_CONTENT_DEDUP_MIN_CHARS) return null;
  return normalized;
}

function duplicateContentKey(message: ChatMessage): string | null {
  const normalized = normalizedDuplicateContent(message);
  return normalized ? `${message.role}\u0000${normalized}` : null;
}

export const ChatMessages = forwardRef<ChatMessagesHandle, ChatMessagesProps>(function ChatMessages({ messages, serverMessages, channelState, loading, botId, selectionMode, selectedMessages, onToggleSelect, onEnterSelectionMode, onSelectAll, onDeselectAll, onExportSelected, onDeleteSelected, onExitSelectionMode, onLoadOlder, hasOlderMessages, loadingOlder, onReplyTo, queuedMessages, onCancelQueued, controlRequests, onRespondControlRequest, uiLanguage }, ref) {
  const containerRef = useRef<HTMLDivElement>(null);
  const language = uiLanguage ?? channelState.responseLanguage;
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const userScrolledUp = useRef(false);
  const prevMsgCount = useRef(0);
  const animateFromRef = useRef(0);
  const loadingOlderRef = useRef(false);

  useImperativeHandle(ref, () => ({
    scrollToBottom() {
      userScrolledUp.current = false;
      const el = containerRef.current;
      if (el) {
        requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
      }
    },
  }));

  // Merge local + server messages, dedup by serverId, timestamp proximity,
  // or late-arriving server copies of optimistic streamed assistant messages.
  const allMessages = useMemo(() => {
    if (serverMessages.length === 0) return [...messages].sort(compareChatMessages);
    if (messages.length === 0) return [...serverMessages].sort(compareChatMessages);

    const localMessages = messages.filter((message) => (
      !serverMessages.some((serverMessage) => (
        shouldPreferServerAssistantMessage(
          message,
          serverMessage,
          TIMESTAMP_DEDUP_WINDOW_MS,
        )
      ))
    ));

    // Build set of local serverIds for exact match
    const localServerIds = new Set(localMessages.map((m) => m.serverId).filter(Boolean));

    // Build timestamp index for proximity dedup (same role within 10s = duplicate)
    const localByRole = new Map<string, number[]>();
    const optimisticByContent = new Map<string, number[]>();
    for (const m of localMessages) {
      const ts = m.timestamp ?? 0;
      if (!localByRole.has(m.role)) localByRole.set(m.role, []);
      localByRole.get(m.role)!.push(ts);
      if (!m.serverId) {
        const key = duplicateContentKey(m);
        if (key) {
          if (!optimisticByContent.has(key)) optimisticByContent.set(key, []);
          optimisticByContent.get(key)!.push(ts);
        }
      }
    }

    const filtered = serverMessages.filter((sm) => {
      // Exact serverId match
      if (sm.serverId && localServerIds.has(sm.serverId)) return false;
      // Timestamp proximity: if a local message with same role exists within window, skip
      const smTs = sm.timestamp ?? 0;
      const roleTimes = localByRole.get(sm.role);
      if (roleTimes) {
        for (const lt of roleTimes) {
          if (Math.abs(smTs - lt) < TIMESTAMP_DEDUP_WINDOW_MS) return false;
        }
      }
      const contentKey = duplicateContentKey(sm);
      const contentTimes = contentKey ? optimisticByContent.get(contentKey) : undefined;
      if (contentTimes) {
        for (const lt of contentTimes) {
          if (Math.abs(smTs - lt) < OPTIMISTIC_CONTENT_DEDUP_WINDOW_MS) return false;
        }
      }
      return true;
    });

    return [...localMessages, ...filtered].sort(compareChatMessages);
  }, [messages, serverMessages]);

  // Track which messages should animate (only newly added ones)
  if (allMessages.length > prevMsgCount.current) {
    animateFromRef.current = prevMsgCount.current;
  }
  prevMsgCount.current = allMessages.length;

  const scrollToBottom = useCallback(() => {
    userScrolledUp.current = false;
    setShowScrollBtn(false);
    const el = containerRef.current;
    if (el) {
      requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
    }
  }, []);

  // Track scroll position + load older messages when scrolling near top
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handleScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      const scrolled = distFromBottom > 100;
      userScrolledUp.current = scrolled;
      setShowScrollBtn(scrolled);

      // Load older messages when near top (within 200px)
      if (el.scrollTop < 200 && hasOlderMessages && onLoadOlder && !loadingOlderRef.current) {
        loadingOlderRef.current = true;
        const prevScrollHeight = el.scrollHeight;
        onLoadOlder().then(() => {
          // Preserve scroll position after prepending older messages
          requestAnimationFrame(() => {
            const newScrollHeight = el.scrollHeight;
            el.scrollTop = newScrollHeight - prevScrollHeight;
            loadingOlderRef.current = false;
          });
        }).catch(() => {
          loadingOlderRef.current = false;
        });
      }
    };
    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, [hasOlderMessages, onLoadOlder]);

  // Scroll to bottom before first paint — prevents flash of old messages at top
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  // Auto-scroll on new messages or streaming — use scrollTop, not scrollIntoView
  useEffect(() => {
    if (userScrolledUp.current) return;
    const el = containerRef.current;
    if (!el) return;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }, [allMessages.length, channelState.streamingText, channelState.thinkingText]);

  const pendingControlRequests = useMemo(
    () => (controlRequests ?? []).filter((request) => request.state === "pending"),
    [controlRequests],
  );
  const liveQueuedMessages = queuedMessages ?? [];
  const inlineRunVisible = hasInlineRunStatus(
    channelState,
    liveQueuedMessages,
    pendingControlRequests,
  );

  // Show typing dots only as a fallback. The inline run snapshot now carries
  // active work in the transcript while the right inspector keeps details.
  const showTyping = channelState.streaming && !inlineRunVisible && !channelState.streamingText && !channelState.thinkingText && !channelState.thinkingStartedAt;

  const selectableCount = useMemo(() => {
    return allMessages.filter((m) => m.role !== "system").length;
  }, [allMessages]);

  const selectedCount = selectedMessages?.size ?? 0;
  const allSelected = selectableCount > 0 && selectedCount === selectableCount;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Selection mode bar */}
      {selectionMode && (
        <div className="flex items-center gap-3 px-4 py-2.5 bg-gray-50 border-b border-black/[0.06] shrink-0">
          <button
            onClick={allSelected ? onDeselectAll : onSelectAll}
            className="flex items-center gap-2 text-sm text-secondary/70 hover:text-foreground transition-colors cursor-pointer"
          >
            <div className={`w-4.5 h-4.5 rounded border-2 flex items-center justify-center transition-colors ${
              allSelected ? "bg-[#7C3AED] border-[#7C3AED]" : selectedCount > 0 ? "bg-[#7C3AED]/30 border-[#7C3AED]" : "border-black/20 bg-white"
            }`}>
              {(allSelected || selectedCount > 0) && (
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                  {allSelected ? <polyline points="20 6 9 17 4 12" /> : <line x1="6" y1="12" x2="18" y2="12" />}
                </svg>
              )}
            </div>
            {t(language, "Select all", "전체 선택")}
          </button>
          <span className="text-sm text-secondary/50">
            {isKorean(language) ? `${selectedCount}개 선택됨` : `${selectedCount} selected`}
          </span>
          <div className="flex-1" />
          <button
            onClick={onExportSelected}
            disabled={selectedCount === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-secondary/70 hover:bg-black/[0.04] hover:text-foreground transition-colors cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="18" cy="5" r="3" />
              <circle cx="6" cy="12" r="3" />
              <circle cx="18" cy="19" r="3" />
              <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
              <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
            </svg>
            {t(language, "Export", "내보내기")}
          </button>
          <button
            onClick={onDeleteSelected}
            disabled={selectedCount === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-red-500 hover:bg-red-50 transition-colors cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
            {t(language, "Delete", "삭제")}
          </button>
          <button
            onClick={onExitSelectionMode}
            className="text-sm text-secondary/60 hover:text-foreground transition-colors cursor-pointer"
          >
            {t(language, "Cancel", "취소")}
          </button>
        </div>
      )}

    <div
      ref={containerRef}
      className="flex-1 overflow-y-auto overflow-x-hidden px-3 sm:px-4 md:px-8 lg:px-12 py-6 chat-channel-fade"
      style={{ overflowAnchor: "auto" }}
    >
      <div className="max-w-5xl mx-auto">
        {/* Loading older messages spinner */}
        {loadingOlder && (
          <div className="flex justify-center py-3">
            <div className="w-5 h-5 border-2 border-black/10 border-t-black/40 rounded-full animate-spin" />
          </div>
        )}

        {loading && (
          <MessageSkeleton />
        )}

        {!loading && allMessages.length === 0 && !channelState.streaming && (
          <div className="flex flex-col items-center justify-center h-full min-h-[200px] gap-2">
            <div className="w-10 h-10 rounded-full bg-black/[0.04] flex items-center justify-center">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-secondary/60">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z" />
              </svg>
            </div>
            <p className="text-secondary/50 text-sm">Start a conversation</p>
          </div>
        )}

        {/* Mid-turn steering messages belong at the assistant text offset
            that was visible when the user sent them. Render the assistant
            answer in segments so the transcript chronology matches that. */}
        {!loading && (() => {
          const streamingNow = !!channelState.streaming;
          let mainMessages = allMessages;
          let midTurnInjected: typeof allMessages = [];
          if (streamingNow) {
            let splitAt = allMessages.length;
            for (let i = allMessages.length - 1; i >= 0; i--) {
              if (allMessages[i].injected) splitAt = i;
              else break;
            }
            if (splitAt < allMessages.length) {
              mainMessages = allMessages.slice(0, splitAt);
              midTurnInjected = allMessages.slice(splitAt);
            }
          }
          const animationProps = (index: number) => ({
            className: index >= animateFromRef.current ? "chat-msg-in" : "",
            style: index >= animateFromRef.current
              ? { animationDelay: `${Math.min((index - animateFromRef.current) * 30, 150)}ms` }
              : undefined,
          });
          const renderMessage = (
            msg: typeof allMessages[number],
            i: number,
            offset: number,
            key = msg.id,
          ) => (
            <div
              key={key}
              {...animationProps(i + offset)}
            >
              <MessageBubble
                role={msg.role}
                content={msg.content}
                timestamp={msg.timestamp}
                thinkingContent={msg.thinkingContent}
                thinkingDuration={msg.thinkingDuration}
                activities={msg.activities}
                taskBoard={msg.taskBoard}
                researchEvidence={msg.researchEvidence}
                usage={msg.usage}
                botId={botId}
                replyTo={msg.replyTo}
                injected={msg.injected}
                selectionMode={selectionMode}
                selected={selectedMessages?.has(msg.id)}
                onSelect={() => onToggleSelect?.(msg.id)}
                onContextAction={(action) => {
                  if (action === "select") onEnterSelectionMode?.(msg.id);
                  else if (action === "reply") onReplyTo?.(msg);
                }}
              />
            </div>
          );

          const renderAssistantChunk = (
            key: string,
            content: string,
            index: number,
            options: {
              timestamp?: number;
              isStreaming?: boolean;
              sourceMessage?: ChatMessage;
              includeSourceMeta?: boolean;
            } = {},
          ) => {
            if (!content) return null;
            const source = options.sourceMessage;
            return (
              <div key={key} {...animationProps(index)}>
                <MessageBubble
                  role="assistant"
                  content={content}
                  timestamp={options.timestamp}
                  isStreaming={options.isStreaming}
                  thinkingContent={options.includeSourceMeta ? source?.thinkingContent : undefined}
                  thinkingDuration={options.includeSourceMeta ? source?.thinkingDuration : undefined}
                  activities={options.includeSourceMeta ? source?.activities : undefined}
                  taskBoard={options.includeSourceMeta ? source?.taskBoard : undefined}
                  researchEvidence={options.includeSourceMeta ? source?.researchEvidence : undefined}
                  usage={options.includeSourceMeta ? source?.usage : undefined}
                  botId={botId}
                  selectionMode={source ? selectionMode : undefined}
                  selected={source ? selectedMessages?.has(source.id) : undefined}
                  onSelect={source ? () => onToggleSelect?.(source.id) : undefined}
                  onContextAction={source
                    ? (action) => {
                        if (action === "select") onEnterSelectionMode?.(source.id);
                        else if (action === "reply") onReplyTo?.(source);
                      }
                    : undefined}
                />
              </div>
            );
          };

          const anchoredInjected = (items: typeof allMessages) =>
            items
              .filter((msg) => msg.role === "user" && msg.injected && typeof msg.injectedAfterChars === "number")
              .map((msg, order) => ({
                msg,
                order,
                anchor: Math.max(0, Math.floor(msg.injectedAfterChars ?? 0)),
              }))
              .sort((a, b) => a.anchor - b.anchor || a.order - b.order);

          const renderAssistantWithInjected = (
            content: string,
            injectedMessages: typeof allMessages,
            keyPrefix: string,
            baseIndex: number,
            options: {
              timestamp?: number;
              isStreaming?: boolean;
              sourceMessage?: ChatMessage;
            } = {},
          ): ReactNode[] => {
            const anchored = anchoredInjected(injectedMessages);
            if (anchored.length === 0) {
              const node = renderAssistantChunk(
                `${keyPrefix}:assistant`,
                content,
                baseIndex,
                {
                  timestamp: options.timestamp,
                  isStreaming: options.isStreaming,
                  sourceMessage: options.sourceMessage,
                  includeSourceMeta: true,
                },
              );
              return node ? [node] : [];
            }

            const nodes: ReactNode[] = [];
            let cursor = 0;
            let chunkCount = 0;
            for (const { msg, anchor } of anchored) {
              const splitAt = Math.min(anchor, content.length);
              const chunk = content.slice(cursor, splitAt);
              const chunkNode = renderAssistantChunk(
                `${keyPrefix}:assistant:${chunkCount}`,
                chunk,
                baseIndex + nodes.length,
                {
                  timestamp: options.timestamp,
                  sourceMessage: options.sourceMessage,
                  includeSourceMeta: chunkCount === 0,
                },
              );
              if (chunkNode) {
                nodes.push(chunkNode);
                chunkCount += 1;
              }
              nodes.push(renderMessage(msg, nodes.length, baseIndex, `${keyPrefix}:inject:${msg.id}`));
              cursor = splitAt;
            }

            const tail = content.slice(cursor);
            const tailNode = renderAssistantChunk(
              `${keyPrefix}:assistant:${chunkCount}`,
              tail,
              baseIndex + nodes.length,
              {
                timestamp: options.timestamp,
                isStreaming: options.isStreaming,
                sourceMessage: options.sourceMessage,
                includeSourceMeta: chunkCount === 0,
              },
            );
            if (tailNode) nodes.push(tailNode);
            return nodes;
          };

          const renderFinalizedMessages = (items: typeof allMessages): ReactNode[] => {
            const nodes: ReactNode[] = [];
            let pendingInjected: typeof allMessages = [];

            for (const [index, msg] of items.entries()) {
              if (msg.role === "user" && msg.injected && typeof msg.injectedAfterChars === "number") {
                pendingInjected.push(msg);
                continue;
              }

              if (msg.role === "assistant" && pendingInjected.length > 0) {
                nodes.push(
                  ...renderAssistantWithInjected(
                    msg.content,
                    pendingInjected,
                    `final:${msg.id}`,
                    index,
                    {
                      timestamp: msg.timestamp,
                      sourceMessage: msg,
                    },
                  ),
                );
                pendingInjected = [];
                continue;
              }

              if (pendingInjected.length > 0) {
                nodes.push(
                  ...pendingInjected.map((pending, pendingIndex) =>
                    renderMessage(
                      pending,
                      pendingIndex,
                      Math.max(0, index - pendingInjected.length),
                      `pending-injected:${pending.id}`,
                    ),
                  ),
                );
                pendingInjected = [];
              }

              nodes.push(renderMessage(msg, index, 0));
            }

            if (pendingInjected.length > 0) {
              nodes.push(
                ...pendingInjected.map((pending, pendingIndex) =>
                  renderMessage(pending, pendingIndex, items.length, `pending-injected:${pending.id}`),
                ),
              );
            }

            return nodes;
          };

          const anchoredMidTurnInjected = midTurnInjected.filter(
            (msg) => typeof msg.injectedAfterChars === "number",
          );
          const unanchoredMidTurnInjected = midTurnInjected.filter(
            (msg) => typeof msg.injectedAfterChars !== "number",
          );

          return (
            <>
              {renderFinalizedMessages(mainMessages)}

              {/* Loading indicator between thinking completion and first text delta */}
              {channelState.streaming &&
                !channelState.streamingText &&
                channelState.thinkingStartedAt !== null &&
                channelState.thinkingText !== "" && (
                <div className="chat-msg-in flex justify-start mb-4">
                  <div className="w-full max-w-full py-1 text-sm text-secondary/50 animate-pulse">
                    {writingAnswerLabel(language)}
                  </div>
                </div>
              )}

              {channelState.streamingText &&
                (anchoredMidTurnInjected.length > 0 ? (
                  renderAssistantWithInjected(
                    channelState.streamingText,
                    anchoredMidTurnInjected,
                    "live",
                    mainMessages.length,
                    { isStreaming: true },
                  )
                ) : (
                  <MessageBubble
                    role="assistant"
                    content={channelState.streamingText}
                    isStreaming
                    botId={botId}
                  />
                ))}

              {!channelState.streamingText &&
                anchoredMidTurnInjected.map((msg, i) =>
                  renderMessage(msg, i, mainMessages.length, `pending-injected:${msg.id}`),
                )}

              {unanchoredMidTurnInjected.map((msg, i) =>
                renderMessage(msg, i, mainMessages.length),
              )}

              <InlineRunStatus
                channelState={channelState}
                queuedMessages={liveQueuedMessages}
                pendingRequests={pendingControlRequests}
                uiLanguage={language}
              />
            </>
          );
        })()}

        {!loading && pendingControlRequests.length > 0 && (
          <div className="mt-2">
            {pendingControlRequests.map((request) => (
              <ControlRequestCard
                key={request.requestId}
                request={request}
                onRespond={(req, response) =>
                  onRespondControlRequest?.(req, response)
                }
              />
            ))}
          </div>
        )}

        {!loading && showTyping && !channelState.fileProcessing && (
          <div className="chat-msg-in">
            <TypingIndicator />
          </div>
        )}

        {/* Queued user messages (Claude Code CLI-style). Kept visually
            distinct from sent messages so pending follow-ups are obvious. */}
        {!loading && queuedMessages && queuedMessages.length > 0 && (
          <div className="mt-3 flex justify-end">
            <div className="w-full max-w-[92%] sm:max-w-[82%] rounded-2xl border border-amber-500/25 bg-amber-50 px-3 py-2 shadow-[0_1px_8px_rgba(245,158,11,0.10)]">
              <div className="mb-1.5 flex items-center justify-between gap-2 text-[10px] font-semibold uppercase tracking-wide text-amber-800/70">
                <span>{t(language, "Queued follow-ups", "대기 중인 후속 메시지")}</span>
                <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[9px] text-amber-800">
                  {waitingCountLabel(queuedMessages.length, language)}
                </span>
              </div>
              <div className="space-y-1.5">
                {queuedMessages.map((q, index) => (
                  <div key={q.id} className="flex justify-end">
                    <div
                      className="group w-full rounded-xl border border-dashed border-amber-500/25 bg-white/75 px-3 py-2 text-left text-[13px] text-foreground/75"
                      data-chat-queued-card="true"
                    >
                      <div className="flex items-start gap-3">
                        <div className="min-w-0 flex-1">
                          <span className="mb-0.5 flex items-center justify-between gap-2 text-[10px] font-semibold uppercase tracking-wide text-amber-800/70">
                            <span>{queuedIndexLabel(index + 1, language)}</span>
                            <span className="normal-case tracking-normal">
                              {t(language, "Waiting for current run", "현재 실행 대기 중")}
                            </span>
                          </span>
                          <span className="block whitespace-pre-wrap break-words">{q.content}</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => onCancelQueued?.(q.id)}
                          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-red-500/15 bg-red-500/10 text-lg font-semibold leading-none text-red-600 transition-colors hover:border-red-500/35 hover:bg-red-500/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/70 focus-visible:ring-offset-2"
                          aria-label={t(language, `Cancel queued follow-up #${index + 1}`, `대기 중인 후속 메시지 #${index + 1} 취소`)}
                          title={t(language, "Cancel queued follow-up", "대기 중인 후속 메시지 취소")}
                          data-chat-queued-cancel="true"
                        >
                          <span aria-hidden="true">×</span>
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Anchor element for overflow-anchor */}
        <div style={{ overflowAnchor: "auto", height: 1 }} />
      </div>

      {/* Scroll to bottom button — sticky so it floats at bottom of visible scroll area */}
      {showScrollBtn && (
        <div className="sticky bottom-4 flex justify-center z-10 pointer-events-none">
          <button
            onClick={scrollToBottom}
            className="pointer-events-auto flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/90 border border-black/10 shadow-lg backdrop-blur-sm text-xs font-medium text-secondary/80 hover:bg-white transition-all cursor-pointer"
            aria-label="Scroll to bottom"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 5v14M19 12l-7 7-7-7" />
            </svg>
            <span>New messages</span>
          </button>
        </div>
      )}
    </div>
    </div>
  );
});
