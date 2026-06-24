"use client";

/**
 * PR-F7 — Budgets sub-tab for the Customize hub.
 *
 * Surfaces three operator-authored cost-vocabulary knobs that the runtime
 * already enforces via MAGI_* env variables, but had no Customize surface
 * until this PR:
 *
 *   - Max tool calls per turn       (MAGI_TOOL_MAX_CALLS_PER_TURN)
 *   - Max steps brake (hard)         (MAGI_MAX_STEPS_BRAKE_HARD)
 *   - Loop-guard hard threshold      (MAGI_LOOP_GUARD_HARD_THRESHOLD)
 *
 * Persistence is via PUT /v1/app/customize/budgets; the backend applier
 * (`apply_budgets_if_enabled`) projects the save onto live env at turn entry
 * via `setdefault` so an explicit operator env always wins. When that
 * happens the budget the user just saved is dormant — we render the env
 * value next to each row in a read-only badge so the user can see why.
 *
 * Inputs accept positive integers (or empty = clear). Save submits the union
 * of all 3 fields; the backend rejects 0 / negatives / non-int / unknown keys.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Gauge } from "lucide-react";

import type { BudgetsResponse, VerificationBudgets } from "@/lib/customize-api";

/** Frozen UI vocabulary — order is the render order in the form. */
interface BudgetField {
  /** Persisted dict key (matches the backend BUDGET_ENV_MAP). */
  key: keyof VerificationBudgets;
  label: string;
  description: string;
  /** Inline placeholder hint about the runtime default. */
  defaultHint: string;
}

const BUDGET_FIELDS: ReadonlyArray<BudgetField> = [
  {
    key: "maxToolCallsPerTurn",
    label: "Max tool calls per turn",
    description:
      "Hard cap on the number of tool calls the agent may make in a single turn before the toolhost refuses with max_tool_calls_exhausted. Lower = tighter budget, higher = more iteration headroom.",
    defaultHint: "Runtime default: 64. Range 1–4096.",
  },
  {
    key: "maxStepsBrakeHard",
    label: "Max steps brake (hard)",
    description:
      "Hard ceiling for the max-steps brake. Today the runtime carries this as a sentinel only (no numeric flag is registered yet); the F7 surface persists the value so the planned brake wiring picks it up without a dashboard migration.",
    defaultHint: "Runtime default: unset (brake inert until wired).",
  },
  {
    key: "loopGuardHardThreshold",
    label: "Loop-guard hard threshold",
    description:
      "Number of consecutive matching tool calls before the loop-guard hard-escalates and aborts the turn. Lower = trip earlier on repetition, higher = tolerate iterative search.",
    defaultHint: "Runtime default: 5. Eval profile seeds 50.",
  },
];

interface BudgetsTabProps {
  budgets: VerificationBudgets;
  effectiveEnv: BudgetsResponse["effectiveEnv"];
  envMap: BudgetsResponse["envMap"];
  loading?: boolean;
  saving?: boolean;
  /** Surface error from the most recent failed load or save. */
  error?: string | null;
  /** Called with the union of all 3 fields when the user clicks Save. */
  onSave: (next: VerificationBudgets) => void;
  /** Optional reload trigger (used by the parent on retry). */
  onReload?: () => void;
}

/** Trim leading zeros and reject anything non-numeric — keep "" to clear. */
function _coerceInput(raw: string): string {
  const trimmed = raw.replace(/[^0-9]/g, "").replace(/^0+(?=\d)/, "");
  return trimmed;
}

/** Convert the typed `value` (empty string OR positive int string) to the
 * persisted shape (omit empty strings). */
function _toBudgets(values: Record<string, string>): VerificationBudgets {
  const out: VerificationBudgets = {};
  for (const f of BUDGET_FIELDS) {
    const raw = values[f.key];
    if (!raw) continue;
    const n = Number.parseInt(raw, 10);
    if (Number.isFinite(n) && n > 0) {
      (out as Record<string, number>)[f.key] = n;
    }
  }
  return out;
}

