"use client";

/**
 * AddRulePicker — in-page picker that asks "What do you want to do?" and
 * hands a choice back to the parent so it can mount the right authoring
 * form in the SAME scroll position.
 *
 * Earlier shape (an overlay modal) had a footgun: dismissing the modal
 * dropped the user back at the page header with no visible follow-up —
 * the inline authoring form rendered below the rules table, off-screen
 * for any non-trivial customize.json. The redesign drops the modal
 * entirely; the picker now renders in-place where the user clicked
 * "Add rule".
 *
 * ``AddRuleModal`` is retained as a thin re-export so any in-flight
 * callers that imported the old name keep compiling; new code should
 * import ``AddRulePicker``.
 */

import { Ban, Filter, ShieldOff, Wand2, X as XIcon } from "lucide-react";
import React from "react";


export type AddRuleChoice =
  | "block-answer"
  | "restrict-tool"
  | "filter-result"
  | "rewire-builtin";


export interface AddRulePickerProps {
  /** Called when the user picks a choice. Parent should mount the
   *  matching authoring form and clear the picker by setting
   *  ``hidden=true`` (or unmounting the picker). */
  onPick: (choice: AddRuleChoice) => void;
  /** Called when the user dismisses the picker via the X button. */
  onCancel: () => void;
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


export function AddRulePicker({
  onPick,
  onCancel,
}: AddRulePickerProps): React.ReactElement {
  return (
    <section
      aria-label="Pick a rule kind"
      className="rounded-2xl border border-primary/20 bg-primary/[0.02] p-4 shadow-sm"
    >
      <header className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-bold text-foreground">Add a rule</h3>
          <p className="mt-0.5 text-xs text-secondary">
            Pick what you want the agent to do (or not do). We route you to
            the right authoring form below.
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

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {CHOICES.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => onPick(c.id)}
            className="flex flex-col items-start gap-2 rounded-xl border border-black/[0.08] bg-white p-4 text-left transition-colors hover:border-primary hover:bg-primary/[0.05]"
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
    </section>
  );
}


/**
 * Back-compat alias for the previous overlay shape. Retained so legacy
 * imports keep compiling; new callers should import ``AddRulePicker``
 * directly. The wrapper ignores ``open`` and forwards the picker as if
 * it were always-open — the parent now controls visibility through its
 * own state (rendering the picker or not).
 *
 * @deprecated Use ``AddRulePicker`` and let the parent toggle the mount.
 */
export interface AddRuleModalProps {
  open: boolean;
  onClose: () => void;
  onPick: (choice: AddRuleChoice) => void;
}


export function AddRuleModal({
  open,
  onClose,
  onPick,
}: AddRuleModalProps): React.ReactElement | null {
  if (!open) return null;
  return <AddRulePicker onCancel={onClose} onPick={onPick} />;
}
