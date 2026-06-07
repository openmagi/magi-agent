"use client";

import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type ReactNode } from "react";
import { CHAT_ATTACHMENT_ACCEPT, validateFile } from "@/lib/chat/attachments";
import { isImageMimetype, formatFileSize } from "@/lib/chat/attachment-marker";
import { extractClipboardImageFiles } from "@/lib/chat/clipboard-images";
import { kbUploadKey } from "@/lib/chat/kb-uploads";
import type { ChatResponseLanguage, ReplyTo, KbDocReference } from "@/lib/chat/types";
import { isStreamingComposerBlockedByQueue } from "@/lib/chat/send-policy";
import type { StreamingComposerMode } from "@/lib/chat/send-policy";
import { SKILLS } from "@/lib/skills-catalog";
import type { KbDocEntry } from "@/hooks/use-kb-docs";
import type { PendingKbUpload } from "@/lib/chat/kb-uploads";

interface SlashEntry {
  command: string;
  label: string;
  category: string;
  builtin?: boolean;
  searchText?: string;
}

const BUILTIN_COMMANDS: SlashEntry[] = [
  { command: "reset", label: "Reset conversation", category: "system", builtin: true },
  { command: "status", label: "Show bot status", category: "system", builtin: true },
  { command: "compact", label: "Compact memory", category: "system", builtin: true },
  { command: "help", label: "Show help", category: "system", builtin: true },
];

const BUNDLED_SLASH: SlashEntry[] = (() => {
  const entries: SlashEntry[] = [...BUILTIN_COMMANDS];
  for (const skill of SKILLS) {
    if (!skill.commands?.length) continue;
    entries.push({ command: skill.commands[0], label: skill.id, category: skill.category });
    for (let i = 1; i < skill.commands.length; i++) {
      entries.push({ command: skill.commands[i], label: skill.id, category: skill.category });
    }
  }
  return entries;
})();

export interface ChatInputCustomSkill {
  name: string;
  title: string;
  description?: string;
  tags?: string[];
}

export function buildSlashEntries(customSkills: ChatInputCustomSkill[] = []): SlashEntry[] {
  const entries: SlashEntry[] = [...BUNDLED_SLASH];
  const seenCommands = new Set(entries.map((entry) => entry.command.toLowerCase()));

  for (const skill of customSkills) {
    const command = normalizeSlashCommand(skill.name);
    if (!command) continue;
    const dedupeKey = command.toLowerCase();
    if (seenCommands.has(dedupeKey)) continue;
    seenCommands.add(dedupeKey);
    const label = skill.title.trim() || command;
    entries.push({
      command,
      label,
      category: "custom",
      searchText: [
        command,
        label,
        skill.description ?? "",
        ...(skill.tags ?? []),
      ].join(" "),
    });
  }

  return entries;
}

export function getSlashMatches(entries: SlashEntry[], query: string): SlashEntry[] {
  const normalizedQuery = query.toLowerCase();
  if (normalizedQuery === "") return entries.slice(0, 12);
  return entries
    .filter((entry) => {
      const haystack = `${entry.command} ${entry.label} ${entry.category} ${entry.searchText ?? ""}`.toLowerCase();
      return haystack.includes(normalizedQuery);
    })
    .slice(0, 12);
}

function normalizeSlashCommand(command: string): string {
  return command.trim().replace(/^\/+/, "").replace(/\s+/g, "-");
}

interface PendingFile {
  file: File;
  previewUrl: string | null;
}

export interface ChatInputHandle {
  addFiles: (files: FileList | File[]) => void;
  focus: () => void;
}

export interface ChatInputSendOptions {
  goalMode?: boolean;
}

export function buildChatInputSendOptions(runUntilDone: boolean): ChatInputSendOptions | undefined {
  return runUntilDone ? { goalMode: true } : undefined;
}

export function nextRunUntilDoneAfterSend(
  current: boolean,
  result: void | boolean,
): boolean {
  if (!current) return false;
  return result === false;
}

