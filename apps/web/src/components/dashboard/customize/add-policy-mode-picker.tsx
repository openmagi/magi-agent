"use client";

/**
 * AddPolicyModePicker — 3-mode entry point (NL / Guided / Raw).
 *
 * Maps the control-plane "How do you want to author this policy?" prompt
 * to magi-agent. NL ships in PR-E1 via the existing ``NlRuleCompose``;
 * Guided is a placeholder until PR-E3 lands the toss-style wizard; Raw
 * routes back to the legacy 4-kind picker so power-users can still drop
 * to the structured form.
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
  backing: string;
  badge?: string;
}


const MODES: ReadonlyArray<ModeCard> = [
  {
    id: "nl",
    label: "Natural language",
    description:
      "Describe the policy in plain English or Korean — an LLM compiles it into a draft for you to review.",
    icon: <Sparkles className="h-5 w-5" />,
    backing: "NL → routedKind compiler",
    badge: "Recommended",
  },
  {
    id: "guided",
    label: "Guided assembly",
    description:
      "Step-by-step picker — answer one question per screen. No NL ambiguity, no raw form burden.",
    icon: <SlidersHorizontal className="h-5 w-5" />,
    backing: "Constrained wizard",
  },
  {
    id: "raw",
    label: "Advanced — direct form",
    description:
      "For users who know the underlying primitive. Fill rule fields by hand (kind / scope / firesAt / action).",
    icon: <Code className="h-5 w-5" />,
    backing: "Custom Rule / SeamSpec / Dashboard Check forms",
  },
];


export function AddPolicyModePicker({
  onPick,
  onCancel,
}: AddPolicyModePickerProps): React.ReactElement {
  return (
    <section
      aria-label="How do you want to author this policy?"
      className="rounded-2xl border border-primary/20 bg-primary/[0.02] p-4 shadow-sm"
    >
      <header className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-bold text-foreground">
            How do you want to author this policy?
          </h3>
          <p className="mt-0.5 text-xs text-secondary">
            Pick a path. All three produce the same kind of policy — choose
            what feels right for the rule you have in mind.
          </p>
        </div>
        <button
          type="button"
          onClick={onCancel}
          aria-label="Close add policy picker"
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
            <span className="mt-1 rounded bg-black/[0.04] px-1.5 py-0.5 text-[10px] font-mono text-secondary/80">
              → {m.backing}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
