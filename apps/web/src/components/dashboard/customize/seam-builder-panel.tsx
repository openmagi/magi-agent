"use client";

/**
 * SeamBuilderPanel — Stage E of the PresetSeam NL-spec series (handoff §5,
 * PR-C3). The headless body of the "Advanced" sub-nav section in the
 * Customize hub.
 *
 * Flow
 * ----
 * 1. User types a natural-language policy ("partner approval if
 *    fact-grounding returns review") into the textarea.
 * 2. "Compile" → POST /v1/app/customize/seams/compile. The compiler returns
 *    a structured SeamSpec, the LLM critic verdict, and the deterministic
 *    schemaIssues list. All three are surfaced — none is hidden.
 * 3. "Activate" → PUT /v1/app/customize/seams. Server re-validates; a 422
 *    surfaces schemaIssues back to the user (this should never happen if
 *    the compiler critic was honest, but the deterministic gate is the
 *    last line of defence).
 * 4. The saved spec appears in the "Active specs" list with a Delete
 *    button (→ DELETE /v1/app/customize/seams/{id}).
 *
 * The runtime hot path (``seam_for_user``) reads the persisted specs from
 * customize.json; nothing in this component touches the runtime directly.
 *
 * Default-OFF
 * -----------
 * The endpoints are gated by ``MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED``. When OFF
 * the compile route returns ``{ok: false, error: "seam-spec compiler disabled"}``
 * and this component renders that error verbatim so the user knows the
 * feature needs the flag flipped.
 */

import React, { useCallback, useState } from "react";

import {
  compileSeamSpec,
  deleteSeamSpec,
  putSeamSpec,
  type SeamSpecCompileResponse,
  type SeamSpecDoc,
} from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";
import { PageHint } from "./page-hint";


export { describeSpecActions } from "./describe-draft";
import { describeSpecActions } from "./describe-draft";


export interface SeamBuilderPanelProps {
  /** The currently persisted SeamSpec docs (from
   * ``overrides.verification.seam_specs``). Empty array when none are saved
   * yet or the flag is OFF. */
  seamSpecs: SeamSpecDoc[];
  /** Called after a successful Activate / Delete so the parent can reload
   * the catalog snapshot and refresh the list. */
  onChange: () => void;
}


