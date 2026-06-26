/**
 * Conversational policy compose UI — multi-turn chat replacement for
 * the one-shot NL textarea in ``nl-rule-compose.tsx``.
 *
 * Two-column desktop, stacked mobile:
 *   - Left: chat scroll + starter pills + composer (textarea + Send).
 *   - Right: live IR draft pane (read-only summary + Save CTA).
 *
 * Talks to ``POST /v1/app/customize/custom-rules/compile-interactive``
 * via :func:`compileCustomRuleInteractive`. Each turn round-trips
 * ``(history, draft_so_far, answers)`` → ``(assistant_message, draft,
 * questions, ready_to_save, schema_issues)``. The CLIENT never mutates
 * the draft; only the server's state machine writes to it.
 *
 * Ported from magi-cp's ConversationalCompose pattern with three
 * substantive differences:
 *   1. magi-agent surface (custom_rule.what.{kind,payload}) not magi-cp
 *      IR (trigger.event/matcher/requires).
 *   2. No locale-driven i18n yet — strings are English-only here; the
 *      magi-agent dashboard has not landed a translate() helper for
 *      this surface.
 *   3. No handoff seed: the wizard ↔ NL ↔ raw handoff lives in
 *      ``handoff.ts`` already; we re-use the existing primer rather
 *      than building a base64 decode path.
 *
 * IME / race-condition hazards preserved verbatim from magi-cp (their
 * pattern is the canonical reference):
 *   - composition guard for the Korean IME's Enter-finalize signal.
 *   - separate AbortControllers per fetch path so a stale response
 *     never clobbers a fresher one.
 *   - monotonic request id as the tiebreak when two events observe
 *     pending=false in the same micro-task.
 *   - functional ``setHistory`` everywhere — never a closure snapshot.
 */

"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";

import {
  compileCustomRuleInteractive,
  type InteractiveCompileResponse,
  type InteractiveHistoryTurn,
  type InteractiveQuestion,
} from "@/lib/customize-api";


export interface ConversationalComposeProps {
  /** Authenticated fetch threaded from the parent. */
  agentFetch: (path: string, init?: RequestInit) => Promise<Response>;
  /** Called when the operator clicks Save on a validator-clean draft.
   *  The parent persists via ``putCustomRule`` and toasts on success. */
  onSave: (draft: Record<string, unknown>) => Promise<void> | void;
  /** Called when the operator clicks "Pick different" to back out to
   *  the authoring-mode picker. */
  onPickDifferent: () => void;
  /** Optional textarea pre-fill (e.g. handoff from the guided wizard).
   *  Operator can edit before sending. */
  initialUserMessage?: string;
}


interface HistoryTurn extends InteractiveHistoryTurn {
  /** Per-turn metadata that stays on the client — questions accompany
   *  the assistant turn that prompted them so the chat can re-render
   *  the pill buttons even after subsequent turns. */
  questions?: InteractiveQuestion[];
  /** Discriminator for the rich error-bubble path. */
  errorKind?: "compiler_disabled" | "network" | "upstream";
}


const STARTER_PILLS: Array<{ label: string; fill: string }> = [
  { label: "Block sudo", fill: "Block any shell command that contains sudo." },
  {
    label: "Restrict web fetch",
    fill: "Only allow WebFetch to api.example.com and trusted-source.org.",
  },
  {
    label: "Require citations",
    fill:
      "Block the final answer unless every factual claim cites a source the agent actually read.",
  },
  {
    label: "Audit AWS keys",
    fill:
      "Redact anything that looks like an AWS access key (AKIA followed by 16 alphanumerics) from tool output.",
  },
  {
    label: "Ask before deleting",
    fill:
      "Require human approval before any tool call that deletes files or rows.",
  },
];


