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

// WHEN-group (domain) order + labels — the modal groups by *when a gate fires*
// rather than by semantic category (spec §7). Preview presets are pulled into
// their own collapsed section regardless of domain.
const DOMAIN_ORDER = ["always-on", "coding", "research", "delivery"] as const;

const DOMAIN_LABELS: Record<string, string> = {
  "always-on": "Always-on (security)",
  coding: "Coding tasks",
  research: "Research tasks",
  delivery: "Delivery / General",
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

function Pill({ text, tone }: { text: string; tone: "neutral" | "live" | "lock" | "preview" }) {
  const cls = {
    neutral: "bg-black/[0.05] text-secondary",
    live: "bg-emerald-500/10 text-emerald-600",
    lock: "bg-emerald-500/10 text-emerald-600",
    preview: "bg-amber-500/10 text-amber-600",
  }[tone];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${cls}`}>
      {tone === "lock" ? <Lock className="h-3 w-3" /> : null}
      {text}
    </span>
  );
}

// Tier · opt-method · wiring-state badges (spec §7: e.g. "det · opt-out · live").
function Badges({ preset }: { preset: HarnessPresetItem }) {
  if (preset.enforcement === "always-on") {
    return <Pill text="Always on" tone="lock" />;
  }
  if (preset.enforcement === "preview") {
    return <Pill text="Preview" tone="preview" />;
  }
  // enforcing
  return (
    <div className="flex items-center gap-1.5">
      {preset.tier === "deterministic" ? <Pill text="det" tone="neutral" /> : null}
      {preset.optMethod ? <Pill text={preset.optMethod} tone="neutral" /> : null}
      <Pill text="live" tone="live" />
    </div>
  );
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
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-semibold text-foreground">{preset.title}</p>
          <Badges preset={preset} />
        </div>
        {preset.description ? (
          <p className="mt-1 text-[11px] leading-relaxed text-secondary/80">{preset.description}</p>
        ) : null}
      </div>
      {togglable ? (
        <Toggle
          checked={checked}
          disabled={pending}
          onChange={(next) => onToggle(preset.id, next)}
          label={`Toggle preset ${preset.title}`}
        />
      ) : null}
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

  // Preview presets are pulled out into their own collapsed section regardless of
  // domain; everything else groups by WHEN (domain).
  const previewPresets = catalog.harnessPresets.filter((p) => p.enforcement === "preview");
  const byDomain = new Map<string, HarnessPresetItem[]>();
  for (const preset of catalog.harnessPresets) {
    if (preset.enforcement === "preview") continue;
    const list = byDomain.get(preset.domain) ?? [];
    list.push(preset);
    byDomain.set(preset.domain, list);
  }
  const orderedDomains = [
    ...DOMAIN_ORDER.filter((d) => byDomain.has(d)),
    ...[...byDomain.keys()].filter((d) => !DOMAIN_ORDER.includes(d as never)),
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
          {orderedDomains.map((domain) => {
            const presets = byDomain.get(domain) ?? [];
            if (presets.length === 0) return null;
            return (
              <section key={domain}>
                <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
                  {DOMAIN_LABELS[domain] ?? domain}
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

          {/* Preview (not yet wired) — collapsed, non-toggle */}
          {previewPresets.length > 0 ? (
            <details className="rounded-xl border border-black/[0.06] bg-gray-50/60">
              <summary className="cursor-pointer px-4 py-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
                Not yet wired — preview ({previewPresets.length})
              </summary>
              <div className="space-y-2 px-3 pb-3">
                {previewPresets.map((preset) => (
                  <PresetRow
                    key={preset.id}
                    preset={preset}
                    checked={false}
                    pending={false}
                    onToggle={onTogglePreset}
                  />
                ))}
              </div>
            </details>
          ) : null}

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
