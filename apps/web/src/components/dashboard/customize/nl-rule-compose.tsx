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
  type RoutedKind,
  type RuleCompileResponse,
  type SeamSpecDoc,
} from "@/lib/customize-api";
import {
  putDashboardCheck,
  type DashboardCheck,
} from "@/lib/packs-dashboard-api";
import { useAgentFetch } from "@/lib/local-api";


export interface NlRuleComposeProps {
  /** Called after a successful Activate so the parent can refresh its
   *  catalog snapshot (and the rules table re-renders the new row). */
  onActivated: () => void;
}


const ROUTED_LABEL: Record<RoutedKind, string> = {
  deterministic_ref: "Custom Rule (pre-final, evidence ref)",
  tool_perm: "Custom Rule (before-tool, permission)",
  llm_criterion: "Custom Rule (LLM critic)",
  shacl_constraint: "Custom Rule (SHACL shape)",
  seam_spec: "SeamSpec (rewires a built-in preset)",
  custom_check: "Dashboard Check (after-tool, self-host)",
};


export function NlRuleCompose({
  onActivated,
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
      ) {
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
}: {
  result: RuleCompileResponse;
  canActivate: boolean;
  activateBusy: boolean;
  activateError: string | null;
  onActivate: () => void;
}): React.ReactElement {
  if (!result.ok) {
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
    <section className="space-y-3 rounded-xl border border-black/[0.08] bg-white px-4 py-3">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Routed to
        </p>
        <p className="mt-0.5 text-sm font-bold text-foreground">
          {ROUTED_LABEL[routedKind]}
        </p>
      </div>

      {result.explanation ? (
        <div>
          <p className="text-xs font-semibold text-foreground">This rule will:</p>
          <p className="mt-1 text-xs leading-relaxed text-foreground">
            {result.explanation}
          </p>
        </div>
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
