"use client";

import { useEffect, useState } from "react";
import { Lock } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import type { CustomizeCatalog, HarnessPresetItem } from "@/lib/customize-api";

interface VerificationRuleModalProps {
  open: boolean;
  onClose: () => void;
  catalog: CustomizeCatalog["verification"];
  /** Explicit per-preset overrides; effective state = presetOverrides[id] ?? preset.defaultEnabled. */
  presetOverrides: Record<string, boolean>;
  /** Preset ids with an in-flight PATCH. */
  pendingPresets: Set<string>;
  onTogglePreset: (id: string, enabled: boolean) => void;
  /** USER-RULES.md body + save handler. */
  userRules: string;
  rulesSaving: boolean;
  onSaveRules: (text: string) => void;
  error: string | null;
}

// Category display order + labels (matches harness/presets.py PresetCategory).
const CATEGORY_ORDER = [
  "answer",
  "fact",
  "coding",
  "task",
  "output",
  "research",
  "memory",
  "security",
] as const;

const CATEGORY_LABELS: Record<string, string> = {
  answer: "Answer Quality",
  fact: "Factual Grounding",
  coding: "Coding",
  task: "Task & Goals",
  output: "Output & Delivery",
  research: "Research",
  memory: "Memory",
  security: "Security",
};

function Toggle({
  checked,
  disabled,
  onChange,
  label,
}: {
  checked: boolean;
  disabled: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40 ${
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

function EnforcementBadge({ enforcement }: { enforcement: HarnessPresetItem["enforcement"] }) {
  if (enforcement === "always-on") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-[11px] font-medium text-emerald-600">
        <Lock className="h-3 w-3" />
        Always on
      </span>
    );
  }
  if (enforcement === "preview") {
    return (
      <span className="inline-flex items-center rounded-full bg-amber-500/10 px-2.5 py-1 text-[11px] font-medium text-amber-600">
        Preview
      </span>
    );
  }
  return null; // enforcing → the live toggle is the affordance
}

function PresetRow({
  preset,
  checked,
  pending,
  onToggle,
}: {
  preset: HarnessPresetItem;
  checked: boolean;
  pending: boolean;
  onToggle: (id: string, enabled: boolean) => void;
}) {
  const togglable = preset.enforcement === "enforcing";
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-black/[0.06] bg-white px-4 py-3">
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-foreground">{preset.title}</p>
        {!togglable ? (
          <p className="mt-0.5 text-[11px] leading-relaxed text-secondary/80">
            {preset.enforcement === "always-on"
              ? "Enforced by the runtime — not configurable here."
              : "Surfaced for parity; no runtime gate yet."}
          </p>
        ) : null}
      </div>
      <div className="flex items-center gap-3">
        {togglable ? null : <EnforcementBadge enforcement={preset.enforcement} />}
        {togglable ? (
          <Toggle
            checked={checked}
            disabled={pending}
            onChange={(next) => onToggle(preset.id, next)}
            label={`Toggle preset ${preset.title}`}
          />
        ) : null}
      </div>
    </div>
  );
}

export function VerificationRuleModal({
  open,
  onClose,
  catalog,
  presetOverrides,
  pendingPresets,
  onTogglePreset,
  userRules,
  rulesSaving,
  onSaveRules,
  error,
}: VerificationRuleModalProps): React.ReactElement | null {
  const [rulesDraft, setRulesDraft] = useState(userRules);
  // Re-seed the draft whenever the modal (re)opens with fresh backend state.
  useEffect(() => {
    if (open) setRulesDraft(userRules);
  }, [open, userRules]);

  if (!open) return null;

  const byCategory = new Map<string, HarnessPresetItem[]>();
  for (const preset of catalog.harnessPresets) {
    const list = byCategory.get(preset.category) ?? [];
    list.push(preset);
    byCategory.set(preset.category, list);
  }
  const orderedCategories = [
    ...CATEGORY_ORDER.filter((c) => byCategory.has(c)),
    ...[...byCategory.keys()].filter((c) => !CATEGORY_ORDER.includes(c as never)),
  ];

  const rulesDirty = rulesDraft !== userRules;

  return (
    <Modal open={open} onClose={onClose}>
      <div className="p-6">
        <div className="mb-1 flex items-start justify-between">
          <h2 className="text-lg font-semibold text-foreground">Verification Rules</h2>
          <button
            type="button"
            onClick={onClose}
            className="-mr-1 -mt-1 p-1 text-secondary transition-colors hover:text-foreground"
            aria-label="Close"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <p className="mb-5 text-xs leading-relaxed text-secondary">
          Toggle the verification gates that constrain your agent&apos;s output. Changes are saved
          immediately. Presets marked <span className="font-medium text-amber-600">Preview</span> are not
          yet wired to a runtime gate; <span className="font-medium text-emerald-600">Always on</span>{" "}
          gates are enforced by the runtime and can&apos;t be turned off here.
        </p>

        {error ? (
          <div className="mb-4 rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-xs text-red-600">
            {error}
          </div>
        ) : null}

        <div className="space-y-6">
          {orderedCategories.map((category) => {
            const presets = byCategory.get(category) ?? [];
            if (presets.length === 0) return null;
            return (
              <section key={category}>
                <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
                  {CATEGORY_LABELS[category] ?? category}
                </h3>
                <div className="space-y-2">
                  {presets.map((preset) => (
                    <PresetRow
                      key={preset.id}
                      preset={preset}
                      checked={presetOverrides[preset.id] ?? preset.defaultEnabled}
                      pending={pendingPresets.has(preset.id)}
                      onToggle={onTogglePreset}
                    />
                  ))}
                </div>
              </section>
            );
          })}

          {/* Custom rules (USER-RULES.md) */}
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
              Custom Rules
            </h3>
            <p className="mb-2 text-xs leading-relaxed text-secondary">
              Free-text instructions injected into your agent&apos;s system prompt every turn.
            </p>
            <textarea
              value={rulesDraft}
              onChange={(e) => setRulesDraft(e.target.value)}
              rows={5}
              placeholder="e.g. Always cite sources. Never delete files without confirming."
              className="w-full resize-y rounded-xl border border-black/[0.10] bg-white px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
            />
            <div className="mt-2 flex justify-end">
              <button
                type="button"
                disabled={!rulesDirty || rulesSaving}
                onClick={() => onSaveRules(rulesDraft)}
                className="inline-flex min-h-[36px] items-center rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {rulesSaving ? "Saving…" : rulesDirty ? "Save rules" : "Saved"}
              </button>
            </div>
          </section>
        </div>
      </div>
    </Modal>
  );
}