export function SeamBuilderPanel({
  seamSpecs,
  onChange,
}: SeamBuilderPanelProps): React.ReactElement {
  const agentFetch = useAgentFetch();

  const [nlText, setNlText] = useState("");
  const [compileBusy, setCompileBusy] = useState(false);
  const [compileResult, setCompileResult] = useState<SeamSpecCompileResponse | null>(
    null,
  );
  const [activateBusy, setActivateBusy] = useState(false);
  const [activateError, setActivateError] = useState<string | null>(null);
  const [deleteBusyId, setDeleteBusyId] = useState<string | null>(null);

  const handleCompile = useCallback(async () => {
    if (!nlText.trim()) {
      setCompileResult({ ok: false, error: "Enter a policy description first." });
      return;
    }
    setCompileBusy(true);
    setActivateError(null);
    try {
      const out = await compileSeamSpec(agentFetch, nlText);
      setCompileResult(out);
    } finally {
      setCompileBusy(false);
    }
  }, [agentFetch, nlText]);

  const handleActivate = useCallback(async () => {
    if (!compileResult?.ok || !compileResult.spec) return;
    setActivateBusy(true);
    setActivateError(null);
    try {
      await putSeamSpec(agentFetch, compileResult.spec);
      setNlText("");
      setCompileResult(null);
      onChange();
    } catch (err) {
      setActivateError(err instanceof Error ? err.message : "Activate failed");
    } finally {
      setActivateBusy(false);
    }
  }, [agentFetch, compileResult, onChange]);

  const handleDelete = useCallback(
    async (id: string) => {
      setDeleteBusyId(id);
      try {
        await deleteSeamSpec(agentFetch, id);
        onChange();
      } catch (err) {
        setActivateError(err instanceof Error ? err.message : "Delete failed");
      } finally {
        setDeleteBusyId(null);
      }
    },
    [agentFetch, onChange],
  );

  const canActivate = Boolean(
    compileResult?.ok
      && compileResult.spec
      && (compileResult.schemaIssues?.length ?? 0) === 0,
  );

  return (
    <div className="space-y-6">
      <PageHint
        tone="warning"
        title="Rewire existing presets — does NOT add new gates"
        can={[
          { text: <>Flip a built-in preset between <strong>opt-in</strong> and <strong>opt-out</strong></> },
          { text: <>Swap which <code>controls_refs</code> a preset enforces</> },
          { text: <>Add a brand-new <code>preset_id</code> that mutates wiring</> },
        ]}
        cannot={[
          { text: <>Author a brand-new gate → use <strong>Verification → Gates</strong></> },
          { text: <>Run arbitrary Python at lifecycle events → use <strong>Hooks</strong></> },
        ]}
        note={
          <>
            Misconfiguring a seam can silently disable an enforcement gate.
            The compile → review → activate flow forces a human verdict
            before the runtime consumes the change. Default-OFF behind{" "}
            <code>MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED</code>.
          </>
        }
      />

      <section className="space-y-3">
        <label htmlFor="seam-nl" className="block text-sm font-semibold text-foreground">
          Policy in natural language
        </label>
        <textarea
          id="seam-nl"
          value={nlText}
          onChange={(event) => setNlText(event.target.value)}
          rows={4}
          placeholder="e.g. require partner approval when fact-grounding returns review"
          className="w-full rounded-lg border border-black/[0.08] bg-white px-3 py-2 text-sm leading-6 text-foreground shadow-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          aria-label="SeamSpec natural-language policy input"
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
          {compileResult ? (
            <button
              type="button"
              onClick={() => {
                setCompileResult(null);
                setActivateError(null);
              }}
              className="inline-flex items-center rounded-lg border border-black/[0.08] bg-white px-3 py-1.5 text-xs font-medium text-secondary hover:bg-black/[0.03]"
            >
              Clear
            </button>
          ) : null}
        </div>
      </section>

      {compileResult ? (
        <CompileResultView
          result={compileResult}
          canActivate={canActivate}
          activateBusy={activateBusy}
          activateError={activateError}
          onActivate={handleActivate}
        />
      ) : null}

      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">
          Active specs ({seamSpecs.length})
        </h3>
        {seamSpecs.length === 0 ? (
          <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs leading-relaxed text-secondary">
            No SeamSpecs saved yet. Compile + activate a policy above to add one.
          </p>
        ) : (
          <ul className="space-y-2">
            {seamSpecs.map((doc) => (
              <li
                key={doc.id ?? Math.random().toString(36)}
                className="rounded-xl border border-black/[0.06] bg-white px-4 py-3"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-xs font-mono text-secondary">{doc.id}</p>
                    <p className="mt-1 text-xs leading-relaxed text-foreground">
                      {doc.actions.length} action{doc.actions.length === 1 ? "" : "s"}
                      {" — "}
                      {doc.actions
                        .map(
                          (a) =>
                            `${a.op}:${a.preset_id}${a.wiring ? ` (${a.wiring})` : ""}`,
                        )
                        .join(", ")}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => (doc.id ? handleDelete(doc.id) : undefined)}
                    disabled={!doc.id || deleteBusyId === doc.id}
                    className="inline-flex shrink-0 items-center rounded-lg border border-red-200 bg-white px-2 py-1 text-[11px] font-medium text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {deleteBusyId === doc.id ? "Deleting…" : "Delete"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}


function CompileResultView({
  result,
  canActivate,
  activateBusy,
  activateError,
  onActivate,
}: {
  result: SeamSpecCompileResponse;
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

  const spec = result.spec!;
  const review = result.review;
  const schemaIssues = result.schemaIssues ?? [];
  const verdictTone =
    review?.verdict === "aligned"
      ? "text-emerald-700"
      : review?.verdict === "unknown"
        ? "text-secondary"
        : "text-amber-700";

  const humanSummary = describeSpecActions(spec);

  return (
    <section className="space-y-3 rounded-xl border border-black/[0.08] bg-white px-4 py-3">
      <div>
        <p className="text-sm font-semibold text-foreground">This spec will:</p>
        <ul className="mt-1 list-disc space-y-1 pl-5 text-xs leading-relaxed text-foreground">
          {humanSummary.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </div>
      <details className="rounded-lg bg-gray-50/80 p-2">
        <summary className="cursor-pointer text-[11px] font-medium text-secondary">
          View raw SeamSpec JSON
        </summary>
        <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-white p-3 text-[11px] leading-relaxed text-foreground">
          {JSON.stringify(spec, null, 2)}
        </pre>
      </details>

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
            Activate is disabled while schema issues are present or the compile
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
