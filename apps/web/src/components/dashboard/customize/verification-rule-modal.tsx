"use client";

import { Lock } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import type { CustomizeCatalog, CustomizeOverrides } from "@/lib/customize-api";

interface VerificationRuleModalProps {
  open: boolean;
  onClose: () => void;
  catalog: CustomizeCatalog["verification"];
  overrides: CustomizeOverrides["verification"];
  onToggleRecipe: (id: string, enabled: boolean) => void;
  onTogglePreset: (id: string, enabled: boolean) => void;
  onToggleHook: (name: string, enabled: boolean) => void;
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45 focus-visible:ring-offset-2 ${
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

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
      {children}
    </h3>
  );
}

function Row({
  title,
  description,
  trailing,
}: {
  title: string;
  description: string;
  trailing: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-xl border border-black/[0.06] bg-white px-4 py-3">
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-foreground">{title}</p>
        {description ? (
          <p className="mt-1 text-xs leading-relaxed text-secondary">{description}</p>
        ) : null}
      </div>
      {trailing}
    </div>
  );
}

export function VerificationRuleModal({
  open,
  onClose,
  catalog,
  overrides,
  onToggleRecipe,
  onTogglePreset,
  onToggleHook,
}: VerificationRuleModalProps): React.ReactElement | null {
  if (!open) return null;

  const securityHooks = catalog.hooks.filter((hook) => hook.alwaysOn);
  const generalHooks = catalog.hooks.filter((hook) => !hook.alwaysOn);

  return (
    <Modal open={open} onClose={onClose}>
      <div className="p-6">
        {/* Header */}
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
        <p className="mb-6 text-xs text-secondary">
          Choose which recipes, harness presets, and hooks enforce your agent&apos;s output. Changes
          apply to this local session only.
        </p>

        <div className="space-y-6">
          {/* Recipes */}
          <section>
            <SectionTitle>Recipes</SectionTitle>
            {catalog.recipes.length === 0 ? (
              <div className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs text-secondary">
                No recipes available.
              </div>
            ) : (
              <div className="space-y-2">
                {catalog.recipes.map((recipe) => {
                  const enabled = overrides.recipes.includes(recipe.id) || recipe.enabled;
                  return (
                    <Row
                      key={recipe.id}
                      title={recipe.title}
                      description={recipe.description}
                      trailing={
                        <Toggle
                          checked={enabled}
                          onChange={(next) => onToggleRecipe(recipe.id, next)}
                          label={`Toggle recipe ${recipe.title}`}
                        />
                      }
                    />
                  );
                })}
              </div>
            )}
          </section>

          {/* Harness Presets */}
          <section>
            <SectionTitle>Harness Presets</SectionTitle>
            {catalog.harnessPresets.length === 0 ? (
              <div className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs text-secondary">
                No harness presets available.
              </div>
            ) : (
              <div className="space-y-2">
                {catalog.harnessPresets.map((preset) => {
                  const enabled = overrides.harness_presets.includes(preset.id) || preset.enabled;
                  return (
                    <Row
                      key={preset.id}
                      title={preset.title}
                      description={preset.description}
                      trailing={
                        <Toggle
                          checked={enabled}
                          onChange={(next) => onTogglePreset(preset.id, next)}
                          label={`Toggle preset ${preset.title}`}
                        />
                      }
                    />
                  );
                })}
              </div>
            )}
          </section>

          {/* Security (Always On) hooks */}
          {securityHooks.length > 0 ? (
            <section>
              <SectionTitle>Security (Always On)</SectionTitle>
              <div className="space-y-2">
                {securityHooks.map((hook) => (
                  <Row
                    key={hook.name}
                    title={hook.title}
                    description={hook.point}
                    trailing={
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-[11px] font-medium text-emerald-600">
                        <Lock className="h-3 w-3" />
                        Locked
                      </span>
                    }
                  />
                ))}
              </div>
            </section>
          ) : null}

          {/* General hooks */}
          {generalHooks.length > 0 ? (
            <section>
              <SectionTitle>General</SectionTitle>
              <div className="space-y-2">
                {generalHooks.map((hook) => {
                  const enabled = overrides.hooks[hook.name] ?? hook.enabled;
                  return (
                    <Row
                      key={hook.name}
                      title={hook.title}
                      description={hook.point}
                      trailing={
                        <Toggle
                          checked={enabled}
                          onChange={(next) => onToggleHook(hook.name, next)}
                          label={`Toggle hook ${hook.title}`}
                        />
                      }
                    />
                  );
                })}
              </div>
            </section>
          ) : null}

          {/* Add custom rule (coming soon) */}
          <section>
            <button
              type="button"
              disabled
              className="w-full cursor-not-allowed rounded-xl border border-dashed border-black/[0.12] bg-gray-50/60 px-4 py-3 text-sm font-medium text-secondary/70"
            >
              Add custom rule (coming soon)
            </button>
          </section>
        </div>
      </div>
    </Modal>
  );
}
