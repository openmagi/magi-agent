"use client";

/**
 * NlRuleCompose — primary "describe a rule in English" surface for the
 * Rules page. Hits POST /v1/app/customize/rules/compile (PR-D1) and
 * surfaces the routedKind, plain-English explanation, LLM critic
 * verdict, and deterministic schema-issues honestly so the operator
 * can sanity-check before activating.
 *
 * Activate routes by ``routedKind`` to the matching existing PUT
 * endpoint (this PR does NOT introduce a new persistence path):
 *
 *   - deterministic_ref / tool_perm / llm_criterion / shacl_constraint
 *     → putCustomRule  (customize-api.ts)
 *   - seam_spec
 *     → putSeamSpec    (customize-api.ts)
 *   - custom_check
 *     → putDashboardCheck  (packs-dashboard-api.ts)
 *
 * Flag-OFF: the compile route returns
 * ``{ok:false, error:"nl-rule compiler disabled"}`` and that string is
 * rendered verbatim so the operator knows the flag needs flipping.
 */

import React, { useCallback, useState } from "react";

import {
  compileRule,
  putCustomRule,
  putSeamSpec,
  type ArchitectPrimitive,
  type ArchitectProposal,
  type ConversationTurn,
  type CustomRule,
  type InterviewQuestion,
  type MissingFieldEntry,
  type RoutedKind,
  type RuleCompileResponse,
  type SeamSpecDoc,
} from "@/lib/customize-api";
import {
  putDashboardCheck,
  type DashboardCheck,
} from "@/lib/packs-dashboard-api";
import { useAgentFetch } from "@/lib/local-api";
import { ConversationalCompose } from "./conversational-compose";
import { InterviewMessage } from "./interview-message";
import { NlRuleGuide } from "./nl-rule-guide";
import { ProposalCard } from "./proposal-card";
import { TrustBadge, type TrustClass } from "./trust-badge";


export interface NlRuleComposeProps {
  /** Called after a successful Activate so the parent can refresh its
   *  catalog snapshot (and the rules table re-renders the new row). */
  onActivated: () => void;
  /** Optional callback the parent wires to switch the customize hub
   *  sub-tab to "Evidence" so the F3 honest-degrade banner's
   *  "Browse available fields" button has a real navigation target.
   *  Without this prop the button is hidden — the banner still offers
   *  the advisory degrade path so the operator is never dead-ended. */
  onBrowseEvidence?: () => void;
  /** PR-F-UX6: optional callback to drop the operator into the guided
   *  wizard with the inferred intent pre-filled. When absent the
   *  "Author manually instead" link on the ProposalCard hides. */
  onAuthorManually?: () => void;
  /** PR-F-HANDOFF: optional initial textarea value. The customize hub
   *  passes the serialized wizard primer through this prop when the
   *  operator clicks "Continue in NL" from inside the guided wizard so
   *  the chat resumes exactly where the wizard left off. Treated as a
   *  one-shot seed — once the operator edits the textarea (or another
   *  surface mounts NlRuleCompose without a primer) the value stops
   *  driving the local state. */
  initialNlText?: string;
}


// ---------------------------------------------------------------------------
// PR-F-UX6 — chat-thread types
// ---------------------------------------------------------------------------


/**
 * One turn in the operator-facing chat thread. The first user turn carries
 * the initial NL intent; subsequent user turns are answers to architect
 * interview questions. Assistant turns surface the architect's questions
 * or the proposal preamble (the ProposalCard itself renders out-of-band
 * below the thread).
 */
interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}


/**
 * The current architect state. Mutually exclusive with the legacy
 * compile-result view; the legacy view is preserved for backward-
 * compatibility with the flag-OFF / one-shot success branch (and for any
 * input the architect routes to legacy compile).
 */
type ArchitectState =
  | { kind: "idle" }
  | { kind: "interview"; questions: InterviewQuestion[]; proposalError?: string }
  | { kind: "proposal"; proposal: ArchitectProposal };


// ---------------------------------------------------------------------------
// PR-F-UX6 — groupId generator for hybrid activation
// ---------------------------------------------------------------------------


function newGroupId(): string {
  // crypto.randomUUID is available in evergreen browsers + Node 19+. Fall
  // back to a Math.random base36 string for jsdom test environments that
  // do not expose crypto.randomUUID.
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `grp_${crypto.randomUUID()}`;
  }
  return `grp_${Math.random().toString(36).slice(2, 12)}`;
}


/**
 * Lift an architect primitive into the persistence-shape needed by the
 * matching PUT route. Primitives whose backing storage is a CustomRule
 * (deterministic_ref / tool_perm / llm_criterion / shacl_constraint /
 * field_constraint / capability_scope) get the optional `groupId` stamped
 * onto the rule envelope; seam_spec / custom_check primitives pass
 * through untouched (those routes do not share the custom_rules table).
 */
