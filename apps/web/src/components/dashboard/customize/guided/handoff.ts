/**
 * F-HANDOFF — wizard → NL bidirectional handoff helpers.
 *
 * Serializes the AuthorWizard's in-progress :class:`Draft` plus the
 * currently-open step into a friendly first-person English primer that the
 * NL surface (NlRuleCompose) seeds its textarea with. The compiler then
 * picks up from where the wizard left off so the operator never has to
 * re-type the context they already entered via the picker.
 *
 * Honest-degrade: empty/unset fields are OMITTED from the primer (never
 * rendered as "lifecycle: (empty)"). The primer is capped at
 * ``PRIMER_MAX_CHARS`` so it never blows out the compile request.
 *
 * Pure functions, no React, no fetch — source-string + behavioural tested
 * via :file:`handoff.local.test.ts`.
 */


// ---------------------------------------------------------------------------
// Domain mirror (kept narrow on purpose — only the slice the primer reads).
//
// The serializer is intentionally permissive about the Draft shape: it
// accepts every field author-wizard.tsx's :class:`Draft` declares as
// optional/string-ish and skips any that are empty. That keeps this module
// from coupling to the wizard's full Draft union (which is exported only
// as a local type to keep author-wizard.tsx self-contained).
// ---------------------------------------------------------------------------

export interface HandoffDraft {
  lifecycle: string;
  scope: string;
  toolTarget: string;
  toolName: string;
  conditionKind: string;
  archetype: string;
  // payload fields — each is checked for empty-string before being added
  // to the primer. The serializer does not validate them (the wizard's
  // own stepIsComplete is the authority on validity); it only reports
  // what the user has filled in so far.
  domain?: string;
  domainAllowlist?: string;
  pathPrefix?: string;
  pathAllowlist?: string;
  evidenceRef?: string;
  shapeTtl?: string;
  criterion?: string;
  regexPattern?: string;
  regexIsRegex?: boolean;
  llmToolMatch?: string;
  llmContentMatchEnabled?: boolean;
  llmContentMatchPattern?: string;
  fcEvidenceType?: string;
  fcField?: string;
  fcOperator?: string;
  fcValue?: string;
  fcCrossTargetType?: string;
  fcCrossTargetField?: string;
  piTargetArgKey?: string;
  piValue?: string;
  piConditionPattern?: string;
  orPattern?: string;
  orReplacement?: string;
  orScope?: string;
  orIsRegex?: boolean;
  // PR-F-EXEC3 — shell_command / shell_check draft slice. Same shape as
  // the wizard's :class:`Draft` exposes (source + inline body OR file path
  // + timeout + env-var allowlist + shell interpreter). The serializer
  // surfaces each non-empty field as a "key: value" entry in the primer so
  // the NL compose surface picks up where the wizard left off without the
  // operator re-typing the script identity.
  shSource?: "inline" | "file";
  shInline?: string;
  shPath?: string;
  shTimeoutSeconds?: number;
  shEnvVars?: string;
  shShell?: "bash" | "sh";
  ruleId?: string;
  description?: string;
}


export type HandoffStepKey =
  | "trigger"
  | "condition"
  | "specifics"
  | "action"
  | "name"
  | "review";


// PR-F-HANDOFF — soft cap on the primer length so a long SHACL paste or
// regex blob doesn't blow out the compile request. The compiler reads it
// as a normal user message, so a generous limit is fine; 1000 chars is
// enough room for ~10 axis sentences + a short SHACL snippet.
export const PRIMER_MAX_CHARS = 1000;


// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------


/**
 * Build the first-person primer message handed to NlRuleCompose when the
 * operator clicks "Continue in NL" from the wizard.
 *
 * Shape: opening sentence (lifecycle/condition/archetype/tool), then a
 * "I had filled in:" enumeration of the populated picker fields, then a
 * "stuck at <step>" pointer, then a closing "Please help me finish" ask.
 * Each clause is OMITTED when the corresponding fields are empty so the
 * primer reads naturally even from very-early-stage drafts.
 */
