/**
 * Conversational POLICY compose UI: multi-turn chat authoring for a
 * multi-rule policy (a PRODUCER that records evidence + a GATE that
 * blocks a high-risk tool until that evidence exists this session).
 *
 * This is the policy-level sibling of ``conversational-compose.tsx``
 * (which authors ONE custom_rule). Same interaction model on purpose:
 * the operator asked for "the same as the existing single-rule flow, but
 * multi-turn, for a policy".
 *   - Left: chat scroll + starter pills + composer (textarea + Send).
 *   - Right: live policy draft pane (producer card + gate card + Save CTA).
 *
 * Talks to ``POST /v1/app/policies/compile/interactive`` via
 * :func:`compilePolicyInteractive`. Each turn round-trips
 * ``(history, paramsSoFar, answers)`` → ``(assistant_message, params,
 * plan, questions, ready_to_save, schema_issues)``. The CLIENT never
 * mutates params/plan; only the server's state machine writes them.
 *
 * Unlike the single-rule component (whose parent persists via
 * ``putCustomRule``), the policy save is a 3-store composition
 * (producer sidecar + gate custom_rule + Policy record + binding) behind
 * one endpoint, so this component owns the save: on a ready plan it calls
 * :func:`savePolicyFromPlan` itself and reports the saved ids up via
 * ``onSaved``. A save failure surfaces inline as a banner; it does not
 * throw.
 *
 * IME / race-condition hazards preserved from ``conversational-compose.tsx``
 * (the canonical reference):
 *   - composition guard for the Korean IME's Enter-finalize signal.
 *   - a monotonic request id: every async branch compares its id against
 *     the current one and drops its writes once a newer send has started,
 *     so a slow response never clobbers a fresher one.
 *   - an AbortController per send, aborted on unmount (kept for parity with
 *     the reference; the reqId guard above is the actual stale-drop, since
 *     the signal is not threaded into the fetch helper).
 *   - functional ``setHistory`` everywhere, never a closure snapshot.
 */

"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";

import {
  compilePolicyInteractive,
  savePolicyFromPlan,
  type InteractiveHistoryTurn,
  type InteractiveQuestion,
  type PolicyFromPlanResponse,
} from "@/lib/customize-api";


export interface ConversationalPolicyComposeProps {
  /** Authenticated fetch threaded from the parent. */
  agentFetch: (path: string, init?: RequestInit) => Promise<Response>;
  /** Called after the policy persists (producer + gate + Policy). The
   *  parent reloads its catalog snapshot and closes the authoring pane. */
  onSaved: (result: PolicyFromPlanResponse) => void;
  /** Optional textarea pre-fill (e.g. a handoff from another surface).
   *  Operator can edit before sending. */
  initialUserMessage?: string;
}


interface HistoryTurn extends InteractiveHistoryTurn {
  /** Per-turn metadata that stays on the client; questions accompany the
   *  assistant turn that prompted them so the chat can re-render the pill
   *  buttons even after subsequent turns. */
  questions?: InteractiveQuestion[];
  /** Discriminator for the rich error-bubble path. */
  errorKind?: "network" | "upstream";
}


const STARTER_PILLS: Array<{ label: string; fill: string }> = [
  {
    label: "Gate Bash on a verified source",
    fill:
      "Before Bash runs, require that a trustworthy source was fetched and verified this session. Trusted domains: sec.gov, europa.eu.",
  },
  {
    label: "Verify before deploy",
    fill:
      "Block the deploy tool unless an official source was fetched from an allowlisted domain this session; otherwise ask for approval.",
  },
  {
    label: "Official source before a purchase",
    fill:
      "Require source credibility (fetched from a regulatory site like sec.gov) before the payment tool can run.",
  },
];


