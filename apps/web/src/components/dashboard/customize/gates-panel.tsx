"use client";

/**
 * Gates inner-tab body — minimal stack of the two existing gate authoring
 * surfaces.
 *
 * Earlier revisions wrapped each in a tall banner with framing copy, but
 * CustomRulesSection and CustomChecksSection already render their own
 * ``Custom Rules`` / ``Custom Checks`` headers and intro paragraphs. The
 * double-banner made the page feel like 80% prose / 20% action and hid
 * the actual ``+ Add custom rule`` button. The framing for which gate
 * fires when lives in the inner-tab hint (``VerificationTabs``) — this
 * component is now intentionally a near-passthrough.
 *
 * Why two backends on one page
 * ----------------------------
 * Custom Rules (pre-final + before-tool) and Custom Checks (after-tool)
 * are different code paths because they were authored by different PRs.
 * From the user's perspective both author a runtime gate; merging them on
 * one page lets the user pick by *what they want to enforce* without
 * needing to know which code path implements it.
 *
 * SeamSpec stays on the Advanced sub-nav and is intentionally not
 * surfaced here — it rewires existing presets rather than adding a gate.
 */

import React from "react";

import type {
  CustomizeCatalog,
  CustomRule,
  ShaclCompileResponse,
} from "@/lib/customize-api";

import { CustomChecksSection } from "./custom-checks-section";
import { PageHint } from "./page-hint";
import { CustomRulesSection } from "./verification-rule-modal";

type ConversationTurn = { role: "user" | "assistant"; content: string };


export interface GatesPanelProps {
  customRuleMenu: CustomizeCatalog["verification"]["customRuleMenu"];
  customRules: CustomRule[];
  customRuleBusy: boolean;
  onAddCustomRule: (rule: CustomRule) => void;
  onToggleCustomRule: (rule: CustomRule, enabled: boolean) => void;
  onDeleteCustomRule: (id: string) => void;
  onCompileShacl: (
    nlText: string,
    sampleRecords?: unknown[],
    priorTurns?: ConversationTurn[],
  ) => Promise<ShaclCompileResponse>;
}


export function GatesPanel({
  customRuleMenu,
  customRules,
  customRuleBusy,
  onAddCustomRule,
  onToggleCustomRule,
  onDeleteCustomRule,
  onCompileShacl,
}: GatesPanelProps): React.ReactElement {
  return (
    <div className="space-y-6">
      <PageHint
        title="Add your own enforcement gates"
        can={[
          { text: <><strong>pre-final</strong> — block the final answer on missing evidence / SHACL / LLM criterion</> },
          { text: <><strong>before-tool</strong> — deny or require approval for a tool by name or source domain</> },
          { text: <><strong>after-tool</strong> — strip or block on a tool result by regex / LLM check</> },
        ]}
        cannot={[
          { text: <>Rewire an existing built-in preset → use <strong>Advanced</strong></> },
          { text: <>Soft preferences the model can ignore → use <strong>Guidance</strong></> },
        ]}
        note={
          <>
            The form below uses the <em>fires at</em> picker to decide
            timing. After-tool checks ship as a separate code path
            (Dashboard pack authoring); self-host only — requires{" "}
            <code>MAGI_DASHBOARD_PACK_AUTHORING_ENABLED</code>.
          </>
        }
      />

      <CustomRulesSection
        menu={customRuleMenu}
        rules={customRules}
        busy={customRuleBusy}
        onAdd={onAddCustomRule}
        onToggle={onToggleCustomRule}
        onDelete={onDeleteCustomRule}
        onCompileShacl={onCompileShacl}
      />

      <CustomChecksSection busy={customRuleBusy} />
    </div>
  );
}
