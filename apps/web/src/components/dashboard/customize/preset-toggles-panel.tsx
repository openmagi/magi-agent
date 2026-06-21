"use client";

/**
 * Presets inner-tab body — the 36 built-in PresetSeam gate toggles, grouped
 * by WHEN-domain (always-on / coding / research / delivery) with each group
 * collapsible via a native ``<details>`` disclosure. The "preview" (not-yet-
 * wired) presets sit in their own collapsed group at the bottom.
 *
 * UX polish (Kevin's 2026-06-20 review): a flat list of 36 rows is hard to
 * scan. Group headers expose an ``enabled/total`` count so the user can see
 * at a glance whether a domain has any opt-outs without expanding it, and
 * an Expand-/Collapse-all bar lets them flip every group at once.
 *
 * Extracted from ``verification-rule-modal.tsx`` so the new inner-tab page
 * can mount preset toggles in isolation. The row + badge helpers
 * (``PresetRow``) and the domain ordering constants are re-used as exports
 * from that module so the modal renderer and the inner-tab page stay in
 * sync.
 */

import { ChevronRight } from "lucide-react";
import React, { useMemo, useState } from "react";

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


const PREVIEW_GROUP_KEY = "__preview__";


export function PresetTogglesPanel({
  presets,
  presetOverrides,
  pendingPresets,
  onTogglePreset,
}: PresetTogglesPanelProps): React.ReactElement {
  const previewPresets = presets.filter((p) => p.enforcement === "preview");
  const byDomain = useMemo(() => {
    const map = new Map<string, HarnessPresetItem[]>();
    for (const preset of presets) {
      if (preset.enforcement === "preview") continue;
      const list = map.get(preset.domain) ?? [];
      list.push(preset);
      map.set(preset.domain, list);
    }
    return map;
  }, [presets]);
  const orderedDomains = [
    ...DOMAIN_ORDER.filter((d) => byDomain.has(d)),
    ...[...byDomain.keys()].filter((d) => !DOMAIN_ORDER.includes(d as never)),
  ];

  // Track which groups the user has collapsed. Default = all open so the
  // first visit shows the full surface; the user can collapse what they
  // do not need. Preview group always defaults to collapsed (it is the
  // "no live effect" bucket).
  const [collapsed, setCollapsed] = useState<Set<string>>(
    () => new Set([PREVIEW_GROUP_KEY]),
  );
  const toggleGroup = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const setAllCollapsed = (value: boolean) => {
    if (value) {
      const all = new Set<string>(orderedDomains);
      all.add(PREVIEW_GROUP_KEY);
      setCollapsed(all);
    } else {
      setCollapsed(new Set());
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs leading-relaxed text-secondary">
          Built-in PresetSeam gates ship default-on (security/quality) or
          default-off (capability). Toggling a row flips the runtime decision
          — opt-out for default-on gates, opt-in for default-off ones. The
          catalog source of truth is{" "}
          <code>magi_agent/customize/preset_map.py</code>.
        </p>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => setAllCollapsed(false)}
            className="rounded-md px-2 py-1 text-[11px] font-medium text-secondary hover:bg-black/[0.04] hover:text-foreground"
          >
            Expand all
          </button>
          <button
            type="button"
            onClick={() => setAllCollapsed(true)}
            className="rounded-md px-2 py-1 text-[11px] font-medium text-secondary hover:bg-black/[0.04] hover:text-foreground"
          >
            Collapse all
          </button>
        </div>
      </div>

      {orderedDomains.map((domain) => {
        const list = byDomain.get(domain) ?? [];
        if (list.length === 0) return null;
        const enabled = list.filter(
          (p) => presetOverrides[p.id] ?? p.defaultEnabled,
        ).length;
        const open = !collapsed.has(domain);
        return (
          <CollapsibleGroup
            key={domain}
            title={DOMAIN_LABELS[domain] ?? domain}
            badge={`${enabled}/${list.length}`}
            open={open}
            onToggle={() => toggleGroup(domain)}
          >
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
          </CollapsibleGroup>
        );
      })}

      {previewPresets.length > 0 ? (
        <CollapsibleGroup
          title="Not yet wired — preview"
          badge={`${previewPresets.length}`}
          tone="muted"
          open={!collapsed.has(PREVIEW_GROUP_KEY)}
          onToggle={() => toggleGroup(PREVIEW_GROUP_KEY)}
        >
          <div className="space-y-2">
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
        </CollapsibleGroup>
      ) : null}
    </div>
  );
}


function CollapsibleGroup({
  title,
  badge,
  open,
  onToggle,
  tone = "default",
  children,
}: {
  title: string;
  badge: string;
  open: boolean;
  onToggle: () => void;
  tone?: "default" | "muted";
  children: React.ReactNode;
}): React.ReactElement {
  const wrapCls =
    tone === "muted"
      ? "rounded-xl border border-black/[0.06] bg-gray-50/60"
      : "rounded-xl border border-black/[0.06] bg-white";
  return (
    <section className={wrapCls}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-4 py-3 text-left"
      >
        <ChevronRight
          aria-hidden="true"
          className={`h-4 w-4 shrink-0 text-secondary transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
        <span className="flex-1 truncate text-sm font-semibold text-foreground">
          {title}
        </span>
        <span className="shrink-0 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
          {badge}
        </span>
      </button>
      {open ? <div className="border-t border-black/[0.04] px-3 py-3">{children}</div> : null}
    </section>
  );
}
