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
  type CustomRule,
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
import { NlRuleGuide } from "./nl-rule-guide";
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
}


const ROUTED_LABEL: Record<RoutedKind, string> = {
  deterministic_ref: "Custom Rule (pre-final, evidence ref)",
  tool_perm: "Custom Rule (before-tool, permission)",
  llm_criterion: "Custom Rule (LLM critic)",
  shacl_constraint: "Custom Rule (SHACL shape)",
  field_constraint: "Custom Rule (field constraint — picker authored)",
  seam_spec: "SeamSpec (rewires a built-in preset)",
  custom_check: "Dashboard Check (after-tool, self-host)",
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
}: NlRuleComposeProps): React.ReactElement {
  const agentFetch = useAgentFetch();

  const [nlText, setNlText] = useState("");
  const [compileBusy, setCompileBusy] = useState(false);
  const [result, setResult] = useState<RuleCompileResponse | null>(null);
  const [activateBusy, setActivateBusy] = useState(false);
  const [activateError, setActivateError] = useState<string | null>(null);

  const handleCompile = useCallback(async () => {
    if (!nlText.trim()) {
      setResult({ ok: false, error: "Enter a rule description first." });
      return;
    }
    setCompileBusy(true);
    setActivateError(null);
    try {
      const out = await compileRule(agentFetch, nlText);
      setResult(out);
    } finally {
      setCompileBusy(false);
    }
  }, [agentFetch, nlText]);

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

  return (
    <section
      aria-label="Describe a rule in English"
      className="space-y-3 rounded-2xl border border-primary/20 bg-primary/[0.02] p-4 shadow-sm"
    >
      <header>
        <h3 className="text-sm font-bold text-foreground">
          Describe a rule in English
        </h3>
        <p className="mt-0.5 text-xs text-secondary">
          Type what you want the agent to do or not do — the compiler picks
          the right backing primitive (custom rule, seam rewire, after-tool
          check) and surfaces a draft for you to review before activating.
        </p>
      </header>

      <NlRuleGuide onPickExample={(text) => setNlText(text)} />

      <textarea
        value={nlText}
        onChange={(e) => setNlText(e.target.value)}
        rows={3}
        placeholder='e.g. "deny shell_exec", "block answers when tests have not run on coding turns", "rewire fact-grounding to opt-in"'
        className="w-full resize-y rounded-lg border border-black/[0.10] bg-white px-3 py-2 text-sm leading-6 text-foreground shadow-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        aria-label="Rule policy in natural language"
      />

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleCompile}
          disabled={compileBusy || !nlText.trim()}
          className="inline-flex items-center rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {compileBusy ? "Compiling…" : "Compile"}
        </button>
        {result ? (
          <button
            type="button"
            onClick={() => {
              setResult(null);
              setActivateError(null);
            }}
            className="inline-flex items-center rounded-lg border border-black/[0.08] bg-white px-3 py-1.5 text-xs font-medium text-secondary hover:bg-black/[0.03]"
          >
            Clear
          </button>
        ) : null}
      </div>

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