interface ChatInputProps {
  onSend: (
    text: string,
    files?: File[],
    options?: ChatInputSendOptions,
  ) => void | boolean | Promise<void | boolean>;
  onReset?: () => void;
  disabled?: boolean;
  streaming?: boolean;
  onCancel?: () => void;
  /** Active reply target (shown as banner above input). */
  replyingTo?: ReplyTo | null;
  /** Clear the active reply target. */
  onCancelReply?: () => void;
  /** Number of messages currently queued for this channel (Claude Code CLI-style). */
  queuedCount?: number;
  /** Called when the user clicks the "Cancel queue" button. */
  onCancelQueue?: () => void;
  /** Short status text shown next to the stop button. */
  cancelHint?: string;
  /** True when the user has already reached `MAX_QUEUED_MESSAGES`. */
  queueFull?: boolean;
  /** Composer behavior for text entered while a run is streaming. */
  streamingMode?: StreamingComposerMode;
  /** Called when the user switches between queueing and steering during a live run. */
  onStreamingModeChange?: (mode: StreamingComposerMode) => void;
  /** Force steering off when parent context cannot be injected safely. */
  steeringDisabled?: boolean;
  /** Short explanation shown when steering is unavailable. */
  steeringDisabledReason?: string;
  /** All KB documents available for @ autocomplete. */
  kbDocs?: KbDocEntry[];
  /** Called when user selects a KB doc via @ autocomplete. */
  onSelectKbDoc?: (doc: KbDocReference) => void;
  /** Live upload/indexing state for files currently attached in the composer. */
  uploadStates?: Record<string, PendingKbUpload>;
  /** Optional controls rendered as compact trailing controls inside the composer shell. */
  composerAccessory?: ReactNode;
  /** App UI language. Response language can differ for the current assistant turn. */
  uiLanguage?: ChatResponseLanguage;
  /** Custom skills installed for the current bot and exposed via slash autocomplete. */
  customSkills?: ChatInputCustomSkill[];
}

interface ComposerEnterEvent {
  key: string;
  shiftKey?: boolean;
  keyCode?: number;
  nativeEvent?: {
    isComposing?: boolean;
  };
}

interface ComposerEnterOptions {
  mobileWeb?: boolean;
}

export function prefersMobileWebLineBreaks(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  if (/Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(ua)) {
    return true;
  }

  const touchPoints = navigator.maxTouchPoints ?? 0;
  if (touchPoints <= 0 || typeof window === "undefined") return false;

  const coarsePointer =
    typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
  const noHover =
    typeof window.matchMedia === "function" && window.matchMedia("(hover: none)").matches;
  const narrowViewport = window.innerWidth < 768;
  return coarsePointer && (noHover || narrowViewport);
}

export function shouldSendComposerOnEnter(
  event: ComposerEnterEvent,
  options: ComposerEnterOptions = {},
): boolean {
  if (event.key !== "Enter" || event.shiftKey) return false;
  const nativeIsComposing = event.nativeEvent?.isComposing === true || event.keyCode === 229;
  if (nativeIsComposing) return false;
  return !(options.mobileWeb ?? prefersMobileWebLineBreaks());
}