export function serializeDraftToPrimer(
  draft: HandoffDraft,
  currentStep: HandoffStepKey,
): string {
  const opening = buildOpeningSentence(draft);
  const filled = buildFilledClause(draft);
  const stuck = buildStuckClause(currentStep, draft);
  const closing =
    "Please help me finish authoring this policy from where I left off.";

  const parts = [opening, filled, stuck, closing].filter(
    (p): p is string => p !== null && p.length > 0,
  );
  const joined = parts.join(" ");
  if (joined.length <= PRIMER_MAX_CHARS) return joined;
  // Truncate at the soft cap with a marker so the compiler does not silently
  // see a malformed sentence. Honest-degrade — the operator can re-edit
  // the textarea after the primer is dropped in.
  return joined.slice(0, PRIMER_MAX_CHARS - 3) + "...";
}


// ---------------------------------------------------------------------------
// Clause builders
// ---------------------------------------------------------------------------


function buildOpeningSentence(draft: HandoffDraft): string {
  const segments: string[] = [];
  if (draft.lifecycle) {
    segments.push(`at the "${draft.lifecycle}" lifecycle`);
  }
  if (draft.scope && draft.scope !== "always") {
    segments.push(`scoped to ${draft.scope} turns`);
  }
  if (draft.conditionKind && draft.conditionKind !== "none") {
    segments.push(`with the "${draft.conditionKind}" condition`);
  }
  if (draft.archetype) {
    segments.push(`taking the "${draft.archetype}" action`);
  }

  const toolPhrase = describeToolTarget(draft);
  if (toolPhrase) segments.push(toolPhrase);

  if (segments.length === 0) {
    return "I started authoring a policy in the guided wizard.";
  }
  return `I started authoring a policy ${segments.join(", ")}.`;
}


function describeToolTarget(draft: HandoffDraft): string | null {
  if (draft.toolTarget === "specific") {
    const name = draft.toolName?.trim();
    if (name) return `for the tool "${name}"`;
    return "for a specific tool (no name picked yet)";
  }
  if (draft.toolTarget === "any") {
    // "any tool" is only meaningful for tool-bearing lifecycles. We surface
    // it explicitly so the primer is unambiguous about scope.
    if (
      draft.lifecycle === "before_tool_use"
      || draft.lifecycle === "after_tool_use"
    ) {
      return "matching any tool";
    }
  }
  return null;
}


/**
 * Render the populated payload-field bag as a comma-separated "key: value"
 * enumeration. Empty fields and non-string booleans/flags that don't carry
 * standalone meaning are skipped so the primer stays readable.
 */
function buildFilledClause(draft: HandoffDraft): string | null {
  const entries: string[] = [];

  const pushIf = (label: string, value: string | undefined): void => {
    if (!value) return;
    const trimmed = value.trim();
    if (!trimmed) return;
    // For long values (SHACL TTL, multi-line regex) clip to a single-line
    // excerpt so the primer stays under PRIMER_MAX_CHARS even with a beefy
    // textarea paste. The compiler can ask for the full text later.
    const clipped =
      trimmed.length > 120 ? trimmed.slice(0, 117) + "..." : trimmed;
    entries.push(`${label}: ${clipped}`);
  };

  pushIf("domain", draft.domain);
  pushIf("domain allowlist", draft.domainAllowlist);
  pushIf("path prefix", draft.pathPrefix);
  pushIf("path allowlist", draft.pathAllowlist);
  pushIf("evidence ref", draft.evidenceRef);
  pushIf("SHACL shape", draft.shapeTtl);
  pushIf("LLM criterion", draft.criterion);
  pushIf("regex pattern", draft.regexPattern);
  if (draft.regexIsRegex) entries.push("regex flag: on");
  pushIf("LLM tool match", draft.llmToolMatch);
  if (draft.llmContentMatchEnabled) {
    pushIf("LLM content pre-filter", draft.llmContentMatchPattern);
  }
  pushIf("field-constraint evidence type", draft.fcEvidenceType);
  pushIf("field-constraint field", draft.fcField);
  pushIf("field-constraint operator", draft.fcOperator);
  pushIf("field-constraint value", draft.fcValue);
  pushIf("cross-record target type", draft.fcCrossTargetType);
  pushIf("cross-record target field", draft.fcCrossTargetField);
  pushIf("prompt-injection arg key", draft.piTargetArgKey);
  pushIf("prompt-injection value", draft.piValue);
  pushIf("prompt-injection pre-filter", draft.piConditionPattern);
  pushIf("output-rewrite pattern", draft.orPattern);
  pushIf("output-rewrite replacement", draft.orReplacement);
  if (draft.orScope && draft.orScope !== "match_only") {
    pushIf("output-rewrite scope", draft.orScope);
  }
  if (draft.orIsRegex === false) entries.push("output-rewrite regex: off");
  // PR-F-EXEC3 — shell payload subset. Each field is omitted when empty so
  // the primer reads naturally for very-early-stage drafts (e.g. the
  // operator picked the "shell" archetype but hasn't typed a script yet).
  // shInline can grow long; the pushIf clip at 120 chars keeps the primer
  // under PRIMER_MAX_CHARS for any realistic script paste.
  if (draft.shSource) {
    pushIf("shell source", draft.shSource);
  }
  pushIf("shell script", draft.shInline);
  pushIf("shell script path", draft.shPath);
  if (
    typeof draft.shTimeoutSeconds === "number"
    && Number.isFinite(draft.shTimeoutSeconds)
    && draft.shTimeoutSeconds !== 30
  ) {
    // 30s is the EMPTY-draft default; only surface it when the operator
    // bumped the timeout so the primer stays minimal in the common case.
    entries.push(`shell timeout: ${draft.shTimeoutSeconds}s`);
  }
  pushIf("shell env-var allowlist", draft.shEnvVars);
  if (draft.shShell && draft.shShell !== "bash") {
    // bash is the EMPTY-draft default; only surface it when the operator
    // picked the alternate sh interpreter.
    pushIf("shell interpreter", draft.shShell);
  }
  pushIf("policy id", draft.ruleId);
  pushIf("description", draft.description);

  if (entries.length === 0) return null;
  return `I had filled in: ${entries.join("; ")}.`;
}


