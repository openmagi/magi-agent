"use client";

import { useEffect, useCallback, useLayoutEffect, useMemo, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { getLocalAccessToken } from "@/lib/local-auth";
const usePrivy = () => ({ getAccessToken: getLocalAccessToken, user: { id: "local" } });
import { ChatSidebar } from "@/components/chat/chat-sidebar";
import { ChatMessages } from "@/components/chat/chat-messages";
import type { ChatMessagesHandle } from "@/components/chat/chat-messages";
import { ChatInput } from "@/components/chat/chat-input";
import type {
  ChatInputCustomSkill,
  ChatInputHandle,
  ChatInputSendOptions,
} from "@/components/chat/chat-input";
import { ChatModelPicker } from "@/components/chat/chat-model-picker";
import { KbContextBar } from "@/components/chat/kb-context-bar";
import { KbSidePanel } from "@/components/chat/kb-side-panel";
import { useChatStore, syncResetCounters } from "@/lib/chat/chat-store";
import { subscribeToPushMessages } from "@/lib/chat/push-realtime";
import * as chatApi from "@/lib/chat/chat-client";
import { setChatTokenGetter } from "@/lib/chat/chat-client";
import { setAttachmentTokenGetter } from "@/lib/chat/attachments";
import { buildReplyPreview } from "@/lib/chat/attachment-marker";
import { getNextChannelAfterDeletion } from "@/lib/chat/channel-navigation";
import { useE2EE } from "@/lib/chat/use-e2ee";
import { mergeChatHistoryPage } from "@/lib/chat/history-merge";
import { persistUserHistoryMessage } from "@/lib/chat/user-history-persistence";
import {
  researchEvidenceFromChannelState,
  researchEvidenceFromServerMessage,
  stripResearchEvidenceMarker,
} from "@/lib/chat/research-evidence";
import { applyMissionEvent } from "@/lib/chat/missions";
import {
  findLatestAssistantServerMessage,
  shouldPatchAssistantTextFromServer,
} from "@/lib/chat/server-reconcile";
import {
  channelStateFromActiveSnapshot,
  isLiveActiveSnapshot,
} from "@/lib/chat/active-snapshot";
import { shouldHandlePageFileDrop } from "@/lib/chat/file-drop";
import type {
  Channel,
  ChannelMemoryMode,
  ChatMessage,
  InspectedSource,
  KbDocReference,
  QueuedMessage,
  ReplyTo,
  ServerMessage,
} from "@/lib/chat/types";
import { useKbDocs } from "@/hooks/use-kb-docs";
import { MAX_QUEUED_MESSAGES } from "@/lib/chat/queue-constants";
import { getStreamingSendMode, type StreamingComposerMode } from "@/lib/chat/send-policy";
import {
  buildEscCancelDecision,
  cancelActiveTurnWithQueueHandoff,
} from "@/lib/chat/interrupt-handoff";
import { buildMessageContentWithKbContext, mergeKbDocReferences } from "@/lib/chat/kb-send";
import { detectMessageResponseLanguage } from "@/lib/chat/message-language";
import { shouldRetryEmptyCompletion } from "@/lib/chat/empty-response";
import {
  buildChatExportFilename,
  buildChatExportMarkdown,
  normalizeSelectedChatExportMessages,
} from "@/lib/chat/export";
import type { ChatExportMessage } from "@/lib/chat/export";
import { kbUploadKey, uploadChatFilesToKb, splitImageAndOtherFiles, uploadImagesAsAttachmentMarkers } from "@/lib/chat/kb-uploads";
import type { PendingKbUpload } from "@/lib/chat/kb-uploads";
import {
  channelModelSelectionFromChannel,
  channelModelSelectionToRuntimeModel,
  getChannelModelSelection,
  setChannelModelSelection,
  type ChannelModelSelection,
} from "@/lib/chat/channel-model-selection";
import { useI18n } from "@/lib/i18n";
import { useMessages } from "@/lib/i18n";
import { localizeChannel } from "@/lib/chat/channel-i18n";
import { formatChannelBaseLabel, formatChannelMemoryLabel } from "@/lib/chat/channel-memory-mode";
import { StepTelegram } from "@/components/onboarding/step-telegram";
import { useWorkspaceFiles } from "@/hooks/use-workspace-files";

interface ChatViewClientProps {
  botId: string;
  botName: string;
  botStatus: string;
  modelSelection: string;
  apiKeyMode: string;
  subscriptionPlan: string | null;
  bots: { id: string; name: string; status: string }[];
  maxBots: number;
  initialChannel: string;
  routerType: string | null;
  telegramBotUsername: string | null;
  telegramOwnerId: number | null;
}

const DEFAULT_CATEGORIES = ["General"];
const MAX_INSPECTED_SOURCES = 50;

function appendInspectedSource(
  existing: InspectedSource[] | undefined,
  source: InspectedSource,
): InspectedSource[] {
  const deduped = (existing ?? []).filter((item) => item.sourceId !== source.sourceId);
  return [...deduped, source].slice(-MAX_INSPECTED_SOURCES);
}

function getProvisioningStepLabel(step: string | null, t: ReturnType<typeof useMessages>): string {
  if (!step) return t.onboarding.settingUp;
  if (step.includes("namespace") || step.includes("volume") || step.includes("secrets"))
    return t.onboarding.provisioningStepCreatingResources;
  if (step.includes("network") || step.includes("template") || step.includes("dynamic") || step.includes("config"))
    return t.onboarding.provisioningStepConfiguringBot;
  if (step.includes("skill") || step.includes("specialist") || step.includes("lifecycle") || step.includes("pod") || step.includes("Creating pod"))
    return t.onboarding.provisioningStepStartingServices;
  if (step.includes("waiting") || step.includes("container"))
    return t.onboarding.provisioningStepConnecting;
  return t.onboarding.settingUp;
}

function formatChatCopy(template: string, values: Record<string, string | number>): string {
  return Object.entries(values).reduce(
    (text, [key, value]) => text.replaceAll(`{${key}}`, String(value)),
    template,
  );
}

const RETRYABLE_PATTERN =
  /gateway|pod.*not.*available|503|502|504|429|529|rate.?limit|overload|temporarily busy|timeout|network|ECONNR/i;
const MAX_RETRIES = 8;
const RETRY_DELAY_MS = 5_000;
const POLL_INTERVAL_MS = 3_000;
const ASSISTANT_CATCHUP_LIMIT = 10;
const ASSISTANT_CATCHUP_ATTEMPTS = 3;
const ASSISTANT_CATCHUP_RETRY_MS = 750;
const ASSISTANT_CATCHUP_PAST_WINDOW_MS = 15_000;
const ASSISTANT_CATCHUP_FUTURE_WINDOW_MS = 60_000;
const HISTORY_PREVIEW_LIMIT = 5;
const HISTORY_PAGE_SIZE = 100;
const HISTORY_AUTO_BACKFILL_PAGES = 4;
const CUSTOM_CATEGORIES_KEY = (botId: string) => `clawy:customCategories:${botId}`;
const CHANNELS_CACHE_KEY = (botId: string) => `clawy:channels:${botId}`;

const TELEGRAM_BANNER_KEY = (botId: string) => `clawy:telegramBannerDismissed:${botId}`;

interface ChatExportDraft {
  channelName: string;
  title: string;
  filename: string;
  markdown: string;
  messages: ChatExportMessage[];
}

function mapServerMessages(msgs: ServerMessage[]): ChatMessage[] {
  return msgs.map((m) => ({
    id: m.id,
    role: m.role === "system" ? "assistant" : m.role,
    content: stripResearchEvidenceMarker(m.content),
    timestamp: new Date(m.created_at).getTime(),
    serverId: m.id,
    researchEvidence: researchEvidenceFromServerMessage(m),
  }));
}

function mergeFetchedServerMessages(botId: string, channel: string, msgs: ServerMessage[]): void {
  if (msgs.length === 0) return;
  const last = msgs[msgs.length - 1];
  if (!last) return;
  const mapped = mapServerMessages(msgs);
  const prev = useChatStore.getState().serverMessages[channel] ?? [];
  const serverIds = new Set(mapped.map((m) => m.serverId));
  const kept = prev.filter((m) => !m.serverId || !serverIds.has(m.serverId));
  const merged = [...kept, ...mapped].sort((a, b) => a.timestamp - b.timestamp);
  useChatStore.getState().setServerMessages(channel, merged, { botId });
  useChatStore
    .getState()
    .setLastServerFetch(channel, last.created_at, { botId });
}

export function ChatViewClient({
  botId,
  botName,
  botStatus,
  modelSelection,
  apiKeyMode,
  routerType,
  subscriptionPlan,
  bots,
  maxBots,
  initialChannel,
  telegramBotUsername,
  telegramOwnerId,
}: ChatViewClientProps) {
  const router = useRouter();
  const { getAccessToken, ready, authenticated, logout } = usePrivy();
  const { locale } = useI18n();
  const t = useMessages();
  const store = useChatStore();
  const { ready: e2eeReady, saveMessages, loadMessages, deleteMessages } = useE2EE(botId);
  const [editing, setEditing] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [exportDraft, setExportDraft] = useState<ChatExportDraft | null>(null);
  const [exportLink, setExportLink] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [creatingExportLink, setCreatingExportLink] = useState(false);
  const [undoData, setUndoData] = useState<{ channel: string; messages: import("@/lib/chat/types").ChatMessage[]; serverMessages: import("@/lib/chat/types").ChatMessage[] } | null>(null);
  const undoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [hasOlderMessages, setHasOlderMessages] = useState(false);
  const [initialHistoryLoading, setInitialHistoryLoading] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [customCategories, setCustomCategories] = useState<string[]>([]);
  const [customSkills, setCustomSkills] = useState<ChatInputCustomSkill[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showTelegramGuide, setShowTelegramGuide] = useState(false);
  const [escArmedUntil, setEscArmedUntil] = useState<number | null>(null);
  const [streamingComposerMode, setStreamingComposerMode] = useState<StreamingComposerMode>("queue");
  const [currentBotStatus, setCurrentBotStatus] = useState(botStatus);
  const [provisioningStep, setProvisioningStep] = useState<string | null>(null);
  const [provisioningPct, setProvisioningPct] = useState(0);
  const provisionStartRef = useRef<number>(Date.now());
  const chatMessagesRef = useRef<ChatMessagesHandle>(null);
  const chatInputRef = useRef<ChatInputHandle>(null);
  const interruptHandoffChannelsRef = useRef(new Set<string>());
  const olderHistoryCursorRef = useRef<Record<string, string | null>>({});
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const dragCounterRef = useRef(0);
  /** Active reply target for the current composer. Cleared on channel switch, send, or manual cancel. */
  const [replyingTo, setReplyingTo] = useState<ReplyTo | null>(null);
  const currentChannels = store.channels;

  // --- KB Context Picker ---
  const { collections: kbCollections, allDocs: kbAllDocs, loading: kbLoading, refreshing: kbRefreshing, refresh: kbRefresh } = useKbDocs(botId);
  const {
    files: workspaceFiles,
    loading: workspaceLoading,
    refreshing: workspaceRefreshing,
    refresh: workspaceRefresh,
  } = useWorkspaceFiles(botId);
  const [selectedKbDocs, setSelectedKbDocs] = useState<KbDocReference[]>([]);
  const [uploadStates, setUploadStates] = useState<Record<string, PendingKbUpload>>({});
  const [preparedUploadRefs, setPreparedUploadRefs] = useState<Record<string, KbDocReference>>({});
  const fallbackChannelModelSelection = useMemo<ChannelModelSelection>(() => ({
    modelSelection,
    routerType: routerType ?? "standard",
  }), [modelSelection, routerType]);
  const [channelModelSelection, setChannelModelSelectionState] =
    useState<ChannelModelSelection>(fallbackChannelModelSelection);
  const serverBackedChannelModelSelection = useCallback(
    (channelName: string): ChannelModelSelection => {
      const channel = currentChannels.find((candidate) => candidate.name === channelName);
      return channelModelSelectionFromChannel(channel) ?? fallbackChannelModelSelection;
    },
    [currentChannels, fallbackChannelModelSelection],
  );
  const resolveChannelRuntimeModel = useCallback(
    (channelName: string, queuedModelOverride?: string) => {
      if (queuedModelOverride) return queuedModelOverride;
      return channelModelSelectionToRuntimeModel(
        getChannelModelSelection(botId, channelName, serverBackedChannelModelSelection(channelName)),
      );
    },
    [botId, serverBackedChannelModelSelection],
  );
  const isCurrentBot = useCallback(() => useChatStore.getState().botId === botId, [botId]);

  const handleToggleKbDoc = useCallback((doc: KbDocReference) => {
    setSelectedKbDocs((prev) => {
      const exists = prev.some((d) => d.id === doc.id);
      return exists ? prev.filter((d) => d.id !== doc.id) : [...prev, doc];
    });
  }, []);

  const handleRemoveKbDoc = useCallback((docId: string) => {
    setSelectedKbDocs((prev) => prev.filter((d) => d.id !== docId));
  }, []);

  const clearAcceptedKbContext = useCallback(() => {
    setSelectedKbDocs((prev) => (prev.length === 0 ? prev : []));
  }, []);

  const handleUploadUpdate = useCallback((update: PendingKbUpload) => {
    setUploadStates((prev) => ({
      ...prev,
      [update.key]: update,
    }));
    if (update.phase === "ready" && update.ref) {
      setPreparedUploadRefs((prev) => ({
        ...prev,
        [update.key]: update.ref!,
      }));
    }
  }, []);

  const resolveKbDocsForFiles = useCallback(async (files?: File[]): Promise<KbDocReference[]> => {
    if (!files || files.length === 0) return [];

    const existingRefs = files
      .map((file) => preparedUploadRefs[kbUploadKey(file)])
      .filter((ref): ref is KbDocReference => !!ref);
    const missingFiles = files.filter((file) => !preparedUploadRefs[kbUploadKey(file)]);

    if (missingFiles.length === 0) return existingRefs;

    const uploadedRefs = await uploadChatFilesToKb(botId, missingFiles, handleUploadUpdate);
    kbRefresh();
    return mergeKbDocReferences(existingRefs, uploadedRefs);
  }, [botId, handleUploadUpdate, kbRefresh, preparedUploadRefs]);

  const handleReplyTo = useCallback((msg: ChatMessage) => {
    if (msg.role !== "user" && msg.role !== "assistant") return;
    const preview = buildReplyPreview(msg.content);
    if (!preview) return;
    setReplyingTo({
      messageId: msg.serverId ?? msg.id,
      preview,
      role: msg.role,
    });
    // Focus composer for quick typing (best-effort).
    chatInputRef.current?.focus();
  }, []);

  const mergeE2EEMessages = useCallback((channel: string, msgs: ChatMessage[]) => {
    useChatStore.setState((state) => ({
      ...(state.botId === botId
        ? {
            messages: {
              ...state.messages,
              [channel]: mergeChatHistoryPage(state.messages[channel] ?? [], msgs),
            },
          }
        : {}),
    }));
  }, [botId]);

  const handleUserHistorySaveError = useCallback((channel: string, err: unknown) => {
    console.error("[chat] failed to save user message after retry:", err);
    useChatStore.getState().setChannelState(channel, {
      saveError: locale === "ko"
        ? "메시지 저장 실패 — 새로고침 후 대화가 사라질 수 있습니다."
        : "Failed to save message — conversation may be lost after refresh.",
    }, { botId });
  }, [botId, locale]);

  const handleCancelReply = useCallback(() => setReplyingTo(null), []);

  // Clear reply target when the user switches channels.
  useEffect(() => {
    setReplyingTo(null);
  }, [initialChannel]);

  const handleChatDrop = useCallback((e: React.DragEvent) => {
    const shouldAttachFiles = shouldHandlePageFileDrop(e);
    dragCounterRef.current = 0;
    setIsDraggingOver(false);
    if (shouldAttachFiles && e.dataTransfer.files.length > 0) {
      chatInputRef.current?.addFiles(e.dataTransfer.files);
    }
  }, []);

  const handleChatDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);

  const handleChatDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragCounterRef.current += 1;
    if (e.dataTransfer.types.includes("Files")) {
      setIsDraggingOver(true);
    }
  }, []);

  const handleChatDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current === 0) {
      setIsDraggingOver(false);
    }
  }, []);

  // Telegram banner: show if not fully connected and not dismissed
  const telegramNotConnected = !telegramBotUsername || !telegramOwnerId;
  const [bannerDismissed, setBannerDismissed] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      return localStorage.getItem(TELEGRAM_BANNER_KEY(botId)) === "1";
    } catch { return false; }
  });
  const showTelegramBanner = telegramNotConnected && !bannerDismissed;

  const handleDismissBanner = useCallback(() => {
    setBannerDismissed(true);
    try { localStorage.setItem(TELEGRAM_BANNER_KEY(botId), "1"); } catch { /* ignore */ }
  }, [botId]);

  // Load custom categories from localStorage
  useEffect(() => {
    try {
      const raw = localStorage.getItem(CUSTOM_CATEGORIES_KEY(botId));
      if (raw) setCustomCategories(JSON.parse(raw) as string[]);
    } catch { /* ignore */ }
  }, [botId]);

  // Set token getter whenever it changes
  useEffect(() => {
    setChatTokenGetter(getAccessToken);
    setAttachmentTokenGetter(getAccessToken);
  }, [getAccessToken]);

  useEffect(() => {
    if (!ready || !authenticated) {
      setCustomSkills([]);
      return;
    }
    let cancelled = false;
    setCustomSkills([]);

    (async () => {
      const token = await getAccessToken().catch(() => null);
      if (!token || cancelled) return;
      const res = await fetch(`/api/bots/${botId}/custom-skills`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok || cancelled) return;
      const json = (await res.json()) as { skills?: ChatInputCustomSkill[] };
      if (!cancelled) setCustomSkills(Array.isArray(json.skills) ? json.skills : []);
    })().catch(() => {
      if (!cancelled) setCustomSkills([]);
    });

    return () => {
      cancelled = true;
    };
  }, [authenticated, botId, getAccessToken, ready]);

  // Poll provisioning status
  useEffect(() => {
    if (currentBotStatus !== "provisioning") return;
    const PROVISION_ESTIMATE_MS = 180_000;

    const tick = () => {
      const elapsed = Date.now() - provisionStartRef.current;
      setProvisioningPct(Math.min((elapsed / PROVISION_ESTIMATE_MS) * 95, 95));
    };
    tick();
    const progressTimer = setInterval(tick, 1_000);

    const poll = async () => {
      try {
        const token = await getAccessToken();
        const res = await fetch(`/api/bots/${botId}/status`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data.provisioningStep) setProvisioningStep(data.provisioningStep);
        if (data.status === "active") {
          setCurrentBotStatus("active");
          setProvisioningPct(100);
        }
      } catch { /* retry next interval */ }
    };
    poll();
    const pollTimer = setInterval(poll, 5_000);

    return () => {
      clearInterval(progressTimer);
      clearInterval(pollTimer);
    };
  }, [currentBotStatus, botId, getAccessToken]);

  // Set active channel — runs on every channel navigation
  useEffect(() => {
    store.setActiveChannel(initialChannel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialChannel]);

  // Initialize store + fetch channels only after Privy is ready
  useEffect(() => {
    if (!ready || !authenticated) return;
    store.setBotId(botId);
    // Re-apply initialChannel after setBotId (which resets to "general")
    store.setActiveChannel(initialChannel);

    // 1. Instant: load cached channels from localStorage
    const cached = useChatStore.getState().channels;
    if (cached.length === 0) {
      try {
        const raw = localStorage.getItem(CHANNELS_CACHE_KEY(botId));
        if (raw) {
          const parsed = JSON.parse(raw) as Channel[];
          if (parsed.length > 0) store.setChannels(parsed, { botId });
        }
      } catch { /* ignore */ }
    }

    // 2. Sync reset counters from server (for cross-device session sync)
    syncResetCounters(botId, getAccessToken).catch(() => {});

    // 3. Background: fetch fresh channels from API
    chatApi
      .fetchChannels(botId)
      .then((chs) => {
        store.setChannels(chs, { botId });
        try {
          localStorage.setItem(CHANNELS_CACHE_KEY(botId), JSON.stringify(chs));
        } catch { /* ignore */ }
      })
      .catch((err) => {
        console.error("[chat] Failed to load channels:", err);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId, ready, authenticated]);

  // Load E2EE messages from Supabase on channel switch — E2EE is source of truth
  // Two-phase load: Phase 1 fetches latest 5 messages for instant display,
  // Phase 2 backfills the remaining history so user sees recent messages immediately.
  useLayoutEffect(() => {
    if (!e2eeReady || !initialChannel) {
      setInitialHistoryLoading(false);
      return;
    }
    let cancelled = false;
    setInitialHistoryLoading(true);
    setHasOlderMessages(false);
    olderHistoryCursorRef.current[initialChannel] = null;

    const loadInitialHistory = async () => {
      try {
        try {
          // Phase 1: latest 5 messages (fast — small payload, quick decrypt)
          const { messages: latestMsgs } = await loadMessages(initialChannel, undefined, HISTORY_PREVIEW_LIMIT, { latest: true });
          if (cancelled || !isCurrentBot()) return;
          if (latestMsgs.length > 0) mergeE2EEMessages(initialChannel, latestMsgs);
        } finally {
          if (!cancelled && isCurrentBot()) {
            setInitialHistoryLoading(false);
          }
        }

        // Phase 2: latest history page.
        const firstPage = await loadMessages(initialChannel, undefined, HISTORY_PAGE_SIZE, { latest: true });
        if (cancelled || !isCurrentBot()) return;
        if (firstPage.messages.length > 0) mergeE2EEMessages(initialChannel, firstPage.messages);

        let cursor = firstPage.hasMore ? firstPage.nextBefore : null;
        olderHistoryCursorRef.current[initialChannel] = cursor;
        setHasOlderMessages(!!cursor);

        // Phase 3: background backfill several older pages so long chats don't
        // appear truncated until the user manually scrolls to the top.
        for (let page = 0; page < HISTORY_AUTO_BACKFILL_PAGES && cursor && !cancelled; page++) {
          const olderPage = await loadMessages(initialChannel, undefined, HISTORY_PAGE_SIZE, { before: cursor });
          if (cancelled || !isCurrentBot()) return;
          if (olderPage.messages.length > 0) mergeE2EEMessages(initialChannel, olderPage.messages);
          cursor = olderPage.hasMore ? olderPage.nextBefore : null;
          olderHistoryCursorRef.current[initialChannel] = cursor;
          setHasOlderMessages(!!cursor);
        }
      } catch {
        // Keep the latest preview if deeper history loading fails.
      }
    };

    void loadInitialHistory();

    return () => { cancelled = true; };
  }, [e2eeReady, initialChannel, isCurrentBot, loadMessages, mergeE2EEMessages]);

  const handleLoadOlder = useCallback(async () => {
    if (!e2eeReady || !initialChannel || loadingOlder || !hasOlderMessages) return;
    setLoadingOlder(true);
    try {
      const cursor = olderHistoryCursorRef.current[initialChannel];
      if (!cursor) {
        setHasOlderMessages(false);
        return;
      }

      const olderPage = await loadMessages(
        initialChannel,
        undefined,
        HISTORY_PAGE_SIZE,
        { before: cursor },
      );
      if (!isCurrentBot()) return;
      const nextCursor = olderPage.hasMore ? olderPage.nextBefore : null;
      olderHistoryCursorRef.current[initialChannel] = nextCursor;
      setHasOlderMessages(!!nextCursor);
      if (olderPage.messages.length > 0) mergeE2EEMessages(initialChannel, olderPage.messages);
    } finally {
      setLoadingOlder(false);
    }
  }, [e2eeReady, hasOlderMessages, initialChannel, isCurrentBot, loadMessages, loadingOlder, mergeE2EEMessages]);

  // #111 Streaming resume on refresh/app return. Once a channel is rehydrated
  // from Redis snapshot, keep polling that snapshot until it disappears; then
  // fetch app_channel_messages and clear the transient streaming bubble.
  useEffect(() => {
    if (!ready || !authenticated || !initialChannel) return;
    let cancelled = false;
    let sawSnapshot = false;
    const channel = initialChannel;

    const pollSnapshot = async () => {
      try {
        const state = useChatStore.getState();
        const existing = state.channelStates[channel];
        if (existing?.streaming && !existing.reconnecting && !sawSnapshot) return;

        const snap = await chatApi.getActiveSnapshot(botId, channel);
        if (cancelled || !isCurrentBot()) return;

        if (isLiveActiveSnapshot(snap)) {
          sawSnapshot = true;
          useChatStore
            .getState()
            .setChannelState(channel, channelStateFromActiveSnapshot(snap, existing), { botId });
          return;
        }

        if (sawSnapshot || existing?.reconnecting) {
          const msgs = await chatApi.fetchChannelMessages(botId, channel, undefined, 50);
          if (!cancelled && isCurrentBot()) {
            mergeFetchedServerMessages(botId, channel, msgs);
            useChatStore.getState().setChannelState(channel, {
              streaming: false,
              streamingText: "",
              thinkingText: "",
              hasTextContent: false,
              reconnecting: false,
              turnPhase: null,
              heartbeatElapsedMs: null,
              pendingInjectionCount: 0,
              subagents: [],
              documentDraft: null,
            }, { botId });
          }
          sawSnapshot = false;
        }
      } catch {
        // Best-effort recovery loop; the normal server-message poll remains active.
      }
    };

    void pollSnapshot();
    const interval = setInterval(() => { void pollSnapshot(); }, 2_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [botId, initialChannel, isCurrentBot, ready, authenticated]);

  // Poll for server messages
  useEffect(() => {
    if (!ready || !authenticated) return;
    const interval = setInterval(() => {
      const { activeChannel, lastServerFetch } = useChatStore.getState();
      const channel = activeChannel; // capture for async callback
      if (!channel) return;
      const since = lastServerFetch[channel];
      chatApi
        .fetchChannelMessages(botId, channel, since)
        .then((msgs) => {
          if (msgs.length > 0) {
            const mapped = mapServerMessages(msgs);
            if (!isCurrentBot()) return;
            const existing =
              useChatStore.getState().serverMessages[channel] ?? [];
            // Deduplicate by serverId before appending
            const existingIds = new Set(existing.map((m) => m.serverId).filter(Boolean));
            const newOnly = mapped.filter((m) => !existingIds.has(m.serverId));
            if (newOnly.length > 0) {
              useChatStore
                .getState()
                .setServerMessages(channel, [...existing, ...newOnly], { botId });
            }
            useChatStore
              .getState()
              .setLastServerFetch(
                channel,
                msgs[msgs.length - 1].created_at,
                { botId },
              );
          }
        })
        .catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [botId, isCurrentBot, ready, authenticated]);

  // §7.15 — Supabase Realtime subscription for push_messages. Opens a
  // subscription scoped to (botId, activeChannel); dedupe happens in
  // the store via receivePushMessage. Re-subscribes whenever the
  // active channel changes (or the bot/auth flips).
  useEffect(() => {
    if (!ready || !authenticated || !botId) return;
    const channel = store.activeChannel;
    if (!channel) return;
    let cancelled = false;
    let handle: { unsubscribe: () => Promise<void> } | null = null;
    (async () => {
      const accessToken = await getAccessToken().catch(() => null);
      if (cancelled) return;
      handle = await subscribeToPushMessages({
        botId,
        channel,
        accessToken,
        onInsert: (row) => {
          useChatStore.getState().receivePushMessage(channel, row, { botId });
        },
      });
    })();
    return () => {
      cancelled = true;
      void handle?.unsubscribe();
    };
  }, [botId, store.activeChannel, ready, authenticated, getAccessToken]);

  // Recover messages when page becomes visible (tab switch, screen unlock)
  useEffect(() => {
    if (!ready || !authenticated) return;
    const handleVisibility = () => {
      if (document.visibilityState !== "visible") return;
      const { activeChannel } = useChatStore.getState();
      const channel = activeChannel; // capture for async callback
      if (!channel) return;
      // Re-fetch latest messages for active channel (ignore lastServerFetch to catch up)
      chatApi
        .fetchChannelMessages(botId, channel, undefined, 50)
        .then((msgs) => {
          if (msgs.length > 0) {
            const mapped = mapServerMessages(msgs);
            if (!isCurrentBot()) return;
            // Merge with existing rather than replacing
            const prev = useChatStore.getState().serverMessages[channel] ?? [];
            const serverIds = new Set(mapped.map((m) => m.serverId));
            const kept = prev.filter((m) => !m.serverId || !serverIds.has(m.serverId));
            const merged = [...kept, ...mapped].sort((a, b) => a.timestamp - b.timestamp);
            useChatStore.getState().setServerMessages(channel, merged, { botId });
            useChatStore
              .getState()
              .setLastServerFetch(channel, msgs[msgs.length - 1].created_at, { botId });
          }
        })
        .catch(() => {});
      // Refresh channel list
      chatApi.fetchChannels(botId).then((chs) => {
        useChatStore.getState().setChannels(chs, { botId });
      }).catch(() => {});
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, [botId, isCurrentBot, ready, authenticated]);

  // Poll E2EE messages from Supabase (messages sent from other clients)
  useEffect(() => {
    if (!e2eeReady) return;
    const e2eeLastFetch: Record<string, string> = {};

    const interval = setInterval(() => {
      const { activeChannel } = useChatStore.getState();
      const channel = activeChannel; // capture for async callback
      if (!channel) return;
      const since = e2eeLastFetch[channel];
      loadMessages(channel, since, 50).then(({ messages: msgs, deletions }) => {
        // Handle deletions from other devices
        if (deletions.length > 0) {
          const channelWideDelete = deletions.some((d) => d.client_msg_id === null);
          if (channelWideDelete) {
            // Another device cleared the channel
            useChatStore.setState((state) => ({
              ...(state.botId === botId
                ? {
                    messages: { ...state.messages, [channel]: (state.messages[channel] ?? []).filter((m) => m.role === "system") },
                    serverMessages: { ...state.serverMessages, [channel]: [] },
                  }
                : {}),
            }));
          } else {
            const deletedIds = new Set(deletions.map((d) => d.client_msg_id!));
            useChatStore.getState().removeMessages(channel, deletedIds, { botId });
          }
        }
        if (msgs.length > 0) {
          if (!isCurrentBot()) return;
          const existing = useChatStore.getState().messages[channel] ?? [];
          const existingIds = new Set(existing.map((m) => m.id));
          const newMsgs = msgs.filter((m) => !existingIds.has(m.id) && !(m.serverId && existingIds.has(m.serverId)));
          for (const msg of newMsgs) {
            useChatStore.getState().addMessage(channel, msg, { botId });
          }
          const latest = msgs[msgs.length - 1];
          e2eeLastFetch[channel] = new Date(latest.timestamp).toISOString();
        }
      }).catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [botId, e2eeReady, isCurrentBot, loadMessages]);

  /**
   * Core send path — this is what actually hits the network. Split out of
   * `handleSend` so the queue-drain callback in `onDone` can reuse it
   * without re-running the streaming-vs-idle check.
   */
  const performSend = useCallback(
    async (
      text: string,
      explicitReply: ReplyTo | null,
      kbDocs: KbDocReference[],
      modelOverride?: string,
      sendOptions?: ChatInputSendOptions,
    ) => {
      const channel = useChatStore.getState().activeChannel;
      if (!channel) return;
      const turnModel = resolveChannelRuntimeModel(channel, modelOverride);
      const messageText = buildMessageContentWithKbContext(text, kbDocs);
      if (!messageText.trim()) return;
      if (!isCurrentBot()) return;
      const responseLanguage = detectMessageResponseLanguage(messageText);

      const activeReply = explicitReply;

      const userMsg: ChatMessage = {
        id: `user-${Date.now()}`,
        role: "user",
        content: messageText,
        timestamp: Date.now(),
        ...(activeReply ? { replyTo: activeReply } : {}),
      };
      store.addMessage(channel, userMsg, { botId });
      persistUserHistoryMessage({
        e2eeReady,
        saveMessages,
        channel,
        message: userMsg,
        content: messageText,
        onError: (err) => handleUserHistorySaveError(channel, err),
      });
      chatMessagesRef.current?.scrollToBottom();
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
        currentGoal: text.trim() || messageText.trim(),
        pendingGoalMissionTitle: sendOptions?.goalMode ? (text.trim() || messageText.trim()) : null,
        pendingInjectionCount: 0,
        subagents: [],
        documentDraft: null,
        inspectedSources: [],
        citationGate: null,
        runtimeTraces: [],
        turnUsage: undefined,
        responseLanguage,
      }, { botId });
      if (!isCurrentBot()) return;

      const allMessages = useChatStore.getState().messages[channel] ?? [];

      const attempt = async (retryCount: number): Promise<void> => {
        const controller = new AbortController();
        store.setAbortController(channel, controller, { botId });

        try {
          await chatApi.sendMessage(botId, channel, allMessages, {
            model: turnModel,
            ...(sendOptions?.goalMode ? { goalMode: true } : {}),
            replyTo: activeReply ?? undefined,
            onDelta: (delta) => {
              if (!isCurrentBot()) return;
              const s = useChatStore.getState().channelStates[channel];
              store.setChannelState(channel, {
                streamingText: (s?.streamingText ?? "") + delta,
                hasTextContent: true,
                ...(s?.fileProcessing ? { fileProcessing: false } : {}),
              }, { botId });
            },
            onThinkingDelta: (delta) => {
              if (!isCurrentBot()) return;
              const s = useChatStore.getState().channelStates[channel];
              store.setChannelState(channel, {
                thinkingText: (s?.thinkingText ?? "") + delta,
                ...(s?.fileProcessing ? { fileProcessing: false } : {}),
              }, { botId });
            },
            onToolActivity: (activeTools) => {
              if (!isCurrentBot()) return;
              store.setChannelState(channel, { activeTools }, { botId });
            },
            onSubagentActivity: (subagents) => {
              if (!isCurrentBot()) return;
              store.setChannelState(channel, { subagents }, { botId });
            },
            onTaskBoard: (snapshot) => {
              if (!isCurrentBot()) return;
              // Full-board overwrite — server always sends complete board.
              store.setChannelState(channel, { taskBoard: snapshot }, { botId });
            },
            onBrowserFrame: (browserFrame) => {
              if (!isCurrentBot()) return;
              store.setChannelState(channel, { browserFrame }, { botId });
            },
            onDocumentDraft: (documentDraft) => {
              if (!isCurrentBot()) return;
              store.setChannelState(channel, { documentDraft }, { botId });
            },
            onMissionEvent: (event) => {
              if (!isCurrentBot()) return;
              const currentState = store.getChannelState(channel);
              store.setChannelState(channel, applyMissionEvent(currentState, event), { botId });
            },
            onSourceInspected: (source) => {
              if (!isCurrentBot()) return;
              const existing = useChatStore.getState().channelStates[channel]?.inspectedSources;
              store.setChannelState(channel, {
                inspectedSources: appendInspectedSource(existing, source),
              }, { botId });
            },
            onCitationGate: (citationGate) => {
              if (!isCurrentBot()) return;
              store.setChannelState(channel, { citationGate }, { botId });
            },
            onRuntimeTrace: (trace) => {
              if (!isCurrentBot()) return;
              const existing = useChatStore.getState().channelStates[channel]?.runtimeTraces ?? [];
              store.setChannelState(channel, {
                runtimeTraces: [...existing, trace].slice(-12),
              }, { botId });
            },
            onTurnPhase: (phase) => {
              store.setChannelState(channel, { turnPhase: phase }, { botId });
              if (phase === "committed") {
                const hasQueued = (useChatStore.getState().queuedMessages[channel] ?? []).length > 0;
                if (hasQueued) {
                  controller.abort();
                  store.finalizeStream(channel, undefined, { botId });
                  drainQueueRef.current?.(channel);
                }
              }
            },
            onHeartbeat: (elapsedMs) => {
              store.setChannelState(channel, { heartbeatElapsedMs: elapsedMs }, { botId });
            },
            onPendingInjectionCount: (queuedCount) => {
              store.setChannelState(channel, {
                pendingInjectionCount: queuedCount,
              }, { botId });
            },
            onUsage: (usage) => {
              if (!isCurrentBot()) return;
              store.setChannelState(channel, { turnUsage: usage }, { botId });
            },
            onControlEvent: (event) => {
              useChatStore.getState().applyControlEvent(channel, event);
            },
            onContentReplace: (text) => {
              if (!isCurrentBot()) return;
              store.setChannelState(channel, {
                streamingText: text,
                hasTextContent: !!text,
              }, { botId });
            },
            onResponseClear: () => {
              if (!isCurrentBot()) return;
              const currentState = store.getChannelState(channel);
              const prevThinking = currentState?.thinkingText || "";
              store.setChannelState(channel, {
                streamingText: "",
                heartbeatElapsedMs: null,
                pendingInjectionCount: 0,
                subagents: [],
                documentDraft: null,
                turnUsage: undefined,
                responseLanguage: currentState?.responseLanguage ?? responseLanguage,
                thinkingText: prevThinking,
                thinkingStartedAt: prevThinking
                  ? currentState?.thinkingStartedAt || Date.now()
                  : null,
              }, { botId });
            },
            onDone: () => {
              if (!isCurrentBot()) return;
              const s = useChatStore.getState().channelStates[channel];
              if (shouldRetryEmptyCompletion(s, retryCount, MAX_RETRIES)) {
                const nextRetry = retryCount + 1;
                store.setChannelState(channel, {
                  streaming: true,
                  streamingText: "",
                  thinkingText: "",
                  hasTextContent: false,
                  activeTools: [],
                  browserFrame: null,
                  documentDraft: null,
                  subagents: [],
                  taskBoard: null,
                  fileProcessing: false,
                  turnPhase: "pending",
                  heartbeatElapsedMs: null,
                  pendingInjectionCount: 0,
                  turnUsage: undefined,
                  error: `Connecting to bot... (${nextRetry}/${MAX_RETRIES})`,
                }, { botId });
                window.setTimeout(() => {
                  if (!isCurrentBot()) return;
                  void attempt(nextRetry);
                }, RETRY_DELAY_MS);
                return;
              }
              const assistantText = s?.streamingText || "";
              const thinkingContent = s?.thinkingText || undefined;
              const thinkingDuration = s?.thinkingStartedAt
                ? Math.round((Date.now() - s.thinkingStartedAt) / 1000)
                : undefined;
              const researchEvidence = researchEvidenceFromChannelState(s);
              const usage = s?.turnUsage;
              // Generate stable ID BEFORE finalizeStream so E2EE clientMsgId matches local message ID
              const assistantFinalizedAt = Date.now();
              const assistantMsgId = `assistant-${assistantFinalizedAt}`;
              store.finalizeStream(channel, assistantMsgId, { botId });
              if (e2eeReady && assistantText) {
                const msgs = [
                  { role: "user" as const, content: messageText, clientMsgId: userMsg.id },
                  { role: "assistant" as const, content: assistantText, clientMsgId: assistantMsgId, thinkingContent, thinkingDuration, researchEvidence, usage },
                ];
                saveMessages(channel, msgs).catch(async () => {
                  await new Promise((r) => setTimeout(r, 2000));
                  saveMessages(channel, msgs).catch((err) => {
                    console.error("[chat] failed to save messages after retry:", err);
                    store.setChannelState(channel, {
                      saveError: locale === "ko"
                        ? "메시지 저장 실패 — 새로고침 후 대화가 사라질 수 있습니다."
                        : "Failed to save message — conversation may be lost after refresh.",
                    }, { botId });
                  });
                });
              }
              // Catch-up fetch: if stream ended but text looks truncated,
              // pull the committed assistant message from chat history.
              if (assistantText && botId) {
                void (async () => {
                  try {
                    for (let attempt = 0; attempt < ASSISTANT_CATCHUP_ATTEMPTS; attempt += 1) {
                      if (attempt > 0) {
                        await new Promise((resolve) => {
                          setTimeout(resolve, ASSISTANT_CATCHUP_RETRY_MS * attempt);
                        });
                      }
                      const history = await chatApi.fetchChannelMessages(
                        botId,
                        channel,
                        undefined,
                        ASSISTANT_CATCHUP_LIMIT,
                      );
                      const latest = findLatestAssistantServerMessage(history);
                      if (!latest) continue;
                      const latestTimestamp = Date.parse(latest.created_at);
                      if (
                        !Number.isFinite(latestTimestamp) ||
                        latestTimestamp < assistantFinalizedAt - ASSISTANT_CATCHUP_PAST_WINDOW_MS ||
                        latestTimestamp > assistantFinalizedAt + ASSISTANT_CATCHUP_FUTURE_WINDOW_MS
                      ) {
                        continue;
                      }
                      if (shouldPatchAssistantTextFromServer(assistantText, latest.content)) {
                        store.addMessage(channel, {
                          id: assistantMsgId,
                          role: "assistant",
                          content: latest.content,
                          timestamp: latestTimestamp,
                          serverId: latest.id,
                          researchEvidence: researchEvidenceFromServerMessage(latest),
                        }, { botId });
                      }
                      return;
                    }
                  } catch { /* best-effort */ }
                })();
              }
              // Drain the first queued message, if any (Claude Code CLI style).
              drainQueueRef.current?.(channel);
            },
            onError: async (err) => {
              if (!isCurrentBot()) return;
              // Don't retry if we already received partial content — would send duplicate
              const currentState = useChatStore.getState().channelStates[channel];
              const hasVisibleContent = !!currentState?.hasTextContent;
              if (
                !hasVisibleContent &&
                retryCount < MAX_RETRIES &&
                RETRYABLE_PATTERN.test(err.message)
              ) {
                store.setChannelState(channel, {
                  streamingText: "",
                  thinkingText: "",
                  hasTextContent: false,
                  turnPhase: "pending",
                  heartbeatElapsedMs: null,
                  pendingInjectionCount: 0,
                  subagents: [],
                  documentDraft: null,
                  turnUsage: undefined,
                  error: `Connecting to bot... (${retryCount + 1}/${MAX_RETRIES})`,
                }, { botId });
                await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
                if (!isCurrentBot()) return;
                return attempt(retryCount + 1);
              }
              if (hasVisibleContent && RETRYABLE_PATTERN.test(err.message)) {
                // SSE dropped mid-response — poll active-snapshot to recover
                store.setChannelState(channel, { reconnecting: true, error: null }, { botId });
                let recovered = false;
                for (let poll = 0; poll < 60; poll++) {
                  if (!isCurrentBot()) break;
                  await new Promise((r) => setTimeout(r, 2000));
                  try {
                    const snap = await chatApi.getActiveSnapshot(botId, channel);
                    if (isLiveActiveSnapshot(snap)) {
                      const existing = useChatStore.getState().channelStates[channel];
                      store.setChannelState(
                        channel,
                        channelStateFromActiveSnapshot(snap, existing),
                        { botId },
                      );
                    } else {
                      // Snapshot gone = turn finished server-side
                      const msgs = await chatApi.fetchChannelMessages(botId, channel);
                      if (msgs?.length) mergeFetchedServerMessages(botId, channel, msgs);
                      store.setChannelState(channel, {
                        streaming: false,
                        streamingText: "",
                        thinkingText: "",
                        reconnecting: false,
                        turnPhase: null,
                      }, { botId });
                      recovered = true;
                      break;
                    }
                  } catch { /* continue polling */ }
                }
                if (!recovered) {
                  store.setChannelState(channel, {
                    error: err.message,
                    turnPhase: "aborted",
                    reconnecting: false,
                  }, { botId });
                  store.finalizeStream(channel, undefined, { botId });
                  store.clearQueue(channel, { botId });
                } else {
                  drainQueueRef.current?.(channel);
                }
              } else if (hasVisibleContent) {
                // Non-retryable error with partial content — keep the partial
                // text, but mark it as incomplete so it cannot look like a
                // successful final answer.
                store.setChannelState(channel, {
                  error: err.message,
                  turnPhase: "aborted",
                }, { botId });
                store.finalizeStream(channel, undefined, { botId });
                store.clearQueue(channel, { botId });
              } else {
                store.setChannelState(channel, {
                  streaming: false,
                  streamingText: "",
                  thinkingText: "",
                  hasTextContent: false,
                  turnPhase: null,
                  heartbeatElapsedMs: null,
                  pendingInjectionCount: 0,
                  subagents: [],
                  documentDraft: null,
                  error: err.message,
                }, { botId });
                // Terminal error — drop the queue so the user isn't surprised
                // by a phantom send against a broken session.
                store.clearQueue(channel, { botId });
              }
            },
            signal: controller.signal,
          });
        } catch (err) {
          if (controller.signal.aborted) return;
          if (err instanceof Error && err.name === "AuthExpiredError") {
            logout();
            router.push("/login");
            return;
          }
          store.setChannelState(channel, {
            streaming: false,
            streamingText: "",
            thinkingText: "",
            hasTextContent: false,
            turnPhase: null,
            heartbeatElapsedMs: null,
            pendingInjectionCount: 0,
            subagents: [],
            documentDraft: null,
            error: err instanceof Error ? err.message : "Unknown error",
          }, { botId });
          // Recovery: fetch latest server messages after SSE drop
          // Bot likely completed the response server-side
          setTimeout(() => {
            chatApi
              .fetchChannelMessages(botId, channel, undefined, 50)
              .then((msgs) => {
                if (msgs.length > 0) {
                  if (!isCurrentBot()) return;
                  const mapped = mapServerMessages(msgs);
                  useChatStore.getState().setServerMessages(channel, mapped, { botId });
                  useChatStore
                    .getState()
                    .setLastServerFetch(channel, msgs[msgs.length - 1].created_at, { botId });
                  // Clear error if we recovered the message
                  store.setChannelState(channel, { error: null }, { botId });
                }
              })
              .catch(() => {});
          }, 2000);
        }
      };

      await attempt(0);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [botId, e2eeReady, isCurrentBot, resolveChannelRuntimeModel, saveMessages],
  );

  /**
   * Ref-stored drain callback — `performSend`'s `onDone` calls through this
   * to avoid a circular useCallback dependency. Ref is rebound below once
   * the handler closure is available.
   */
  const drainQueueRef = useRef<((channel: string) => void) | null>(null);

  /**
   * Entry point wired to `ChatInput.onSend`. During streaming this enqueues
   * the message instead of hitting the network; otherwise it calls
   * `performSend` directly. The `replyingTo` state is snapshotted here so
   * the queue carries the correct quote even if the user changes the reply
   * target before the queue drains.
   */
  const handleSend = useCallback(
    async (text: string, files?: File[], sendOptions?: ChatInputSendOptions) => {
      const channel = useChatStore.getState().activeChannel;
      if (!channel) return false;
      const state = useChatStore.getState();
      const isStreaming = !!state.channelStates[channel]?.streaming;
      const activeReply = replyingTo;
      setReplyingTo(null);

      let messageText = text;
      let uploadedKbDocs: KbDocReference[] = [];
      try {
        if (files && files.length > 0) {
          const { imageFiles, otherFiles } = splitImageAndOtherFiles(files);
          if (imageFiles.length > 0) {
            const markers = await uploadImagesAsAttachmentMarkers(botId, channel, imageFiles);
            messageText = markers + (messageText ? `\n${messageText}` : "");
          }
          if (otherFiles.length > 0) {
            uploadedKbDocs = await resolveKbDocsForFiles(otherFiles);
          }
        }
      } catch (err) {
        console.error("[chat] file upload failed:", err);
        if (activeReply) setReplyingTo(activeReply);
        store.setChannelState(channel, {
          error: err instanceof Error ? err.message : "Failed to upload files",
        }, { botId });
        return false;
      }
      if (!isCurrentBot()) return false;

      const messageKbDocs = mergeKbDocReferences(selectedKbDocs, uploadedKbDocs);

      if (isStreaming) {
        const hasFiles = !!(files && files.length > 0);
        const sendMode = getStreamingSendMode({
          hasFiles,
          hasKbContext: messageKbDocs.length > 0,
          requestedMode: streamingComposerMode,
        });
        if (sendMode === "inject") {
          try {
            const sessionKey = chatApi.buildSessionKey(botId, channel);
            const injectedAfterChars =
              useChatStore.getState().channelStates[channel]?.streamingText?.length ?? 0;
            const result = await chatApi.injectMessage(botId, sessionKey, messageText, "web");
            if (result.injected) {
              const injectedMsg: ChatMessage = {
                id: `injected-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                role: "user",
                content: messageText,
                timestamp: Date.now(),
                injected: true,
                injectedAfterChars,
                ...(activeReply ? { replyTo: activeReply } : {}),
              };
              store.addMessage(channel, injectedMsg, { botId });
              persistUserHistoryMessage({
                e2eeReady,
                saveMessages,
                channel,
                message: injectedMsg,
                content: messageText,
                onError: (err) => handleUserHistorySaveError(channel, err),
              });
              clearAcceptedKbContext();
              return true;
            }
          } catch (err) {
            console.warn("[chat] inject failed, falling back to queue:", err);
          }
        }
        const queued: QueuedMessage = {
          id: `queued-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          content: messageText,
          queuedAt: Date.now(),
          modelOverride: resolveChannelRuntimeModel(channel),
          ...(sendOptions?.goalMode ? { goalMode: true } : {}),
          ...(activeReply ? { replyTo: activeReply } : {}),
          ...(messageKbDocs.length > 0 ? { kbDocs: messageKbDocs } : {}),
        };
        const ok = state.enqueueMessage(channel, queued, { botId });
        if (!ok) {
          if (activeReply) setReplyingTo(activeReply);
          store.setChannelState(channel, {
            error: `Queue full (max ${MAX_QUEUED_MESSAGES}). Wait for the bot to finish.`,
          }, { botId });
          return false;
        }
        clearAcceptedKbContext();
        return true;
      }

      void performSend(messageText, activeReply, messageKbDocs, undefined, sendOptions).catch((err) => {
        console.error("[chat] send failed:", err);
      });
      clearAcceptedKbContext();
      return true;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [botId, clearAcceptedKbContext, e2eeReady, handleUserHistorySaveError, isCurrentBot, replyingTo, resolveKbDocsForFiles, saveMessages, selectedKbDocs, streamingComposerMode, performSend],
  );

  // Rebind the drain ref on every render so the closure sees the freshest
  // `performSend` (captures current replyingTo/e2ee state via its own deps).
  drainQueueRef.current = (channel: string) => {
    const next = useChatStore.getState().dequeueFirst(channel, { botId });
    if (!next) return;
    // Defer one tick so the finalized-assistant setState lands before we
    // start the next turn — avoids visually clobbering the just-done message.
    setTimeout(() => {
      const queuedKbDocs = next.kbDocs ?? [];
      performSend(
        next.content,
        next.replyTo ?? null,
        queuedKbDocs,
        next.modelOverride,
        next.goalMode ? { goalMode: true } : undefined,
      ).catch((err) => {
        console.error("[chat] drain queue send failed:", err);
      });
    }, 0);
  };

  const cancelChannelTurn = useCallback((channel: string) => {
    if (interruptHandoffChannelsRef.current.has(channel)) return;
    interruptHandoffChannelsRef.current.add(channel);
    void cancelActiveTurnWithQueueHandoff({
      hasQueued: () => (useChatStore.getState().queuedMessages[channel] ?? []).length > 0,
      promoteQueuedForHandoff: () => {
        useChatStore.getState().promoteNextQueuedMessage(channel, { botId });
      },
      cancelStream: (options) => {
        store.cancelStream(channel, { ...options, botId });
      },
      interrupt: (handoffRequested) => {
        const sessionKey = chatApi.buildSessionKey(botId, channel);
        return chatApi.interruptTurn(botId, sessionKey, handoffRequested, "web");
      },
      drainQueue: () => {
        drainQueueRef.current?.(channel);
      },
    }).then((result) => {
      setEscArmedUntil(null);
      if (
        result.handoffRequested &&
        !result.drained
      ) {
        store.setChannelState(channel, {
          error: "Interrupted current turn, but could not hand off the queued message yet. Please send again.",
        }, { botId });
      }
    }).catch((err) => {
      console.warn("[chat] runtime interrupt failed:", err);
    }).finally(() => {
      interruptHandoffChannelsRef.current.delete(channel);
    });
  }, [botId, store]);

  const handleCancel = useCallback(() => {
    const channel = useChatStore.getState().activeChannel;
    if (!channel) return;
    cancelChannelTurn(channel);
  }, [cancelChannelTurn]);

  const handleCancelQueue = useCallback(() => {
    const channel = useChatStore.getState().activeChannel;
    if (!channel) return;
    store.clearQueue(channel, { botId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId]);

  const handleCancelQueued = useCallback((id: string) => {
    const channel = useChatStore.getState().activeChannel;
    if (!channel) return;
    store.removeFromQueue(channel, id, { botId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId]);

  /**
   * Is ANY channel currently streaming? Used to arm the ESC listener below.
   * We subscribe to the store here (not reading `channelState.streaming`
   * directly) because that variable is computed further down the render
   * body and isn't available at this point in the component.
   */
  const anyStreaming = useChatStore(
    (s) => Object.values(s.channelStates).some((cs) => cs.streaming),
  );

  useEffect(() => {
    if (!anyStreaming) setStreamingComposerMode("queue");
  }, [anyStreaming]);

  useEffect(() => {
    if (!botId || !store.activeChannel) return;
    const channel = store.activeChannel;
    let cancelled = false;
    const sessionKey = chatApi.buildSessionKey(botId, channel);
    Promise.allSettled([
      chatApi.fetchControlEvents(botId, sessionKey, channel),
      chatApi.fetchControlRequests(botId, sessionKey, channel),
    ])
      .then(([eventsResult, requestsResult]) => {
        if (cancelled) return;
        const state = useChatStore.getState();
        if (eventsResult.status === "fulfilled") {
          for (const event of eventsResult.value.events) {
            state.applyControlEvent(channel, event);
          }
        }
        if (requestsResult.status === "fulfilled") {
          state.hydrateControlRequests(channel, requestsResult.value);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [botId, store.activeChannel]);

  const handleRespondControlRequest = useCallback(
    async (
      request: Parameters<typeof chatApi.respondToControlRequest>[1],
      response: Parameters<typeof chatApi.respondToControlRequest>[2],
    ) => {
      const updated = await chatApi.respondToControlRequest(botId, request, response);
      const channel = request.channelName ?? useChatStore.getState().activeChannel;
      useChatStore.getState().upsertControlRequest(channel, updated);
    },
    [botId],
  );

  // ESC-to-cancel (Claude Code CLI-style). Only active while streaming so
  // ESC still closes modals / clears selections during idle states. Uses
  // bubble phase (`capture: false`) so any dialog that stops propagation
  // on its own ESC handler wins — e.g. delete-confirm / telegram modals
  // take precedence by stopping the event before it reaches this listener.
  useEffect(() => {
    if (!anyStreaming) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      // Skip if focus is inside a dialog — role=dialog or aria-modal.
      const active = document.activeElement as HTMLElement | null;
      if (active && active.closest('[role="dialog"], [aria-modal="true"]')) return;
      // Skip during IME composition (Korean/Japanese input).
      if ((e as KeyboardEvent & { isComposing?: boolean }).isComposing) return;
      e.preventDefault();
      const channel = useChatStore.getState().activeChannel;
      if (!channel) return;
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
  }, [store.activeChannel]);

  useEffect(() => {
    if (escArmedUntil === null) return;
    const delay = Math.max(0, escArmedUntil - Date.now());
    const id = window.setTimeout(() => setEscArmedUntil(null), delay);
    return () => window.clearTimeout(id);
  }, [escArmedUntil]);

  const handleTelegramConnect = useCallback(
    async (token: string, username: string) => {
      try {
        const accessToken = await getAccessToken();
        const res = await fetch(`/api/bots/${botId}/connect-telegram`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${accessToken}`,
          },
          body: JSON.stringify({ telegramBotToken: token, telegramBotUsername: username }),
        });
        if (res.ok) {
          setShowTelegramGuide(false);
          setBannerDismissed(true);
          router.refresh();
        }
      } catch { /* ignore */ }
    },
    [botId, getAccessToken, router],
  );

  // --- Message deletion handlers ---
  const handleDeleteSelected = useCallback(() => {
    setShowDeleteConfirm(true);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    setShowDeleteConfirm(false);
    const { activeChannel, selectedMessages: selected, messages: allMsgs, serverMessages: allServer } = useChatStore.getState();
    const selectedIds = selected[activeChannel];
    if (!selectedIds || selectedIds.size === 0) return;

    // Backup for undo
    const backupMessages = allMsgs[activeChannel] ?? [];
    const backupServer = allServer[activeChannel] ?? [];
    setUndoData({ channel: activeChannel, messages: backupMessages, serverMessages: backupServer });

    // Optimistic removal + exit selection
    store.removeMessages(activeChannel, selectedIds, { botId });
    store.exitSelectionMode();

    // Clear previous undo timer
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current);

    // Start undo window (3s), then commit to server
    undoTimerRef.current = setTimeout(async () => {
      setUndoData(null);
      // Delete from E2EE remote
      const ids = Array.from(selectedIds);
      const isAllMessages = ids.length === backupMessages.filter((m) => m.role !== "system").length + backupServer.length;
      await deleteMessages(activeChannel, ids, isAllMessages);
    }, 3000);
  }, [botId, store, deleteMessages]);

  const handleUndo = useCallback(() => {
    if (!undoData) return;
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
    // Restore messages
    useChatStore.setState((state) => ({
      ...(state.botId === botId
        ? {
            messages: { ...state.messages, [undoData.channel]: undoData.messages },
            serverMessages: { ...state.serverMessages, [undoData.channel]: undoData.serverMessages },
          }
        : {}),
    }));
    setUndoData(null);
  }, [botId, undoData]);

  const buildSelectedExportDraft = useCallback((): ChatExportDraft | null => {
    const { activeChannel, selectedMessages: selectedByChannel, messages: localByChannel, serverMessages: serverByChannel } = useChatStore.getState();
    const selected = selectedByChannel[activeChannel];
    if (!activeChannel || !selected || selected.size === 0) return null;

    const combined = [
      ...(localByChannel[activeChannel] ?? []),
      ...(serverByChannel[activeChannel] ?? []),
    ];
    const normalized = normalizeSelectedChatExportMessages(combined, selected);
    const unique = Array.from(
      new Map(normalized.map((message) => [`${message.role}:${message.timestamp}:${message.content}`, message])).values(),
    );
    if (unique.length === 0) return null;

    const exportedAt = new Date();
    const title = `${botName} / ${activeChannel}`;
    const markdown = buildChatExportMarkdown({
      botName,
      channelName: activeChannel,
      exportedAt,
      messages: unique,
    });

    return {
      channelName: activeChannel,
      title,
      filename: buildChatExportFilename({
        botName,
        channelName: activeChannel,
        exportedAt,
      }),
      markdown,
      messages: unique,
    };
  }, [botName]);

  const handleExportSelected = useCallback(() => {
    const draft = buildSelectedExportDraft();
    setExportLink(null);
    setExportError(null);
    if (!draft) {
      const channel = useChatStore.getState().activeChannel;
      if (channel) {
        store.setChannelState(channel, {
          error: "Select at least one user or assistant message to export.",
        }, { botId });
      }
      return;
    }
    setExportDraft(draft);
  }, [botId, buildSelectedExportDraft, store]);

  const handleDownloadExport = useCallback(() => {
    if (!exportDraft) return;
    const blob = new Blob([exportDraft.markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = exportDraft.filename;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  }, [exportDraft]);

  const handleCreatePublicExport = useCallback(async () => {
    if (!exportDraft || creatingExportLink) return;
    const confirmed = window.confirm(t.chat.createPublicLinkDescription);
    if (!confirmed) return;

    setCreatingExportLink(true);
    setExportError(null);
    try {
      const token = await getAccessToken();
      const response = await fetch("/api/chat/exports", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          botId,
          channelName: exportDraft.channelName,
          title: exportDraft.title,
          messages: exportDraft.messages,
          markdown: exportDraft.markdown,
        }),
      });
      const body = (await response.json().catch(() => null)) as { export?: { url?: string }; error?: string } | null;
      if (!response.ok || !body?.export?.url) {
        throw new Error(body?.error ?? "Failed to create public link");
      }
      setExportLink(body.export.url);
      await navigator.clipboard?.writeText(body.export.url).catch(() => {});
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Failed to create public link");
    } finally {
      setCreatingExportLink(false);
    }
  }, [botId, creatingExportLink, exportDraft, getAccessToken, t.chat.createPublicLinkDescription]);

  const handleReset = useCallback(() => {
    const channel = useChatStore.getState().activeChannel;
    if (!channel) return;
    store.resetSession(channel, getAccessToken);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [getAccessToken]);

  const handleChannelSelect = useCallback(
    (name: string) => {
      store.setActiveChannel(name);
      router.push(`/dashboard/${botId}/chat/${encodeURIComponent(name)}`);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [botId, router],
  );

  const handleRefreshChannels = useCallback(() => {
    setRefreshing(true);
    chatApi
      .fetchChannels(botId)
      .then((chs) => {
        store.setChannels(chs, { botId });
        try { localStorage.setItem(CHANNELS_CACHE_KEY(botId), JSON.stringify(chs)); } catch { /* ignore */ }
      })
      .catch((err) => {
        console.error("[chat] Failed to refresh channels:", err);
      })
      .finally(() => {
        setTimeout(() => setRefreshing(false), 500);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId]);

  const handleCreateChannel = useCallback(
    (name: string, memoryMode: ChannelMemoryMode = "normal") => {
      // Create ASCII slug for name; keep original as displayName for non-ASCII input
      const slug = name.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
      const channelName = slug || `ch-${Date.now().toString(36)}`;
      const displayName = slug !== name.toLowerCase().replace(/\s+/g, "-") ? name : undefined;
      chatApi
        .createChannel(botId, channelName, displayName, undefined, memoryMode)
        .then((ch) => {
          const existing = useChatStore.getState().channels;
          const alreadyExists = existing.some((c) => c.name === ch.name);
          if (!alreadyExists) {
            const updated = [...existing, ch];
            store.setChannels(updated, { botId });
            try { localStorage.setItem(CHANNELS_CACHE_KEY(botId), JSON.stringify(updated)); } catch { /* ignore */ }
          }
          handleChannelSelect(ch.name);
        })
        .catch((err) => {
          console.error("[chat] Failed to create channel:", err);
        });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [botId, handleChannelSelect],
  );

  const handleDeleteChannel = useCallback(
    (channelName: string) => {
      if (!confirm(`Delete #${channelName}?`)) return;
      chatApi
        .deleteChannel(botId, channelName)
        .then(() => {
          const currentState = useChatStore.getState();
          const remaining = currentState.channels.filter((c) => c.name !== channelName);
          const nextChannel = getNextChannelAfterDeletion(currentState.channels, channelName);
          store.setChannels(remaining, { botId });
          try { localStorage.setItem(CHANNELS_CACHE_KEY(botId), JSON.stringify(remaining)); } catch { /* ignore */ }
          if (currentState.activeChannel === channelName) {
            if (nextChannel) {
              handleChannelSelect(nextChannel);
            } else {
              store.setActiveChannel("");
              router.push(`/dashboard/${botId}/chat`);
            }
          }
        })
        .catch((err) => {
          console.error("[chat] Failed to delete channel:", err);
        });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [botId, handleChannelSelect, router],
  );

  const handleCreateCategory = useCallback(
    (name: string) => {
      const allCats = [...DEFAULT_CATEGORIES, ...customCategories];
      if (allCats.includes(name)) return;
      const updated = [...customCategories, name];
      setCustomCategories(updated);
      try {
        localStorage.setItem(CUSTOM_CATEGORIES_KEY(botId), JSON.stringify(updated));
      } catch { /* ignore */ }
    },
    [botId, customCategories],
  );

  const handleReorderChannels = useCallback(
    (reordered: Channel[]) => {
      store.setChannels(reordered, { botId });
      try { localStorage.setItem(CHANNELS_CACHE_KEY(botId), JSON.stringify(reordered)); } catch { /* ignore */ }
      chatApi.reorderChannels(
        botId,
        reordered.map((c) => ({ name: c.name, position: c.position, category: c.category || "Other" })),
      ).catch(() => {});
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [botId],
  );

  const handleDeleteCategory = useCallback(
    (name: string) => {
      if (DEFAULT_CATEGORIES.includes(name)) return;
      // Move channels in this category to "Other"
      const currentChannels = useChatStore.getState().channels;
      const updated = currentChannels.map((c) =>
        (c.category || "General") === name ? { ...c, category: "Other" } : c,
      );
      store.setChannels(updated, { botId });
      // Sync reorder to server
      chatApi.reorderChannels(
        botId,
        updated.map((c) => ({ name: c.name, position: c.position, category: c.category || "Other" })),
      ).catch(() => {});
      // Remove from custom categories
      const newCats = customCategories.filter((c) => c !== name);
      setCustomCategories(newCats);
      try {
        localStorage.setItem(CUSTOM_CATEGORIES_KEY(botId), JSON.stringify(newCats));
      } catch { /* ignore */ }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [botId, customCategories],
  );

  const { activeChannel, messages, serverMessages, channelStates, channels, selectionMode, selectedMessages, queuedMessages, controlRequests } =
    useChatStore();
  const hasActiveChannel = activeChannel.length > 0;
  const activeChannelRecord = useMemo(
    () => channels.find((c) => c.name === activeChannel) ?? null,
    [activeChannel, channels],
  );
  const activeChannelTitle = useMemo(() => {
    if (!hasActiveChannel) return t.chat.channelsTitle;
    return formatChannelBaseLabel({
      name: activeChannel,
      display_name: localizeChannel(activeChannel, activeChannelRecord?.display_name ?? null, locale),
    });
  }, [activeChannel, activeChannelRecord?.display_name, hasActiveChannel, locale, t.chat.channelsTitle]);
  const activeChannelMemoryLabel = useMemo(() => {
    if (!hasActiveChannel) return null;
    return formatChannelMemoryLabel(activeChannelRecord?.memory_mode);
  }, [activeChannelRecord?.memory_mode, hasActiveChannel]);
  const channelState = channelStates[activeChannel] ?? {
    streaming: false,
    streamingText: "",
    thinkingText: "",
    error: null,
  };

  const handleStartExportSelection = useCallback(() => {
    if (!activeChannel) return;
    store.startSelectionMode(activeChannel);
  }, [activeChannel, store]);

  useEffect(() => {
    if (!activeChannel) {
      setChannelModelSelectionState(fallbackChannelModelSelection);
      return;
    }
    setChannelModelSelectionState(
      getChannelModelSelection(botId, activeChannel, serverBackedChannelModelSelection(activeChannel)),
    );
  }, [activeChannel, botId, fallbackChannelModelSelection, serverBackedChannelModelSelection]);

  const handleChannelModelSelectionChange = useCallback(
    (nextModelSelection: string, nextRouterType: string) => {
      if (!activeChannel) return;
      const next = {
        modelSelection: nextModelSelection,
        routerType: nextRouterType,
      };
      setChannelModelSelectionState(next);
      setChannelModelSelection(botId, activeChannel, next);
      void chatApi.updateChannel(botId, activeChannel, {
        model_selection: nextModelSelection,
        router_type: nextRouterType,
      }).catch((err) => {
        console.warn("[chat] failed to persist channel model selection:", err);
      });
    },
    [activeChannel, botId],
  );

  return (
    <div className="flex h-full bg-background overflow-hidden">
      <ChatSidebar
        channels={channels}
        activeChannel={activeChannel}
        currentBotId={botId}
        botName={botName}
        botStatus={currentBotStatus}
        bots={bots}
        maxBots={maxBots}
        editing={editing}
        customCategories={customCategories}
        refreshing={refreshing}
        mobileOpen={sidebarOpen}
        onChannelSelect={handleChannelSelect}
        onDeleteChannel={handleDeleteChannel}
        onCreateChannel={handleCreateChannel}
        onCreateCategory={handleCreateCategory}
        onDeleteCategory={handleDeleteCategory}
        onRefreshChannels={handleRefreshChannels}
        onToggleEdit={() => setEditing(!editing)}
        onCancelEdit={() => setEditing(false)}
        onMobileClose={() => setSidebarOpen(false)}
        onReorderChannels={handleReorderChannels}
        onRenameChannel={(channelName, newDisplayName) => {
          // Optimistic update — chatApi.updateChannel returns void; reflect
          // the change locally and let the next fetchChannels reconcile
          // against the server. On error the rename simply doesn't stick.
          const next = useChatStore
            .getState()
            .channels.map((c) =>
              c.name === channelName ? { ...c, display_name: newDisplayName } : c,
            );
          store.setChannels(next, { botId });
          try { localStorage.setItem(CHANNELS_CACHE_KEY(botId), JSON.stringify(next)); } catch { /* ignore */ }
          chatApi
            .updateChannel(botId, channelName, { display_name: newDisplayName })
            .catch((err) => console.error("[chat] rename channel failed:", err));
        }}
        onRenameCategory={(oldName, newName) => {
          // Re-tag every channel currently in oldName via batched PATCHes.
          const affected = useChatStore
            .getState()
            .channels.filter((c) => c.category === oldName);
          const next = useChatStore
            .getState()
            .channels.map((c) =>
              c.category === oldName ? { ...c, category: newName } : c,
            );
          store.setChannels(next, { botId });
          try { localStorage.setItem(CHANNELS_CACHE_KEY(botId), JSON.stringify(next)); } catch { /* ignore */ }
          Promise.all(
            affected.map((c) =>
              chatApi.updateChannel(botId, c.name, { category: newName }),
            ),
          ).catch((err) => console.error("[chat] rename category failed:", err));
          if (customCategories.includes(oldName)) {
            const updated = customCategories.map((c) => (c === oldName ? newName : c));
            setCustomCategories(updated);
            try { localStorage.setItem(CUSTOM_CATEGORIES_KEY(botId), JSON.stringify(updated)); } catch { /* ignore */ }
          }
        }}
      />
      <div
        className="flex-1 flex flex-col min-w-[300px] relative"
        onDrop={handleChatDrop}
        onDragOver={handleChatDragOver}
        onDragEnter={handleChatDragEnter}
        onDragLeave={handleChatDragLeave}
      >
        {/* Drag overlay */}
        {isDraggingOver && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-primary/[0.04] border-2 border-dashed border-primary/30 rounded-2xl pointer-events-none">
            <div className="flex items-center gap-2 text-sm text-primary/70 font-medium bg-white/90 backdrop-blur-sm px-5 py-3 rounded-xl border border-primary/20 shadow-sm">
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
              {t.chat.dropFilesToAttach}
            </div>
          </div>
        )}
        {/* Channel header */}
        <div className="px-4 md:px-6 py-3 flex items-center gap-3 border-b border-black/[0.06]">
          {/* Mobile: hamburger for sidebar */}
          <button
            onClick={() => setSidebarOpen(true)}
            className="md:hidden p-1.5 -ml-1 text-secondary/60 hover:text-foreground rounded-xl hover:bg-black/[0.04] transition-all duration-200"
            aria-label={t.chat.openChannels}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </button>
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <h1 className="min-w-0 truncate text-sm font-medium text-foreground/80">
              {activeChannelTitle}
            </h1>
            {activeChannelMemoryLabel && (
              <span
                className="shrink-0 rounded-full border border-black/[0.08] bg-black/[0.035] px-2 py-0.5 text-[11px] font-medium leading-none text-secondary/75"
                aria-label={activeChannelMemoryLabel}
                title={activeChannelMemoryLabel}
              >
                {activeChannelMemoryLabel}
              </span>
            )}
          </div>
          {/* Export */}
          {hasActiveChannel && (
            <button
              onClick={handleStartExportSelection}
              className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] text-secondary/55 hover:text-foreground/75 rounded-lg hover:bg-black/[0.04] transition-all duration-200 cursor-pointer"
              aria-label={t.chat.startExportSelection}
              title={t.chat.startExportSelection}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <circle cx="18" cy="5" r="3" />
                <circle cx="6" cy="12" r="3" />
                <circle cx="18" cy="19" r="3" />
                <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
                <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
              </svg>
              <span>{t.chat.exportConversation}</span>
            </button>
          )}
          {/* Session reset */}
          {hasActiveChannel && (
            <button
              onClick={handleReset}
              className="px-2.5 py-1 text-[11px] text-secondary/50 hover:text-foreground/70 rounded-lg hover:bg-black/[0.04] transition-all duration-200"
              aria-label={t.chat.resetSession}
            >
              {t.chat.reset}
            </button>
          )}
          {/* Mobile: dashboard link */}
          <a
            href={`/dashboard/${botId}/overview`}
            className="md:hidden p-1.5 text-secondary/60 hover:text-foreground rounded-xl hover:bg-black/[0.04] transition-all duration-200"
            aria-label="Dashboard"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" />
            </svg>
          </a>
        </div>

        {/* Telegram connection banner */}
        {showTelegramBanner && (
          <div className="px-4 md:px-6 pt-2">
            <div className="flex items-center gap-3 bg-[#2AABEE]/10 border border-[#2AABEE]/20 rounded-xl px-4 py-2.5">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" className="shrink-0">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8l-1.6 7.52c-.12.54-.44.67-.9.42l-2.48-1.83-1.2 1.15c-.13.13-.24.24-.5.24l.18-2.52 4.56-4.12c.2-.18-.04-.27-.3-.1L8.5 13.37l-2.42-.76c-.52-.16-.53-.52.12-.77l9.46-3.64c.44-.16.82.1.68.6z" fill="#2AABEE"/>
              </svg>
              <button
                onClick={() => setShowTelegramGuide(true)}
                className="flex-1 text-left cursor-pointer"
              >
                <p className="text-[13px] text-foreground/90 font-medium">
                  {telegramBotUsername
                    ? t.botCard.telegramBannerStart.replace("{username}", telegramBotUsername)
                    : t.botCard.telegramBannerConnect}
                </p>
              </button>
              <button
                onClick={handleDismissBanner}
                className="shrink-0 p-1 text-secondary/50 hover:text-foreground/70 transition-colors rounded-lg hover:bg-black/[0.04]"
                aria-label={t.chat.dismiss}
              >
                <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                  <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
                </svg>
              </button>
            </div>
          </div>
        )}

        {/* Provisioning overlay */}
        {currentBotStatus === "provisioning" ? (
          <div className="flex-1 flex items-center justify-center px-4">
            <div className="w-full max-w-sm text-center">
              <div className="w-12 h-12 mx-auto mb-4 rounded-full bg-primary/10 flex items-center justify-center">
                <svg className="w-6 h-6 text-primary animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
              </div>
              <h2 className="text-base font-bold text-foreground mb-1">
                {t.botCard.provisioningOverlayTitle}
              </h2>
              <p className="text-sm text-secondary mb-5">
                {t.botCard.provisioningOverlayDesc}
              </p>

              {/* Progress bar */}
              <div className="w-full h-2 rounded-full bg-black/[0.06] overflow-hidden mb-2">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-primary to-primary-light transition-all duration-1000 ease-out"
                  style={{ width: `${Math.max(provisioningPct, 2)}%` }}
                />
              </div>

              <div className="flex items-center justify-between text-xs text-secondary">
                <span>{Math.round(provisioningPct)}%</span>
                <span>{getProvisioningStepLabel(provisioningStep, t)}</span>
              </div>
            </div>
          </div>
        ) : hasActiveChannel ? (
          <>
            <ChatMessages
              ref={chatMessagesRef}
              key={activeChannel}
              messages={messages[activeChannel] ?? []}
              serverMessages={serverMessages[activeChannel] ?? []}
              channelState={channelState}
              uiLanguage={locale}
              loading={!e2eeReady || initialHistoryLoading}
              botId={botId}
              selectionMode={selectionMode}
              selectedMessages={selectedMessages[activeChannel]}
              onToggleSelect={(msgId) => store.toggleMessageSelection(activeChannel, msgId)}
              onEnterSelectionMode={(msgId) => store.enterSelectionMode(activeChannel, msgId)}
              onSelectAll={() => store.selectAllMessages(activeChannel)}
              onDeselectAll={() => store.deselectAllMessages(activeChannel)}
              onExportSelected={handleExportSelected}
              onDeleteSelected={handleDeleteSelected}
              onExitSelectionMode={() => store.exitSelectionMode()}
              onReplyTo={handleReplyTo}
              queuedMessages={queuedMessages[activeChannel]}
              onCancelQueued={handleCancelQueued}
              controlRequests={controlRequests[activeChannel] ?? []}
              onRespondControlRequest={handleRespondControlRequest}
              onLoadOlder={handleLoadOlder}
              hasOlderMessages={hasOlderMessages}
              loadingOlder={loadingOlder}
            />

            {channelState.error && (
              <div className="px-4 pb-1">
                <div className="max-w-3xl mx-auto text-xs text-red-400/80 bg-red-500/[0.06] rounded-xl px-3 py-2">
                  {channelState.error}
                </div>
              </div>
            )}

            {selectedKbDocs.length > 0 && (
              <div className="px-4 md:px-8 lg:px-12">
                <div className="max-w-3xl mx-auto">
                  <KbContextBar docs={selectedKbDocs} onRemove={handleRemoveKbDoc} />
                </div>
              </div>
            )}

            <ChatInput
              ref={chatInputRef}
              onSend={handleSend}
              onReset={handleReset}
              streaming={channelState.streaming}
              uiLanguage={locale}
              onCancel={handleCancel}
              disabled={currentBotStatus !== "active"}
              replyingTo={replyingTo}
              onCancelReply={handleCancelReply}
              queuedCount={(queuedMessages[activeChannel] ?? []).length}
              onCancelQueue={handleCancelQueue}
              cancelHint={escArmedUntil === null ? undefined : t.chat.escAgainToStop}
              queueFull={(queuedMessages[activeChannel] ?? []).length >= MAX_QUEUED_MESSAGES}
              streamingMode={streamingComposerMode}
              onStreamingModeChange={setStreamingComposerMode}
              steeringDisabled={selectedKbDocs.length > 0}
              steeringDisabledReason={t.chat.selectedKnowledgeSendsAfterRun}
              kbDocs={kbAllDocs}
              onSelectKbDoc={handleToggleKbDoc}
              uploadStates={uploadStates}
              customSkills={customSkills}
              composerAccessory={
                <ChatModelPicker
                  botId={botId}
                  modelSelection={channelModelSelection.modelSelection}
                  routerType={channelModelSelection.routerType}
                  apiKeyMode={apiKeyMode}
                  subscriptionPlan={subscriptionPlan}
                  persistMode="local"
                  menuPlacement="top"
                  onModelSelectionChange={handleChannelModelSelectionChange}
                />
              }
            />
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center px-6">
            <div className="max-w-sm text-center">
              <h2 className="text-base font-semibold text-foreground">{t.chat.noChannelsTitle}</h2>
              <p className="mt-2 text-sm text-secondary">
                {t.chat.noChannelsDescription}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* KB Side Panel */}
      <KbSidePanel
        botId={botId}
        collections={kbCollections}
        loading={kbLoading}
        refreshing={kbRefreshing}
        workspaceFiles={workspaceFiles}
        workspaceLoading={workspaceLoading}
        workspaceRefreshing={workspaceRefreshing}
        selectedDocs={selectedKbDocs}
        onToggleDoc={handleToggleKbDoc}
        onRefresh={kbRefresh}
        onWorkspaceRefresh={workspaceRefresh}
        getAccessToken={getAccessToken}
        missionChannelType="app"
        missionChannelId={activeChannel}
        channelState={channelState}
        uiLanguage={locale}
        queuedMessages={queuedMessages[activeChannel] ?? []}
        controlRequests={controlRequests[activeChannel] ?? []}
      />

      {/* Telegram connect modal */}
      {showTelegramGuide && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setShowTelegramGuide(false)}
        >
          <div
            className="bg-white border border-black/10 rounded-2xl w-full max-w-md mx-4 max-h-[85vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-bold text-foreground">{t.botCard.telegramModalTitle}</h3>
                <button
                  onClick={() => setShowTelegramGuide(false)}
                  className="text-secondary hover:text-foreground transition-colors cursor-pointer p-1"
                >
                  <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                    <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
                  </svg>
                </button>
              </div>

              {telegramBotUsername && !telegramOwnerId ? (
                /* Case B: token exists, needs /start */
                <div>
                  <div className="bg-[#2AABEE]/8 border border-[#2AABEE]/15 rounded-xl p-4 mb-4">
                    <p className="text-sm font-semibold text-foreground mb-1">{t.botCard.telegramModalSendStart}</p>
                    <p className="text-[13px] text-secondary leading-relaxed mb-3">
                      {t.botCard.telegramModalSendStartDesc.replace("{command}", "/start")}
                    </p>
                    <a
                      href={`https://t.me/${telegramBotUsername}?start=1`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center justify-center gap-2 bg-[#2AABEE] rounded-xl py-3 hover:bg-[#2AABEE]/90 transition-colors"
                    >
                      <span className="text-sm font-semibold text-white">
                        {formatChatCopy(t.chat.openTelegram, { username: telegramBotUsername })}
                      </span>
                      <span className="text-secondary">{"\u2197"}</span>
                    </a>
                  </div>
                </div>
              ) : (
                /* Case A: no token — show StepTelegram auto-connect */
                <StepTelegram onConnect={handleTelegramConnect} />
              )}
            </div>
          </div>
        </div>
      )}

      {/* Selected message export modal */}
      {exportDraft && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setExportDraft(null)}
        >
          <div
            className="bg-white border border-black/10 rounded-2xl w-full max-w-md mx-4 p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 mb-4">
              <div>
                <h3 className="text-base font-bold text-foreground">{t.chat.exportSelectedMessagesTitle}</h3>
                <p className="mt-1 text-sm text-secondary">
                  {formatChatCopy(t.chat.exportSelectedMessagesCount, {
                    count: exportDraft.messages.length,
                    channel: exportDraft.channelName,
                  })}
                </p>
              </div>
              <button
                onClick={() => setExportDraft(null)}
                className="text-secondary hover:text-foreground transition-colors cursor-pointer p-1"
                aria-label={t.chat.closeExportDialog}
              >
                <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                  <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
                </svg>
              </button>
            </div>

            <div className="space-y-2">
              <button
                onClick={handleDownloadExport}
                className="w-full flex items-center justify-between gap-3 rounded-xl border border-black/[0.08] bg-black/[0.03] px-4 py-3 text-left hover:bg-black/[0.06] transition-colors cursor-pointer"
              >
                <span>
                  <span className="block text-sm font-semibold text-foreground">{t.chat.downloadMarkdown}</span>
                  <span className="block text-xs text-secondary/70">{exportDraft.filename}</span>
                </span>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-secondary/70">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
              </button>

              <button
                onClick={() => void handleCreatePublicExport()}
                disabled={creatingExportLink}
                className="w-full flex items-center justify-between gap-3 rounded-xl border border-[#7C3AED]/20 bg-[#7C3AED]/5 px-4 py-3 text-left hover:bg-[#7C3AED]/10 transition-colors cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
              >
                <span>
                  <span className="block text-sm font-semibold text-foreground">{t.chat.createPublicLink}</span>
                  <span className="block text-xs text-secondary/70">
                    {t.chat.createPublicLinkDescription}
                  </span>
                </span>
                {creatingExportLink ? (
                  <svg className="w-4 h-4 text-[#7C3AED] animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <circle cx="12" cy="12" r="10" strokeDasharray="31.4 31.4" strokeLinecap="round" />
                  </svg>
                ) : (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-[#7C3AED]">
                    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                  </svg>
                )}
              </button>
            </div>

            {exportError && (
              <div className="mt-3 rounded-xl bg-red-500/[0.06] px-3 py-2 text-xs text-red-500">
                {exportError}
              </div>
            )}

            {exportLink && (
              <div className="mt-3 rounded-xl border border-black/[0.08] bg-black/[0.03] p-3">
                <p className="mb-2 text-xs font-medium text-secondary/70">{t.chat.publicLinkCreated}</p>
                <div className="flex items-center gap-2">
                  <input
                    readOnly
                    value={exportLink}
                    className="min-w-0 flex-1 rounded-lg border border-black/[0.08] bg-white px-2 py-1.5 text-xs text-secondary"
                  />
                  <button
                    onClick={() => navigator.clipboard?.writeText(exportLink).catch(() => {})}
                    className="rounded-lg bg-black/[0.06] px-2.5 py-1.5 text-xs font-medium text-foreground hover:bg-black/[0.1] transition-colors cursor-pointer"
                  >
                    {t.chat.copy}
                  </button>
                  <a
                    href={exportLink}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-lg bg-[#7C3AED] px-2.5 py-1.5 text-xs font-medium text-white hover:bg-[#6D28D9] transition-colors"
                  >
                    {t.chat.open}
                  </a>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Delete confirmation modal */}
      {showDeleteConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setShowDeleteConfirm(false)}
        >
          <div
            className="bg-white border border-black/10 rounded-2xl w-full max-w-sm mx-4 p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-full bg-red-50 flex items-center justify-center shrink-0">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="3 6 5 6 21 6" />
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                </svg>
              </div>
              <div>
                <h3 className="text-base font-bold text-foreground">{t.chat.deleteMessagesTitle}</h3>
                <p className="text-sm text-secondary">
                  {formatChatCopy(t.chat.deleteMessagesCount, {
                    count: selectedMessages[activeChannel]?.size ?? 0,
                  })}
                </p>
              </div>
            </div>
            <p className="text-xs text-secondary/60 mb-4">
              {t.chat.deleteMessagesWarning}
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium text-foreground bg-black/[0.04] hover:bg-black/[0.08] transition-colors cursor-pointer"
              >
                {t.chat.cancel}
              </button>
              <button
                onClick={handleConfirmDelete}
                className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium text-white bg-red-500 hover:bg-red-600 transition-colors cursor-pointer"
              >
                {t.chat.delete}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Undo toast */}
      {undoData && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 animate-in slide-in-from-bottom-4 fade-in duration-200">
          <div className="flex items-center gap-3 bg-gray-900 text-white rounded-xl px-4 py-3 shadow-lg">
            <span className="text-sm">{t.chat.messagesDeleted}</span>
            <button
              onClick={handleUndo}
              className="text-sm font-medium text-[#7C3AED] hover:text-[#9b6aed] transition-colors cursor-pointer"
            >
              {t.chat.undo}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