export function shouldCancelStopOnPointerDown(pointerType: string): boolean {
  return pointerType === "touch" || pointerType === "pen";
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

function slashEntryLabel(entry: SlashEntry, language?: ChatResponseLanguage): string {
  if (!entry.builtin) return entry.label;
  switch (entry.command) {
    case "reset":
      return t(language, "Reset conversation", "대화 초기화");
    case "status":
      return t(language, "Show bot status", "봇 상태 보기");
    case "compact":
      return t(language, "Compact memory", "메모리 압축");
    case "help":
      return t(language, "Show help", "도움말 보기");
    default:
      return entry.label;
  }
}

function slashCategoryLabel(category: string, language?: ChatResponseLanguage): string {
  if (category === "system") return t(language, "system", "시스템");
  return category;
}

export const ChatInput = forwardRef<ChatInputHandle, ChatInputProps>(function ChatInput(
  {
    onSend,
    onReset,
    disabled,
    streaming,
    onCancel,
    replyingTo,
    onCancelReply,
    queuedCount = 0,
    onCancelQueue,
    cancelHint: _cancelHint,
    queueFull = false,
    streamingMode = "queue",
    onStreamingModeChange,
    steeringDisabled = false,
    steeringDisabledReason,
    kbDocs,
    onSelectKbDoc,
    uploadStates,
    composerAccessory,
    uiLanguage,
    customSkills,
  },
  ref,
) {
  const [text, setText] = useState("");
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [slashIdx, setSlashIdx] = useState(0);
  const [kbIdx, setKbIdx] = useState(0);
  const [runUntilDone, setRunUntilDone] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const slashRef = useRef<HTMLDivElement>(null);
  const stopPointerHandledRef = useRef(false);
  const stopPointerResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const language = uiLanguage;
  const steeringUnavailable = steeringDisabled || pendingFiles.length > 0;
  const effectiveStreamingMode: StreamingComposerMode =
    streamingMode === "steer" && steeringUnavailable ? "queue" : streamingMode;
  const queueBlocked = isStreamingComposerBlockedByQueue({
    queueFull,
    mode: effectiveStreamingMode,
  });
  const steeringUnavailableReason =
    pendingFiles.length > 0
      ? t(language, "Attachments will send after the current run.", "첨부파일은 현재 실행 후 전송됩니다.")
      : steeringDisabledReason
        ?? t(language, "Selected context will send after the current run.", "선택한 컨텍스트는 현재 실행 후 전송됩니다.");

  // Slash autocomplete: detect "/word" token at cursor position (works mid-sentence)
  const [cursorPos, setCursorPos] = useState(0);
  const slashToken = useMemo(() => {
    // Find the slash-token at or before cursor
    const before = text.slice(0, cursorPos);
    const match = before.match(/(?:^|\s)(\/\S*)$/);
    if (!match) return null;
    const token = match[1]; // e.g. "/know"
    const query = token.slice(1).toLowerCase(); // e.g. "know"
    const start = before.length - token.length;
    return query.includes(" ") ? null : { query, start, end: start + token.length };
  }, [text, cursorPos]);
  const slashQuery = slashToken?.query ?? null;
  const prevQueryRef = useRef(slashQuery);
  const slashEntries = useMemo(() => buildSlashEntries(customSkills), [customSkills]);
  const slashMatches = useMemo(() => {
    if (slashQuery === null) return [];
    return getSlashMatches(slashEntries, slashQuery);
  }, [slashEntries, slashQuery]);
  const slashOpen = slashMatches.length > 0;

  // Reset index when query changes (no useEffect — derive synchronously)
  if (prevQueryRef.current !== slashQuery) {
    prevQueryRef.current = slashQuery;
    if (slashIdx !== 0) setSlashIdx(0);
  }

  // Scroll selected item into view
  useEffect(() => {
    if (!slashOpen || !slashRef.current) return;
    const el = slashRef.current.children[slashIdx] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [slashIdx, slashOpen]);

  const acceptSlash = useCallback((entry: SlashEntry) => {
    if (slashToken) {
      // Replace only the slash token portion of the text
      const before = text.slice(0, slashToken.start);
      const after = text.slice(slashToken.end);
      const replacement = `/${entry.command} `;
      setText(before + replacement + after);
      // Move cursor after the inserted command
      const newPos = slashToken.start + replacement.length;
      setCursorPos(newPos);
      requestAnimationFrame(() => {
        textareaRef.current?.setSelectionRange(newPos, newPos);
      });
    } else {
      setText(`/${entry.command} `);
    }
    setSlashIdx(0);
    textareaRef.current?.focus();
  }, [slashToken, text]);

  // --- @ KB autocomplete ---
  const kbRef = useRef<HTMLDivElement>(null);
  const kbToken = useMemo(() => {
    const before = text.slice(0, cursorPos);
    // @ must be preceded by non-alphanumeric (or start of string) to avoid email triggers
    const match = before.match(/(?:^|[^a-zA-Z0-9])@([^\s]*)$/);
    if (!match) return null;
    const query = match[1].toLowerCase();
    const fullMatch = match[0];
    // start of the @-token in the original text
    const start = before.length - fullMatch.length + (fullMatch.length - match[1].length - 1);
    return { query, start, end: before.length };
  }, [text, cursorPos]);
  const kbQuery = kbToken?.query ?? null;
  const prevKbQueryRef = useRef(kbQuery);
  const kbMatches = useMemo(() => {
    if (kbQuery === null || !kbDocs?.length) return [];
    if (kbQuery === "") return kbDocs.filter((d) => d.status === "ready").slice(0, 12);
    return kbDocs
      .filter(
        (d) =>
          d.status === "ready" &&
          (d.filename.toLowerCase().includes(kbQuery) ||
            d.collectionName.toLowerCase().includes(kbQuery)),
      )
      .slice(0, 12);
  }, [kbQuery, kbDocs]);
  const kbOpen = kbMatches.length > 0 && !slashOpen;

  if (prevKbQueryRef.current !== kbQuery) {
    prevKbQueryRef.current = kbQuery;
    if (kbIdx !== 0) setKbIdx(0);
  }

  useEffect(() => {
    if (!kbOpen || !kbRef.current) return;
    const el = kbRef.current.children[kbIdx] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [kbIdx, kbOpen]);

  const acceptKb = useCallback(
    (entry: KbDocEntry) => {
      if (kbToken && onSelectKbDoc) {
        // Remove the @query text from input
        const before = text.slice(0, kbToken.start);
        const after = text.slice(kbToken.end);
        setText(before + after);
        const newPos = kbToken.start;
        setCursorPos(newPos);
        requestAnimationFrame(() => {
          textareaRef.current?.setSelectionRange(newPos, newPos);
        });
        onSelectKbDoc({
          id: entry.id,
          filename: entry.filename,
          collectionId: entry.collectionId,
          collectionName: entry.collectionName,
        });
      }
      setKbIdx(0);
      textareaRef.current?.focus();
    },
    [kbToken, text, onSelectKbDoc],
  );

  const addFiles = useCallback((fileList: FileList | File[]) => {
    const files = Array.from(fileList);
    const newPending: PendingFile[] = [];
    for (const file of files) {
      const error = validateFile(file);
      if (error) {
        alert(error);
        continue;
      }
      const previewUrl = isImageMimetype(file.type) ? URL.createObjectURL(file) : null;
      newPending.push({ file, previewUrl });
    }
    setPendingFiles((prev) => [...prev, ...newPending]);
  }, []);

  useImperativeHandle(ref, () => ({
    addFiles,
    focus: () => textareaRef.current?.focus(),
  }), [addFiles]);

  const removeFile = useCallback((index: number) => {
    setPendingFiles((prev) => {
      const removed = prev[index];
      if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
      return prev.filter((_, i) => i !== index);
    });
  }, []);

  const clearStopPointerHandled = useCallback(() => {
    stopPointerHandledRef.current = false;
    if (stopPointerResetTimerRef.current !== null) {
      clearTimeout(stopPointerResetTimerRef.current);
      stopPointerResetTimerRef.current = null;
    }
  }, []);

  const markStopPointerHandled = useCallback(() => {
    stopPointerHandledRef.current = true;
    if (stopPointerResetTimerRef.current !== null) {
      clearTimeout(stopPointerResetTimerRef.current);
    }
    stopPointerResetTimerRef.current = setTimeout(() => {
      stopPointerHandledRef.current = false;
      stopPointerResetTimerRef.current = null;
    }, 750);
  }, []);

  useEffect(() => clearStopPointerHandled, [clearStopPointerHandled]);

  const handleStopPointerDown = useCallback(
    (event: React.PointerEvent<HTMLButtonElement>) => {
      if (!shouldCancelStopOnPointerDown(event.pointerType)) return;
      event.preventDefault();
      markStopPointerHandled();
      onCancel?.();
    },
    [markStopPointerHandled, onCancel],
  );

  const handleStopClick = useCallback(() => {
    if (stopPointerHandledRef.current) {
      clearStopPointerHandled();
      return;
    }
    onCancel?.();
  }, [clearStopPointerHandled, onCancel]);

  const handleSend = useCallback(async () => {
    const trimmed = text.trim();
    if (!trimmed && pendingFiles.length === 0) return;
    if (trimmed.toLowerCase() === "/reset") {
      onReset?.();
      setText("");
      if (textareaRef.current) textareaRef.current.style.height = "auto";
      return;
    }
    setIsSubmitting(true);
    try {
      const result = await onSend(
        trimmed,
        pendingFiles.length > 0 ? pendingFiles.map((p) => p.file) : undefined,
        buildChatInputSendOptions(runUntilDone),
      );
      if (result === false) return;
      for (const p of pendingFiles) {
        if (p.previewUrl) URL.revokeObjectURL(p.previewUrl);
      }
      setText("");
      setPendingFiles([]);
      setRunUntilDone(nextRunUntilDoneAfterSend(runUntilDone, result));
      if (textareaRef.current) textareaRef.current.style.height = "auto";
    } finally {
      setIsSubmitting(false);
    }
  }, [text, pendingFiles, onSend, onReset, runUntilDone]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      const enterSends = shouldSendComposerOnEnter(e);

      // KB @ autocomplete navigation
      if (kbOpen) {
        if (e.key === "ArrowDown") { e.preventDefault(); setKbIdx((i) => (i + 1) % kbMatches.length); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); setKbIdx((i) => (i - 1 + kbMatches.length) % kbMatches.length); return; }
        if (e.key === "Tab" || enterSends) {
          e.preventDefault();
          if (kbMatches[kbIdx]) acceptKb(kbMatches[kbIdx]);
          return;
        }
        if (e.key === "Escape") { e.preventDefault(); setText(""); return; }
      }
      // Slash autocomplete navigation
      if (slashOpen) {
        if (e.key === "ArrowDown") { e.preventDefault(); setSlashIdx((i) => (i + 1) % slashMatches.length); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); setSlashIdx((i) => (i - 1 + slashMatches.length) % slashMatches.length); return; }
        if (e.key === "Tab" || enterSends) {
          e.preventDefault();
          if (slashMatches[slashIdx]) acceptSlash(slashMatches[slashIdx]);
          return;
        }
        if (e.key === "Escape") { e.preventDefault(); setText(""); return; }
      }

      if (enterSends) {
        e.preventDefault();
        if (disabled || isSubmitting) return;
        if (streaming && queueBlocked) return;
        void handleSend();
      }
    },
    [handleSend, streaming, disabled, isSubmitting, queueBlocked, slashOpen, slashMatches, slashIdx, acceptSlash, kbOpen, kbMatches, kbIdx, acceptKb],
  );

  const handleInput = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      if (e.dataTransfer.files.length > 0) addFiles(e.dataTransfer.files);
    },
    [addFiles],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);

  const handlePaste = useCallback(
    (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
      if (disabled || queueBlocked || isSubmitting) return;
      const imageFiles = extractClipboardImageFiles(e.clipboardData);
      if (imageFiles.length === 0) return;
      e.preventDefault();
      addFiles(imageFiles);
    },
    [addFiles, disabled, isSubmitting, queueBlocked],
  );

  const showStreamingStopControl = streaming && !text.trim() && pendingFiles.length === 0;
  const streamingStopControl = showStreamingStopControl ? (
    <div className="flex min-w-0 shrink-0 items-center gap-1">
      <button
        type="button"
        data-chat-stop-button="true"
        onPointerDown={handleStopPointerDown}
        onClick={handleStopClick}
        className="flex h-8 w-8 items-center justify-center rounded-full border border-red-500/15 bg-red-500/[0.08] text-red-500 transition-colors duration-200 hover:bg-red-500/[0.13] active:scale-95 touch-manipulation cursor-pointer"
        aria-label={t(language, "Stop", "중단")}
        title={t(language, "Stop (ESC)", "중단 (ESC)")}
      >
        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="6" width="12" height="12" rx="2" />
        </svg>
      </button>
      <kbd
        className="hidden pointer-events-none rounded border border-black/[0.06] px-1 py-0.5 font-mono text-[10px] text-secondary/50 md:inline"
        aria-hidden="true"
      >
        ESC
      </kbd>
    </div>
  ) : null;

  const sendEnabled = (text.trim() || pendingFiles.length > 0) && !disabled && !queueBlocked && !isSubmitting;

  return (
    <div
      className="px-3 pb-3 pt-2 sm:px-4 md:px-6 lg:px-10"
      data-chat-composer-dock="true"
      onDrop={handleDrop}
      onDragOver={handleDragOver}
    >
      <div className="mx-auto max-w-3xl">
        {queuedCount > 0 && (
          <div
            className="mb-2.5 flex items-center justify-between gap-3 rounded-2xl border border-amber-200/60 bg-amber-50/80 px-3.5 py-2.5 text-[11px] text-amber-900 backdrop-blur-sm"
            data-chat-queue-strip="true"
          >
            <div className="flex min-w-0 items-center gap-2.5" aria-live="polite">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-500/15 text-amber-600">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M6 8h9a4 4 0 0 1 0 8H9" />
                  <path d="m10 12-4-4 4-4" />
                </svg>
              </span>
              <span className="min-w-0">
                <span className="flex flex-wrap items-center gap-1.5">
                  <span className="font-semibold text-amber-950">
                    {t(language, "Queued after current run", "현재 실행 후 전송 대기")}
                  </span>
                  <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
                    {waitingCountLabel(queuedCount, language)}
                  </span>
                  {queueFull && (
                    <span className="rounded-full bg-red-100 px-1.5 py-0.5 text-[10px] font-semibold text-red-600">
                      {t(language, "Queue full", "대기열 가득 참")}
                    </span>
                  )}
                </span>
              </span>
            </div>
            {onCancelQueue && (
              <button
                type="button"
                onClick={onCancelQueue}
                className="shrink-0 rounded-lg px-2.5 py-1 text-[11px] font-semibold text-amber-700 transition-colors hover:bg-red-100 hover:text-red-600"
              >
                {t(language, "Clear queue", "대기열 비우기")}
              </button>
            )}
          </div>
        )}

        <div
          className="rounded-2xl border border-black/[0.06] bg-white shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_1px_rgba(0,0,0,0.03)]"
          data-chat-composer-panel="true"
        >
          {streaming && (
            <div
              className="flex items-center gap-2 border-b border-black/[0.05] px-3 py-1.5"
              data-chat-composer-toolbar="true"
            >
              <div className="min-w-0" data-streaming-composer-controls="true">
                <div
                  className="inline-grid grid-cols-2 rounded-md bg-black/[0.04] p-0.5"
                  role="group"
                  aria-label={t(language, "Streaming send mode", "실행 중 전송 방식")}
                >
                  <button
                    type="button"
                    data-streaming-mode-option="queue"
                    onClick={() => onStreamingModeChange?.("queue")}
                    className={`min-h-7 touch-manipulation rounded-md px-2.5 text-[11px] font-semibold transition-all ${
                      effectiveStreamingMode === "queue"
                        ? "bg-white text-foreground shadow-[0_1px_3px_rgba(0,0,0,0.08)]"
                        : "text-secondary/65 hover:text-foreground"
                    }`}
                    aria-pressed={effectiveStreamingMode === "queue"}
                    title={t(language, "Send after the current run reaches a checkpoint", "현재 실행이 체크포인트에 도달한 뒤 전송")}
                  >
                    {t(language, "Queue", "대기")}
                  </button>
                  <button
                    type="button"
                    data-streaming-mode-option="steer"
                    onClick={() => { if (!steeringUnavailable) onStreamingModeChange?.("steer"); }}
                    disabled={steeringUnavailable}
                    className={`min-h-7 touch-manipulation rounded-md px-2.5 text-[11px] font-semibold transition-all disabled:cursor-not-allowed disabled:opacity-40 ${
                      effectiveStreamingMode === "steer"
                        ? "bg-white text-foreground shadow-[0_1px_3px_rgba(0,0,0,0.08)]"
                        : "text-secondary/65 hover:text-foreground"
                    }`}
                    aria-pressed={effectiveStreamingMode === "steer"}
                    title={steeringUnavailable ? steeringUnavailableReason : t(language, "Send now as a text-only steering update", "텍스트 전용 조정 메시지를 지금 전송")}
                  >
                    {t(language, "Steer", "조정")}
                  </button>
                </div>
              </div>
              {steeringUnavailable && (
                <span className="text-[10px] leading-snug text-secondary/50" aria-live="polite">
                  {steeringUnavailableReason}
                </span>
              )}
            </div>
          )}

          {replyingTo && (
            <div className="mx-3 mt-2 flex items-start gap-2 rounded-xl bg-primary/[0.04] px-3 py-2 text-sm">
              <svg className="mt-0.5 shrink-0 text-primary" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polyline points="9 17 4 12 9 7" />
                <path d="M20 18v-2a4 4 0 0 0-4-4H4" />
              </svg>
              <div className="min-w-0 flex-1 leading-snug">
                <div className="text-[11px] font-medium text-primary">
                  {t(language, "Replying to", "답장 대상")}{" "}
                  {replyingTo.role === "user" ? t(language, "You", "나") : t(language, "Bot", "봇")}
                </div>
                <div className="truncate text-xs text-secondary/70">{replyingTo.preview}</div>
              </div>
              <button
                type="button"
                onClick={onCancelReply}
                aria-label={t(language, "Cancel reply", "답장 취소")}
                className="-m-1 shrink-0 rounded-md p-1 text-secondary/50 transition-colors hover:bg-black/[0.04] hover:text-foreground cursor-pointer"
              >
                <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
                </svg>
              </button>
            </div>
          )}

          {pendingFiles.length > 0 && (
            <div className="mx-3 mt-2 flex flex-wrap gap-2">
              {pendingFiles.map((pf, i) => (
                <div
                  key={i}
                  className="group relative flex items-center gap-2 rounded-xl border border-black/[0.06] bg-black/[0.02] p-2 max-w-[150px] sm:max-w-[200px]"
                >
                  {pf.previewUrl ? (
                    <img src={pf.previewUrl} alt={pf.file.name} className="h-10 w-10 rounded-lg object-cover" />
                  ) : (
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-black/[0.04]">
                      <svg className="h-5 w-5 text-secondary/50" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                        <path d="M14 2v6h6" />
                      </svg>
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs text-foreground">{pf.file.name}</p>
                    <p className="text-[10px] text-secondary/45">{formatFileSize(pf.file.size)}</p>
                    {uploadStates?.[kbUploadKey(pf.file)] && (() => {
                      const state = uploadStates[kbUploadKey(pf.file)];
                      const isFailed = state?.phase === "failed";
                      const isActive = state?.phase === "uploading" || state?.phase === "indexing";
                      return (
                        <>
                          {isActive && (
                            <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-black/[0.06]">
                              <div className={`h-full rounded-full transition-all duration-700 ${state?.phase === "indexing" ? "w-3/4 bg-primary/60" : "w-1/3 bg-primary/40"} animate-pulse`} />
                            </div>
                          )}
                          {state?.message && (
                            <p className={`truncate text-[10px] ${isFailed ? "text-red-500" : "text-secondary/55"}`}>{state.message}</p>
                          )}
                        </>
                      );
                    })()}
                  </div>
                  <button
                    onClick={() => removeFile(i)}
                    disabled={isSubmitting}
                    aria-label={t(language, "Remove file", "파일 제거")}
                    className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-red-500 text-[10px] text-white opacity-0 transition-opacity group-hover:opacity-100 cursor-pointer"
                  >
                    <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="relative min-w-0 px-1" data-chat-input-shell="true">
            {slashOpen && (
              <div
                ref={slashRef}
                className="absolute bottom-full left-0 right-0 mb-1 max-h-48 overflow-y-auto rounded-xl border border-black/[0.06] bg-white shadow-lg sm:max-h-64 z-50"
              >
                {slashMatches.map((entry, i) => (
                  <button
                    key={`${entry.command}-${entry.label}`}
                    type="button"
                    onMouseDown={(e) => { e.preventDefault(); acceptSlash(entry); }}
                    className={`flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition-colors cursor-pointer ${
                      i === slashIdx ? "bg-primary/[0.06] text-foreground" : "text-secondary hover:bg-black/[0.02]"
                    }`}
                  >
                    <span className="shrink-0 font-mono text-xs font-medium text-primary">/{entry.command}</span>
                    <span className="truncate text-xs text-secondary/70">{slashEntryLabel(entry, language)}</span>
                    <span className="ml-auto shrink-0 rounded-full bg-black/[0.04] px-1.5 py-0.5 text-[10px] text-secondary/60">
                      {slashCategoryLabel(entry.category, language)}
                    </span>
                  </button>
                ))}
              </div>
            )}
            {kbOpen && (
              <div
                ref={kbRef}
                className="absolute bottom-full left-0 right-0 mb-1 max-h-48 overflow-y-auto rounded-xl border border-black/[0.06] bg-white shadow-lg sm:max-h-64 z-50"
              >
                <div className="border-b border-black/[0.04] px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-secondary/45">
                  {t(language, "Knowledge Base", "지식 베이스")}
                </div>
                {kbMatches.map((entry, i) => (
                  <button
                    key={entry.id}
                    type="button"
                    onMouseDown={(e) => { e.preventDefault(); acceptKb(entry); }}
                    className={`flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition-colors cursor-pointer ${
                      i === kbIdx ? "bg-primary/[0.06] text-foreground" : "text-secondary hover:bg-black/[0.02]"
                    }`}
                  >
                    <svg className="h-3.5 w-3.5 shrink-0 text-primary/35" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                      <path d="M14 2v6h6" />
                    </svg>
                    <span className="truncate text-xs font-medium text-foreground">{entry.filename}</span>
                    <span className="ml-auto max-w-[100px] shrink-0 truncate rounded-full bg-black/[0.04] px-1.5 py-0.5 text-[10px] text-secondary/60">
                      {entry.collectionName}
                    </span>
                  </button>
                ))}
              </div>
            )}
            <div className="px-3 pb-1 pt-0">
              <textarea
                ref={textareaRef}
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                  setCursorPos(e.target.selectionStart ?? e.target.value.length);
                  handleInput();
                }}
                onKeyDown={handleKeyDown}
                onKeyUp={(e) => setCursorPos((e.target as HTMLTextAreaElement).selectionStart ?? cursorPos)}
                onClick={(e) => setCursorPos((e.target as HTMLTextAreaElement).selectionStart ?? cursorPos)}
                onPaste={handlePaste}
                placeholder={t(language, "Message...", "메시지...")}
                rows={1}
                disabled={disabled || isSubmitting}
                data-chat-input-field="true"
                className="block min-h-[44px] w-full resize-none bg-transparent py-2.5 text-[15px] leading-6 text-foreground placeholder-secondary/40 outline-none disabled:opacity-40"
                style={{ maxHeight: 160 }}
              />
            </div>
          </div>

          <div
            className="flex items-center gap-1 border-t border-black/[0.04] px-2 py-1.5"
            data-chat-composer-controls="true"
            data-chat-composer-actions="true"
          >
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled || queueBlocked || isSubmitting}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-secondary/45 transition-colors hover:bg-black/[0.04] hover:text-secondary/70 touch-manipulation cursor-pointer disabled:cursor-not-allowed disabled:opacity-25"
              aria-label={t(language, "Attach file", "파일 첨부")}
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              accept={CHAT_ATTACHMENT_ACCEPT}
              multiple
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files);
                e.target.value = "";
              }}
            />

            <button
              type="button"
              onClick={() => setRunUntilDone((value) => !value)}
              disabled={disabled || isSubmitting}
              aria-pressed={runUntilDone}
              data-chat-goal-toggle="true"
              className={`flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-[11px] font-medium transition-all touch-manipulation disabled:cursor-not-allowed disabled:opacity-30 ${
                runUntilDone
                  ? "bg-primary/[0.08] text-primary"
                  : "text-secondary/45 hover:bg-black/[0.04] hover:text-secondary/70"
              }`}
              title={t(language, "Run the next message as a goal mission", "다음 메시지를 목표 미션으로 실행")}
            >
              <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <circle cx="12" cy="12" r="6" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v3m0 12v3m9-9h-3M6 12H3" />
              </svg>
              <span className="hidden whitespace-nowrap sm:inline">
                {t(language, "Run until done", "완료까지 실행")}
              </span>
            </button>

            {composerAccessory && (
              <div className="flex min-w-0 items-center" data-composer-accessory="bottom-row">
                {composerAccessory}
              </div>
            )}

            <div className="ml-auto flex shrink-0 items-center">
              {showStreamingStopControl ? streamingStopControl : (
                <button
                  onClick={() => void handleSend()}
                  disabled={!sendEnabled}
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-all duration-200 touch-manipulation cursor-pointer disabled:cursor-not-allowed ${
                    sendEnabled
                      ? "bg-primary text-white shadow-[0_1px_4px_rgba(124,58,237,0.3)] hover:bg-primary/90 active:scale-95"
                      : "bg-black/[0.06] text-secondary/30"
                  }`}
                  aria-label={
                    streaming
                      ? effectiveStreamingMode === "steer"
                        ? t(language, "Steer current run", "현재 실행 조정")
                        : t(language, "Queue message", "메시지 대기")
                      : t(language, "Send", "전송")
                  }
                  title={
                    queueBlocked
                      ? t(language, "Queue full - wait for the bot to finish", "대기열이 가득 찼습니다 - 봇이 끝날 때까지 기다려 주세요")
                      : streaming
                        ? effectiveStreamingMode === "steer"
                          ? t(language, "Steer current run", "현재 실행 조정")
                          : t(language, "Queue message (fires after current response)", "메시지 대기 (현재 응답 후 전송)")
                        : t(language, "Send", "전송")
                  }
                >
                  <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
                    <path d="M5 12h14M12 5l7 7-7 7" />
                  </svg>
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
});