export function ConversationalCompose({
  agentFetch,
  onSave,
  onPickDifferent,
  initialUserMessage,
}: ConversationalComposeProps): React.ReactElement {
  const [history, setHistory] = useState<HistoryTurn[]>([]);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [readyToSave, setReadyToSave] = useState(false);
  const [schemaIssues, setSchemaIssues] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [input, setInput] = useState(initialUserMessage ?? "");
  /** Multi-select staging — qid → picked option values. */
  const [picks, setPicks] = useState<Record<string, string[]>>({});

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const mountedRef = useRef(true);
  const reqIdRef = useRef(0);
  const sendAbortRef = useRef<AbortController | null>(null);
  /** IME composition guard — Korean (Hangul) IME signals Enter to
   *  finalize a composition; we MUST NOT send on that keystroke. */
  const composingRef = useRef(false);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      if (sendAbortRef.current) sendAbortRef.current.abort();
    };
  }, []);

  // Auto-scroll to the bottom on every history append so the most
  // recent turn is always in view.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [history, pending]);

  const sendTurn = useCallback(
    async (args: {
      userText: string | null;
      answers: Record<string, string> | null;
      userBubble?: string;
    }) => {
      if (pending) return;
      const { userText, answers, userBubble } = args;
      const bubbleText = userBubble ?? userText ?? "";
      // Optimistically append the user bubble so the operator sees
      // their input land before the assistant responds.
      if (bubbleText.trim()) {
        setHistory((prev) => [
          ...prev,
          { role: "user", content: bubbleText.trim() },
        ]);
      }
      if (userText !== null) setInput("");

      setPending(true);
      const ctrl = new AbortController();
      sendAbortRef.current = ctrl;
      const myId = ++reqIdRef.current;
      let answersSent = false;

      try {
        // Build the next-turn POST body from current history (stripping
        // the per-turn `questions` metadata) plus the new user turn.
        const wireHistory: InteractiveHistoryTurn[] = history.map((t) => ({
          role: t.role,
          content: t.content,
        }));
        if (bubbleText.trim()) {
          wireHistory.push({ role: "user", content: bubbleText.trim() });
        }
        const body = await compileCustomRuleInteractive(agentFetch, {
          history: wireHistory,
          draft_so_far: draft,
          answers,
        });
        answersSent = true;

        if (!mountedRef.current) return;
        if (myId !== reqIdRef.current) return; // stale response

        const errored = body.ok === false && typeof body.error === "string";
        if (errored) {
          const errorKind: HistoryTurn["errorKind"] = body.error?.includes(
            "disabled",
          )
            ? "compiler_disabled"
            : body.error?.includes("Network")
              ? "network"
              : "upstream";
          setHistory((prev) => [
            ...prev,
            {
              role: "assistant",
              content: errorBubbleText(errorKind, body.error ?? ""),
              errorKind,
            },
          ]);
          return;
        }

        const assistantMsg = body.assistant_message ?? "";
        const questions = body.questions ?? [];
        setHistory((prev) => [
          ...prev,
          { role: "assistant", content: assistantMsg, questions },
        ]);
        setDraft(body.draft ?? null);
        setReadyToSave(!!body.ready_to_save);
        setSchemaIssues(body.schema_issues ?? []);
      } finally {
        if (mountedRef.current && myId === reqIdRef.current) {
          setPending(false);
          if (answersSent) setPicks({});
        }
      }
    },
    [agentFetch, draft, history, pending],
  );

  const onSendClick = useCallback(() => {
    if (!input.trim()) return;
    void sendTurn({ userText: input, answers: null });
  }, [input, sendTurn]);

  const onTextareaKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key !== "Enter") return;
      if (e.shiftKey) return; // Shift+Enter = newline
      if (e.nativeEvent.isComposing) return; // IME composition active
      if (composingRef.current) return; // Safari fallback
      e.preventDefault();
      onSendClick();
    },
    [onSendClick],
  );

  const onStarterClick = useCallback((fill: string) => {
    setInput(fill);
  }, []);

  const onSingleSelectPick = useCallback(
    (qid: string, value: string, label: string) => {
      void sendTurn({
        userText: null,
        answers: { [qid]: value },
        userBubble: label,
      });
    },
    [sendTurn],
  );

  const togglePick = useCallback((qid: string, value: string) => {
    setPicks((prev) => {
      const current = new Set(prev[qid] ?? []);
      if (current.has(value)) current.delete(value);
      else current.add(value);
      return { ...prev, [qid]: Array.from(current) };
    });
  }, []);

  const onMultiSubmit = useCallback(
    (qid: string, labels: Record<string, string>) => {
      const picked = picks[qid] ?? [];
      if (picked.length === 0) return;
      void sendTurn({
        userText: null,
        answers: { [qid]: picked.join(",") },
        userBubble: picked.map((v) => labels[v] ?? v).join(", "),
      });
    },
    [picks, sendTurn],
  );

  const onSaveClick = useCallback(async () => {
    if (!draft || !readyToSave || saving) return;
    setSaving(true);
    try {
      await onSave(draft);
    } finally {
      if (mountedRef.current) setSaving(false);
    }
  }, [draft, onSave, readyToSave, saving]);

  return (
    <div
      className="flex flex-col gap-4"
      data-testid="conversational-compose-root"
    >
      <header className="flex items-center justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
            Authoring
          </p>
          <h2 className="mt-0.5 text-base font-semibold text-foreground">
            Conversational compose
          </h2>
        </div>
        <button
          type="button"
          onClick={onPickDifferent}
          className="text-xs text-secondary hover:text-foreground"
        >
          ← Pick different
        </button>
      </header>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_360px]">
        {/* Left column: chat */}
        <section
          className="flex flex-col gap-3 rounded-lg border border-primary/15 bg-white p-4"
          data-testid="conversational-chat-column"
        >
          <div
            ref={scrollRef}
            role="log"
            aria-live="polite"
            className="flex max-h-[480px] min-h-[260px] flex-col gap-3 overflow-y-auto"
            data-testid="conversational-chat-scroll"
          >
            {history.length === 0 ? (
              <div
                className="rounded-md border border-primary/15 bg-primary/[0.04] px-3 py-2 text-sm text-foreground"
                data-testid="conversational-intro"
              >
                Describe what you want this rule to do, or pick a starter
                below. I'll ask one or two short questions to fill the gaps
                and the draft on the right fills in live.
              </div>
            ) : null}
            {history.map((turn, idx) => (
              <ChatBubble
                key={idx}
                turn={turn}
                onSinglePick={onSingleSelectPick}
                onTogglePick={togglePick}
                onMultiSubmit={onMultiSubmit}
                picks={picks}
                disabled={pending}
              />
            ))}
            {pending ? (
              <div
                className="text-xs italic text-secondary/70"
                data-testid="conversational-typing"
              >
                thinking…
              </div>
            ) : null}
          </div>

          {history.length === 0 ? (
            <div
              className="flex flex-wrap gap-1.5"
              data-testid="conversational-starters"
            >
              {STARTER_PILLS.map((pill) => (
                <button
                  key={pill.label}
                  type="button"
                  onClick={() => onStarterClick(pill.fill)}
                  className="rounded-full border border-secondary/20 bg-white px-2.5 py-1 text-xs text-secondary hover:border-primary/40 hover:text-foreground"
                >
                  {pill.label}
                </button>
              ))}
            </div>
          ) : null}

          <div className="flex flex-col gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onTextareaKeyDown}
              onCompositionStart={() => {
                composingRef.current = true;
              }}
              onCompositionEnd={() => {
                composingRef.current = false;
              }}
              placeholder="e.g. block any shell exec call"
              rows={2}
              disabled={pending || saving}
              data-testid="conversational-input"
              className="w-full resize-y rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
            <div className="flex items-center justify-end">
              <button
                type="button"
                onClick={onSendClick}
                disabled={pending || saving || !input.trim()}
                data-testid="conversational-send"
                className="rounded-full bg-primary px-3 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                Send
              </button>
            </div>
          </div>
        </section>

        {/* Right column: live draft pane */}
        <DraftPane
          draft={draft}
          readyToSave={readyToSave}
          schemaIssues={schemaIssues}
          saving={saving}
          onSave={onSaveClick}
        />
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// ChatBubble + question pills
// ---------------------------------------------------------------------------


