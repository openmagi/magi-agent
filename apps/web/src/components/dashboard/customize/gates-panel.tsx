"use client";

/**
 * Gates inner-tab body — unifies the previously-split "Custom Rules" and
 * "Custom Checks" surfaces into one page organized by **when the gate
 * fires** (the only distinction users actually care about):
 *
 *  * **Pre-final** — block the final answer when an evidence rule fails
 *    (deterministic_ref / shacl_constraint / llm_criterion).
 *  * **Before-tool** — deny or require approval for a tool invocation
 *    (tool_perm kind).
 *  * **After-tool** — strip or block on a tool result via pattern / LLM
 *    check (Dashboard pack authoring, self-host).
 *
 * The first two kinds both flow through the existing ``CustomRulesSection``
 * builder (kind picker → firesAt picker → action picker). Kevin's UX
 * complaint was that the previous page presented them as if they were
 * separate features; the section header here re-frames them as one
 * "authoring rules" form that internally handles both timings.
 *
 * The third kind (after-tool) is a different code path (Dashboard pack
 * authoring) so it stays mounted as its own ``CustomChecksSection`` —
 * still on this page, with a header that explains the timing distinction.
 *
 * NOTE: the SeamSpec "Advanced" rule builder lives in its own top-level
 * sub-nav section. SeamSpec MUTATES preset wiring; it does NOT add a new
 * gate. It is intentionally not surfaced here so users do not conflate
 * "add a gate" with "rewire an existing preset".
 */

import React from "react";

import type {
  CustomizeCatalog,
  CustomRule,
  ShaclCompileResponse,
} from "@/lib/customize-api";

import { CustomChecksSection } from "./custom-checks-section";
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
    <div className="space-y-8">
      <section
        aria-labelledby="gates-authoring-header"
        className="space-y-3"
      >
        <header className="rounded-xl border border-black/[0.06] bg-gray-50/40 px-4 py-3">
          <h3
            id="gates-authoring-header"
            className="text-sm font-semibold text-foreground"
          >
            Authoring rules — pre-final &amp; before-tool
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-secondary">
            One builder for two timings. Pick <em>fires at: pre_final</em> to
            block the final answer (deterministic evidence check, SHACL shape,
            or LLM criterion). Pick <em>fires at: before_tool_use</em> to deny
            or require approval for a tool invocation (tool-permission rule).
            The fired-at picker decides which runtime hook receives the rule.
          </p>
        </header>
        <CustomRulesSection
          menu={customRuleMenu}
          rules={customRules}
          busy={customRuleBusy}
          onAdd={onAddCustomRule}
          onToggle={onToggleCustomRule}
          onDelete={onDeleteCustomRule}
          onCompileShacl={onCompileShacl}
        />
      </section>

      <section aria-labelledby="gates-after-tool-header" className="space-y-3">
        <header className="rounded-xl border border-black/[0.06] bg-gray-50/40 px-4 py-3">
          <h3
            id="gates-after-tool-header"
            className="text-sm font-semibold text-foreground"
          >
            After-tool result checks
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-secondary">
            Separate code path (Dashboard pack authoring) — fires <strong>after</strong>{" "}
            a tool returns. Define a pattern or LLM check on the tool result;
            <em> block</em> emits a deny-on-present record that stops the
            final answer, while <em>audit</em> only records the match. Self-
            host only — requires{" "}
            <code>MAGI_DASHBOARD_PACK_AUTHORING_ENABLED</code>.
          </p>
        </header>
        <CustomChecksSection busy={customRuleBusy} />
      </section>

      <p className="text-[11px] leading-relaxed text-secondary/80">
        Looking for the SeamSpec rule builder? That mutates the wiring of an
        existing built-in preset (opt-in / opt-out, which evidence ref a
        preset controls). It lives in the <strong>Advanced</strong> sub-nav
        — it does not add a new gate, so it is intentionally not surfaced on
        this page.
      </p>
    </div>
  );
}
