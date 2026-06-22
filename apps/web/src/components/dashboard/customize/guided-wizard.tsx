"use client";

/**
 * Guided policy wizard — kind router (PR-E3).
 *
 * Asks "what kind of policy?" once, then mounts the right per-kind
 * sub-wizard. All sub-wizards share the chrome + RadioCard primitive
 * from ``guided/wizard-chrome.tsx`` so the look/feel stays consistent.
 *
 * PR-E2 shipped the block-bad-answer flow inline; PR-E3 extracted it
 * into a sub-wizard and added three siblings (restrict-tool /
 * filter-result / rewire-builtin) so every routedKind has a Guided
 * authoring path that matches the toss-style "one decision per step"
 * UX from control-plane.
 */

import { Ban, Filter, ShieldOff, X as XIcon } from "lucide-react";
import React, { useState } from "react";

import type { CustomizeCatalog } from "@/lib/customize-api";
import type { EvidenceTypeEntry } from "@/lib/policy-model";

import { BlockAnswerWizard } from "./guided/block-answer-wizard";
import { FilterResultWizard } from "./guided/filter-result-wizard";
import { RestrictToolWizard } from "./guided/restrict-tool-wizard";


export type GuidedKind =
  | "block-answer"
  | "restrict-tool"
  | "filter-result";


export interface GuidedWizardProps {
  catalog: CustomizeCatalog;
  evidenceTypes: EvidenceTypeEntry[];
  onActivated: () => void;
  onPickDifferent: () => void;
  onCancel: () => void;
}


interface KindCard {
  id: GuidedKind;
  label: string;
  description: string;
  icon: React.ReactNode;
}


const KINDS: ReadonlyArray<KindCard> = [
  {
    id: "restrict-tool",
    label: "Before a tool runs",
    description:
      "Lifecycle: before_tool_use. Deny or require approval for a tool by name, a fetch domain, or a domain allowlist.",
    icon: <ShieldOff className="h-5 w-5" />,
  },
  {
    id: "filter-result",
    label: "After a tool returns",
    description:
      "Lifecycle: after_tool_use. Inspect the tool's output by literal / regex match and block or audit when it fires.",
    icon: <Filter className="h-5 w-5" />,
  },
  {
    id: "block-answer",
    label: "Before the final answer commits",
    description:
      "Lifecycle: pre_final. Stop the answer with an evidence-ref check, a SHACL shape, or an LLM critic.",
    icon: <Ban className="h-5 w-5" />,
  },
];


export function GuidedWizard({
  catalog,
  evidenceTypes,
  onActivated,
  onPickDifferent,
  onCancel,
}: GuidedWizardProps): React.ReactElement {
  const [kind, setKind] = useState<GuidedKind | null>(null);

  if (kind === null) {
    return <KindPicker onCancel={onCancel} onPickDifferent={onPickDifferent} onPick={setKind} />;
  }
  // "← Pick different" inside a sub-wizard goes back to the kind picker;
  // the parent's onPickDifferent (back to the mode picker) is one step up
  // and is reachable by the KindPicker's own ← Pick different.
  const backToKindPicker = () => setKind(null);

  if (kind === "block-answer") {
    return (
      <BlockAnswerWizard
        catalog={catalog}
        evidenceTypes={evidenceTypes}
        onActivated={onActivated}
        onPickDifferent={backToKindPicker}
        onCancel={onCancel}
      />
    );
  }
  if (kind === "restrict-tool") {
    return (
      <RestrictToolWizard
        onActivated={onActivated}
        onPickDifferent={backToKindPicker}
        onCancel={onCancel}
      />
    );
  }
  return (
    <FilterResultWizard
      onActivated={onActivated}
      onPickDifferent={backToKindPicker}
      onCancel={onCancel}
    />
  );
}


function KindPicker({
  onCancel,
  onPickDifferent,
  onPick,
}: {
  onCancel: () => void;
  onPickDifferent: () => void;
  onPick: (kind: GuidedKind) => void;
}): React.ReactElement {
  return (
    <section
      aria-label="Pick a guided policy kind"
      className="rounded-2xl border border-primary/20 bg-primary/[0.02] p-5 shadow-sm"
    >
      <header className="mb-3 flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={onPickDifferent}
          className="rounded-lg px-2 py-1 text-[11px] font-medium text-secondary hover:bg-black/[0.04] hover:text-foreground"
        >
          ← Pick different mode
        </button>
        <div>
          <h3 className="text-sm font-bold text-foreground">
            What kind of policy do you want to author?
          </h3>
          <p className="mt-0.5 text-xs text-secondary">
            Pick one — we route you to the right step-by-step wizard.
          </p>
        </div>
        <button
          type="button"
          onClick={onCancel}
          aria-label="Cancel"
          className="rounded-lg p-1.5 text-secondary hover:bg-black/[0.04] hover:text-foreground"
        >
          <XIcon className="h-4 w-4" />
        </button>
      </header>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {KINDS.map((k) => (
          <button
            key={k.id}
            type="button"
            onClick={() => onPick(k.id)}
            className="flex flex-col items-start gap-2 rounded-xl border border-black/[0.08] bg-white p-4 text-left transition-colors hover:border-primary hover:bg-primary/[0.05]"
          >
            <span className="rounded-lg bg-primary/10 p-2 text-primary">
              {k.icon}
            </span>
            <span className="text-sm font-semibold text-foreground">
              {k.label}
            </span>
            <span className="text-xs leading-relaxed text-secondary">
              {k.description}
            </span>
          </button>
        ))}
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-secondary/80">
        Other lifecycle events the runtime exposes via the HookBus
        (UserPromptSubmit, Stop, SubagentStop, on_compaction, …) do not
        yet have a Guided authoring path — they need to be wired through
        the custom_rules contract first. For now, file-authored
        ~/.magi/settings.json hooks cover those events.
      </p>
    </section>
  );
}
