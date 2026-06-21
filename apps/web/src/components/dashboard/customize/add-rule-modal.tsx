"use client";

/**
 * AddRuleModal — single entry-point that asks "What do you want to do?"
 * and routes the user to the right backing primitive's existing form.
 *
 * Phase 1 of the unified Rules redesign: kills the "user has to know
 * which tab to author each rule type in" footgun. Phase 2 will replace
 * this picker with a guided wizard that fills the form fields
 * automatically; this PR keeps the existing rich forms exactly as they
 * are (CustomRulesSection / CustomChecksSection / SeamBuilderPanel) so
 * the diff stays scoped to navigation, not authoring logic.
 */

import { Ban, Filter, ShieldOff, Wand2 } from "lucide-react";
import React from "react";


export type AddRuleChoice =
  | "block-answer"
  | "restrict-tool"
  | "filter-result"
  | "rewire-builtin";


export interface AddRuleModalProps {
  open: boolean;
  onClose: () => void;
  onPick: (choice: AddRuleChoice) => void;
}


interface ChoiceCard {
  id: AddRuleChoice;
  label: string;
  description: string;
  icon: React.ReactNode;
  backing: string;
}


const CHOICES: ReadonlyArray<ChoiceCard> = [
  {
    id: "block-answer",
    label: "Block bad answer",
    description:
      "Stop the final answer when evidence is missing, a SHACL shape fails, or an LLM criterion rejects it.",
    icon: <Ban className="h-5 w-5" />,
    backing: "Custom Rule (pre-final)",
  },
  {
    id: "restrict-tool",
    label: "Restrict tool",
    description:
      "Deny or require approval for a specific tool, or block a source domain before the agent calls it.",
    icon: <ShieldOff className="h-5 w-5" />,
    backing: "Custom Rule (before-tool)",
  },
  {
    id: "filter-result",
    label: "Filter tool result",
    description:
      "Strip or block on a tool's output by regex or LLM check before the agent reads it.",
    icon: <Filter className="h-5 w-5" />,
    backing: "Custom Check (after-tool, self-host only)",
  },
  {
    id: "rewire-builtin",
    label: "Rewire a built-in preset",
    description:
      "Flip an existing built-in rule's wiring (opt-in / opt-out), swap which evidence ref it controls, or add a new preset id.",
    icon: <Wand2 className="h-5 w-5" />,
    backing: "SeamSpec (Advanced)",
  },
];


export function AddRuleModal({
  open,
  onClose,
  onPick,
}: AddRuleModalProps): React.ReactElement | null {
  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Add a rule"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-6"
    >
      <div className="w-full max-w-2xl rounded-2xl bg-white shadow-xl">
        <header className="flex items-center justify-between border-b border-black/[0.06] px-5 py-4">
          <div>
            <h2 className="text-base font-bold text-foreground">Add a rule</h2>
            <p className="mt-0.5 text-xs text-secondary">
              Pick what you want the agent to do (or not do). We route you to
              the right authoring form.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg p-1.5 text-secondary hover:bg-black/[0.04] hover:text-foreground"
          >
            ✕
          </button>
        </header>

        <div className="grid grid-cols-1 gap-3 p-5 sm:grid-cols-2">
          {CHOICES.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => onPick(c.id)}
              className="flex flex-col items-start gap-2 rounded-xl border border-black/[0.06] bg-white p-4 text-left transition-colors hover:border-primary hover:bg-primary/[0.04]"
            >
              <span className="rounded-lg bg-primary/10 p-2 text-primary">
                {c.icon}
              </span>
              <span className="text-sm font-semibold text-foreground">
                {c.label}
              </span>
              <span className="text-xs leading-relaxed text-secondary">
                {c.description}
              </span>
              <span className="mt-1 rounded bg-black/[0.04] px-1.5 py-0.5 text-[10px] font-mono text-secondary/80">
                → {c.backing}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