function stampGroupId<T>(payload: T, groupId: string | null): T {
  if (groupId === null) return payload;
  if (!payload || typeof payload !== "object") return payload;
  return { ...(payload as Record<string, unknown>), groupId } as T;
}


async function activatePrimitive(
  agentFetch: (path: string, init?: RequestInit) => Promise<Response>,
  primitive: ArchitectPrimitive,
  groupId: string | null,
): Promise<void> {
  const kind = primitive.kind;
  if (
    kind === "deterministic_ref"
      || kind === "tool_perm"
      || kind === "llm_criterion"
      || kind === "shacl_constraint"
      || kind === "field_constraint"
      || kind === "capability_scope"
  ) {
    const rule = stampGroupId(primitive.payload as CustomRule, groupId);
    await putCustomRule(agentFetch, rule);
    return;
  }
  if (kind === "seam_spec") {
    await putSeamSpec(agentFetch, primitive.payload as SeamSpecDoc);
    return;
  }
  if (kind === "custom_check") {
    await putDashboardCheck(agentFetch, primitive.payload as DashboardCheck);
    return;
  }
}


const ROUTED_LABEL: Record<RoutedKind, string> = {
  deterministic_ref: "Custom Rule (pre-final, evidence ref)",
  tool_perm: "Custom Rule (before-tool, permission)",
  llm_criterion: "Custom Rule (LLM critic)",
  shacl_constraint: "Custom Rule (SHACL shape)",
  field_constraint: "Custom Rule (field constraint — picker authored)",
  seam_spec: "SeamSpec (rewires a built-in preset)",
  custom_check: "Dashboard Check (after-tool, self-host)",
  capability_scope: "Custom Rule (spawn-time toolset cap)",
};

/**
 * Hint prefix sent on the secondary "Author as advisory llm_criterion
 * instead?" action. The backend NL compiler is biased toward whichever
 * primitive the operator names — so the simplest, contract-stable way to
 * degrade is to prepend the same intent with an explicit routing hint.
 * The compiler will recompile, route to `llm_criterion`, and the result
 * view will surface it with the "Advisory" badge below.
 */
const ADVISORY_HINT_PREFIX =
  "Route as an advisory llm_criterion (no structured field): ";


