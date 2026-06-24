"use client";

import type { ControlPlaneBehaviorItem } from "@/lib/customize-api";

interface BehaviorsPanelProps {
  behaviors: ControlPlaneBehaviorItem[];
  /** Explicit per-behavior override (tri-state). Absent → use catalog `enabled`. */
  overrides: Record<string, boolean>;
  onToggle: (id: string, enabled: boolean) => void;
  /** Ids whose PATCH request is currently in-flight. */
  pendingIds?: Set<string>;
  /** Transient error from the most recent failed PATCH. */
  error?: string | null;
}

function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${
        checked ? "bg-primary" : "bg-black/15"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-200 ${
          checked ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}

/**
 * Toggle list for in-context control-plane behaviors (facts-survey replan, goal
 * nudge, etc.). These are gated on `MAGI_*_ENABLED` flags that the lab/dogfood
 * runtime profile seeds ON; an explicit toggle here wins over that seed.
 */
export function BehaviorsPanel({
  behaviors,
  overrides,
  onToggle,
  pendingIds,
  error,
}: BehaviorsPanelProps): React.ReactElement {
  return (
    <>
      {error ? (
        <div className="mb-4 rounded-xl border border-amber-500/25 bg-amber-500/[0.08] px-4 py-3 text-xs leading-5 text-amber-800">
          {error}
        </div>
      ) : null}

      {behaviors.length === 0 ? (
        <div className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-8 text-center text-sm leading-6 text-secondary">
          No control-plane behaviors reported by the local runtime.
        </div>
      ) : (
        <div className="space-y-2">
          {behaviors.map((b) => {
            const enabled = overrides[b.id] ?? b.enabled;
            const isPending = pendingIds?.has(b.id) ?? false;
            return (
              <div
                key={b.id}
                className="flex items-start justify-between gap-4 rounded-xl border border-black/[0.06] bg-[var(--glass-regular-bg)] backdrop-blur-xl px-4 py-3"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-sm font-semibold text-foreground">{b.label}</p>
                    <span className="inline-flex items-center rounded-full bg-black/[0.05] px-2 py-0.5 font-mono text-[11px] font-medium text-secondary">
                      {b.env_var}
                    </span>
                  </div>
                  {b.description ? (
                    <p className="mt-1 text-xs leading-relaxed text-secondary">{b.description}</p>
                  ) : null}
                </div>
                <Toggle
                  checked={enabled}
                  onChange={(next) => onToggle(b.id, next)}
                  label={`Toggle ${b.label}`}
                  disabled={isPending}
                />
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