export function BudgetsTab({
  budgets,
  effectiveEnv,
  envMap,
  loading,
  saving,
  error,
  onSave,
  onReload,
}: BudgetsTabProps): React.ReactElement {
  // Local edit buffer — string-typed so the user can clear a field. Synced
  // from props on mount and when the parent reloads the persisted state.
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      BUDGET_FIELDS.map((f) => [f.key, String(budgets[f.key] ?? "")]),
    ),
  );

  useEffect(() => {
    setValues(
      Object.fromEntries(
        BUDGET_FIELDS.map((f) => [f.key, String(budgets[f.key] ?? "")]),
      ),
    );
  }, [budgets]);

  const dirty = useMemo(() => {
    for (const f of BUDGET_FIELDS) {
      const persisted = String(budgets[f.key] ?? "");
      if (values[f.key] !== persisted) return true;
    }
    return false;
  }, [values, budgets]);

  const handleSave = useCallback(() => {
    onSave(_toBudgets(values));
  }, [onSave, values]);

  return (
    <>
      <div className="mb-5 rounded-xl border border-black/[0.06] bg-gray-50/80 px-4 py-3 text-xs leading-5 text-secondary">
        <div className="flex items-start gap-2">
          <Gauge className="mt-0.5 h-4 w-4 shrink-0 text-secondary/80" />
          <p>
            Per-bot cost ceilings. Saved values are projected onto the
            matching <code className="rounded bg-black/[0.05] px-1 font-mono">MAGI_*</code> env
            at turn entry. An explicit operator env (k8s deployment, shell
            export, dogfood profile) always wins — when that happens the value
            you save here is dormant, and the resolved env appears in the
            badge next to the field.
          </p>
        </div>
      </div>

      {error ? (
        <div className="mb-4 flex items-center justify-between gap-4 rounded-xl border border-amber-500/25 bg-amber-500/[0.08] px-4 py-3 text-xs leading-5 text-amber-800">
          <span>{error}</span>
          {onReload ? (
            <button
              type="button"
              onClick={onReload}
              className="rounded-lg border border-amber-500/30 bg-white px-3 py-1 text-xs font-semibold text-amber-800 transition-colors hover:bg-amber-50"
            >
              Retry
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="space-y-3">
        {BUDGET_FIELDS.map((f) => {
          const envName = envMap[f.key] ?? "";
          const envValue = effectiveEnv[f.key] ?? null;
          const envOverridesSave =
            envValue !== null &&
            envValue !== undefined &&
            values[f.key] !== "" &&
            envValue !== String(values[f.key] ?? "");

          return (
            <div
              key={f.key}
              className="rounded-xl border border-black/[0.06] bg-white px-4 py-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <label
                  htmlFor={`budget-${f.key}`}
                  className="text-sm font-semibold text-foreground"
                >
                  {f.label}
                </label>
                {envName ? (
                  <span className="inline-flex items-center rounded-full bg-black/[0.05] px-2 py-0.5 font-mono text-[11px] font-medium text-secondary">
                    {envName}
                  </span>
                ) : null}
              </div>
              <p className="mt-1 text-xs leading-relaxed text-secondary">
                {f.description}
              </p>
              <p className="mt-1 text-[11px] leading-relaxed text-secondary/80">
                {f.defaultHint}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <input
                  id={`budget-${f.key}`}
                  inputMode="numeric"
                  pattern="[0-9]*"
                  type="text"
                  value={values[f.key] ?? ""}
                  placeholder="Unset"
                  onChange={(e) =>
                    setValues((prev) => ({
                      ...prev,
                      [f.key]: _coerceInput(e.target.value),
                    }))
                  }
                  disabled={loading || saving}
                  className="w-40 rounded-lg border border-black/[0.10] bg-white px-3 py-2 font-mono text-sm text-foreground focus-visible:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50"
                  aria-describedby={`budget-${f.key}-env`}
                />
                <span
                  id={`budget-${f.key}-env`}
                  className="text-[11px] leading-tight text-secondary"
                >
                  {envValue === null || envValue === undefined ? (
                    <>Env unset — your save takes effect next turn.</>
                  ) : (
                    <>
                      Env currently:{" "}
                      <code className="rounded bg-black/[0.05] px-1 font-mono">
                        {envValue}
                      </code>
                      {envOverridesSave ? (
                        <span className="ml-1 text-amber-700">
                          (operator env wins — your save is dormant)
                        </span>
                      ) : null}
                    </>
                  )}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-5 flex items-center justify-end gap-3">
        <button
          type="button"
          onClick={handleSave}
          disabled={!dirty || saving || loading}
          className="inline-flex min-h-[40px] items-center rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save budgets"}
        </button>
      </div>
    </>
  );
}