export function NlRuleCompose({
  onActivated,
  onBrowseEvidence,
  onAuthorManually,
  initialNlText,
}: NlRuleComposeProps): React.ReactElement {
  const agentFetch = useAgentFetch();

  // Conversational mode toggle (PR-F-CONV): the default surface is
  // the magi-cp-style chat-driven builder; the operator can flip back
  // into the F-UX6 one-shot+interview textarea via the toggle below.
  // We keep both behind the SAME parent state so the wizard handoff /
  // "Pick different" affordances continue to work without adding
  // another phase to AddState.
  const [conversational, setConversational] = useState(true);
  if (conversational) {
    return (
      <ConversationalCompose
        agentFetch={agentFetch}
        initialUserMessage={initialNlText}
        onPickDifferent={() => setConversational(false)}
        onSave={async (draft) => {
          // The conversational compiler only sets ready_to_save once
          // ``validate_custom_rule`` accepts the draft, so the PUT
          // should round-trip without surfacing a 4xx.
          try {
            await putCustomRule(agentFetch, draft as unknown as CustomRule);
            onActivated();
          } catch (err: unknown) {
            // Toast / banner handling lives in ``onActivated``'s
            // caller (the customize hub reloads on success); failures
            // surface via the draft pane's schema-issues block on the
            // NEXT compile turn. Re-throw so the caller sees it.
            throw err;
          }
        }}
      />
    );
  }

  // PR-F-HANDOFF — seed local state with the wizard primer when the parent
  // mounted NlRuleCompose with a non-empty initialNlText. The state is
  // otherwise owned locally so the operator can freely edit / overwrite
  // the primer once the surface is on screen.
  const [nlText, setNlText] = useState<string>(initialNlText ?? "");
  const [compileBusy, setCompileBusy] = useState(false);
  const [result, setResult] = useState<RuleCompileResponse | null>(null);
  const [activateBusy, setActivateBusy] = useState(false);
  const [activateError, setActivateError] = useState<string | null>(null);
  // PR-F-UX6 chat-thread state — accumulates user / assistant turns across
  // the interview loop. Empty when the surface is still in single-shot mode.
  const [thread, setThread] = useState<ChatTurn[]>([]);
  const [architectState, setArchitectState] = useState<ArchitectState>({
    kind: "idle",
  });

  const resetSurface = useCallback(() => {
    setResult(null);
    setActivateError(null);
    setThread([]);
    setArchitectState({ kind: "idle" });
  }, []);

  const applyResponse = useCallback(
    (out: RuleCompileResponse) => {
      // PR-F-UX6: classify the response into one of three surfaces.
      if (out.mode === "interview" && out.questions) {
        setArchitectState({ kind: "interview", questions: out.questions });
        // Mirror the architect's first question into the thread so the
        // operator sees a transcript of what was asked.
        if (out.questions.length > 0) {
          const summary = out.questions.map((q) => `Q: ${q.question}`).join("\n");
          setThread((prior) => [
            ...prior,
            { role: "assistant", content: summary },
          ]);
        }
        setResult(null);
        return;
      }
      if (out.mode === "proposal" && out.proposal) {
        setArchitectState({ kind: "proposal", proposal: out.proposal });
        setThread((prior) => [
          ...prior,
          {
            role: "assistant",
            content: `Proposal: ${out.proposal!.summary}`,
          },
        ]);
        setResult(null);
        return;
      }
      // Legacy single-shot success / clarifyingQuestions / error.
      setArchitectState({ kind: "idle" });
      setResult(out);
    },
    [],
  );

  const handleCompile = useCallback(async () => {
    if (!nlText.trim()) {
      setResult({ ok: false, error: "Enter a rule description first." });
      return;
    }
    setCompileBusy(true);
    setActivateError(null);
    const initialTurn: ChatTurn = { role: "user", content: nlText.trim() };
    setThread([initialTurn]);
    try {
      const out = await compileRule(agentFetch, nlText);
      applyResponse(out);
    } finally {
      setCompileBusy(false);
    }
  }, [agentFetch, applyResponse, nlText]);

  /**
   * PR-F-UX6: send an interview answer. Appends the answer to the thread,
   * forwards the full chat history as `priorTurns` to the backend, and
   * forces `mode=interview` so the architect keeps the loop rolling.
   */
  const handleInterviewAnswer = useCallback(
    async (answer: string) => {
      const nextThread: ChatTurn[] = [
        ...thread,
        { role: "user", content: answer },
      ];
      setThread(nextThread);
      setCompileBusy(true);
      try {
        const priorTurns: ConversationTurn[] = nextThread.map((t) => ({
          role: t.role,
          content: t.content,
        }));
        const out = await compileRule(agentFetch, nlText, priorTurns, "interview");
        applyResponse(out);
      } finally {
        setCompileBusy(false);
      }
    },
    [agentFetch, applyResponse, nlText, thread],
  );

  /**
   * PR-F-UX6: "Refine" — ask the architect to revise the proposal in
   * response to a freeform tweak. We reuse the interview machinery: the
   * tweak is appended to the thread as a user turn and the architect
   * re-runs with mode=interview.
   */
  const handleRefineProposal = useCallback(async () => {
    // The simplest "Refine" affordance is to wipe the proposal and let the
    // architect re-interview. The thread is preserved so the user can build
    // on the prior context.
    setArchitectState({ kind: "idle" });
    setCompileBusy(true);
    try {
      const priorTurns: ConversationTurn[] = thread.map((t) => ({
        role: t.role,
        content: t.content,
      }));
      const out = await compileRule(
        agentFetch,
        nlText,
        priorTurns,
        "interview",
      );
      applyResponse(out);
    } finally {
      setCompileBusy(false);
    }
  }, [agentFetch, applyResponse, nlText, thread]);

  /**
   * PR-F-UX6: activate an architect proposal.
   *
   * `mode: "single"` → one putCustomRule (or putSeamSpec / putDashboardCheck)
   *                    call; no groupId.
   * `mode: "hybrid"` → N saves sharing a newly-generated groupId. The
   *                    dashboard's RulesTable groups by groupId and renders
   *                    the hybrid composition as one logical row.
   */
  const handleActivateProposal = useCallback(async () => {
    if (architectState.kind !== "proposal") return;
    const proposal = architectState.proposal;
    setActivateBusy(true);
    setActivateError(null);
    try {
      const groupId = proposal.mode === "hybrid" ? newGroupId() : null;
      for (const primitive of proposal.primitives) {
        await activatePrimitive(agentFetch, primitive, groupId);
      }
      resetSurface();
      setNlText("");
      onActivated();
    } catch (err) {
      setActivateError(
        err instanceof Error ? err.message : "Activate failed",
      );
    } finally {
      setActivateBusy(false);
    }
  }, [agentFetch, architectState, onActivated, resetSurface]);

  const handleAuthorManually = useCallback(() => {
    // Drop to the wizard with whatever intent state was inferred. The
    // wizard does NOT yet read the intent map back (that wiring is a
    // separate follow-up); for now we just kick the parent and let it
    // route to the wizard.
    if (onAuthorManually) onAuthorManually();
  }, [onAuthorManually]);

  /**
   * F3 secondary recovery action: when the compiler returns
   * `error="field_not_in_catalog"`, the operator can choose to re-route
   * the same intent as an advisory `llm_criterion` (which doesn't require
   * a structured-evidence field). We re-issue the compile with a routing
   * hint prepended so the backend picks the new primitive, then stamp
   * `advisory: true` onto the resulting llm_criterion draft (the backend
   * kind menu does not inject it) so the Advisory badge renders and the
   * persisted CustomRule round-trips with the marker.
   */
  const handleDegradeToAdvisory = useCallback(async () => {
    if (!nlText.trim()) return;
    setCompileBusy(true);
    setActivateError(null);
    try {
      const hinted = `${ADVISORY_HINT_PREFIX}${nlText.trim()}`;
      const out = await compileRule(agentFetch, hinted);
      setResult(stampAdvisoryIfApplicable(out));
    } finally {
      setCompileBusy(false);
    }
  }, [agentFetch, nlText]);

  const handleActivate = useCallback(async () => {
    if (!result?.ok || !result.routedKind || result.draft === undefined) return;
    setActivateBusy(true);
    setActivateError(null);
    try {
      if (
        result.routedKind === "deterministic_ref"
          || result.routedKind === "tool_perm"
          || result.routedKind === "llm_criterion"
          || result.routedKind === "shacl_constraint"
          || result.routedKind === "field_constraint"
      ) {
        // field_constraint persists through the same putCustomRule path
        // as shacl_constraint (the backend compiles the structured IR to
        // a SHACL shape at save time; the on-disk kind alias is preserved
        // via `authoredAs` per the F3 design doc).
        await putCustomRule(agentFetch, result.draft as CustomRule);
      } else if (result.routedKind === "seam_spec") {
        await putSeamSpec(agentFetch, result.draft as SeamSpecDoc);
      } else if (result.routedKind === "custom_check") {
        await putDashboardCheck(agentFetch, result.draft as DashboardCheck);
      }
      setNlText("");
      setResult(null);
      onActivated();
    } catch (err) {
      setActivateError(err instanceof Error ? err.message : "Activate failed");
    } finally {
      setActivateBusy(false);
    }
  }, [agentFetch, onActivated, result]);

  const canActivate = Boolean(
    result?.ok
      && result.routedKind
      && result.draft !== undefined
      && (result.schemaIssues?.length ?? 0) === 0,
  );

  const inInterviewLoop =
    architectState.kind === "interview" || architectState.kind === "proposal";

  return (
    <section
      aria-label="Describe a rule in English"
      className="space-y-3 rounded-2xl border border-primary/20 bg-primary/[0.02] p-4 shadow-sm"
    >
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-bold text-foreground">
            Describe a rule in English
          </h3>
          <p className="mt-0.5 text-xs text-secondary">
            The compiler is a policy architect, not a sentence parser. Tell it
            what you want; it will ask what it needs and propose the right
            primitive — or a hybrid composition — with each component&apos;s trust
            class shown honestly.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setConversational(true)}
          className="shrink-0 rounded-full border border-primary/30 bg-white px-2.5 py-1 text-[11px] font-medium text-primary hover:bg-primary/[0.06]"
          data-testid="nl-conversational-toggle"
          title="Build it step-by-step in chat — the assistant asks 1-2 short questions per turn and the draft fills in live."
        >
          Back to conversational ▸
        </button>
      </header>

      <NlRuleGuide onPickExample={(text) => setNlText(text)} />

      <textarea
        value={nlText}
        onChange={(e) => setNlText(e.target.value)}
        rows={3}
        placeholder='e.g. "audit AWS keys", "stop the agent from editing /etc/", "block answers when tests have not run on coding turns"'
        className="w-full resize-y rounded-lg border border-black/[0.10] bg-white px-3 py-2 text-sm leading-6 text-foreground shadow-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        aria-label="Rule policy in natural language"
        disabled={inInterviewLoop}
      />

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleCompile}
          disabled={compileBusy || !nlText.trim() || inInterviewLoop}
          className="inline-flex items-center rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {compileBusy ? "Compiling…" : "Compile"}
        </button>
        {result || inInterviewLoop ? (
          <button
            type="button"
            onClick={() => {
              resetSurface();
            }}
            className="inline-flex items-center rounded-lg border border-black/[0.08] bg-white px-3 py-1.5 text-xs font-medium text-secondary hover:bg-black/[0.03]"
          >
            Clear
          </button>
        ) : null}
      </div>

      {thread.length > 0 ? (
        <ChatThread turns={thread} busy={compileBusy} />
      ) : null}

      {architectState.kind === "interview"
        ? architectState.questions.map((q, idx) => (
            <InterviewMessage
              key={`q-${idx}-${q.question}`}
              question={q}
              onAnswer={handleInterviewAnswer}
              busy={compileBusy}
            />
          ))
        : null}

      {architectState.kind === "proposal" ? (
        <ProposalCard
          proposal={architectState.proposal}
          busy={activateBusy}
          errorText={activateError}
          onActivate={handleActivateProposal}
          onRefine={handleRefineProposal}
          onAuthorManually={handleAuthorManually}
        />
      ) : null}

      {result ? (
        <CompileResultView
          result={result}
          canActivate={canActivate}
          activateBusy={activateBusy}
          activateError={activateError}
          onActivate={handleActivate}
          onDegradeToAdvisory={handleDegradeToAdvisory}
          onBrowseEvidence={onBrowseEvidence}
          onUpdateDraft={(nextDraft) =>
            setResult((prev) =>
              prev ? { ...prev, draft: nextDraft } : prev,
            )
          }
        />
      ) : null}
    </section>
  );
}


