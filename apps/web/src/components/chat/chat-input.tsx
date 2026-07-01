"use client";

import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type ReactNode } from "react";
import { CHAT_ATTACHMENT_ACCEPT, validateFile } from "@/chat-core/attachments";
import { isImageMimetype, formatFileSize } from "@/chat-core";
import { extractClipboardImageFiles } from "@/chat-core";
import { kbUploadKey } from "@/chat-core/kb-uploads";
import type { ChatResponseLanguage, ReplyTo, KbDocReference, AgentModeSummary } from "@/chat-core";
import { isStreamingComposerBlockedByQueue } from "@/chat-core";
import { SKILLS } from "@/lib/skills-catalog";
import type { KbDocEntry } from "@/hooks/use-kb-docs";
import type { PendingKbUpload } from "@/chat-core/kb-uploads";
import {
  buildExplicitRecipeSelection,
  sanitizeChatRecipeOption,
  type ChatRecipeOption,
  type ChatRecipeSelectionMode,
  type ExplicitRecipeSelectionRequest,
  type ReasoningEffort,
  REASONING_EFFORT_VALUES,
  DEFAULT_REASONING_EFFORT,
} from "@/chat-core";

export type { ChatRecipeOption, ChatRecipeSelectionMode };

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

const BUNDLED_SKILL_ENTRIES: SlashEntry[] = (() => {
  const entries: SlashEntry[] = [];
  for (const skill of SKILLS) {
    if (!skill.commands?.length) continue;
    for (const command of skill.commands) {
      entries.push({ command, label: skill.id, category: skill.category });
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
  // Order: system builtins, then the user's own custom/learned skills, then
  // bundled skills. Custom skills come before the bundled catalog so they
  // surface in the bare "/" browse list, which is capped downstream.
  const entries: SlashEntry[] = [...BUILTIN_COMMANDS];
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

  for (const entry of BUNDLED_SKILL_ENTRIES) {
    if (seenCommands.has(entry.command.toLowerCase())) continue;
    entries.push(entry);
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
  explicitRecipeSelection?: ExplicitRecipeSelectionRequest["explicitRecipeSelection"];
  /** Cross-provider reasoning-effort level. Only honored by models that
   * support reasoning (Anthropic extended thinking / OpenAI o-series & GPT-5
   * reasoning_effort / Gemini thinking). Backend wiring lands in a follow-up. */
  reasoningEffort?: ReasoningEffort;
  /** Active agent-mode id (posture). Empty/undefined → bot default (no field on
   * the payload). Runtime resolves it into the system prompt + tool delta. */
  agentMode?: string;
}

export function buildChatInputSendOptions(
  recipeMode: ChatRecipeSelectionMode = "auto",
  recipe?: ChatRecipeOption,
  reasoningEffort?: ReasoningEffort,
  goalMode = false,
  agentMode?: string | null,
): ChatInputSendOptions {
  const explicitRecipeSelection = buildExplicitRecipeSelection(
    recipeMode,
    recipe,
  )?.explicitRecipeSelection;
  // Phase 1 of the goal-loop design (clawy docs/plans/2026-06-21-magi-goal-
  // loop-clean-break-judge-design.md). Caller passes the live toggle state.
  // Toggle OFF → no goalMode field on the payload (byte-identical to a non-
  // goal-mode send today). Toggle ON → backend goal-loop policy activates.
  return {
    ...(goalMode ? { goalMode: true } : {}),
    ...(explicitRecipeSelection ? { explicitRecipeSelection } : {}),
    ...(reasoningEffort ? { reasoningEffort } : {}),
    // Empty string (the "Default" selector option) → no field, byte-identical
    // to a send with no mode selected.
    ...(agentMode ? { agentMode } : {}),
  };
}

export function nextRecipeModeAfterSend(
  current: ChatRecipeSelectionMode,
  result: void | boolean,
): ChatRecipeSelectionMode {
  if (result === false) return current;
  return current === "this_turn" ? "auto" : current;
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
  /** True when the user has already reached `MAX_QUEUED_MESSAGES`. */
  queueFull?: boolean;
  /** True when the current parent context allows a text-only mid-turn injection. */
  canAttemptStreamingInject?: boolean;
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
  /** Safe public recipe refs available for explicit per-turn/session requests. */
  availableRecipes?: ChatRecipeOption[];
  /** True when the currently-selected model supports a reasoning-effort knob
   * (Anthropic extended thinking / OpenAI o-series & GPT-5 / Gemini thinking).
   * When false the effort dropdown is hidden. */
  supportsReasoningEffort?: boolean;
  /** Current reasoning effort. Defaults to `DEFAULT_REASONING_EFFORT` when
   * undefined. Ignored when `supportsReasoningEffort` is false. */
  reasoningEffort?: ReasoningEffort;
  /** Called when the user picks a different effort level. */
  onReasoningEffortChange?: (effort: ReasoningEffort) => void;
  /** User-authored agent modes (postures) available to select. When empty the
   * mode selector is hidden — a bot with no modes behaves exactly as today. */
  availableModes?: AgentModeSummary[];
  /** Active agent-mode id, or null/"" for the bot default. */
  agentMode?: string | null;
  /** Called when the user picks a different mode (empty string = default). */
  onAgentModeChange?: (modeId: string) => void;
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
    queueFull = false,
    canAttemptStreamingInject = true,
    kbDocs,
    onSelectKbDoc,
    uploadStates,
    composerAccessory,
    uiLanguage,
    customSkills,
    availableRecipes = [],
    supportsReasoningEffort = false,
    reasoningEffort,
    onReasoningEffortChange,
    availableModes = [],
    agentMode,
    onAgentModeChange,
  },
  ref,
) {
  const effectiveReasoningEffort: ReasoningEffort = reasoningEffort ?? DEFAULT_REASONING_EFFORT;
  // Only honor a selected mode id that still exists in the list the composer
  // currently knows about. This keeps the <select> value and the sent value in
  // sync; if a mode is deleted while selected and the composer's list has since
  // refreshed, the stale id falls back to "" (Default) rather than hitting the
  // wire. (The composer's list is not live-synced with the Customize panel, so
  // this is best-effort; the runtime also tolerates an unknown id.)
  const effectiveAgentMode =
    agentMode && availableModes.some((mode) => mode.id === agentMode) ? agentMode : "";
  const [text, setText] = useState("");
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [slashIdx, setSlashIdx] = useState(0);
  const [kbIdx, setKbIdx] = useState(0);
  const [recipeMode, setRecipeMode] = useState<ChatRecipeSelectionMode>("auto");
  // Goal-mode toggle (Phase 1 opt-in). Default OFF — backend goal-loop policy
  // only activates when the user explicitly opts in for a given send. Promotion
  // to default-ON (Phase 2) is gated on the design doc's measurement criteria.
  const [goalMode, setGoalMode] = useState<boolean>(false);
  const [selectedRecipeId, setSelectedRecipeId] = useState(
    availableRecipes[0]?.recipeId ?? "",
  );
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const slashRef = useRef<HTMLDivElement>(null);
  const stopPointerHandledRef = useRef(false);
  const stopPointerResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const language = uiLanguage;
  const canInjectCurrentComposer = !!streaming && canAttemptStreamingInject && pendingFiles.length === 0;
  const queueBlocked = isStreamingComposerBlockedByQueue({
    queueFull,
    canAttemptInject: canInjectCurrentComposer,
  });
  const liveRunModeLabel = canInjectCurrentComposer
    ? t(language, "Auto-steers when possible", "가능하면 자동 조정")
    : t(language, "Will queue after run", "현재 실행 후 대기");

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
  const safeRecipeOptions = useMemo(
    () => availableRecipes.flatMap((recipe) => {
      const safeRecipe = sanitizeChatRecipeOption(recipe);
      return safeRecipe && !safeRecipe.disabled ? [safeRecipe] : [];
    }),
    [availableRecipes],
  );
  const selectedRecipe =
    safeRecipeOptions.find((recipe) => recipe.recipeId === selectedRecipeId) ??
    safeRecipeOptions[0];

  useEffect(() => {
    if (!safeRecipeOptions.length) {
      setSelectedRecipeId("");
      setRecipeMode("auto");
      return;
    }
    if (!safeRecipeOptions.some((recipe) => recipe.recipeId === selectedRecipeId)) {
      setSelectedRecipeId(safeRecipeOptions[0]?.recipeId ?? "");
    }
  }, [safeRecipeOptions, selectedRecipeId]);
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
        buildChatInputSendOptions(
          recipeMode,
          selectedRecipe,
          supportsReasoningEffort ? effectiveReasoningEffort : undefined,
          goalMode,
          effectiveAgentMode,
        ),
      );
      if (result === false) return;
      for (const p of pendingFiles) {
        if (p.previewUrl) URL.revokeObjectURL(p.previewUrl);
      }
      setText("");
      setPendingFiles([]);
      setRecipeMode(nextRecipeModeAfterSend(recipeMode, result));
      if (textareaRef.current) textareaRef.current.style.height = "auto";
    } finally {
      setIsSubmitting(false);
    }
  }, [
    text,
    pendingFiles,
    onSend,
    onReset,
    recipeMode,
    selectedRecipe,
    supportsReasoningEffort,
    effectiveReasoningEffort,
    goalMode,
    effectiveAgentMode,
  ]);

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
              className="flex min-w-0 items-center gap-2 border-b border-black/[0.05] px-3 py-1.5"
              data-chat-composer-toolbar="true"
              data-chat-live-run-toolbar="true"
            >
              <div className="min-w-0" data-chat-live-run-status="true">
                <span className="inline-flex min-h-7 touch-manipulation items-center gap-2 rounded-md bg-black/[0.04] px-2.5 text-[11px] font-medium text-secondary/70">
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" aria-hidden="true" />
                  <span className="shrink-0 font-semibold text-foreground/75">
                    {t(language, "Live run", "실행 중")}
                  </span>
                  <span className="truncate">{liveRunModeLabel}</span>
                </span>
              </div>
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
                className="block min-h-[44px] w-full resize-none bg-transparent py-2.5 text-base leading-6 text-foreground placeholder-secondary/40 outline-none disabled:opacity-40"
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
              onClick={() => setGoalMode((v) => !v)}
              disabled={disabled || isSubmitting}
              aria-pressed={goalMode}
              data-chat-goal-toggle="true"
              className={`flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-[11px] font-medium transition-all touch-manipulation disabled:cursor-not-allowed disabled:opacity-30 ${
                goalMode
                  ? "bg-primary/[0.08] text-primary"
                  : "text-secondary/45 hover:bg-black/[0.04] hover:text-secondary/70"
              }`}
              title={t(
                language,
                "Run this message as a goal mission — the agent keeps acting until the task is complete (Phase 1 opt-in)",
                "이 메시지를 목표 미션으로 실행 — 작업이 끝날 때까지 에이전트가 계속 진행합니다 (Phase 1 옵트인)",
              )}
            >
              <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <circle cx="12" cy="12" r="6" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v3m0 12v3m9-9h-3M6 12H3" />
              </svg>
              <span className="hidden whitespace-nowrap sm:inline">
                {t(language, "Goal mission", "목표 미션")}
              </span>
            </button>

            {safeRecipeOptions.length > 0 && (
              <div
                className="flex min-w-0 shrink items-center gap-1 rounded-md border border-black/[0.05] bg-black/[0.015] px-1 py-0.5"
                data-chat-recipe-selector="true"
              >
                <span
                  className="hidden shrink-0 px-1 text-[10px] font-semibold uppercase text-secondary/40 sm:inline"
                  data-chat-recipe-label="true"
                >
                  {t(language, "Recipe", "레시피")}
                </span>
                <select
                  value={recipeMode}
                  onChange={(event) => setRecipeMode(event.target.value as ChatRecipeSelectionMode)}
                  disabled={disabled || isSubmitting}
                  aria-label={t(language, "Recipe mode", "레시피 모드")}
                  data-chat-recipe-mode-selector="true"
                  className="h-6 max-w-[8.5rem] truncate rounded bg-transparent px-1 text-[11px] font-medium text-secondary/60 outline-none transition-colors hover:text-secondary/80 focus-visible:ring-2 focus-visible:ring-primary/20 focus-visible:ring-offset-0 disabled:opacity-35"
                >
                  <option value="auto">{t(language, "Auto", "자동")}</option>
                  <option value="this_turn">{t(language, "This turn only", "이번 턴만")}</option>
                  <option value="session">{t(language, "Session default", "세션 기본값")}</option>
                </select>
                <select
                  value={selectedRecipe?.recipeId ?? ""}
                  onChange={(event) => setSelectedRecipeId(event.target.value)}
                  disabled={disabled || isSubmitting || recipeMode === "auto"}
                  aria-label={t(language, "Recipe", "레시피")}
                  data-chat-recipe-ref-selector="true"
                  className="h-6 max-w-[10rem] truncate rounded bg-transparent px-1 text-[11px] text-secondary/65 outline-none transition-colors hover:text-secondary/85 focus-visible:ring-2 focus-visible:ring-primary/20 focus-visible:ring-offset-0 disabled:opacity-35"
                >
                  {safeRecipeOptions.map((recipe) => (
                    <option key={recipe.recipeId} value={recipe.recipeId}>
                      {recipe.label ?? recipe.recipeId}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {composerAccessory && (
              <div className="flex min-w-0 items-center" data-composer-accessory="bottom-row">
                {composerAccessory}
              </div>
            )}

            {availableModes.length > 0 && (
              <div className="flex shrink-0 items-center" data-chat-mode-selector="dropdown">
                <label className="sr-only" htmlFor="chat-input-agent-mode">
                  {t(language, "Agent mode", "에이전트 모드")}
                </label>
                <select
                  id="chat-input-agent-mode"
                  value={effectiveAgentMode}
                  onChange={(event) => onAgentModeChange?.(event.target.value)}
                  disabled={disabled || isSubmitting}
                  aria-label={t(language, "Agent mode", "에이전트 모드")}
                  title={t(
                    language,
                    "Agent mode (posture) for this turn — applies a saved system prompt + tool set",
                    "이번 턴의 에이전트 모드 (포스처) — 저장된 시스템 프롬프트와 도구 세트를 적용합니다",
                  )}
                  className="h-8 max-w-[9rem] cursor-pointer truncate rounded-md border border-border/60 bg-transparent px-2 text-xs text-muted-foreground hover:border-border focus:border-foreground/40 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="">{t(language, "Default", "기본값")}</option>
                  {availableModes.map((mode) => (
                    <option key={mode.id} value={mode.id}>
                      {mode.displayName}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {supportsReasoningEffort && (
              <div className="flex shrink-0 items-center" data-reasoning-effort="dropdown">
                <label className="sr-only" htmlFor="chat-input-reasoning-effort">
                  Reasoning effort
                </label>
                <select
                  id="chat-input-reasoning-effort"
                  value={effectiveReasoningEffort}
                  onChange={(event) => {
                    const next = event.target.value as ReasoningEffort;
                    onReasoningEffortChange?.(next);
                  }}
                  disabled={disabled}
                  aria-label="Reasoning effort"
                  title="Reasoning effort for this turn"
                  className="h-8 cursor-pointer rounded-md border border-border/60 bg-transparent px-2 text-xs text-muted-foreground hover:border-border focus:border-foreground/40 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {REASONING_EFFORT_VALUES.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
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
                      ? canInjectCurrentComposer
                        ? t(language, "Send to current run", "현재 실행에 전송")
                        : t(language, "Queue message", "메시지 대기")
                      : t(language, "Send", "전송")
                  }
                  title={
                    queueBlocked
                      ? t(language, "Queue full - wait for the bot to finish", "대기열이 가득 찼습니다 - 봇이 끝날 때까지 기다려 주세요")
                      : streaming
                        ? canInjectCurrentComposer
                          ? t(language, "Send to current run", "현재 실행에 전송")
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