interface ChatBubbleProps {
  turn: HistoryTurn;
  onSinglePick: (qid: string, value: string, label: string) => void;
  onTogglePick: (qid: string, value: string) => void;
  onMultiSubmit: (qid: string, labels: Record<string, string>) => void;
  picks: Record<string, string[]>;
  disabled: boolean;
}


function ChatBubble({
  turn,
  onSinglePick,
  onTogglePick,
  onMultiSubmit,
  picks,
  disabled,
}: ChatBubbleProps): React.ReactElement {
  const isUser = turn.role === "user";
  return (
    <div
      className={`flex flex-col gap-1 ${isUser ? "items-end" : "items-start"}`}
      data-testid={`chat-turn-${turn.role}`}
    >
      <div
        className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
          isUser
            ? "bg-primary/10 text-foreground"
            : turn.errorKind
              ? "border border-amber-400/40 bg-amber-50 text-amber-900"
              : "border border-secondary/15 bg-white text-foreground"
        }`}
      >
        {turn.content}
      </div>
      {!isUser && turn.questions && turn.questions.length > 0 ? (
        <div className="flex flex-col gap-2 pt-1">
          {turn.questions.map((q) => (
            <QuestionPicker
              key={q.id}
              question={q}
              onSinglePick={onSinglePick}
              onTogglePick={onTogglePick}
              onMultiSubmit={onMultiSubmit}
              picks={picks}
              disabled={disabled}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}


interface QuestionPickerProps {
  question: InteractiveQuestion;
  onSinglePick: (qid: string, value: string, label: string) => void;
  onTogglePick: (qid: string, value: string) => void;
  onMultiSubmit: (qid: string, labels: Record<string, string>) => void;
  picks: Record<string, string[]>;
  disabled: boolean;
}


function QuestionPicker({
  question,
  onSinglePick,
  onTogglePick,
  onMultiSubmit,
  picks,
  disabled,
}: QuestionPickerProps): React.ReactElement | null {
  if (question.kind === "text") {
    // Text questions are answered via the main textarea — render a
    // soft hint only.
    return (
      <div
        className="text-xs italic text-secondary/70"
        data-testid={`q-text-${question.id}`}
      >
        Reply in the box below to answer.
      </div>
    );
  }
  const options = question.options ?? [];
  if (options.length === 0) return null;
  const labels: Record<string, string> = {};
  options.forEach((o) => {
    labels[o.value] = o.label;
  });
  const picked = picks[question.id] ?? [];
  return (
    <div
      className="flex flex-col gap-1"
      data-testid={`q-pick-${question.id}`}
    >
      <div className="flex flex-wrap gap-1.5">
        {options.map((opt) => {
          const isPicked = picked.includes(opt.value);
          return (
            <button
              key={opt.value}
              type="button"
              disabled={disabled}
              aria-pressed={
                question.kind === "multi_select" ? isPicked : undefined
              }
              onClick={() => {
                if (question.kind === "single_select") {
                  onSinglePick(question.id, opt.value, opt.label);
                } else {
                  onTogglePick(question.id, opt.value);
                }
              }}
              title={opt.hint}
              className={`rounded-full border px-3 py-1 text-xs ${
                isPicked
                  ? "border-primary bg-primary/15 text-foreground"
                  : "border-secondary/20 bg-white text-secondary hover:border-primary/40 hover:text-foreground"
              } disabled:cursor-not-allowed disabled:opacity-50`}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
      {question.kind === "multi_select" ? (
        <div>
          <button
            type="button"
            disabled={disabled || picked.length === 0}
            onClick={() => onMultiSubmit(question.id, labels)}
            className="rounded-full bg-primary px-3 py-0.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Submit ({picked.length})
          </button>
        </div>
      ) : null}
    </div>
  );
}


// ---------------------------------------------------------------------------
// DraftPane — right column live summary + Save CTA
// ---------------------------------------------------------------------------


interface DraftPaneProps {
  draft: Record<string, unknown> | null;
  readyToSave: boolean;
  schemaIssues: string[];
  saving: boolean;
  onSave: () => Promise<void> | void;
}


function DraftPane({
  draft,
  readyToSave,
  schemaIssues,
  saving,
  onSave,
}: DraftPaneProps): React.ReactElement {
  const summary = summarizeDraft(draft);
  return (
    <aside
      className="flex h-full flex-col gap-3 rounded-lg border border-primary/15 bg-white p-4"
      data-testid="draft-pane"
    >
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Live draft
        </p>
        <p className="mt-1 text-sm text-secondary">
          {draft
            ? "The chat below fills these fields as you answer."
            : "Start the conversation; the draft will appear here."}
        </p>
      </header>

      <dl
        className="grid grid-cols-[max-content_1fr] gap-x-2 gap-y-1 text-xs"
        data-testid="draft-pane-summary"
      >
        {summary.map((row) => (
          <React.Fragment key={row.label}>
            <dt className="text-secondary/70">{row.label}</dt>
            <dd className="text-foreground">
              {row.value || (
                <span className="italic text-secondary/40">(not set yet)</span>
              )}
            </dd>
          </React.Fragment>
        ))}
      </dl>

      {schemaIssues.length > 0 ? (
        <div
          className="rounded-md border border-amber-400/40 bg-amber-50 px-2 py-1.5 text-xs text-amber-900"
          data-testid="draft-pane-schema-issues"
        >
          <p className="font-semibold">Validator needs:</p>
          <ul className="list-disc pl-4">
            {schemaIssues.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-auto">
        <button
          type="button"
          onClick={() => {
            void onSave();
          }}
          disabled={!readyToSave || saving}
          data-testid="draft-pane-save"
          className="w-full rounded-lg bg-primary px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "Saving…" : readyToSave ? "Save policy" : "Save (not ready)"}
        </button>
      </div>
    </aside>
  );
}


// ---------------------------------------------------------------------------
// Pure helpers (testable via source-grep + behavioural test)
// ---------------------------------------------------------------------------


export function summarizeDraft(
  draft: Record<string, unknown> | null,
): Array<{ label: string; value: string }> {
  if (!draft) {
    return [
      { label: "What", value: "" },
      { label: "When", value: "" },
      { label: "Action", value: "" },
      { label: "Scope", value: "" },
    ];
  }
  const what = isPlainObject(draft.what) ? draft.what : {};
  const kind = typeof what.kind === "string" ? what.kind : "";
  const fires = typeof draft.firesAt === "string" ? draft.firesAt : "";
  const action = typeof draft.action === "string" ? draft.action : "";
  const scope = typeof draft.scope === "string" ? draft.scope : "";
  return [
    { label: "What", value: humanizeKind(kind) },
    { label: "When", value: humanizeSlot(fires) },
    { label: "Action", value: humanizeAction(action) },
    { label: "Scope", value: humanizeScope(scope) },
  ];
}


function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}


function humanizeKind(kind: string): string {
  const labels: Record<string, string> = {
    tool_perm: "Restrict a tool",
    llm_criterion: "AI judge on the answer",
    deterministic_ref: "Require a check passed",
    shacl_constraint: "Structured rule",
    capability_scope: "Narrow spawned subagent",
    prompt_injection: "Append to input",
    output_rewrite: "Redact tool output",
    shell_command: "Run a shell script",
    shell_check: "Verifier shell script",
  };
  return labels[kind] ?? "";
}


function humanizeSlot(slot: string): string {
  if (!slot) return "";
  return slot.replace(/_/g, " ");
}


function humanizeAction(action: string): string {
  const labels: Record<string, string> = {
    block: "Block",
    audit: "Audit only",
    ask_approval: "Ask for approval",
    retry: "Retry",
    override: "Override tool result",
  };
  return labels[action] ?? "";
}


function humanizeScope(scope: string): string {
  const labels: Record<string, string> = {
    always: "Every turn",
    coding: "Coding turns",
    research: "Research turns",
    delivery: "Delivery turns",
    memory: "Memory turns",
    task: "Task-queue turns",
  };
  return labels[scope] ?? "";
}


function errorBubbleText(
  kind: NonNullable<HistoryTurn["errorKind"]>,
  raw: string,
): string {
  if (kind === "compiler_disabled") {
    return (
      "The conversational compiler is not enabled on this runtime. " +
      "Set MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED=1 and restart the agent, " +
      "or use the one-shot natural-language box or the guided wizard."
    );
  }
  if (kind === "network") {
    return "Network glitch — please try sending again.";
  }
  return `Compiler error: ${raw || "unknown"}. Try again or use the guided wizard.`;
}
