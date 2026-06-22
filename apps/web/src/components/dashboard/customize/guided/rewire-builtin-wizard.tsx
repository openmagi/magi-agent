"use client";

/**
 * Rewire-built-in-preset Guided wizard — SeamSpec kind.
 *
 * Produces a structured SeamSpec doc via the existing /seams PUT route
 * (no NL compile in this flow — that's the NL mode's job). Wizard scope
 * is intentionally narrow to the most common rewire: flip wiring on an
 * existing built-in preset. Other SeamSpec capabilities (add a brand-new
 * preset_id, swap controls_refs) stay on the Raw mode for power-users
 * until the byproduct extractor surfaces builtin controls_refs as a
 * pickable list.
 *
 * Steps:
 *   1. Pick a built-in preset
 *   2. Pick the new wiring (opt_in / opt_out)
 *   3. Name (override doc id auto-derived from preset)
 *   4. Review
 */

import React, { useState } from "react";

import {
  putSeamSpec,
  type CustomizeCatalog,
  type SeamSpecDoc,
} from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";

import { RadioCard, WizardChrome } from "./wizard-chrome";


type Wiring = "opt_in" | "opt_out";


interface Draft {
  presetId: string;
  newWiring: Wiring;
  docId: string;
  description: string;
}


const EMPTY: Draft = {
  presetId: "",
  newWiring: "opt_in",
  docId: "",
  description: "",
};


const TOTAL = 4;


export interface RewireBuiltinWizardProps {
  catalog: CustomizeCatalog;
  onActivated: () => void;
  onPickDifferent: () => void;
  onCancel: () => void;
}


export function RewireBuiltinWizard({
  catalog,
  onActivated,
  onPickDifferent,
  onCancel,
}: RewireBuiltinWizardProps): React.ReactElement {
  const agentFetch = useAgentFetch();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Built-in presets that are togglable (not always-on / preview) are the
  // ones a SeamSpec rewire actually affects at runtime.
  const presets = catalog.verification.harnessPresets.filter(
    (p) => p.enforcement === "enforcing",
  );

  return (
    <WizardChrome
      step={step}
      total={TOTAL}
      onPickDifferent={onPickDifferent}
      onCancel={onCancel}
      onBack={() => setStep((s) => Math.max(s - 1, 0))}
      onNext={() => setStep((s) => Math.min(s + 1, TOTAL - 1))}
      onSave={async () => {
        setSaving(true);
        setSaveError(null);
        try {
          await putSeamSpec(agentFetch, buildSpec(draft));
          onActivated();
        } catch (err) {
          setSaveError(err instanceof Error ? err.message : "Save failed");
        } finally {
          setSaving(false);
        }
      }}
      canAdvance={stepIsComplete(step, draft)}
      saving={saving}
      error={saveError}
    >
      {step === 0 ? <PresetStep draft={draft} setDraft={setDraft} presets={presets} /> : null}
      {step === 1 ? <WiringStep draft={draft} setDraft={setDraft} presets={presets} /> : null}
      {step === 2 ? <NameStep draft={draft} setDraft={setDraft} /> : null}
      {step === 3 ? <ReviewStep draft={draft} presets={presets} /> : null}
    </WizardChrome>
  );
}


function PresetStep({
  draft,
  setDraft,
  presets,
}: {
  draft: Draft;
  setDraft: React.Dispatch<React.SetStateAction<Draft>>;
  presets: CustomizeCatalog["verification"]["harnessPresets"];
}): React.ReactElement {
  if (presets.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs text-secondary">
        No togglable built-in presets in this runtime. Rewire is only
        useful when the runtime ships presets that are user-controllable.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Which built-in preset do you want to rewire?</h2>
      <p className="text-xs text-secondary">
        Rewiring overrides how the preset's toggle behaves at runtime.
        It does NOT add a brand-new gate — use the other Guided wizards
        for that.
      </p>
      <div className="space-y-2">
        {presets.map((preset) => (
          <RadioCard
            key={preset.id}
            checked={draft.presetId === preset.id}
            onClick={() =>
              setDraft((d) => ({
                ...d,
                presetId: preset.id,
                docId: `rewire-${preset.id}`,
              }))
            }
            label={preset.title}
            description={preset.description}
            monoLabel={`current wiring: ${preset.optMethod ?? "?"}`}
          />
        ))}
      </div>
    </div>
  );
}


