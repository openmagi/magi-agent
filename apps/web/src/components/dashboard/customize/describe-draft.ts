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
  | "shacl_constraint"
  | "capability_scope";


/** Permission class cap for ``capability_scope`` drafts. Mirrors the
 *  runtime's ``_PERMISSION_CLASSES`` frozenset in
 *  ``magi_agent/customize/capability_scope.py`` (``readonly`` / ``safe_write``
 *  / ``null`` = uncapped). */
export type CapabilityPermissionClass = "readonly" | "safe_write" | null;


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
  /** F4 — capability_scope narrows the spawned-child toolset by removing
   *  named tools. Empty list (default) = no tool denials authored yet. */
  denyTools?: string[];
  /** F4 — capability_scope caps the spawned-child permission class.
   *  ``null`` (default) = no cap. */
  maxPermissionClass?: CapabilityPermissionClass;
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
    // describe-draft is consumed by verification-rule-modal whose
    // llm_criterion form is pre_final-only (after-tool llm_criterion
    // lives under d.kind="after_tool" below, where contentMatch is
    // honored). pre_final has no tool output, so contentMatch is
    // rejected by the backend (_validate_content_match). The F6.5
    // contentMatch preview clause was previously rendered here and
    // would have lied; removed.
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
  if (d.kind === "capability_scope") {
    const denies = (d.denyTools ?? []).map((s) => s.trim()).filter(Boolean);
    const cap = d.maxPermissionClass ?? null;
    if (denies.length === 0 && !cap) return null;
    const parts: string[] = [];
    if (denies.length > 0) {
      const toolList = denies.join(", ");
      parts.push(`cannot use ${toolList}`);
    }
    if (cap) {
      parts.push(`capped at ${cap} permission class`);
    }
    return `Subagents ${parts.join(", ")}.`;
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
