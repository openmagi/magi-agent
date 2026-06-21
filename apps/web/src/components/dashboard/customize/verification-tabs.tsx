"use client";

/**
 * Verification inner-tab switcher (UX restructure, B option).
 *
 * The Verification sub-nav page used to render every Verification primitive
 * as a single long scrolling pile: preset toggles, the SHACL/Custom-Rules
 * builder, Custom Checks, and the Freeform Guidance textarea. That made the
 * page feel like an unsorted dump — users could not tell whether they
 * should be toggling a preset, authoring a rule, or typing soft prose.
 *
 * This inner-tab split organizes the page by what the user is trying to
 * DO, with three honest labels and per-tab guidance copy:
 *
 *  * **Presets**  — toggle on/off the built-in PresetSeam gates.
 *  * **Gates**    — author your own custom rules (deterministic, LLM, tool-
 *                   permission, after-tool regex). Hard enforcement.
 *  * **Guidance** — Freeform text injected into the system prompt every
 *                   turn. Soft (the model can ignore it).
 *
 * The deeper "Advanced" SeamSpec rule builder (which *mutates* preset
 * wiring) stays in its own top-level sub-nav section — it is a different
 * axis of control, not another sibling rule type.
 *
 * No backend changes. The three inner tabs reuse the same headless panels
 * that the legacy modal wraps; mounting them via this switcher just hides
 * sections the user is not currently looking at.
 */

import React, { useEffect, useState } from "react";

import type {
  CustomizeCatalog,
  CustomRule,
  ShaclCompileResponse,
} from "@/lib/customize-api";

type ConversationTurn = { role: "user" | "assistant"; content: string };

import { GuidancePanel } from "./guidance-panel";
import { GatesPanel } from "./gates-panel";
import { PresetTogglesPanel } from "./preset-toggles-panel";


export type VerificationTab = "presets" | "gates" | "guidance";


export interface VerificationTabsProps {
  catalog: CustomizeCatalog["verification"];
  presetOverrides: Record<string, boolean>;
  pendingPresets: Set<string>;
  onTogglePreset: (presetId: string, next: boolean) => void;
  customRules: CustomRule[];
  onAddCustomRule: (rule: CustomRule) => void;
  onToggleCustomRule: (rule: CustomRule, enabled: boolean) => void;
  onDeleteCustomRule: (id: string) => void;
  customRuleBusy: boolean;
  userRules: string;
  rulesSaving: boolean;
  onSaveRules: (text: string) => void;
  onCompileShacl: (
    nlText: string,
    sampleRecords?: unknown[],
    priorTurns?: ConversationTurn[],
  ) => Promise<ShaclCompileResponse>;
  error?: string | null;
  /** Optional initial active inner-tab. Defaults to "presets". */
  initialTab?: VerificationTab;
}


const TABS: ReadonlyArray<{ id: VerificationTab; label: string; hint: string }> = [
  {
    id: "presets",
    label: "Presets",
    hint: "Toggle the built-in gates that ship with the runtime.",
  },
  {
    id: "gates",
    label: "Gates",
    hint:
      "Author your own enforcement rules. The fires-at picker on each rule decides timing: pre-final (block the answer) / before-tool (deny a tool) / after-tool (strip a result).",
  },
  {
    id: "guidance",
    label: "Guidance",
    hint:
      "Soft instructions injected into the system prompt — the model is asked to follow them but is not forced to.",
  },
];


export function VerificationTabs(props: VerificationTabsProps): React.ReactElement {
  const { initialTab = "presets" } = props;
  const [tab, setTab] = useState<VerificationTab>(initialTab);
  useEffect(() => setTab(initialTab), [initialTab]);

  const active = TABS.find((t) => t.id === tab) ?? TABS[0];

  return (
    <div className="space-y-5">
      <nav
        aria-label="Verification primitives"
        className="flex gap-1 rounded-xl border border-black/[0.06] bg-gray-50/60 p-1"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            aria-current={t.id === tab ? "page" : undefined}
            className={`flex-1 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${
              t.id === tab
                ? "bg-white text-foreground shadow-sm"
                : "text-secondary hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <p className="text-xs leading-relaxed text-secondary">{active.hint}</p>

      {props.error ? (
        <div className="rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-xs text-red-600">
          {props.error}
        </div>
      ) : null}

      {tab === "presets" ? (
        <PresetTogglesPanel
          presets={props.catalog.harnessPresets}
          presetOverrides={props.presetOverrides}
          pendingPresets={props.pendingPresets}
          onTogglePreset={props.onTogglePreset}
        />
      ) : null}

      {tab === "gates" ? (
        <GatesPanel
          customRuleMenu={props.catalog.customRuleMenu}
          customRules={props.customRules}
          customRuleBusy={props.customRuleBusy}
          onAddCustomRule={props.onAddCustomRule}
          onToggleCustomRule={props.onToggleCustomRule}
          onDeleteCustomRule={props.onDeleteCustomRule}
          onCompileShacl={props.onCompileShacl}
        />
      ) : null}

      {tab === "guidance" ? (
        <GuidancePanel
          userRules={props.userRules}
          rulesSaving={props.rulesSaving}
          onSaveRules={props.onSaveRules}
        />
      ) : null}
    </div>
  );
}