export function ConversationalPolicyCompose({
  agentFetch,
  onSaved,
  initialUserMessage,
}: ConversationalPolicyComposeProps): React.ReactElement {
  const [history, setHistory] = useState<HistoryTurn[]>([]);
  /** Server-driven policy params (gatedTool / evidenceLabel / …). */
  const [params, setParams] = useState<Record<string, unknown> | null>(null);
  /** Assembled producer+gate+binding plan; present only when ready. */
  const [plan, setPlan] = useState<Record<string, unknown> | null>(null);
  const [readyToSave, setReadyToSave] = useState(false);
  const [schemaIssues, setSchemaIssues] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [input, setInput] = useState(initialUserMessage ?? "");
  /** Multi-select staging: qid maps to picked option values. */
  const [picks, setPicks] = useState<Record<string, string[]>>({});

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const mountedRef = useRef(true);
  const reqIdRef = useRef(0);
  const sendAbortRef = useRef<AbortController | null>(null);
  /** IME composition guard: Korean (Hangul) IME signals Enter to
   *  finalize a composition; we MUST NOT send on that keystroke. */
  const composingRef = useRef(false);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      if (sendAbortRef.current) sendAbortRef.current.abort();
    };
  }, []);

  // Auto-scroll to the bottom on every history append so the most recent
  // turn is always in view.
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
      // Guard on saving too: while a save is in flight a still-rendered
      // question pill could otherwise fire a concurrent compile turn.
      if (pending || saving) return;
      const { userText, answers, userBubble } = args;
      const bubbleText = userBubble ?? userText ?? "";
      // Optimistically append the user bubble so the operator sees their
      // input land before the assistant responds.
      if (bubbleText.trim()) {
        setHistory((prev) => [
          ...prev,
          { role: "user", content: bubbleText.trim() },
        ]);
      }
      if (userText !== null) setInput("");
      // A fresh turn invalidates any prior save error.
      setSaveError(null);

      setPending(true);
      const ctrl = new AbortController();
      sendAbortRef.current = ctrl;
      const myId = ++reqIdRef.current;
      let answersSent = false;

      try {
        // Build the next-turn POST body from current history (stripping the
        // per-turn `questions` metadata) plus the new user turn.
        const wireHistory: InteractiveHistoryTurn[] = history.map((t) => ({
          role: t.role,
          content: t.content,
        }));
        if (bubbleText.trim()) {
          wireHistory.push({ role: "user", content: bubbleText.trim() });
        }
        const body = await compilePolicyInteractive(agentFetch, {
          history: wireHistory,
          paramsSoFar: params,
          answers,
        });
        answersSent = true;

        if (!mountedRef.current) return;
        if (myId !== reqIdRef.current) return; // stale response

        // The policy interactive route returns its timeout / failure
        // envelopes as {ready_to_save:false, error:"compile timed out"} at
        // HTTP 200 with NO ok field, and the client helper synthesizes
        // {ok:false, error} on a non-2xx / network failure. Treat a present
        // error string as an error EITHER way: the success and
        // LLM-unavailable envelopes never carry an error key, so this cannot
        // false-positive. Checking only ok===false would let a 200 error
        // fall through to the success path and wipe accumulated params.
        const rawError = typeof body.error === "string" ? body.error : "";
        const errored = body.ok === false || rawError.length > 0;
        if (errored) {
          const errorKind: HistoryTurn["errorKind"] = rawError.includes(
            "Network",
          )
            ? "network"
            : "upstream";
          setHistory((prev) => [
            ...prev,
            {
              role: "assistant",
              content: errorBubbleText(errorKind, rawError),
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
        setParams(body.params ?? null);
        setPlan(body.plan ?? null);
        setReadyToSave(!!body.ready_to_save);
        setSchemaIssues(body.schema_issues ?? []);
      } finally {
        if (mountedRef.current && myId === reqIdRef.current) {
          setPending(false);
          if (answersSent) setPicks({});
        }
      }
    },
    [agentFetch, history, params, pending, saving],
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
    if (!plan || !readyToSave || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const result = await savePolicyFromPlan(agentFetch, plan);
      if (!mountedRef.current) return;
      if (result.ok === false) {
        setSaveError(result.error ?? "Save failed");
        return;
      }
      onSaved(result);
    } finally {
      if (mountedRef.current) setSaving(false);
    }
  }, [agentFetch, onSaved, plan, readyToSave, saving]);

  return (
    <div
      className="flex flex-col gap-4"
      data-testid="conversational-policy-root"
    >
      <p className="text-xs leading-relaxed text-secondary">
        A policy links two rules: one <strong>records</strong> that a
        trustworthy source was fetched, the other <strong>blocks</strong> a
        high-risk tool until that evidence exists this session. Describe it
        below; I&apos;ll ask for the gaps and assemble both rules.
      </p>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_360px]">
        {/* Left column: chat */}
        <section
          className="flex flex-col gap-3 rounded-lg border border-primary/15 bg-white p-4"
          data-testid="conversational-policy-chat-column"
        >
          <div
            ref={scrollRef}
            role="log"
            aria-live="polite"
            className="flex max-h-[480px] min-h-[260px] flex-col gap-3 overflow-y-auto"
            data-testid="conversational-policy-chat-scroll"
          >
            {history.length === 0 ? (
              <div
                className="rounded-md border border-primary/15 bg-primary/[0.04] px-3 py-2 text-sm text-foreground"
                data-testid="conversational-policy-intro"
              >
                Describe the policy (which tool to gate, what counts as a
                trustworthy source, and what to do when it&apos;s missing), or
                pick a starter below. I&apos;ll ask one or two short questions
                and the policy on the right fills in live.
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
                disabled={pending || saving}
              />
            ))}
            {pending ? (
              <div
                className="text-xs italic text-secondary/70"
                data-testid="conversational-policy-typing"
              >
                thinking…
              </div>
            ) : null}
          </div>

          {history.length === 0 ? (
            <div
              className="flex flex-wrap gap-1.5"
              data-testid="conversational-policy-starters"
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
              placeholder="e.g. require a verified sec.gov source before Bash runs"
              rows={2}
              disabled={pending || saving}
              data-testid="conversational-policy-input"
              className="w-full resize-y rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
            <div className="flex items-center justify-end">
              <button
                type="button"
                onClick={onSendClick}
                disabled={pending || saving || !input.trim()}
                data-testid="conversational-policy-send"
                className="rounded-full bg-primary px-3 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                Send
              </button>
            </div>
          </div>
        </section>

        {/* Right column: live policy draft pane */}
        <PolicyDraftPane
          params={params}
          readyToSave={readyToSave}
          schemaIssues={schemaIssues}
          saving={saving}
          saveError={saveError}
          plan={plan}
          onSave={onSaveClick}
        />
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// ChatBubble + question pills (private mirror of conversational-compose.tsx)
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
      data-testid={`policy-chat-turn-${turn.role}`}
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
    // Text questions are answered via the main textarea; render a soft
    // hint only.
    return (
      <div
        className="text-xs italic text-secondary/70"
        data-testid={`policy-q-text-${question.id}`}
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
      data-testid={`policy-q-pick-${question.id}`}
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
// PolicyDraftPane: right column producer + gate cards + Save CTA
// ---------------------------------------------------------------------------


interface PolicyDraftPaneProps {
  params: Record<string, unknown> | null;
  readyToSave: boolean;
  schemaIssues: string[];
  saving: boolean;
  saveError: string | null;
  plan: Record<string, unknown> | null;
  onSave: () => Promise<void> | void;
}


function PolicyDraftPane({
  params,
  readyToSave,
  schemaIssues,
  saving,
  saveError,
  plan,
  onSave,
}: PolicyDraftPaneProps): React.ReactElement {
  const view = summarizePolicyParams(params);
  return (
    <aside
      className="flex h-full flex-col gap-3 rounded-lg border border-primary/15 bg-white p-4"
      data-testid="policy-draft-pane"
    >
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Live policy
        </p>
        <p className="mt-1 text-sm text-secondary">
          {params
            ? "The chat below fills these two linked rules as you answer."
            : "Start the conversation; the policy will appear here."}
        </p>
      </header>

      {/* Producer card */}
      <div
        className="rounded-md border border-secondary/15 bg-secondary/[0.03] px-3 py-2"
        data-testid="policy-draft-producer"
      >
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Records
        </p>
        <p className="mt-1 text-xs leading-relaxed text-foreground">
          {view.evidenceLabel ? (
            <>
              <strong>{view.evidenceLabel}</strong> whenever{" "}
              <span className="font-mono">{view.fetchTool}</span> fetches from{" "}
              {view.domains ? (
                <span className="font-mono">{view.domains}</span>
              ) : (
                <span className="italic text-secondary/50">
                  (no domains set yet)
                </span>
              )}
            </>
          ) : (
            <span className="italic text-secondary/40">(not set yet)</span>
          )}
        </p>
      </div>

      {/* Gate card */}
      <div
        className="rounded-md border border-secondary/15 bg-secondary/[0.03] px-3 py-2"
        data-testid="policy-draft-gate"
      >
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Blocks
        </p>
        <p className="mt-1 text-xs leading-relaxed text-foreground">
          {view.gatedTool ? (
            <>
              <span className="font-mono">{view.gatedTool}</span> until that
              evidence exists this session.
              {view.onUnavailable ? (
                <>
                  {" "}
                  If missing, <strong>{view.onUnavailable}</strong>.
                </>
              ) : null}
            </>
          ) : (
            <span className="italic text-secondary/40">(not set yet)</span>
          )}
        </p>
      </div>

      {schemaIssues.length > 0 ? (
        <div
          className="rounded-md border border-amber-400/40 bg-amber-50 px-2 py-1.5 text-xs text-amber-900"
          data-testid="policy-draft-schema-issues"
        >
          <p className="font-semibold">Validator needs:</p>
          <ul className="list-disc pl-4">
            {schemaIssues.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {plan ? (
        <details className="rounded-md bg-gray-50/80 p-2">
          <summary className="cursor-pointer text-[11px] font-medium text-secondary">
            View assembled plan (producer + gate + binding)
          </summary>
          <pre
            className="mt-2 max-h-72 overflow-auto rounded-lg bg-white p-3 text-[11px] leading-relaxed text-foreground"
            data-testid="policy-draft-plan-json"
          >
            {JSON.stringify(plan, null, 2)}
          </pre>
        </details>
      ) : null}

      {saveError ? (
        <div
          className="rounded-md border border-red-400/40 bg-red-50 px-2 py-1.5 text-xs text-red-800"
          data-testid="policy-draft-save-error"
        >
          {saveError}
        </div>
      ) : null}

      <div className="mt-auto">
        <button
          type="button"
          onClick={() => {
            void onSave();
          }}
          disabled={!readyToSave || saving}
          data-testid="policy-draft-save"
          className="w-full rounded-lg bg-primary px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving
            ? "Saving…"
            : readyToSave
              ? "Save policy"
              : "Save (not ready)"}
        </button>
      </div>
    </aside>
  );
}


// ---------------------------------------------------------------------------
// Pure helpers (testable via source-grep + behavioural test)
// ---------------------------------------------------------------------------


export interface PolicyParamsView {
  evidenceLabel: string;
  gatedTool: string;
  fetchTool: string;
  domains: string;
  /** Plain-language rendering of the onUnavailable branch, or "" if unset. */
  onUnavailable: string;
}


/**
 * Reduce the server-driven policy params into a plain-language view for the
 * two draft cards. Reads defensively; params arrive incrementally across
 * turns, so any field may be absent until the operator supplies it.
 *
 * ``fetchTool`` defaults to ``web_fetch`` in the templater, so we surface
 * that same default here rather than "(not set)" once the label is known.
 */
export function summarizePolicyParams(
  params: Record<string, unknown> | null,
): PolicyParamsView {
  const p = params ?? {};
  const evidenceLabel =
    typeof p.evidenceLabel === "string" ? p.evidenceLabel : "";
  const gatedTool = typeof p.gatedTool === "string" ? p.gatedTool : "";
  const fetchTool =
    typeof p.fetchTool === "string" && p.fetchTool.trim()
      ? p.fetchTool
      : "web_fetch";
  const domains = Array.isArray(p.allowlistDomains)
    ? (p.allowlistDomains.filter((d) => typeof d === "string") as string[]).join(
        ", ",
      )
    : "";
  return {
    evidenceLabel,
    gatedTool,
    fetchTool,
    domains,
    onUnavailable: humanizeOnUnavailable(p.onUnavailable),
  };
}


function humanizeOnUnavailable(value: unknown): string {
  if (value === "deny") return "deny the tool";
  if (value === "ask") return "ask for approval";
  return "";
}


function errorBubbleText(
  kind: NonNullable<HistoryTurn["errorKind"]>,
  raw: string,
): string {
  if (kind === "network") {
    return "Network glitch. Please try sending again.";
  }
  return `Compiler error: ${raw || "unknown"}. Try again or author the producer and gate rules individually.`;
}
