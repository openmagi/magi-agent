/**
 * Plain-English live-preview helpers for the Custom Rule + SeamSpec draft
 * forms. Pure functions — no React, no fetch — so they can be source-string
 * unit-tested in the standard vitest node environment without mocking the
 * panel imports.
 *
 * Phase-2 of Kevin's UX feedback: he could not predict what the persisted
 * rule actually does from the form's jargon picker labels. These helpers
 * eat the current draft state and emit one human sentence so the user sees
 * the runtime consequence before clicking "Add rule".
 */

import type { SeamSpecDoc } from "@/lib/customize-api";


export type CustomRuleKind =
  | "deterministic_ref"
  | "tool_perm"
  | "llm_criterion"
  | "after_tool"
  | "shacl_constraint";


export interface CustomRuleDraft {
  kind: CustomRuleKind;
  scope: string;
  ref: string;
  refLabel: string;
  matchType: "tool" | "domain" | "domainAllowlist";
  matchValue: string;
  decision: "deny" | "ask";
  criterion: string;
  toolMatch: string;
  contentPattern: string;
  contentIsRegex: boolean;
  contentNegate: boolean;
  shaclMode: "nl" | "raw";
  shaclPreviewOk: boolean;
  rawTtlHasContent: boolean;
}


/**
 * Returns the "This rule will: ..." sentence for the LIVE draft, or
 * ``null`` when the draft is too empty to describe — the caller hides
 * the preview line in that case so misleading text never flashes.
 */
export function describeDraft(d: CustomRuleDraft): string | null {
  const whenClause = d.scope === "always" ? "Every turn" : `On ${d.scope} turns`;

  if (d.kind === "deterministic_ref") {
    if (!d.ref) return null;
    return `${whenClause}, block the final answer unless the runtime has emitted "${d.refLabel || d.ref}" evidence this turn.`;
  }
  if (d.kind === "shacl_constraint") {
    const ready = d.shaclMode === "nl" ? d.shaclPreviewOk : d.rawTtlHasContent;
    if (!ready) return null;
    return `${whenClause}, block the final answer when the SHACL shape does NOT conform on any evidence record.`;
  }
  if (d.kind === "llm_criterion") {
    if (!d.criterion.trim()) return null;
    return `${whenClause}, block the final answer when an LLM critic judges that "${d.criterion.trim()}" is false.`;
  }
  if (d.kind === "tool_perm") {
    if (!d.matchValue.trim()) return null;
    const verb = d.decision === "ask" ? "require human approval for" : "deny";
    let target = "";
    if (d.matchType === "tool") target = `the tool "${d.matchValue.trim()}"`;
    else if (d.matchType === "domain") target = `any fetch to ${d.matchValue.trim()}`;
    else
      target = `any fetch outside [${d.matchValue
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
        .join(", ")}]`;
    return `Before the agent calls a tool, ${verb} ${target}.`;
  }
  if (d.kind === "after_tool") {
    if (!d.toolMatch.trim()) return null;
    const tools = d.toolMatch
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .join(", ");
    const triggers: string[] = [];
    if (d.contentPattern.trim()) {
      const flavor = d.contentIsRegex ? "matches regex" : "contains";
      const verb = d.contentNegate ? `does NOT ${flavor}` : flavor;
      triggers.push(`result ${verb} "${d.contentPattern.trim()}"`);
    }
    if (d.criterion.trim()) {
      triggers.push(`an LLM critic judges "${d.criterion.trim()}" true`);
    }
    if (triggers.length === 0) return null;
    return `After ${tools || "any tool"} returns, override the result when ${triggers.join(" OR ")}.`;
  }
  return null;
}


/**
 * One bullet per ``SeamSpec`` action so the human reviewer can sanity-check
 * the compiler before activating. Phase-2 of the UX rework — the raw JSON
 * dump told power users what changed but not the runtime intent.
 */
export function describeSpecActions(spec: SeamSpecDoc): string[] {
  return spec.actions.map((a) => {
    const wiring = a.wiring ? ` (wiring=${a.wiring})` : "";
    const kind = a.controls_kind ? `, controls_kind=${a.controls_kind}` : "";
    const refs = a.controls_refs
      ? `, refs=[${a.controls_refs.join(", ")}]`
      : "";
    if (a.op === "add_seam") {
      return `Add a brand-new preset "${a.preset_id}"${wiring}${kind}${refs}.`;
    }
    if (a.op === "modify_seam") {
      const overrides: string[] = [];
      if (a.wiring !== undefined) overrides.push(`wiring → ${a.wiring}`);
      if (a.controls_kind !== undefined)
        overrides.push(`controls_kind → ${a.controls_kind}`);
      if (a.controls_refs !== undefined)
        overrides.push(`controls_refs → [${a.controls_refs.join(", ")}]`);
      if (a.runtime_default_on !== undefined)
        overrides.push(
          `runtime_default_on → ${a.runtime_default_on ? "true" : "false"}`,
        );
      const overrideText =
        overrides.length === 0 ? "no field overrides" : overrides.join("; ");
      return `Modify existing preset "${a.preset_id}": ${overrideText}.`;
    }
    return `Unknown op "${a.op}" on preset "${a.preset_id}".`;
  });
}