const WIRING_OPTIONS: ReadonlyArray<{ id: Wiring; label: string; description: string }> = [
  {
    id: "opt_in",
    label: "opt-in",
    description: "Toggle ON adds the check; the gate stays OFF by default until a user enables the preset.",
  },
  {
    id: "opt_out",
    label: "opt-out",
    description: "Toggle ON is the default; the user must explicitly disable the preset to remove the check.",
  },
];


function WiringStep({
  draft,
  setDraft,
  presets,
}: {
  draft: Draft;
  setDraft: React.Dispatch<React.SetStateAction<Draft>>;
  presets: CustomizeCatalog["verification"]["harnessPresets"];
}): React.ReactElement {
  const current = presets.find((p) => p.id === draft.presetId);
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">What should the new wiring be?</h2>
      <p className="text-xs text-secondary">
        Pick the wiring this preset should adopt after the rewire. Current
        wiring: <code>{current?.optMethod ?? "?"}</code>.
      </p>
      <div className="space-y-2">
        {WIRING_OPTIONS.map((opt) => (
          <RadioCard
            key={opt.id}
            checked={draft.newWiring === opt.id}
            onClick={() => setDraft((d) => ({ ...d, newWiring: opt.id }))}
            label={opt.label}
            description={opt.description}
          />
        ))}
      </div>
    </div>
  );
}


function NameStep({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Name your override</h2>
      <p className="text-xs text-secondary">
        Auto-derived from the preset id; rename if you want a different
        label in the audit ledger.
      </p>
      <label className="block">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Override doc ID
        </span>
        <input
          type="text"
          value={draft.docId}
          onChange={(e) => setDraft((d) => ({ ...d, docId: e.target.value }))}
          aria-label="Override doc ID"
          className="mt-1 w-full rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm font-mono text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </label>
      <label className="block">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Description (optional)
        </span>
        <input
          type="text"
          value={draft.description}
          onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
          aria-label="Description"
          className="mt-1 w-full rounded-lg border border-black/[0.10] bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </label>
    </div>
  );
}


function ReviewStep({
  draft,
  presets,
}: {
  draft: Draft;
  presets: CustomizeCatalog["verification"]["harnessPresets"];
}): React.ReactElement {
  const preset = presets.find((p) => p.id === draft.presetId);
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Review</h2>
      <p className="text-xs text-secondary">
        Saving merges the override into the live PresetSeam catalog. The
        change applies immediately to new turns.
      </p>
      <div className="rounded-xl border border-black/[0.06] bg-white p-4">
        <p className="text-sm font-semibold text-foreground">What this rewire does</p>
        <p className="mt-1 text-xs leading-relaxed text-foreground">
          The built-in preset <strong>{preset?.title ?? draft.presetId}</strong>{" "}
          will be wired as <code>{draft.newWiring}</code> (was{" "}
          <code>{preset?.optMethod ?? "?"}</code>).
        </p>
        <hr className="my-3 border-black/[0.05]" />
        <dl className="grid grid-cols-[7rem_1fr] gap-y-1.5 text-xs">
          <dt className="text-secondary">Override ID</dt>
          <dd className="font-mono text-foreground">{draft.docId}</dd>
          <dt className="text-secondary">Preset</dt>
          <dd className="font-mono text-foreground">{draft.presetId}</dd>
          <dt className="text-secondary">New wiring</dt>
          <dd>{draft.newWiring}</dd>
          {draft.description ? (
            <>
              <dt className="text-secondary">Note</dt>
              <dd>{draft.description}</dd>
            </>
          ) : null}
        </dl>
      </div>
    </div>
  );
}


function stepIsComplete(step: number, draft: Draft): boolean {
  if (step === 0) return !!draft.presetId;
  if (step === 1) return !!draft.newWiring;
  if (step === 2) return draft.docId.trim().length > 0;
  return true;
}


function buildSpec(draft: Draft): SeamSpecDoc {
  return {
    id: draft.docId,
    spec_version: "0.1",
    actions: [
      {
        op: "modify_seam",
        preset_id: draft.presetId,
        wiring: draft.newWiring,
      },
    ],
  };
}