function buildStuckClause(
  step: HandoffStepKey,
  draft: HandoffDraft,
): string {
  const stepLabel = STEP_LABEL[step] ?? step;
  const hint = stepFieldHint(step, draft);
  if (hint) {
    return `I got stuck at the ${stepLabel} step (specifically the ${hint}).`;
  }
  return `I got stuck at the ${stepLabel} step.`;
}


const STEP_LABEL: Record<HandoffStepKey, string> = {
  trigger: "trigger",
  condition: "condition",
  specifics: "specifics",
  action: "action",
  name: "name",
  review: "review",
};


/**
 * Best-effort guess at the field the operator was last touching on the
 * stuck step. Returns ``null`` when the step has no obviously-active field
 * (the stuck clause then renders without a field hint).
 */
function stepFieldHint(
  step: HandoffStepKey,
  draft: HandoffDraft,
): string | null {
  if (step === "trigger") {
    if (!draft.lifecycle) return "lifecycle picker";
    if (
      (draft.lifecycle === "before_tool_use"
        || draft.lifecycle === "after_tool_use")
      && draft.toolTarget === "specific"
      && !draft.toolName?.trim()
    ) {
      return "tool name picker";
    }
    return null;
  }
  if (step === "condition") return "condition kind picker";
  if (step === "specifics") {
    switch (draft.conditionKind) {
      case "shacl":
        return "SHACL shape textarea";
      case "llm_criterion":
        return "LLM criterion sentence";
      case "field_constraint":
        return "field-constraint picker";
      case "regex":
        return "regex pattern field";
      case "evidence_ref":
      case "verifier_passed":
        return "ref picker";
      case "domain":
      case "domain_allowlist":
        return "domain field";
      case "path":
      case "path_allowlist":
        return "path field";
      case "prompt_injection":
        return "prompt-injection picker";
      case "output_rewrite":
        return "output-rewrite picker";
      // PR-F-EXEC3 — both operator-defined shell kinds reuse the same
      // ShellCommandPicker / ShellCheckPicker layout. Distinct hint
      // strings keep the primer honest about which contract the operator
      // was stuck on (verifier verdict vs side-effect script).
      case "shell_command":
        return "shell command picker";
      case "shell_check":
        return "shell verifier picker";
      default:
        return null;
    }
  }
  if (step === "action") return "archetype picker";
  if (step === "name") return "policy id field";
  if (step === "review") return null;
  return null;
}
