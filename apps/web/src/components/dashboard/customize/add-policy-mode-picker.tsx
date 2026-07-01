"use client";

/**
 * AddPolicyModePicker: how-to-author entry for a new Rule (NL / Guided / Raw).
 *
 * PR-U3.1 reframe (2026-07-01): this is the Rules-tab, region-aligned add
 * entry. A Rule is an enforcement control: it can BLOCK a turn or a tool, ask
 * for confirmation, or record an audit note. The picker states that region up
 * front and offers three authoring paths that all produce the same kind of
 * rule; it no longer leaks the internal primitive vocabulary into the
 * operator-facing copy (the plain-language cards replace the old mono
 * implementation chips).
 */

import { Code, Sparkles, SlidersHorizontal, X as XIcon } from "lucide-react";
import React from "react";


export type AddPolicyMode = "nl" | "guided" | "raw";


export interface AddPolicyModePickerProps {
  onPick: (mode: AddPolicyMode) => void;
  onCancel: () => void;
}


interface ModeCard {
  id: AddPolicyMode;
  label: string;
  description: string;
  icon: React.ReactNode;
  badge?: string;
}


const MODES: ReadonlyArray<ModeCard> = [
  {
    id: "nl",
    label: "Describe it",
    description:
      "Say what the agent must or must not do, in plain English or Korean. We draft the rule for you to review before it goes live.",
    icon: <Sparkles className="h-5 w-5" />,
    badge: "Recommended",
  },
  {
    id: "guided",
    label: "Answer a few questions",
    description:
      "A short step-by-step: what to check, when it runs, and what happens (block, ask, or just note it). No jargon, no blank form.",
    icon: <SlidersHorizontal className="h-5 w-5" />,
  },
  {
    id: "raw",
    label: "Advanced: fill the form",
    description:
      "For when you already know the exact shape you want and would rather set every field by hand.",
    icon: <Code className="h-5 w-5" />,
  },
];


export function AddPolicyModePicker({
  onPick,
  onCancel,
}: AddPolicyModePickerProps): React.ReactElement {
  return (
    <section
      aria-label="How do you want to add this rule?"
      className="rounded-2xl border border-primary/20 bg-primary/[0.02] p-4 shadow-sm"
    >
      <header className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-bold text-foreground">
            How do you want to add this rule?
          </h3>
          <p className="mt-0.5 text-xs text-secondary">
            A rule enforces something: it can block a turn or a tool, ask for
            confirmation, or record an audit note. Pick the way that feels
            easiest; they all create the same kind of rule.
          </p>
        </div>
        <button
          type="button"
          onClick={onCancel}
          aria-label="Close add rule picker"
          className="rounded-lg p-1.5 text-secondary hover:bg-black/[0.04] hover:text-foreground"
        >
          <XIcon className="h-4 w-4" />
        </button>
      </header>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            onClick={() => onPick(m.id)}
            disabled={m.badge === "Coming soon"}
            className="flex flex-col items-start gap-2 rounded-xl border border-black/[0.08] bg-white p-4 text-left transition-colors hover:border-primary hover:bg-primary/[0.05] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:border-black/[0.08] disabled:hover:bg-white"
          >
            <div className="flex w-full items-center justify-between">
              <span className="rounded-lg bg-primary/10 p-2 text-primary">
                {m.icon}
              </span>
              {m.badge ? (
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                    m.badge === "Recommended"
                      ? "bg-emerald-500/10 text-emerald-700"
                      : "bg-amber-500/10 text-amber-700"
                  }`}
                >
                  {m.badge}
                </span>
              ) : null}
            </div>
            <span className="text-sm font-semibold text-foreground">
              {m.label}
            </span>
            <span className="text-xs leading-relaxed text-secondary">
              {m.description}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