/**
 * ChatThread — minimal transcript of the architect interview. Surfaces
 * the operator's intent + each architect turn in plain text so the user
 * can see what was asked / answered before activating. The chip pickers
 * and ProposalCard render OUT-OF-BAND below the thread (they own their
 * own interactive state).
 */
function ChatThread({
  turns,
  busy,
}: {
  turns: ChatTurn[];
  busy: boolean;
}): React.ReactElement {
  return (
    <ol
      aria-label="Architect chat thread"
      className="space-y-2 rounded-xl border border-black/[0.05] bg-white/60 p-3"
    >
      {turns.map((t, idx) => (
        <li
          key={`turn-${idx}`}
          className={`flex gap-2 text-xs leading-relaxed ${
            t.role === "user" ? "text-foreground" : "text-blue-900"
          }`}
        >
          <span
            className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
              t.role === "user"
                ? "bg-black/[0.05] text-secondary"
                : "bg-blue-500/10 text-blue-700"
            }`}
          >
            {t.role}
          </span>
          <span className="min-w-0 whitespace-pre-wrap break-words">
            {t.content}
          </span>
        </li>
      ))}
      {busy ? (
        <li className="text-[11px] italic text-secondary">
          architect thinking…
        </li>
      ) : null}
    </ol>
  );
}


function CompileResultView({
  result,
  canActivate,
  activateBusy,
  activateError,
  onActivate,
  onDegradeToAdvisory,
  onBrowseEvidence,
  onUpdateDraft,
}: {
  result: RuleCompileResponse;
  canActivate: boolean;
  activateBusy: boolean;
  activateError: string | null;
  onActivate: () => void;
  onDegradeToAdvisory: () => void;
  onBrowseEvidence: (() => void) | undefined;
  onUpdateDraft: (nextDraft: unknown) => void;
}): React.ReactElement {
  if (!result.ok) {
    // F3 honest-degrade: backend refused to silently emit a vacuous shape
    // because the rule referenced a field no producer is known to emit.
    if (result.error === "field_not_in_catalog") {
      return (
        <FieldNotInCatalogBanner
          missingFields={result.missingFields ?? []}
          suggestion={result.suggestion}
          onDegradeToAdvisory={onDegradeToAdvisory}
          onBrowseEvidence={onBrowseEvidence}
        />
      );
    }
    if (result.clarifyingQuestions && result.clarifyingQuestions.length > 0) {
      return (
        <section className="rounded-xl border border-blue-200 bg-blue-50/60 px-4 py-3">
          <p className="text-sm font-semibold text-blue-900">
            Compiler needs clarification
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-blue-900">
            {result.clarifyingQuestions.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </section>
      );
    }
    return (
      <section className="rounded-xl border border-red-200 bg-red-50/60 px-4 py-3">
        <p className="text-sm font-semibold text-red-900">Compile failed</p>
        <p className="mt-1 text-xs leading-relaxed text-red-900">
          {result.error ?? "Unknown error"}
        </p>
      </section>
    );
  }

  const routedKind = result.routedKind!;
  const review = result.review;
  const schemaIssues = result.schemaIssues ?? [];
  const verdictTone =
    review?.verdict === "aligned"
      ? "text-emerald-700"
      : review?.verdict === "unknown"
        ? "text-secondary"
        : "text-amber-700";

  return (
    <section className="space-y-3 rounded-xl border border-black/[0.08] bg-[var(--glass-regular-bg)] backdrop-blur-xl px-4 py-3">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Routed to
        </p>
        <div className="mt-0.5 flex items-center gap-2">
          <p className="text-sm font-bold text-foreground">
            {ROUTED_LABEL[routedKind]}
          </p>
          <TrustBadge trustClass={trustClassForRoutedKind(routedKind)} />
        </div>
      </div>

      {result.explanation ? (
        <div>
          <p className="text-xs font-semibold text-foreground">This rule will:</p>
          <p className="mt-1 text-xs leading-relaxed text-foreground">
            {result.explanation}
          </p>
        </div>
      ) : null}

      {routedKind === "field_constraint" ? (
        <FieldConstraintChips
          draft={result.draft}
          onUpdate={onUpdateDraft}
        />
      ) : null}

      {routedKind === "llm_criterion" && isAdvisoryDraft(result.draft) ? (
        <p className="inline-block rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-700">
          Advisory
        </p>
      ) : null}

      {review ? (
        <div>
          <p className="text-xs font-semibold text-foreground">Reviewer verdict</p>
          <p className={`mt-1 text-xs font-medium ${verdictTone}`}>
            {review.verdict} — confidence {review.confidence.toFixed(2)}
          </p>
          {review.issues.length > 0 ? (
            <ul className="mt-1 list-disc space-y-1 pl-5 text-xs leading-relaxed text-secondary">
              {review.issues.map((i) => (
                <li key={i}>{i}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <div>
        <p className="text-xs font-semibold text-foreground">
          Schema check (deterministic)
        </p>
        {schemaIssues.length === 0 ? (
          <p className="mt-1 text-xs text-emerald-700">No structural issues.</p>
        ) : (
          <ul className="mt-1 list-disc space-y-1 pl-5 text-xs leading-relaxed text-red-700">
            {schemaIssues.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ul>
        )}
      </div>

      <details className="rounded-lg bg-gray-50/80 p-2">
        <summary className="cursor-pointer text-[11px] font-medium text-secondary">
          View raw draft JSON
        </summary>
        <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-white p-3 text-[11px] leading-relaxed text-foreground">
          {JSON.stringify(result.draft, null, 2)}
        </pre>
      </details>

      <div className="flex items-center gap-2 pt-1">
        <button
          type="button"
          onClick={onActivate}
          disabled={!canActivate || activateBusy}
          className="inline-flex items-center rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {activateBusy ? "Activating…" : "Activate"}
        </button>
        {!canActivate ? (
          <p className="text-[11px] leading-tight text-secondary">
            Activate disabled while schema issues are present or the compile
            did not succeed.
          </p>
        ) : null}
      </div>

      {activateError ? (
        <p className="text-xs leading-relaxed text-red-700">{activateError}</p>
      ) : null}
    </section>
  );
}


// ---------------------------------------------------------------------------
// F3 — structured field_constraint chip renderer + honest-degrade banner
// ---------------------------------------------------------------------------

/**
 * Subset of the structured IR the backend emits for `field_constraint`.
 * Single-record predicates use {evidenceType, field, operator, value}.
 * Cross-record cardinality (operator === "forEachExistsCovering") uses
 * {operator, source:{evidenceType, field}, target:{evidenceType, field}}.
 * Either shape may live nested under `draft.what.payload.authoredAs` (the
 * canonical persistence location) or directly on `draft.authoredAs` for
 * the just-compiled draft. We accept both.
 */
interface FieldConstraintIR {
  evidenceType?: string;
  field?: string;
  operator?: string;
  value?: unknown;
  source?: { evidenceType?: string; field?: string };
  target?: { evidenceType?: string; field?: string };
}

function extractFieldConstraintIR(draft: unknown): FieldConstraintIR | null {
  if (!draft || typeof draft !== "object") return null;
  const root = draft as Record<string, unknown>;
  const authoredAsTop = root["authoredAs"];
  if (authoredAsTop && typeof authoredAsTop === "object") {
    return authoredAsTop as FieldConstraintIR;
  }
  const what = root["what"];
  if (what && typeof what === "object") {
    const payload = (what as Record<string, unknown>)["payload"];
    if (payload && typeof payload === "object") {
      const authoredAs = (payload as Record<string, unknown>)["authoredAs"];
      if (authoredAs && typeof authoredAs === "object") {
        return authoredAs as FieldConstraintIR;
      }
      // Fallback: the backend may emit the IR fields directly on payload
      // before the `authoredAs` wrapper is finalised at save time.
      const payloadRec = payload as Record<string, unknown>;
      if (
        "evidenceType" in payloadRec
          || "operator" in payloadRec
          || "source" in payloadRec
      ) {
        return payloadRec as FieldConstraintIR;
      }
    }
  }
  return null;
}

/**
 * F5 — derive the honesty trust class for a routedKind label.
 *
 * Keyed on the backend routedKind names. The only Advisory routing today
 * is `llm_criterion` (LLM critic — guidance the model may ignore); every
 * other routedKind compiles to a deterministic runtime gate
 * (deterministic_ref / tool_perm / shacl_constraint / field_constraint /
 * seam_spec / custom_check). Future Advisory routings extend this
 * switch — the call site stays single-line.
 */
function trustClassForRoutedKind(routedKind: RoutedKind): TrustClass {
  if (routedKind === "llm_criterion") return "advisory";
  return "deterministic";
}


function isAdvisoryDraft(draft: unknown): boolean {
  if (!draft || typeof draft !== "object") return false;
  const root = draft as Record<string, unknown>;
  if (root["advisory"] === true) return true;
  const what = root["what"];
  if (what && typeof what === "object") {
    const payload = (what as Record<string, unknown>)["payload"];
    if (payload && typeof payload === "object") {
      const advisory = (payload as Record<string, unknown>)["advisory"];
      if (advisory === true) return true;
    }
  }
  return false;
}


/**
 * FieldConstraintChips — renders the structured field_constraint IR as
 * editable chips so the operator can sanity-check (and tweak) the picker
 * tuple before activating. Each chip is a labeled inline-input so edits
 * round-trip through `onUpdate` back into `result.draft`.
 */
function FieldConstraintChips({
  draft,
  onUpdate,
}: {
  draft: unknown;
  onUpdate: (nextDraft: unknown) => void;
}): React.ReactElement | null {
  const ir = extractFieldConstraintIR(draft);
  if (!ir) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-[11px] leading-relaxed text-amber-900">
        Structured IR missing. Review the raw draft JSON below.
      </div>
    );
  }

  const writeIR = (next: FieldConstraintIR): void => {
    if (!draft || typeof draft !== "object") {
      onUpdate({ authoredAs: next });
      return;
    }
    const root = draft as Record<string, unknown>;
    const what = root["what"];
    if (what && typeof what === "object") {
      const payload = (what as Record<string, unknown>)["payload"];
      if (payload && typeof payload === "object") {
        const nextPayload = {
          ...(payload as Record<string, unknown>),
          authoredAs: next,
        };
        const nextWhat = { ...(what as Record<string, unknown>), payload: nextPayload };
        onUpdate({ ...root, what: nextWhat });
        return;
      }
    }
    onUpdate({ ...root, authoredAs: next });
  };

  const isCross = ir.operator === "forEachExistsCovering";
  const valueString =
    ir.value === undefined || ir.value === null
      ? ""
      : typeof ir.value === "string"
        ? ir.value
        : JSON.stringify(ir.value);

  return (
    <div>
      <p className="text-xs font-semibold text-foreground">
        Field constraint (structured)
      </p>
      <p className="mt-1 text-[11px] leading-relaxed text-secondary">
        Edit any chip to refine the picker tuple before activating.
      </p>
      {!isCross ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Chip
            label="Evidence type"
            value={ir.evidenceType ?? ""}
            onChange={(v) => writeIR({ ...ir, evidenceType: v })}
          />
          <Chip
            label="Field"
            value={ir.field ?? ""}
            onChange={(v) => writeIR({ ...ir, field: v })}
          />
          <Chip
            label="Operator"
            value={ir.operator ?? ""}
            onChange={(v) => writeIR({ ...ir, operator: v })}
            mono
          />
          <Chip
            label="Value"
            value={valueString}
            onChange={(v) => writeIR({ ...ir, value: v })}
          />
        </div>
      ) : (
        <div className="mt-2 space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
            Operator: forEachExistsCovering
          </p>
          <div className="rounded-lg border border-black/[0.06] bg-gray-50/60 px-3 py-2">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-secondary/70">
              Source
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <Chip
                label="Evidence type"
                value={ir.source?.evidenceType ?? ""}
                onChange={(v) =>
                  writeIR({
                    ...ir,
                    source: { ...(ir.source ?? {}), evidenceType: v },
                  })
                }
              />
              <Chip
                label="Field"
                value={ir.source?.field ?? ""}
                onChange={(v) =>
                  writeIR({
                    ...ir,
                    source: { ...(ir.source ?? {}), field: v },
                  })
                }
              />
            </div>
          </div>
          <div className="rounded-lg border border-black/[0.06] bg-gray-50/60 px-3 py-2">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-secondary/70">
              Target
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <Chip
                label="Evidence type"
                value={ir.target?.evidenceType ?? ""}
                onChange={(v) =>
                  writeIR({
                    ...ir,
                    target: { ...(ir.target ?? {}), evidenceType: v },
                  })
                }
              />
              <Chip
                label="Field"
                value={ir.target?.field ?? ""}
                onChange={(v) =>
                  writeIR({
                    ...ir,
                    target: { ...(ir.target ?? {}), field: v },
                  })
                }
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


function Chip({
  label,
  value,
  onChange,
  mono,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  mono?: boolean;
}): React.ReactElement {
  return (
    <label className="inline-flex items-center gap-1 rounded-full border border-black/[0.08] bg-white px-2 py-0.5 text-[11px] shadow-sm">
      <span className="font-semibold text-secondary/80">{label}:</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
        className={`min-w-[5rem] bg-transparent text-foreground outline-none ${
          mono ? "font-mono" : ""
        }`}
      />
    </label>
  );
}


/**
 * FieldNotInCatalogBanner — the F3 honest-degrade red banner. Surfaces
 * the structured `field_not_in_catalog` error returned by the backend
 * when a rule references a field no producer is known to emit. Offers
 * two paths forward, never a dead end:
 *   1. "Browse available fields" — open the Reusable evidence tab.
 *   2. "Author as advisory llm_criterion instead?" — recompile with a
 *      hint to route the same intent as an advisory llm_criterion.
 */
function FieldNotInCatalogBanner({
  missingFields,
  suggestion,
  onDegradeToAdvisory,
  onBrowseEvidence,
}: {
  missingFields: MissingFieldEntry[];
  suggestion: string | undefined;
  onDegradeToAdvisory: () => void;
  onBrowseEvidence: (() => void) | undefined;
}): React.ReactElement {
  return (
    <section
      role="alert"
      className="rounded-xl border border-red-300 bg-red-50/70 px-4 py-3"
    >
      <p className="text-sm font-semibold text-red-900">
        {"This rule references a field that isn't emitted as structured evidence yet"}
      </p>
      {missingFields.length > 0 ? (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-red-900">
          {missingFields.map((m) => (
            <li key={`${m.evidenceType}.${m.field}`}>
              <span className="font-mono">
                {m.evidenceType}.{m.field}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {suggestion ? (
        <p className="mt-2 text-[11px] leading-relaxed text-red-900/80">
          {suggestion}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {onBrowseEvidence ? (
          <button
            type="button"
            onClick={onBrowseEvidence}
            className="inline-flex items-center rounded-lg border border-red-300 bg-white px-3 py-1.5 text-xs font-semibold text-red-900 shadow-sm hover:bg-red-50"
          >
            Browse available fields
          </button>
        ) : null}
        <button
          type="button"
          onClick={onDegradeToAdvisory}
          className="text-xs font-medium text-red-900 underline underline-offset-2 hover:text-red-700"
        >
          Author as advisory llm_criterion instead?
        </button>
      </div>
    </section>
  );
}


/**
 * Stamp ``advisory: true`` onto a llm_criterion draft when the backend
 * routed to that kind in response to the advisory-hint recompile.
 *
 * The backend kind menu does not currently inject ``advisory: true`` into
 * the llm_criterion payload — see ``magi_agent/customize/rule_compiler.py``
 * ``_KIND_MENU`` lines 250-255. Without this stamp the Advisory badge
 * (predicate :func:`isAdvisoryDraft`) would never render, and the persisted
 * CustomRule would lose the marker on round-trip. We do the stamp on the
 * frontend so a backend round-trip / activate continues to carry it.
 *
 * No-op for non-ok responses, non-llm_criterion routings, or drafts that
 * already carry ``advisory: true``.
 */
function stampAdvisoryIfApplicable(
  response: RuleCompileResponse,
): RuleCompileResponse {
  if (!response.ok) return response;
  if (response.routedKind !== "llm_criterion") return response;
  const draft = response.draft;
  if (!draft || typeof draft !== "object") return response;
  const root = { ...(draft as Record<string, unknown>) };
  const what = root["what"];
  if (what && typeof what === "object") {
    const whatRec = what as Record<string, unknown>;
    const payload = whatRec["payload"];
    const payloadRec =
      payload && typeof payload === "object"
        ? (payload as Record<string, unknown>)
        : {};
    if (payloadRec["advisory"] === true) return response;
    const nextPayload = { ...payloadRec, advisory: true };
    const nextWhat = { ...whatRec, payload: nextPayload };
    return { ...response, draft: { ...root, what: nextWhat } };
  }
  // Defensive: stamp top-level when the draft is not envelope-shaped.
  if (root["advisory"] === true) return response;
  return { ...response, draft: { ...root, advisory: true } };
}
