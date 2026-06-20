"use client";

/**
 * Presets inner-tab body — the 36 built-in PresetSeam gate toggles, grouped
 * by WHEN-domain (always-on / coding / research / delivery) with the
 * "preview" (not-yet-wired) presets collapsed under a details disclosure.
 *
 * Extracted from ``verification-rule-modal.tsx`` so the new inner-tab page
 * can mount preset toggles in isolation. The row + badge helpers
 * (``PresetRow``) and the domain ordering constants are re-used as exports
 * from that module so the modal renderer and the inner-tab page stay in
 * sync.
 */

import React from "react";

import type { CustomizeCatalog, HarnessPresetItem } from "@/lib/customize-api";

import {
  DOMAIN_LABELS,
  DOMAIN_ORDER,
  PresetRow,
} from "./verification-rule-modal";


export interface PresetTogglesPanelProps {
  presets: CustomizeCatalog["verification"]["harnessPresets"];
  presetOverrides: Record<string, boolean>;
  pendingPresets: Set<string>;
  onTogglePreset: (presetId: string, next: boolean) => void;
}


export function PresetTogglesPanel({
  presets,
  presetOverrides,
  pendingPresets,
  onTogglePreset,
}: PresetTogglesPanelProps): React.ReactElement {
  const previewPresets = presets.filter((p) => p.enforcement === "preview");
  const byDomain = new Map<string, HarnessPresetItem[]>();
  for (const preset of presets) {
    if (preset.enforcement === "preview") continue;
    const list = byDomain.get(preset.domain) ?? [];
    list.push(preset);
    byDomain.set(preset.domain, list);
  }
  const orderedDomains = [
    ...DOMAIN_ORDER.filter((d) => byDomain.has(d)),
    ...[...byDomain.keys()].filter((d) => !DOMAIN_ORDER.includes(d as never)),
  ];

  return (
    <div className="space-y-6">
      <p className="text-xs leading-relaxed text-secondary">
        Built-in PresetSeam gates ship default-on (security/quality) or
        default-off (capability). Toggling a row flips the runtime decision
        — opt-out for default-on gates, opt-in for default-off ones. The
        catalog source of truth is{" "}
        <code>magi_agent/customize/preset_map.py</code>.
      </p>
      {orderedDomains.map((domain) => {
        const list = byDomain.get(domain) ?? [];
        if (list.length === 0) return null;
        return (
          <section key={domain}>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
              {DOMAIN_LABELS[domain] ?? domain}
            </h3>
            <div className="space-y-2">
              {list.map((preset) => (
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
    </div>
  );
}
