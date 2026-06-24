"use client";

/**
 * Shared chrome for the Guided policy wizards (PR-E3).
 *
 * One header + progress-bar + back/next/save row + ``RadioCard``
 * primitive that every per-kind sub-wizard reuses, so all wizards share
 * the same toss-style look and keyboard/aria behavior.
 *
 * Each sub-wizard owns its own step list + step bodies; the chrome only
 * cares about ``step``, ``total``, and the per-step Next gate. This
 * keeps each sub-wizard small (~150 LOC) and decoupled from the others.
 */

import React from "react";


export interface WizardChromeProps {
  step: number;
  total: number;
  onPickDifferent: () => void;
  onCancel: () => void;
  onBack: () => void;
  onNext: () => void;
  onSave: () => void;
  canAdvance: boolean;
  saving: boolean;
  /** Inline error rendered above the nav row (e.g. save failure). */
  error?: string | null;
  /** Step body content. */
  children: React.ReactNode;
  /** Wrap the section with an ARIA label so screen-readers announce
   *  "Guided policy wizard, step N / M". */
  ariaLabel?: string;
}


export function WizardChrome({
  step,
  total,
  onPickDifferent,
  onCancel,
  onBack,
  onNext,
  onSave,
  canAdvance,
  saving,
  error,
  children,
  ariaLabel = "Guided policy wizard",
}: WizardChromeProps): React.ReactElement {
  const isFirst = step === 0;
  const isLast = step === total - 1;
  return (
    <section
      aria-label={ariaLabel}
      className="space-y-4 rounded-2xl border border-primary/20 bg-primary/[0.02] p-5 shadow-sm"
    >
      <WizardHeader
        step={step}
        total={total}
        onPickDifferent={onPickDifferent}
      />

      <div className="min-h-[280px]">{children}</div>

      {error ? (
        <p className="rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-xs text-red-700">
          {error}
        </p>
      ) : null}

      <div className="flex items-center justify-between">
        {isFirst ? (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg px-3 py-1.5 text-xs font-medium text-secondary hover:bg-black/[0.04] hover:text-foreground"
          >
            Cancel
          </button>
        ) : (
          <button
            type="button"
            onClick={onBack}
            className="rounded-lg px-3 py-1.5 text-xs font-medium text-secondary hover:bg-black/[0.04] hover:text-foreground"
          >
            ← Back
          </button>
        )}
        {isLast ? (
          <button
            type="button"
            onClick={onSave}
            disabled={saving || !canAdvance}
            className="inline-flex items-center rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save policy"}
          </button>
        ) : (
          <button
            type="button"
            onClick={onNext}
            disabled={!canAdvance}
            className="inline-flex items-center rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Next →
          </button>
        )}
      </div>
    </section>
  );
}


function WizardHeader({
  step,
  total,
  onPickDifferent,
}: {
  step: number;
  total: number;
  onPickDifferent: () => void;
}): React.ReactElement {
  return (
    <header className="flex items-center justify-between gap-3">
      <button
        type="button"
        onClick={onPickDifferent}
        className="rounded-lg px-2 py-1 text-[11px] font-medium text-secondary hover:bg-black/[0.04] hover:text-foreground"
      >
        ← Pick different
      </button>
      <div
        role="progressbar"
        aria-valuenow={step + 1}
        aria-valuemax={total}
        aria-label="Wizard progress"
        className="flex items-center gap-1.5"
      >
        {Array.from({ length: total }).map((_, i) => (
          <span
            key={i}
            className={`h-1.5 rounded-full transition-all ${
              i === step
                ? "w-6 bg-primary"
                : i < step
                  ? "w-1.5 bg-primary/60"
                  : "w-1.5 bg-black/[0.10]"
            }`}
          />
        ))}
        <span className="ml-2 text-[11px] font-medium text-secondary">
          {step + 1} / {total}
        </span>
      </div>
    </header>
  );
}


// ---------------------------------------------------------------------------
// Shared step-body primitive — radio-style card
// ---------------------------------------------------------------------------


export interface RadioCardProps {
  checked: boolean;
  onClick: () => void;
  label: string;
  description?: string;
  badge?: string;
  monoLabel?: string;
  // PR-F-UX1: visible-but-not-selectable. Used for Tier 3 lifecycle entries
  // (hooks that have no custom_rule gate today and must be authored via
  // ~/.magi/settings.json instead). The card renders fainter and ignores
  // clicks; ``disabledReason`` becomes the native HTML tooltip so operators
  // see WHY they cannot pick this option.
  disabled?: boolean;
  disabledReason?: string;
}


export function RadioCard({
  checked,
  onClick,
  label,
  description,
  badge,
  monoLabel,
  disabled = false,
  disabledReason,
}: RadioCardProps): React.ReactElement {
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      aria-pressed={checked}
      aria-disabled={disabled || undefined}
      disabled={disabled}
      title={disabled ? disabledReason : undefined}
      className={`flex w-full items-start justify-between gap-3 rounded-xl border px-4 py-3 text-left transition-colors ${
        disabled
          ? "cursor-not-allowed border-black/[0.06] bg-gray-50/50 opacity-60"
          : checked
            ? "border-primary bg-primary/[0.04]"
            : "border-black/[0.08] bg-white hover:border-primary/40 hover:bg-primary/[0.02]"
      }`}
    >
      <div className="min-w-0">
        <p className="text-sm font-semibold text-foreground">{label}</p>
        {monoLabel ? (
          <p className="mt-0.5 text-[11px] font-mono text-secondary/80">{monoLabel}</p>
        ) : null}
        {description ? (
          <p className="mt-1 text-xs leading-relaxed text-secondary">{description}</p>
        ) : null}
      </div>
      {badge ? (
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
            badge === "recommended"
              ? "bg-emerald-500/10 text-emerald-700"
              : "bg-blue-500/10 text-blue-700"
          }`}
        >
          {badge}
        </span>
      ) : null}
    </button>
  );
}
