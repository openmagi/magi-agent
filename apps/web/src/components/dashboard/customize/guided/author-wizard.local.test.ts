import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./author-wizard.tsx", import.meta.url),
  "utf8",
);

describe("AuthorWizard — variable-length policy authoring (F1.5 + F-UX3)", () => {
  it("declares step plan as a constant 6 steps for all lifecycles (F-UX3 collapse)", () => {
    // PR-F-UX3 — F1.5's standalone Target step was collapsed back into the
    // Trigger step as a sub-fieldset. The step plan no longer branches on
    // lifecycle: every lifecycle returns the same 6-step list. The tool-
    // target axis still exists, it just renders inside TriggerStep.
    expect(src).toContain("function stepPlan(lifecycle: Lifecycle): StepKey[]");
    expect(src).toMatch(
      /pre_final[\s\S]*?\["trigger", "condition", "specifics", "action", "name", "review"\]/,
    );
    // The tool-bearing branch must ALSO return the 6-step plan, not the
    // 7-step F1.5 plan that included "target". The asserted comment makes
    // the intent explicit so a future regression renaming the branch
    // doesn't accidentally re-introduce the 7-step plan.
    expect(src).toMatch(
      /Tool-bearing lifecycles[\s\S]*?return \["trigger", "condition", "specifics", "action", "name", "review"\]/,
    );
    // The 7-step plan from F1.5 must be gone.
    expect(src).not.toContain(
      '["trigger", "target", "condition", "specifics", "action", "name", "review"]',
    );
    // The standalone "target" key must be dropped from the StepKey union.
    expect(src).not.toMatch(/type StepKey = [^;]*?"target"/);
  });

  it("ships one step body per axis: trigger / condition / specifics / archetype / name / review (no TargetStep)", () => {
    expect(src).toContain("TriggerStep");
    expect(src).toContain("ConditionKindStep");
    expect(src).toContain("SpecificsStep");
    expect(src).toContain("ArchetypeStep");
    expect(src).toContain("NameStep");
    expect(src).toContain("ReviewStep");
    // PR-F-UX3 — TargetStep deleted; its fields moved into TriggerStep.
    expect(src).not.toContain("function TargetStep(");
    expect(src).not.toMatch(/currentKey === "target"/);
  });

  it("TriggerStep surfaces Any tool / Specific tool radio + tool-name combobox (folded-in target)", () => {
    // PR-F-UX3 — the Tool target sub-fieldset lives inside TriggerStep,
    // gated on lifecycleHasToolTarget(draft.lifecycle).
    // PR-F-UX7 — the dropdown is now a native combobox
    // (<input list="tool-name-options"> + <datalist>), so the catalog
    // pre-populates suggestions while free-text typing is still allowed
    // for tools that join the runtime dynamically.
    expect(src).toContain("Tool target");
    expect(src).toContain("Which tool(s) does this policy apply to?");
    expect(src).toContain("Any tool");
    expect(src).toContain("Specific tool");
    expect(src).toContain('toolTarget === "specific"');
    // Replaces F1.5's freeform TextField with a real catalog-backed
    // combobox (F-UX3 introduced a <select>; F-UX7 swapped it for a
    // <input list="..."> + <datalist> so free-text is still accepted).
    expect(src).toContain("ToolNameSelect");
    expect(src).toMatch(/<input[\s\S]*?list=\{listId\}[\s\S]*?value=\{value\}/);
    expect(src).toContain('<datalist id={listId}>');
  });

  it("TriggerStep gates the Tool target sub-fieldset on tool-bearing lifecycles only", () => {
    // pre_final / Tier 2 audit slots have no tool layer — the sub-fieldset
    // must be hidden so the operator can't author a draft that drags a
    // stale toolName into a non-tool lifecycle payload.
    expect(src).toContain("lifecycleHasToolTarget");
    expect(src).toMatch(
      /function lifecycleHasToolTarget[\s\S]*?"before_tool_use"[\s\S]*?"after_tool_use"/,
    );
    expect(src).toContain("showToolTarget");
  });

  it("ToolNameSelect sources datalist options from the catalog.tools prop (free-text fallback allowed)", () => {
    // PR-F-UX3 — the suggestion list enumerates real runtime tools so
    // an operator picking from the dropdown cannot save a rule against
    // a tool that doesn't exist. The catalog is threaded through
    // TriggerStep via ``tools={catalog.tools}`` from the wizard hub.
    // PR-F-UX7 — switched <select>+<option> to <datalist>+<option>; the
    // input itself still binds the value (free-text accepted), and the
    // datalist supplies type-ahead suggestions sourced from the same
    // catalog.tools prop.
    expect(src).toContain("tools: ToolItem[]");
    expect(src).toMatch(/<TriggerStep[\s\S]*?tools=\{catalog\.tools\}/);
    expect(src).toMatch(/sorted\.map\(\(t\) => \(?\s*\n?\s*(?:\/\/[^\n]*\n\s*)*<option key=\{t\.name\}/);
  });

  it("TriggerStep renders THREE fieldsets when the lifecycle is tool-bearing (lifecycle + scope + tool target)", () => {
    // F-UX3 adds the Tool target sub-fieldset to the existing
    // lifecycle + scope pair. The fieldset count is 3 in the source.
    expect(src.match(/<fieldset/g)?.length).toBe(3);
    expect(src).toContain("Lifecycle event");
    expect(src).toContain("Turn scope");
    expect(src).toContain("Tool target");
  });

  it("drops the disabled 'emit' archetype (audit+(no condition) covers the same outcome)", () => {
    expect(src).not.toContain("Coming soon");
    expect(src).not.toContain("Megaphone");
    expect(src).not.toMatch(/id:\s*"emit"/);
    // PR-F-MUT3 — extends the union with 'mutate' (Inject / Rewrite card).
    // The 4 original archetypes remain wired; 'mutate' joins as a friendly
    // grouping for the two mutator conditionKinds.
    expect(src).toContain(
      'type Archetype = "block" | "ask" | "audit" | "strip" | "mutate"',
    );
  });

  it("ConditionKind drops 'tool_name' (now promoted to TargetStep)", () => {
    // Conflation of "which tool" with "what condition" is the F1.5 fix.
    // Tool selection moves to TargetStep; the condition list shrinks.
    expect(src).not.toMatch(/type ConditionKind[\s\S]*?"tool_name"/);
  });

  it("availableConditionKinds is FILTERED by lifecycle AND tool target", () => {
    expect(src).toContain(
      "availableConditionKinds(\n  lifecycle: Lifecycle,\n  toolTarget: ToolTarget,\n)",
    );
  });

  it("before_tool_use + target=specific exposes 'none' plus PR-F-MUT1 prompt_injection", () => {
    // Backend tool_perm has no AND between tool name and url-shape matchers,
    // so per-tool rules can only fire unconditionally per call. PR-F-MUT1
    // adds ``prompt_injection`` here as a mutator (not a deny gate) — it
    // doesn't introduce an AND, the rule fires on every call to the
    // chosen tool.
    expect(src).toMatch(
      /toolTarget === "specific"[\s\S]*?return \["none", "prompt_injection"\]/,
    );
  });

  it("before_tool_use + target=any omits 'none' (no wildcard matcher in backend)", () => {
    // tool_perm has no wildcard, so 'no condition' with target=any has no
    // honest backend mapping. The option is omitted instead of synthesised.
    // F6 expanded the matcher list to include path + path_allowlist (the
    // backend tool_perm matcher already supports both). PR-F-MUT1 appends
    // ``prompt_injection`` — a mutator, not a deny gate.
    expect(src).toMatch(
      /target=any: tool_perm has no wildcard[\s\S]*?return \["domain", "domain_allowlist", "path", "path_allowlist", "prompt_injection"\]/,
    );
  });

  it("after_tool_use + target=specific exposes llm_criterion (PR-F-UX4 liberalization, auto-derives toolMatch)", () => {
    // PR-F-UX4 — F6.5's "only target=any" restriction was an UX choice (don't
    // make the user retype the tool name into a second field), NOT a backend
    // constraint. F-UX4 liberalizes by auto-deriving `toolMatch=[draft.toolName]`
    // in customRulePayload when target=specific + llm_criterion + after_tool_use,
    // so both axes expose llm_criterion identically at the picker level.
    // PR-F-MUT2 appends ``output_rewrite`` to the same list as a Mutator
    // entry; the picker still surfaces llm_criterion in both target modes.
    expect(src).toMatch(
      /PR-F-UX4 — liberalization: llm_criterion is now available under BOTH/,
    );
    expect(src).toMatch(
      /toolTarget === "specific"\) \{[\s\S]*?return \["none", "regex", "llm_criterion", "output_rewrite"\]/,
    );
  });

  it("after_tool_use + target=any offers none / regex / llm_criterion / output_rewrite", () => {
    // PR-F-MUT2 — same list as target=specific; the toolMatch.include
    // filter rides on the payload, not the wizard's top-level Target step.
    expect(src).toMatch(/return \["none", "regex", "llm_criterion", "output_rewrite"\]/);
  });

  it("pre_final ignores target and returns evidence_ref / verifier_passed / shacl / llm_criterion / field_constraint (PR-F-UX5)", () => {
    // F-UX5 inserts ``verifier_passed`` next to ``evidence_ref`` so the
    // operator picks raw-evidence vs verdict-primitive distinctly. Both
    // compile to ``deterministic_ref`` on the backend (storage unchanged).
    // F3's ``field_constraint`` and the raw ``shacl`` escape hatch remain.
    expect(src).toMatch(
      /pre_final[\s\S]*?return \["evidence_ref", "verifier_passed", "shacl", "llm_criterion", "field_constraint"\]/,
    );
  });

  it("action archetypes are FILTERED by lifecycle only", () => {
    expect(src).toContain("availableArchetypes");
    expect(src).toMatch(/before_tool_use[\s\S]*?"block", "ask", "audit"/);
    expect(src).toMatch(/after_tool_use[\s\S]*?"block", "audit", "strip"/);
  });

  // -------------------------------------------------------------------------
  // PR-F-LIFE4a — action matrix normalization across lifecycle slots. Each
  // pin-test mirrors the backend ``_LEGAL`` lift exactly so wizard drafts
  // assemble actions the validator accepts. The fan-through tests in
  // tests/test_customize_custom_rules.py + tests/customize_firing/
  // test_life4a_gate_firing.py prove the runtime end of the same matrix.
  // -------------------------------------------------------------------------

  it("F-LIFE4a — on_user_prompt_submit lifts to block + audit + mutate", () => {
    // Backend: _LEGAL["llm_criterion"]["on_user_prompt_submit"] = {audit, block}
    // Wizard preserves "mutate" because prompt_injection wires here.
    expect(src).toMatch(
      /lifecycle === "on_user_prompt_submit"\)\s*\{\s*return \["block", "audit", "mutate"\]/,
    );
  });

  it("F-LIFE4a — before_turn_start lifts to block + ask + audit", () => {
    // Backend: _LEGAL["llm_criterion"]["before_turn_start"] = {audit, block, ask_approval}
    expect(src).toMatch(
      /lifecycle === "before_turn_start"\)\s*\{\s*return \["block", "ask", "audit"\]/,
    );
  });

  it("F-LIFE4a — after_turn_end stays audit-only (no honest block target)", () => {
    expect(src).toMatch(
      /lifecycle === "after_turn_end"\)\s*\{\s*return \["audit"\]/,
    );
  });

  it("F-LIFE4a — before_llm_call + after_llm_call lift to block + audit", () => {
    // Backend: _LEGAL["llm_criterion"]["before_llm_call"|"after_llm_call"] = {audit, block}
    expect(src).toMatch(
      /lifecycle === "before_llm_call" \|\| lifecycle === "after_llm_call"\)\s*\{\s*return \["block", "audit"\]/,
    );
  });

  it("F-LIFE4a — before_compaction lifts to block + audit", () => {
    // Backend: _LEGAL["llm_criterion"]["before_compaction"] = {audit, block}
    expect(src).toMatch(
      /lifecycle === "before_compaction"\)\s*\{\s*return \["block", "audit"\]/,
    );
  });

  it("F-LIFE4a — after_compaction stays audit-only", () => {
    expect(src).toMatch(
      /lifecycle === "after_compaction"\)\s*\{\s*return \["audit"\]/,
    );
  });

  it("F-LIFE4a — on_task_checkpoint lifts to block + ask + audit", () => {
    // Backend: _LEGAL["llm_criterion"]["on_task_checkpoint"] = {audit, block, ask_approval}
    expect(src).toMatch(
      /lifecycle === "on_task_checkpoint"\)\s*\{\s*return \["block", "ask", "audit"\]/,
    );
  });

  it("F-LIFE4a — on_artifact_created lifts to ask + audit only (block honestly impossible)", () => {
    // Backend: _LEGAL["llm_criterion"]["on_artifact_created"] = {audit, ask_approval}
    expect(src).toMatch(
      /lifecycle === "on_artifact_created"\)\s*\{\s*return \["ask", "audit"\]/,
    );
  });

  it("F-LIFE4a — ask archetype carries honest-degrade tooltip about provisional approval surface", () => {
    // The ask card's description must explain that ask records
    // requires_approval=true today and approval surfaces are a follow-up.
    expect(src).toContain("requires_approval=true");
    expect(src).toContain("follow-up");
  });

  it("action step header composes a per-trigger phrase (target + condition together)", () => {
    expect(src).toContain("triggerEventPhrase");
    expect(src).toContain("targetEventPhrase");
    expect(src).toContain("did NOT return ok");
    expect(src).toContain("does NOT conform");
  });

  it("specifics step auto-skips when conditionKind === 'none'", () => {
    expect(src).toContain("isSpecificsEmpty");
    expect(src).toMatch(/draft\.conditionKind === "none"/);
  });

  it("downstream fields auto-reseed when an upstream axis changes", () => {
    expect(src).toContain("reseedDownstream");
    // reseed must consider both lifecycle AND target when filtering kinds.
    expect(src).toContain(
      "availableConditionKinds(merged.lifecycle, merged.toolTarget)",
    );
  });

  it("after-tool DashboardCheck path honors target=specific by setting tool=<name>", () => {
    expect(src).toContain("putDashboardCheck");
    expect(src).toMatch(
      /toolTarget === "specific" \? draft\.toolName\.trim\(\) : "\*"/,
    );
  });

  it("after-tool 'no condition' synthesises pattern='.*' for the DashboardCheck", () => {
    expect(src).toMatch(/conditionKind === "none" \? "\.\*"/);
  });

  it("before-tool tool_perm payload picks match from target + condition", () => {
    expect(src).toContain("customRulePayload");
    expect(src).toContain('toolTarget === "specific"');
    expect(src).toContain("match: { tool: draft.toolName.trim() }");
    expect(src).toContain("match: { domain: draft.domain.trim() }");
    expect(src).toContain("domainAllowlist:");
  });

  it("Review step shows Target row only for tool-bearing lifecycles", () => {
    expect(src).toMatch(/draft\.lifecycle !== "pre_final"[\s\S]*?Target/);
  });

  it("Review step emits plain-English sentence + key/value summary (no raw JSON)", () => {
    expect(src).toContain("describePolicy");
    expect(src).not.toContain("JSON.stringify");
  });

  it("Save button (last step) calls handleSave", () => {
    expect(src).toContain("handleSave");
    expect(src).toContain("onSave={handleSave}");
  });
});


// ---------------------------------------------------------------------------
// PR-F3 — field_constraint condition kind (deterministic SHACL-via-picker)
// ---------------------------------------------------------------------------


describe("AuthorWizard — F3 field_constraint condition kind", () => {
  it("declares field_constraint as a ConditionKind union member", () => {
    // Additive: field_constraint joins the existing kinds. Persists as
    // shacl_constraint on the backend with an authoredAs IR for round-trip.
    expect(src).toMatch(
      /type ConditionKind[\s\S]*?\| "field_constraint"/,
    );
  });

  it("pre_final exposes field_constraint as a preferred deterministic option", () => {
    // pre_final is the canonical home for evidence-shape constraints; the
    // deterministic SHACL compile path lives here so users can author
    // field rules without ever seeing TTL.
    expect(src).toMatch(
      /pre_final[\s\S]*?return \[[^\]]*?"field_constraint"[^\]]*?\]/,
    );
  });

  it("registers a CONDITION_META entry for field_constraint", () => {
    expect(src).toMatch(
      /field_constraint:\s*\{[\s\S]*?label:\s*"Field constraint"/,
    );
    expect(src).toMatch(
      /field_constraint:\s*\{[\s\S]*?Deterministic SHACL compile, no LLM/,
    );
  });

  it("adds structured draft fields for field_constraint authoring", () => {
    // Five-tuple drives the deterministic SHACL synthesis on the backend:
    // evidence type → field → operator → value, plus the cross-record
    // sub-flow for forEachExistsCovering (source/target type+field).
    expect(src).toContain("fcEvidenceType: string");
    expect(src).toContain("fcField: string");
    expect(src).toContain("fcOperator:");
    expect(src).toContain("fcValue: string");
    expect(src).toContain("fcCrossSourceType: string");
    expect(src).toContain("fcCrossSourceField: string");
    expect(src).toContain("fcCrossTargetType: string");
    expect(src).toContain("fcCrossTargetField: string");
  });

  it("SpecificsStep renders a dedicated branch for field_constraint", () => {
    expect(src).toMatch(
      /draft\.conditionKind === "field_constraint"/,
    );
  });

  it("SpecificsStep loads the F2 evidence live-catalog for type/field pickers", () => {
    // PR-F3 wires the deterministic picker against the live catalog so the
    // user only ever picks from types that actually have a registered
    // field vocabulary (inert-producer hide invariant).
    expect(src).toContain("getEvidenceLiveCatalog");
    expect(src).toContain("EvidenceLiveCatalogTypeEntry");
  });

  it("field picker filters out inert-producer types (empty registeredFields)", () => {
    // Spec §5 PR-F3: "only types with non-empty registeredFields are
    // shown; show 'no fields available — producer extension needed' if
    // empty." Hides silent-non-firing shape risk.
    expect(src).toContain("registeredFields");
    expect(src).toMatch(/registeredFields\.length\s*>\s*0/);
  });

  it("offers the full 8 single-record operators plus forEachExistsCovering", () => {
    // Deterministic operators map 1:1 to SHACL constraints on a single
    // evidence record; forEachExistsCovering is the cross-record cardinality
    // form for "for each entry in <source.field>, there exists a <target>"
    // patterns (intent 2 endgame).
    expect(src).toContain('"eq"');
    expect(src).toContain('"neq"');
    expect(src).toContain('"gt"');
    expect(src).toContain('"lt"');
    expect(src).toContain('"ge"');
    expect(src).toContain('"le"');
    expect(src).toContain('"exists"');
    expect(src).toContain('"notExists"');
    expect(src).toContain('"forEachExistsCovering"');
  });

  it("hides the value input for exists/notExists operators", () => {
    // exists/notExists are purely structural — no value is needed and
    // surfacing an input would mislead. The wizard branches on operator
    // shape so the value field disappears for cardinality-only operators.
    expect(src).toMatch(
      /fcOperator === "exists" \|\| .*fcOperator === "notExists"/,
    );
  });

  it("forEachExistsCovering surfaces the cross-record sub-form", () => {
    // Cross-record operator needs source.field + target.evidenceType +
    // target.field; the sub-form replaces the single-record value input
    // when this operator is picked.
    expect(src).toMatch(/fcOperator === "forEachExistsCovering"/);
  });

  it("stepIsComplete validates field_constraint inputs (type + field + operator + value-when-needed)", () => {
    expect(src).toMatch(
      /case "field_constraint":/,
    );
  });

  it("customRulePayload(field_constraint) emits an authoredAs IR for round-trip", () => {
    // Spec §5 schema impact: store as shacl_constraint with authoredAs
    // preserving the structured form so re-editing surfaces chips, not TTL.
    expect(src).toMatch(/case "field_constraint":[\s\S]*?authoredAs:/);
    expect(src).toMatch(/authoredAs:\s*\{[\s\S]*?kind:\s*"field_constraint"/);
  });

  it("customRuleKind maps field_constraint to shacl_constraint storage", () => {
    // Backend storage is shacl_constraint; authoredAs is the round-trip
    // hint. No new backend kind required (additive).
    expect(src).toMatch(
      /conditionKind === "field_constraint"[\s\S]*?"shacl_constraint"/,
    );
  });

  it("describes field_constraint in plain English in the Review step", () => {
    // The reviewer summary must reflect the field-shaped rule rather than
    // dumping raw TTL or the generic "shacl shape" phrase.
    expect(src).toMatch(/case "field_constraint":/);
  });
});


// ---------------------------------------------------------------------------
// PR-F5 — TrustBadge in the Review step
// ---------------------------------------------------------------------------


// ---------------------------------------------------------------------------
// PR-F6 — path / path_allowlist condition kinds (workspace-lock authoring)
// ---------------------------------------------------------------------------


describe("AuthorWizard — F6 path / path_allowlist condition kinds", () => {
  it("declares path + path_allowlist as ConditionKind union members", () => {
    // Additive: both join the existing kinds. Backend tool_perm matcher
    // already supports match.path / match.pathAllowlist (see
    // magi_agent/customize/tool_perm.py); F6 is the frontend surface.
    expect(src).toMatch(/type ConditionKind[\s\S]*?\| "path"/);
    expect(src).toMatch(/type ConditionKind[\s\S]*?\| "path_allowlist"/);
  });

  it("before_tool_use + target=any offers path + path_allowlist alongside domain / domain_allowlist", () => {
    // The before-tool + any-tool branch is the only path tool_perm rules
    // can be authored under — per-tool match has no AND with path matchers
    // in the backend. PR-F-MUT1 appends ``prompt_injection`` to the same
    // branch (mutator surface, not a deny gate); the domain/path matchers
    // remain unchanged.
    expect(src).toMatch(
      /return \["domain", "domain_allowlist", "path", "path_allowlist", "prompt_injection"\]/,
    );
  });

  it("registers CONDITION_META entries for path + path_allowlist", () => {
    expect(src).toMatch(/path:\s*\{[\s\S]*?label:\s*"File \/ path"/);
    expect(src).toMatch(
      /path_allowlist:\s*\{[\s\S]*?label:\s*"Path allowlist"/,
    );
  });

  it("adds pathPrefix + pathAllowlist draft fields", () => {
    expect(src).toContain("pathPrefix: string");
    expect(src).toContain("pathAllowlist: string");
  });

  it("SpecificsStep renders branches for path + path_allowlist", () => {
    expect(src).toMatch(/draft\.conditionKind === "path"/);
    expect(src).toMatch(/draft\.conditionKind === "path_allowlist"/);
  });

  it("stepIsComplete validates non-empty for path / path_allowlist", () => {
    expect(src).toMatch(
      /case "path":\s*\n\s*return draft\.pathPrefix\.trim\(\)\.length > 0/,
    );
    expect(src).toMatch(
      /case "path_allowlist":\s*\n\s*return draft\.pathAllowlist\.trim\(\)\.length > 0/,
    );
  });

  it("customRulePayload(before_tool_use+path) emits match.path = pathPrefix.trim()", () => {
    expect(src).toContain("match: { path: draft.pathPrefix.trim() }");
  });

  it("customRulePayload(before_tool_use+path_allowlist) emits match.pathAllowlist as CSV split", () => {
    expect(src).toMatch(/pathAllowlist: draft\.pathAllowlist[\s\S]*?\.split\(","\)/);
  });

  // F6 honesty audit — the path / path_allowlist CONDITION_META descriptions
  // must only list tools whose manifest arg name is in the backend
  // _PATH_ARG_KEYS = ("path","file","filename","filepath","filePath","pathRef")
  // (magi_agent/customize/tool_perm.py). Glob/Grep surface only `pattern`
  // (plus `glob` on Grep) — neither key intersects _PATH_ARG_KEYS, so the
  // matcher silently does NOT fire on them. The wizard description must not
  // claim otherwise.
  describe("CONDITION_META path / path_allowlist tool-list honesty", () => {
    // Tools whose input_schema actually surfaces a path-shaped key per
    // magi_agent/tools/catalog.py + magi_agent/tools/file_tool_manifests.py.
    // (Limited to the file-write/read/edit suite that operators reach for
    // when authoring a workspace-lock rule.)
    const PATH_BEARING_TOOLS = [
      "FileRead",
      "FileEdit",
      "FileWrite",
      "PatchApply",
    ] as const;

    // Tools that take `pattern` (not `path`) and so are NOT matched by the
    // backend path / pathAllowlist matcher. They must NOT appear in the
    // description as path-bearing.
    const PATTERN_ONLY_TOOLS = ["Glob", "Grep"] as const;

    function extractMetaDescription(kind: string): string {
      // The description is the second property in each meta entry, so this
      // regex tolerates label / description ordering and trailing commas.
      const re = new RegExp(
        `${kind}:\\s*\\{[\\s\\S]*?description:\\s*"([\\s\\S]*?)"[\\s\\S]*?\\}`,
        "m",
      );
      const m = src.match(re);
      if (!m) throw new Error(`CONDITION_META.${kind} not found`);
      return m[1];
    }

    // Helper: identify tool-name tokens advertised as POSITIVE examples,
    // i.e. CamelCase words appearing in the description, EXCLUDING any
    // occurrence inside a negative ("not", "NOT", "does NOT", "not for")
    // clause. We split the description at negative-clause boundaries and
    // only scan the positive half.
    function advertisedTools(desc: string): string[] {
      // Cut everything from the first negative marker onwards so tokens
      // listed as counter-examples ("Does NOT match Glob or Grep") are
      // excluded from the "advertised" set.
      const negMarkers = [
        ". Does NOT",
        "; not for",
        ". Not for",
        "; not ",
        ". Not ",
      ];
      let positive = desc;
      for (const marker of negMarkers) {
        const idx = positive.indexOf(marker);
        if (idx !== -1) positive = positive.slice(0, idx);
      }
      return Array.from(positive.matchAll(/\b([A-Z][a-z]+[A-Z][A-Za-z]*)\b/g)).map(
        (m) => m[1],
      );
    }

    it("path description does NOT advertise Glob / Grep as positive examples", () => {
      const advertised = advertisedTools(extractMetaDescription("path"));
      for (const tool of PATTERN_ONLY_TOOLS) {
        expect(advertised).not.toContain(tool);
      }
    });

    it("path description lists only tools whose manifest arg name intersects _PATH_ARG_KEYS", () => {
      const advertised = advertisedTools(extractMetaDescription("path"));
      // At least one path-bearing tool must be advertised (smoke).
      expect(advertised.length).toBeGreaterThan(0);
      // Every advertised tool must be a real path-bearing tool.
      for (const tool of advertised) {
        expect(PATH_BEARING_TOOLS).toContain(tool as (typeof PATH_BEARING_TOOLS)[number]);
      }
    });

    it("path_allowlist description stays consistent with path (no Glob / Grep as positive examples)", () => {
      // path_allowlist was already honest by omission; this test guards
      // against regressions where someone copies the path description
      // verbatim and reintroduces the Glob/Grep falsehood.
      const advertised = advertisedTools(extractMetaDescription("path_allowlist"));
      for (const tool of PATTERN_ONLY_TOOLS) {
        expect(advertised).not.toContain(tool);
      }
    });
  });
});


describe("AuthorWizard — F5 TrustBadge in Review step", () => {
  it("imports the shared TrustBadge component", () => {
    // The badge is shared across customize surfaces (GuidancePanel,
    // custom-checks-section, rules-table) so the review screen reaches for
    // the same primitive rather than re-rolling an inline pill.
    expect(src).toContain("TrustBadge");
    expect(src).toMatch(/from\s+["'][^"']*trust-badge["']/);
  });

  it("renders a <TrustBadge trustClass={...}> inside ReviewStep", () => {
    // Honesty signal next to the policy summary so the operator sees the
    // trust class (deterministic vs advisory) before clicking Save.
    expect(src).toMatch(/<TrustBadge\s+trustClass=\{[^}]+\}/);
  });

  it("derives the trust class from the draft's conditionKind", () => {
    // llm_criterion is the only Advisory authoring path the wizard offers
    // today; every other conditionKind maps to Deterministic. The
    // derivation must read draft.conditionKind so future kinds re-classify
    // here, not at the call site.
    expect(src).toContain("trustClassForDraft");
    expect(src).toContain('"llm_criterion"');
    expect(src).toContain('"advisory"');
    expect(src).toContain('"deterministic"');
  });
});


// ---------------------------------------------------------------------------
// PR-F6.5 — llm_criterion + contentMatch combo (after-tool deterministic
// pre-filter in front of the advisory critic)
// ---------------------------------------------------------------------------


describe("AuthorWizard — F6.5 llm_criterion + contentMatch combo", () => {
  it("adds the contentMatch draft fields (enabled flag + pattern + isRegex + negate)", () => {
    // The combo lives entirely on the llm_criterion path; four fields keep
    // the wizard form state separate from the after-tool regex
    // dashboard_check path (which already had regexPattern / regexIsRegex).
    expect(src).toContain("llmContentMatchEnabled: boolean");
    expect(src).toContain("llmContentMatchPattern: string");
    expect(src).toContain("llmContentMatchIsRegex: boolean");
    expect(src).toContain("llmContentMatchNegate: boolean");
  });

  it("EMPTY draft seeds the contentMatch fields as off/empty/false/false", () => {
    // Default-OFF: the sub-form must not appear (and the runtime gate must
    // not see a contentMatch payload) until the operator explicitly opts in.
    expect(src).toContain("llmContentMatchEnabled: false");
    expect(src).toContain('llmContentMatchPattern: ""');
    expect(src).toContain("llmContentMatchIsRegex: false");
    expect(src).toContain("llmContentMatchNegate: false");
  });

  it("SpecificsStep gates the contentMatch sub-form on lifecycle === after_tool_use", () => {
    // Pre-final rules have no tool result to pre-filter, and the backend
    // `_validate_content_match` rejects contentMatch on pre_final. The
    // sub-form is hidden outside the after-tool branch so the wizard never
    // lets the user author a guaranteed-reject combo.
    expect(src).toMatch(
      /draft\.conditionKind === "llm_criterion"[\s\S]*?draft\.lifecycle === "after_tool_use"/,
    );
  });

  it("SpecificsStep ships the enable-checkbox copy + helper text", () => {
    // The operator-facing copy must explain WHAT the gate does (only invoke
    // the critic when the tool output matches) so the deterministic vs
    // advisory layering is visible at authoring time.
    expect(src).toContain("Add a regex pre-filter");
    // Prettier wraps the long JSX text onto two source lines, so match on
    // the wrapped pair rather than the single-line literal.
    expect(src).toMatch(
      /only invoke the critic when the\s+tool output matches/,
    );
  });

  it("SpecificsStep surfaces pattern + isRegex + negate inputs when enabled", () => {
    // Progressive disclosure: the three knobs only render when the
    // enabled flag is true, matching the EMPTY draft default-OFF and the
    // backend payload's optional-nested shape.
    expect(src).toContain("draft.llmContentMatchEnabled ?");
    expect(src).toContain("Pre-filter pattern");
    expect(src).toContain("Treat as regular expression");
    expect(src).toContain("Negate");
  });

  it("customRulePayload(after_tool llm_criterion) emits contentMatch when enabled + non-empty pattern", () => {
    // The runtime gate (`magi_agent/customize/after_tool_gate.py`) reads
    // payload.contentMatch as a dict with {pattern, isRegex, negate}. The
    // emit shape must round-trip through `validate_custom_rule`.
    expect(src).toContain("draft.llmContentMatchEnabled");
    expect(src).toContain("payload.contentMatch = {");
    expect(src).toMatch(
      /pattern: draft\.llmContentMatchPattern\.trim\(\)[\s\S]*?isRegex: draft\.llmContentMatchIsRegex[\s\S]*?negate: draft\.llmContentMatchNegate/,
    );
  });

  it("customRulePayload omits contentMatch on pre_final llm_criterion (rejected upstream)", () => {
    // The emit branch is gated on draft.lifecycle === "after_tool_use" so
    // pre_final llm_criterion authoring stays byte-identical to before
    // F6.5. The backend would reject any pre_final contentMatch anyway
    // (`_validate_content_match` errors with 'only valid for after_tool_use').
    expect(src).toMatch(
      /draft\.lifecycle === "after_tool_use"[\s\S]*?draft\.llmContentMatchEnabled[\s\S]*?draft\.llmContentMatchPattern\.trim\(\)\.length > 0/,
    );
  });

  it("stepIsComplete blocks Next when contentMatch is enabled but pattern is empty", () => {
    // An enabled-but-empty pre-filter would compile to a no-match
    // (pattern empty) and the backend `_validate_content_match` rejects
    // it. The wizard refuses to advance before the operator either
    // disables the toggle or fills the pattern.
    expect(src).toContain("draft.llmContentMatchEnabled");
    expect(src).toContain("draft.llmContentMatchPattern.trim().length > 0");
  });

  it("describePolicy reflects the pre-filter when set", () => {
    // The review-step sentence must surface the deterministic gate so the
    // operator sees the combo ("critic invoked only when output ...") and
    // not just the LLM verdict half.
    expect(src).toContain("with pre-filter:");
    expect(src).toContain("critic invoked only when output");
  });
});


// ---------------------------------------------------------------------------
// PR-F6.5 BLOCKER fix — after-tool llm_criterion requires a non-empty
// `toolMatch` list (backend validator
// `magi_agent/customize/custom_rules.py:185`). Without this the wizard
// could never persist the F6.5 combo: PUT /custom-rules returned HTTP 400.
// These tests lock the wizard's tool-name input, payload emit, completion
// gate, and end-to-end shape against the backend's required keys.
// ---------------------------------------------------------------------------


describe("AuthorWizard — F6.5 BLOCKER fix: toolMatch on after-tool llm_criterion", () => {
  it("Draft carries an llmToolMatch comma-separated string", () => {
    // Single string field (not string[]) keeps the form state simple and
    // mirrors the existing domainAllowlist / pathAllowlist comma pattern.
    expect(src).toContain("llmToolMatch: string");
    expect(src).toContain('llmToolMatch: ""');
  });

  it("SpecificsStep renders the tool-name input on after-tool llm_criterion", () => {
    // The input must live inside the `lifecycle === "after_tool_use"`
    // branch so pre_final stays unchanged (it has no tool layer).
    expect(src).toContain("Tool name(s) to match (comma-separated, exact match)");
    expect(src).toContain("update({ llmToolMatch: v })");
    expect(src).toContain("value={draft.llmToolMatch}");
  });

  it("splitToolMatchList helper trims and drops empties", () => {
    // The helper is the single source of truth used by both
    // customRulePayload (emit) and stepIsComplete (gate) so they cannot
    // diverge. Same shape as the existing allowlist split helpers.
    expect(src).toMatch(
      /function splitToolMatchList\(raw: string\): string\[\][\s\S]*?\.split\(","\)[\s\S]*?\.map\(\(s\) => s\.trim\(\)\)[\s\S]*?\.filter\(Boolean\)/,
    );
  });

  it("customRulePayload(after_tool llm_criterion) ALWAYS emits a toolMatch list", () => {
    // The toolMatch emit MUST sit unconditionally inside the
    // `lifecycle === "after_tool_use"` branch — gating it on a sub-flag
    // would let the wizard emit a payload the backend validator rejects.
    //
    // PR-F-UX4: under target=specific the list is auto-derived from
    // draft.toolName ([draft.toolName.trim()]); under target=any it is
    // split from the user-typed llmToolMatch field. Both branches sit
    // inside the same `lifecycle === "after_tool_use"` block, so
    // toolMatch is still emitted on every after-tool path.
    expect(src).toContain("payload.toolMatch =");
    expect(src).toContain("[draft.toolName.trim()]");
    expect(src).toContain("splitToolMatchList(draft.llmToolMatch)");
    expect(src).toMatch(
      /draft\.lifecycle === "after_tool_use"[\s\S]*?payload\.toolMatch =[\s\S]*?\[draft\.toolName\.trim\(\)\][\s\S]*?splitToolMatchList\(draft\.llmToolMatch\)/,
    );
  });

  it("customRulePayload OMITS toolMatch on pre_final (no tool layer)", () => {
    // pre_final llm_criterion stays byte-identical to pre-fix: the backend
    // validator does not require toolMatch outside after_tool_use, and
    // pre_final has no tool to match against.
    // Verified structurally: the toolMatch emit sits inside the
    // `if (draft.lifecycle === "after_tool_use")` block — the only branch
    // assigning payload.toolMatch IN THE llm_criterion case.
    // PR-F-UX4 ternary spans lines, so the assignment is `payload.toolMatch =\n  ...`.
    // The regex tolerates the trailing space-or-newline so it counts the
    // assignment regardless of formatter line wrapping. PR-F-MUT2 adds a
    // SECOND payload.toolMatch assignment (the output_rewrite branch's
    // include-list filter); that one is also gated on the wizard's
    // target=specific axis, NOT on the inbound lifecycle (output_rewrite
    // only fires at after_tool_use to begin with), so it does not violate
    // the "no toolMatch on pre_final" invariant either.
    const matches = src.match(/payload\.toolMatch =[\s\S]/g) ?? [];
    expect(matches.length).toBe(2);
  });

  it("stepIsComplete blocks Next when after-tool llm_criterion toolMatch is empty", () => {
    // splitToolMatchList(draft.llmToolMatch).length > 0 is the completion
    // gate — without it the wizard would advance to Save and the backend
    // would reject with HTTP 400.
    expect(src).toContain('draft.lifecycle !== "after_tool_use"');
    expect(src).toContain("splitToolMatchList(draft.llmToolMatch).length > 0");
  });

  it("emitted after-tool llm_criterion payload matches the backend's required keys (round-trip shape)", () => {
    // Mirrors the validator contract at
    // `magi_agent/customize/custom_rules.py:185-188`:
    //   - toolMatch: list[str], non-empty
    //   - criterion: str OR contentMatch dict (one of)
    // The wizard's payload builder must emit `toolMatch` as a string[] and
    // `criterion` as a string; contentMatch (optional) round-trips its own
    // {pattern,isRegex,negate} dict. Asserting the emit shape here is the
    // closest we can get without spinning up a Python interpreter in
    // vitest — the firing test
    // `tests/customize_firing/test_llm_criterion_content_match_firing.py`
    // covers the runtime half with this exact payload shape.
    //
    // PR-F-UX4 — toolMatch emit is now a ternary on draft.toolTarget:
    // target=specific → auto-derived [draft.toolName.trim()];
    // target=any → splitToolMatchList(draft.llmToolMatch). Both paths
    // satisfy the backend's non-empty list[str] contract.
    expect(src).toContain("payload.toolMatch =");
    expect(src).toContain("splitToolMatchList(draft.llmToolMatch)");
    expect(src).toContain("criterion: draft.criterion.trim()");
    expect(src).toContain("payload.contentMatch = {");
  });

  it("describePolicy surfaces the tool-match list at review time", () => {
    // Operator sanity: the review-step sentence must name the tool(s) the
    // critic actually fires against, mirroring the runtime gate's
    // exact-membership check.
    expect(src).toContain("splitToolMatchList(draft.llmToolMatch)");
    expect(src).toContain("for tool ");
  });
});


// ---------------------------------------------------------------------------
// PR-F-UX5 — evidence vs verifier_passed split (raw evidence record vs
// verdict primitive). Both compile to the same backend deterministic_ref
// payload; the split lives entirely at the UX layer.
// ---------------------------------------------------------------------------


describe("AuthorWizard — PR-F-UX5 evidence_ref vs verifier_passed split", () => {
  it("ConditionKind union gains 'verifier_passed' alongside 'evidence_ref'", () => {
    // Two visibly distinct picker intents; same backend payload.
    expect(src).toMatch(/type ConditionKind[\s\S]*?\| "evidence_ref"/);
    expect(src).toMatch(/type ConditionKind[\s\S]*?\| "verifier_passed"/);
  });

  it("CONDITION_META labels evidence_ref as 'Check evidence record present'", () => {
    // Raw evidence framing: the picker operates over producer-emitted
    // records (evidence:*). The label MUST NOT use 'reference' (the old
    // wording) so the operator sees the input-shape vs verdict-primitive
    // distinction in the picker.
    expect(src).toMatch(
      /evidence_ref:\s*\{[\s\S]*?label:\s*"Check evidence record present"/,
    );
    expect(src).toMatch(/evidence_ref:[\s\S]*?Raw evidence/);
  });

  it("CONDITION_META labels verifier_passed as 'Check verifier / condition passed'", () => {
    // Verdict primitive framing: the picker operates over judgments
    // (verifier:* + bare named conditions). Same backend payload as
    // evidence_ref but a different intent — must be visibly distinct.
    expect(src).toMatch(
      /verifier_passed:\s*\{[\s\S]*?label:\s*"Check verifier \/ condition passed"/,
    );
    expect(src).toMatch(/verifier_passed:[\s\S]*?Verdict primitive/);
  });

  it("evidence_ref picker source narrows to catalog.evidenceMenu only", () => {
    // F-UX5 spec: evidence picker reads ONLY raw-evidence refs. The hub
    // builds evidenceRefOptions from catalog.verification.evidenceMenu and
    // passes it as a separate prop so verifier refs cannot leak into this
    // picker.
    expect(src).toContain("catalog.verification.evidenceMenu");
    expect(src).toContain("evidenceRefOptions");
  });

  it("verifier_passed picker source narrows to catalog.judgmentMenu only", () => {
    // Same split on the verdict-primitive side: picker reads only
    // judgmentMenu (verifier:* + bare named judgments). User-authored
    // refs are NOT mixed in because verifier authoring is a runtime-code
    // surface, not a dashboard surface (F-UX5 principle 1).
    expect(src).toContain("catalog.verification.judgmentMenu");
    expect(src).toContain("judgmentRefOptions");
  });

  it("field_constraint picker keeps reading liveCatalogTypes (evidence-only by construction)", () => {
    // F-UX5 spec: field_constraint picker must show evidence-shape types
    // ONLY (verifiers have no traversable fields). liveCatalogTypes is
    // already evidence-shape-only (filtered by registeredFields presence
    // in FieldConstraintPicker), so the wizard does NOT feed it
    // judgmentRefOptions.
    expect(src).toMatch(/FieldConstraintPicker[\s\S]*?liveCatalogTypes/);
    // Specifically: the field_constraint branch must NOT thread the
    // judgment refs through (would invite a verifier-typed pick → silent
    // dead end at compile time).
    expect(src).not.toMatch(
      /draft\.conditionKind === "field_constraint"[\s\S]*?judgmentRefOptions/,
    );
  });

  it("evidence_ref + verifier_passed BOTH compile to backend kind 'deterministic_ref'", () => {
    // Storage shape is identical for both UX kinds; the split is UX-only.
    // No backend migration needed for existing rules (acceptance #5).
    expect(src).toMatch(
      /conditionKind === "evidence_ref"[\s\S]*?"deterministic_ref"/,
    );
    expect(src).toMatch(
      /conditionKind === "verifier_passed"[\s\S]*?"deterministic_ref"/,
    );
  });

  it("evidence_ref + verifier_passed share the same {ref} payload (backend stays unchanged)", () => {
    // customRulePayload must collapse both cases onto the same {ref}
    // emission so persisted rules are indistinguishable. Either a shared
    // case fallthrough or two identical case bodies — assert structurally
    // that both kinds reach the {ref} emit.
    expect(src).toMatch(/case "evidence_ref":\s*\n\s*case "verifier_passed":/);
  });

  it("stepIsComplete accepts a non-empty evidenceRef for either kind", () => {
    // The draft slot ``evidenceRef`` is reused by both pickers (storage is
    // shared). The completion gate must not require a separate slot for
    // verifier_passed.
    expect(src).toMatch(/case "evidence_ref":\s*\n\s*case "verifier_passed":/);
  });

  it("verifier_passed picker badges built-in entries 'built-in' (and user entries 'user')", () => {
    // The Conditions tab uses the same badge vocabulary; the picker
    // mirrors it so the inventory looks consistent across surfaces.
    expect(src).toMatch(
      /conditionKind === "verifier_passed"[\s\S]*?badge=\{[\s\S]*?"built-in"/,
    );
  });
});


// ---------------------------------------------------------------------------
// PR-F-UX1 — lifecycle audit (Tier 2 expansion + Tier 3 honest-disabled
// surfacing). Backend matrix in magi_agent/customize/custom_rules.py restricts
// the two new firesAt slots to llm_criterion + audit; the wizard mirrors that
// restriction so an operator cannot assemble a draft the backend rejects.
// ---------------------------------------------------------------------------


describe("AuthorWizard — PR-F-UX1 lifecycle audit + Tier 2 expansion", () => {
  it("Lifecycle union gains on_user_prompt_submit + on_subagent_stop", () => {
    expect(src).toMatch(/type Lifecycle[\s\S]*?\| "on_user_prompt_submit"/);
    expect(src).toMatch(/type Lifecycle[\s\S]*?\| "on_subagent_stop"/);
  });

  it("LIFECYCLE_OPTIONS lists Tier 1 (legacy 3) + Tier 2 (2 new) + Tier 3 (file-hook-only) entries", () => {
    // Tier 1 — Tier markers must be present so renderers can distinguish
    // active-vs-disabled and downstream test assertions can read the tier.
    expect(src).toMatch(/id: "before_tool_use"[\s\S]*?tier: "tier1"/);
    expect(src).toMatch(/id: "after_tool_use"[\s\S]*?tier: "tier1"/);
    expect(src).toMatch(/id: "pre_final"[\s\S]*?tier: "tier1"/);
    // Tier 2 — both new active slots
    expect(src).toMatch(/id: "on_user_prompt_submit"[\s\S]*?tier: "tier2"/);
    expect(src).toMatch(/id: "on_subagent_stop"[\s\S]*?tier: "tier2"/);
    // Tier 3 — at least the four file-hook-only entries from the audit
    expect(src).toMatch(/id: "before_llm_call"[\s\S]*?tier: "tier3"/);
    expect(src).toMatch(/id: "after_llm_call"[\s\S]*?tier: "tier3"/);
    expect(src).toMatch(/id: "on_session_start"[\s\S]*?tier: "tier3"/);
    expect(src).toMatch(/id: "on_session_stop"[\s\S]*?tier: "tier3"/);
  });

  it("Tier 3 entries carry the honest 'file hook only' disabledReason tooltip", () => {
    // The disabledReason becomes a native HTML tooltip so operators see WHY
    // they cannot pick this option (Tier 3 = no custom_rule gate today).
    expect(src).toContain("No custom_rule gate yet — file hooks via ~/.magi/settings.json instead.");
    expect(src).toMatch(/disabledReason:\s*\n?\s*"No custom_rule gate yet/);
  });

  it("Tier 2 entries telegraph their action contract honestly", () => {
    // PR-F-LIFE4a lifted on_user_prompt_submit past audit-only — the
    // description now mentions block short-circuits the engine stream so
    // operators see the new contract up-front. on_subagent_stop was
    // lifted in F-LIFE1 (block / ask actions accepted).
    expect(src).toMatch(
      /id: "on_user_prompt_submit"[\s\S]*?block action/,
    );
    expect(src).toMatch(
      /id: "on_subagent_stop"[\s\S]*?block \/ ask actions/,
    );
  });

  it("TriggerStep renders Tier 3 entries DISABLED with onClick suppressed", () => {
    // The renderer flips ``disabled`` on tier3 entries; RadioCard then ignores
    // clicks and dims the card so the option is visible-but-not-selectable.
    expect(src).toMatch(/opt\.tier === "tier3"/);
    expect(src).toMatch(/disabled=\{isDisabled\}/);
    expect(src).toMatch(/disabledReason=\{opt\.disabledReason\}/);
  });

  it("stepPlan(on_user_prompt_submit) drops the target step (6-step plan)", () => {
    // The two Tier 2 slots fire OUTSIDE the tool boundary so they have no
    // tool target axis — same step shape as pre_final.
    expect(src).toMatch(
      /lifecycle === "on_user_prompt_submit" \|\| lifecycle === "on_subagent_stop"[\s\S]*?\["trigger", "condition", "specifics", "action", "name", "review"\]/,
    );
  });

  it("availableConditionKinds(Tier 2) returns ONLY llm_criterion", () => {
    // Backend ``_LEGAL`` matrix entry: only llm_criterion + audit at these
    // slots. The wizard mirrors the restriction so an operator cannot
    // assemble a draft the backend rejects.
    expect(src).toMatch(
      /lifecycle === "on_user_prompt_submit" \|\| lifecycle === "on_subagent_stop"[\s\S]*?return \["llm_criterion"\]/,
    );
  });

  it("availableArchetypes(on_subagent_stop) is lifted to [block, ask, audit] (PR-F-LIFE1)", () => {
    // PR-F-LIFE1 lifts ``on_subagent_stop`` past audit-only — the backend
    // ``_LEGAL`` matrix now accepts (llm_criterion × on_subagent_stop ×
    // {audit, block, ask_approval}). The block / ask verbs are directives
    // to the PARENT caller (the child output has already been emitted),
    // not a mutation of the already-emitted output. The audit row is
    // recorded in either case.
    expect(src).toMatch(
      /lifecycle === "on_subagent_stop"\) \{[\s\S]*?return \["block", "ask", "audit"\]/,
    );
  });

  it("availableArchetypes(on_user_prompt_submit) — PR-F-LIFE4a lifts to block + audit + mutate", () => {
    // The Tier 2 slot on_user_prompt_submit accepts prompt_injection
    // (system-prompt section append) — the wizard surfaces it via the
    // friendly "Inject / Rewrite" archetype card. PR-F-LIFE4a lifted the
    // backend matrix from {audit} to {audit, block}: the gate fan-out
    // short-circuits the engine stream when a block-action criterion
    // fails. Mutate is still surfaced for prompt_injection authoring.
    expect(src).toMatch(
      /lifecycle === "on_user_prompt_submit"\) \{[\s\S]*?return \["block", "audit", "mutate"\]/,
    );
  });

  it("targetEventPhrase + whenForLifecycle describe both new lifecycles in plain English", () => {
    // The Review step's sentence and the ArchetypeStep header must stay
    // honest when the operator picks a Tier 2 slot — the existing wording
    // assumes a tool boundary that does not exist here.
    expect(src).toContain('"When the user submits a prompt"');
    expect(src).toContain('"When a subagent finishes a turn"');
  });

  it("Review step skips the Target row for both new lifecycles", () => {
    // The Target row is tool-bearing-only; the two Tier 2 slots have no
    // tool axis so the row would render "(unnamed tool)" which is a lie.
    // PR-F-LIFE1 adds the two turn-boundary slots to the same skip set.
    expect(src).toMatch(
      /lifecycle !== "pre_final"[\s\S]*?lifecycle !== "on_user_prompt_submit"[\s\S]*?lifecycle !== "on_subagent_stop"[\s\S]*?lifecycle !== "before_turn_start"[\s\S]*?lifecycle !== "after_turn_end"[\s\S]*?Target/,
    );
  });
});


// ---------------------------------------------------------------------------
// PR-F-LIFE1 — turn-boundary lifecycle expansion. Backend matrix in
// magi_agent/customize/custom_rules.py restricts the two new firesAt slots
// (before_turn_start + after_turn_end) to (llm_criterion + audit) and
// (deterministic_ref + audit); the wizard mirrors that restriction so an
// operator cannot assemble a draft the backend rejects. PR-F-LIFE1 also
// lifts on_subagent_stop to additionally accept block + ask.
// ---------------------------------------------------------------------------


describe("AuthorWizard — PR-F-LIFE1 turn-boundary lifecycle expansion", () => {
  it("Lifecycle union gains before_turn_start + after_turn_end", () => {
    expect(src).toMatch(/type Lifecycle[\s\S]*?\| "before_turn_start"/);
    expect(src).toMatch(/type Lifecycle[\s\S]*?\| "after_turn_end"/);
  });

  it("LIFECYCLE_OPTIONS lists the two new turn-boundary slots as Tier 2", () => {
    // Both slots ride on top of the existing run_governed_turn funnel —
    // active wire, not a Tier 3 file-hook-only entry.
    expect(src).toMatch(/id: "before_turn_start"[\s\S]*?tier: "tier2"/);
    expect(src).toMatch(/id: "after_turn_end"[\s\S]*?tier: "tier2"/);
  });

  it("LIFECYCLE_OPTIONS turn-boundary slots — PR-F-LIFE4a lifted before_turn_start; after_turn_end stays audit-only", () => {
    // PR-F-LIFE4a updated the descriptions:
    //   * before_turn_start now telegraphs block / ask accepted
    //   * after_turn_end stays audit-only (no honest block target)
    expect(src).toMatch(
      /id: "before_turn_start"[\s\S]*?block \/ ask actions/,
    );
    expect(src).toMatch(
      /id: "after_turn_end"[\s\S]*?\(audit-only\)/,
    );
  });

  it("stepPlan(turn-boundary) drops the target step (6-step plan)", () => {
    // Turn-boundary slots fire OUTSIDE the tool boundary so they have no
    // tool target axis — same step shape as pre_final / the other Tier 2
    // slots.
    expect(src).toMatch(
      /lifecycle === "before_turn_start" \|\| lifecycle === "after_turn_end"[\s\S]*?\["trigger", "condition", "specifics", "action", "name", "review"\]/,
    );
  });

  it("availableConditionKinds(turn-boundary) returns the conservative set", () => {
    // Backend ``_LEGAL`` has fan-out only for llm_criterion at the new
    // turn-boundary slots. deterministic_ref (which evidence_ref /
    // verifier_passed compile to) was dropped from _LEGAL during the
    // F-LIFE1 review pass because there is no runtime consumer — exposing
    // it would have let the operator persist an inert rule. Mutator kinds
    // (prompt_injection / output_rewrite) are also NOT exposed — no honest
    // mutation target at top-level turn entry / exit.
    expect(src).toMatch(
      /lifecycle === "before_turn_start" \|\| lifecycle === "after_turn_end"[\s\S]*?return \["llm_criterion"\]/,
    );
  });

  it("availableArchetypes(turn-boundary) — PR-F-LIFE4a lifted before_turn_start, after_turn_end stays audit-only", () => {
    // PR-F-LIFE4a updated the matrix:
    //   * before_turn_start → {audit, block, ask_approval} (gate fan-out
    //     short-circuits the engine stream BEFORE rt.engine.run_turn_stream)
    //   * after_turn_end stays {audit} (emission already completed, no
    //     honest target for block / ask)
    // The two slots therefore live in separate if-branches now.
    expect(src).toMatch(
      /lifecycle === "before_turn_start"\)\s*\{\s*return \["block", "ask", "audit"\]/,
    );
    expect(src).toMatch(
      /lifecycle === "after_turn_end"\)\s*\{\s*return \["audit"\]/,
    );
  });

  it("reseedDownstream forces toolTarget=any for the turn-boundary lifecycles", () => {
    // Turn-boundary slots have no tool layer; a stale "specific" pick must
    // not bleed into payloads / Review summaries.
    expect(src).toMatch(
      /merged\.lifecycle === "before_turn_start"[\s\S]*?merged\.lifecycle === "after_turn_end"[\s\S]*?merged\.toolTarget = "any"/,
    );
  });

  it("targetEventPhrase + whenForLifecycle describe both turn-boundary slots in plain English", () => {
    // The Review step's sentence and the ArchetypeStep header must stay
    // honest when the operator picks a turn-boundary slot.
    expect(src).toContain('"When a top-level turn starts"');
    expect(src).toContain('"When a top-level turn ends"');
  });
});


// ---------------------------------------------------------------------------
// PR-F-LIFE2 — per-LLM-call lifecycle expansion. Backend matrix in
// magi_agent/customize/custom_rules.py adds the two new firesAt slots
// (before_llm_call + after_llm_call) under (llm_criterion + audit) ONLY.
// The surrounding ADK plugin (magi_agent/adk_bridge/lifecycle_llm_call_control.py)
// enforces a per-turn critic budget (env MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET,
// default 3) so a misbehaving rule cannot multiply critic cost without
// bound. The wizard mirrors that restriction.
// ---------------------------------------------------------------------------


describe("AuthorWizard — PR-F-LIFE2 per-LLM-call lifecycle expansion", () => {
  it("Lifecycle union gains before_llm_call + after_llm_call", () => {
    expect(src).toMatch(/type Lifecycle[\s\S]*?\| "before_llm_call"/);
    expect(src).toMatch(/type Lifecycle[\s\S]*?\| "after_llm_call"/);
  });

  it("LIFECYCLE_OPTIONS lists the two new per-LLM-call slots as Tier 2", () => {
    // Both slots ride on top of the ADK before/after model callback
    // boundary via the LifecycleLlmCallAuditControl plugin — active wire,
    // not a Tier 3 file-hook-only entry.
    expect(src).toMatch(/id: "before_llm_call"[\s\S]*?tier: "tier2"/);
    expect(src).toMatch(/id: "after_llm_call"[\s\S]*?tier: "tier2"/);
  });

  it("LIFECYCLE_OPTIONS per-LLM-call descriptions telegraph PR-F-LIFE4a lift to block + audit", () => {
    // PR-F-LIFE4a lifted both slots to {audit, block}. Description must
    // explain that block short-circuits / replaces (still budget-gated).
    expect(src).toMatch(/id: "before_llm_call"[\s\S]*?block short-circuits/);
    expect(src).toMatch(/id: "after_llm_call"[\s\S]*?block REPLACES/);
  });

  it("LIFECYCLE_OPTIONS surfaces the per-turn cost ceiling in the option description", () => {
    // The operator must see the cost-ceiling story up-front so they
    // understand why a single LLM call cannot fan-out unboundedly.
    expect(src).toMatch(
      /id: "before_llm_call"[\s\S]*?capped at 3 invocations per turn/,
    );
    expect(src).toMatch(
      /id: "after_llm_call"[\s\S]*?capped at 3 invocations per turn/,
    );
  });

  it("stepPlan(per-LLM-call) drops the target step (6-step plan)", () => {
    // Per-LLM-call slots fire OUTSIDE any tool boundary so they have no
    // tool target axis — same step shape as pre_final / turn-boundary.
    expect(src).toMatch(
      /lifecycle === "before_llm_call" \|\| lifecycle === "after_llm_call"[\s\S]*?\["trigger", "condition", "specifics", "action", "name", "review"\]/,
    );
  });

  it("availableConditionKinds(per-LLM-call) returns ONLY llm_criterion", () => {
    // Backend ``_LEGAL`` has fan-out only for llm_criterion at the per-
    // LLM-call slots. deterministic_ref / mutator kinds are honest-degrade
    // omitted (no runtime consumer).
    expect(src).toMatch(
      /lifecycle === "before_llm_call" \|\| lifecycle === "after_llm_call"[\s\S]*?return \["llm_criterion"\]/,
    );
  });

  it("availableArchetypes(per-LLM-call) lifted to block + audit (PR-F-LIFE4a)", () => {
    // PR-F-LIFE4a lifted the per-call slots to {audit, block}. The same
    // per-turn critic budget that gates the audit fan-out also gates the
    // block decision (cannot block on a call the critic was never paid
    // to evaluate), so a single misbehaving rule cannot blow past the
    // cost ceiling.
    expect(src).toMatch(
      /lifecycle === "before_llm_call" \|\| lifecycle === "after_llm_call"\)\s*\{\s*return \["block", "audit"\]/,
    );
  });

  it("reseedDownstream forces toolTarget=any for the per-LLM-call lifecycles", () => {
    // Per-LLM-call slots have no tool layer; a stale "specific" pick must
    // not bleed into payloads / Review summaries.
    expect(src).toMatch(
      /merged\.lifecycle === "before_llm_call"[\s\S]*?merged\.lifecycle === "after_llm_call"[\s\S]*?merged\.toolTarget = "any"/,
    );
  });

  it("targetEventPhrase + whenForLifecycle describe both per-LLM-call slots in plain English", () => {
    expect(src).toContain('"Before each LLM call"');
    expect(src).toContain('"After each LLM call"');
  });

  it("ReviewStep target row is skipped for the per-LLM-call lifecycles", () => {
    // The Review summary must not show a Target row for slots that have
    // no tool axis — the exclusion list must include both per-LLM-call
    // lifecycles.
    expect(src).toMatch(
      /draft\.lifecycle !== "before_llm_call"[\s\S]*?draft\.lifecycle !== "after_llm_call"/,
    );
  });
});


// ---------------------------------------------------------------------------
// PR-F-LIFE3 — four NEW emitter slots: before_compaction / after_compaction /
// on_task_checkpoint / on_artifact_created. Backend matrix in
// magi_agent/customize/custom_rules.py adds the four new firesAt slots
// under (llm_criterion + audit) ONLY. The runtime sites
// (context_compaction plugin / work-queue driver / file-delivery boundary)
// are all gated by MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED. The
// wizard mirrors the restriction so an operator cannot assemble a draft
// the backend rejects.
// ---------------------------------------------------------------------------


describe("AuthorWizard — PR-F-LIFE3 four new emitter slots", () => {
  it("Lifecycle union gains all four new emitter slots", () => {
    expect(src).toMatch(/type Lifecycle[\s\S]*?\| "before_compaction"/);
    expect(src).toMatch(/type Lifecycle[\s\S]*?\| "after_compaction"/);
    expect(src).toMatch(/type Lifecycle[\s\S]*?\| "on_task_checkpoint"/);
    expect(src).toMatch(/type Lifecycle[\s\S]*?\| "on_artifact_created"/);
  });

  it("LIFECYCLE_OPTIONS lists all four new slots as Tier 2", () => {
    // All four ride on top of existing runtime chokepoints behind the
    // F-LIFE3 master flag — active wires, not Tier 3 file-hook-only.
    expect(src).toMatch(/id: "before_compaction"[\s\S]*?tier: "tier2"/);
    expect(src).toMatch(/id: "after_compaction"[\s\S]*?tier: "tier2"/);
    expect(src).toMatch(/id: "on_task_checkpoint"[\s\S]*?tier: "tier2"/);
    expect(src).toMatch(/id: "on_artifact_created"[\s\S]*?tier: "tier2"/);
  });

  it("LIFECYCLE_OPTIONS F-LIFE3 slots — PR-F-LIFE4a lifted subset per honest runtime contract", () => {
    // PR-F-LIFE4a updated three of four descriptions:
    //   * before_compaction telegraphs block (plugin SKIPs tail-drop)
    //   * after_compaction stays audit-only (already applied)
    //   * on_task_checkpoint telegraphs block (driver halts) + ask
    //   * on_artifact_created telegraphs ask only (block impossible)
    expect(src).toMatch(/id: "before_compaction"[\s\S]*?block tells the plugin to SKIP/);
    expect(src).toMatch(/id: "after_compaction"[\s\S]*?\(audit-only\)/);
    expect(src).toMatch(/id: "on_task_checkpoint"[\s\S]*?block halts further state advancement/);
    expect(src).toMatch(/id: "on_artifact_created"[\s\S]*?ask returns a delivery_intent/);
  });

  it("stepPlan(F-LIFE3 slots) drops the target step (6-step plan)", () => {
    // All four F-LIFE3 slots fire OUTSIDE the tool boundary — same step
    // shape as pre_final / turn-boundary / per-LLM-call.
    expect(src).toMatch(
      /lifecycle === "before_compaction"[\s\S]*?lifecycle === "after_compaction"[\s\S]*?lifecycle === "on_task_checkpoint"[\s\S]*?lifecycle === "on_artifact_created"[\s\S]*?\["trigger", "condition", "specifics", "action", "name", "review"\]/,
    );
  });

  it("availableConditionKinds(F-LIFE3 slots) returns ONLY llm_criterion", () => {
    // Backend ``_LEGAL`` has fan-out only for llm_criterion at the four
    // new emitter slots. deterministic_ref / tool_perm / mutator kinds are
    // honest-degrade omitted (no runtime consumer).
    expect(src).toMatch(
      /lifecycle === "before_compaction"[\s\S]*?lifecycle === "after_compaction"[\s\S]*?lifecycle === "on_task_checkpoint"[\s\S]*?lifecycle === "on_artifact_created"[\s\S]*?return \["llm_criterion"\]/,
    );
  });

  it("availableArchetypes(F-LIFE3 slots) — PR-F-LIFE4a lifts subset per honest runtime contract", () => {
    // PR-F-LIFE4a per-slot lift:
    //   * before_compaction → {audit, block} (plugin skips tail-drop)
    //   * after_compaction stays {audit} (already applied)
    //   * on_task_checkpoint → {audit, block, ask_approval} (driver halts)
    //   * on_artifact_created → {audit, ask_approval} (artifact already written)
    expect(src).toMatch(
      /lifecycle === "before_compaction"\)\s*\{\s*return \["block", "audit"\]/,
    );
    expect(src).toMatch(
      /lifecycle === "after_compaction"\)\s*\{\s*return \["audit"\]/,
    );
    expect(src).toMatch(
      /lifecycle === "on_task_checkpoint"\)\s*\{\s*return \["block", "ask", "audit"\]/,
    );
    expect(src).toMatch(
      /lifecycle === "on_artifact_created"\)\s*\{\s*return \["ask", "audit"\]/,
    );
  });

  it("reseedDownstream forces toolTarget=any for all four F-LIFE3 lifecycles", () => {
    // F-LIFE3 slots have no tool layer; a stale "specific" pick must not
    // bleed into payloads / Review summaries.
    expect(src).toMatch(
      /merged\.lifecycle === "before_compaction"[\s\S]*?merged\.lifecycle === "after_compaction"[\s\S]*?merged\.lifecycle === "on_task_checkpoint"[\s\S]*?merged\.lifecycle === "on_artifact_created"[\s\S]*?merged\.toolTarget = "any"/,
    );
  });

  it("targetEventPhrase + whenForLifecycle describe all four F-LIFE3 slots in plain English", () => {
    // The Review step's sentence and the ArchetypeStep header must stay
    // honest when the operator picks an F-LIFE3 slot.
    expect(src).toContain('"Before context compaction"');
    expect(src).toContain('"After context compaction"');
    expect(src).toContain('"On a work-queue task checkpoint"');
    expect(src).toContain('"On a newly-created artifact"');
  });

  it("ReviewStep target row is skipped for all four F-LIFE3 lifecycles", () => {
    // The Review summary must not show a Target row for the new emitter
    // slots — they have no tool axis.
    expect(src).toMatch(
      /draft\.lifecycle !== "before_compaction"[\s\S]*?draft\.lifecycle !== "after_compaction"[\s\S]*?draft\.lifecycle !== "on_task_checkpoint"[\s\S]*?draft\.lifecycle !== "on_artifact_created"/,
    );
  });
});


// ---------------------------------------------------------------------------
// PR-F-UX2 (F8 core) — RuntimeFieldChips wiring in SpecificsStep.
//
// The chip picker is rendered above every wizard text input that accepts a
// runtime variable reference (regex pattern, contentMatch pattern,
// llm_criterion criterion, SHACL TTL). Each input gets a ref so the
// insertAtCaret helper can splice the chip token at the caret.
// ---------------------------------------------------------------------------


describe("AuthorWizard — PR-F-UX2 runtime-field chip picker wiring", () => {
  it("imports RuntimeFieldChips from the colocated module", () => {
    expect(src).toContain('import { RuntimeFieldChips }');
    expect(src).toContain('from "./runtime-field-chips"');
  });

  it("ships the insertAtCaret helper for cursor-aware chip splicing", () => {
    // Mirrors the chat-input acceptSlash / acceptKb pattern: read selection
    // from the ref, splice the token, restore the caret via
    // requestAnimationFrame.
    expect(src).toContain("function insertAtCaret");
    expect(src).toContain("selectionStart");
    expect(src).toContain("selectionEnd");
    expect(src).toContain("requestAnimationFrame");
    expect(src).toContain("setSelectionRange");
  });

  it("TextField accepts an optional inputRef so SpecificsStep can read the caret", () => {
    expect(src).toContain("inputRef?: React.Ref<HTMLInputElement>");
    // The ref must be forwarded to the underlying <input>.
    expect(src).toMatch(/<input[\s\S]*?ref=\{inputRef\}/);
  });

  it("SpecificsStep declares dedicated refs for each chip-bearing input", () => {
    expect(src).toContain("regexInputRef");
    expect(src).toContain("criterionInputRef");
    expect(src).toContain("contentMatchInputRef");
    expect(src).toContain("shaclTextareaRef");
  });

  it("SpecificsStep resolves chipTool from the wizard's Target step pick", () => {
    // tool_input.* expansion needs the specific tool name; when target=any
    // the chip endpoint surfaces the generic marker + alias hints instead.
    expect(src).toContain("chipTool");
    expect(src).toMatch(/toolTarget === "specific"[\s\S]*?draft\.toolName\.trim\(\)/);
  });

  it("RuntimeFieldChips renders above the regex pattern input", () => {
    expect(src).toMatch(
      /conditionKind === "regex"[\s\S]*?<RuntimeFieldChips[\s\S]*?condition="regex"/,
    );
  });

  it("RuntimeFieldChips renders above the llm_criterion criterion input", () => {
    expect(src).toMatch(
      /<RuntimeFieldChips[\s\S]*?condition="llm_criterion"/,
    );
  });

  it("RuntimeFieldChips renders above the llm_criterion contentMatch sub-form pattern", () => {
    // contentMatch is the deterministic pre-filter pattern when the
    // operator opts in; chip the same variable menu as the regex path.
    expect(src).toMatch(
      /<RuntimeFieldChips[\s\S]*?condition="contentMatch"/,
    );
  });

  it("RuntimeFieldChips renders above the raw SHACL TTL textarea", () => {
    expect(src).toMatch(
      /conditionKind === "shacl"[\s\S]*?<RuntimeFieldChips[\s\S]*?condition="shacl"/,
    );
  });

  it("every chip insertion routes through insertAtCaret + the corresponding ref", () => {
    // The lifecycle + condition + tool tuple is repeated four times (regex,
    // criterion, contentMatch, shacl). Each must hand a unique ref to
    // insertAtCaret so caret restoration targets the right element.
    // Pattern tolerates Prettier's per-arg line break (insertAtCaret(\n  refName)).
    expect(src).toMatch(/insertAtCaret\(\s*regexInputRef/);
    expect(src).toMatch(/insertAtCaret\(\s*criterionInputRef/);
    expect(src).toMatch(/insertAtCaret\(\s*contentMatchInputRef/);
    expect(src).toMatch(/insertAtCaret\(\s*shaclTextareaRef/);
  });

  it("SpecificsStep threads draft.lifecycle into each chip picker (not a hardcoded literal)", () => {
    // The lifecycle prop must be dynamic so changing lifecycle on the
    // Trigger step refetches the right chip menu for the chosen slot.
    const occurrences = src.match(/<RuntimeFieldChips[\s\S]*?lifecycle=\{draft\.lifecycle\}/g) ?? [];
    expect(occurrences.length).toBeGreaterThanOrEqual(4);
  });
});


// ---------------------------------------------------------------------------
// PR-F-UX3 — Target merged into Trigger + tool catalog dropdown.
//
// F1.5 split tool targeting out of the Condition list into its own Target
// step. F-UX3 folds it back into the Trigger step as a third sub-fieldset
// and swaps the freeform <input> for a real catalog-driven <select> so a
// typo can no longer silently produce a no-match rule.
// ---------------------------------------------------------------------------


// ---------------------------------------------------------------------------
// PR-F-UX4 — Condition matrix loosening + auto-populate combos.
//
// F6.5 hid llm_criterion under target=specific to avoid asking the operator
// for the tool name twice; F-UX4 instead auto-derives the toolMatch list
// from draft.toolName so the combo is exposable without duplicate entry.
// Backend payload remains identical (one-tool list).
// ---------------------------------------------------------------------------


describe("AuthorWizard — PR-F-UX4 condition matrix loosening + auto-derive", () => {
  it("after_tool_use + target=specific now exposes llm_criterion (was hidden in F6.5)", () => {
    // Liberalization: the picker matrix matches the backend matrix. The
    // wizard auto-derives toolMatch from the Trigger step's tool pick so
    // the operator does not have to retype the tool name. PR-F-MUT2
    // appends ``output_rewrite`` to the same list as a Mutator entry.
    expect(src).toMatch(
      /toolTarget === "specific"\) \{[\s\S]*?return \["none", "regex", "llm_criterion", "output_rewrite"\]/,
    );
  });

  it("after_tool_use + target=any keeps offering none / regex / llm_criterion / output_rewrite", () => {
    // Symmetric matrix: both target axes expose the same condition list
    // for after_tool_use. The only difference is where the toolMatch list
    // comes from (auto-derived vs typed). PR-F-MUT2 appends
    // ``output_rewrite`` to the same list as a Mutator entry.
    expect(src).toMatch(/return \["none", "regex", "llm_criterion", "output_rewrite"\]/);
  });

  it("customRulePayload auto-derives toolMatch=[draft.toolName] when target=specific", () => {
    // The auto-derivation must sit inside the `lifecycle === "after_tool_use"`
    // branch of the llm_criterion case so pre_final stays unchanged and the
    // target=any path still consumes the user-typed llmToolMatch field.
    expect(src).toContain('draft.toolTarget === "specific" && draft.toolName.trim().length > 0');
    expect(src).toContain("? [draft.toolName.trim()]");
    expect(src).toContain(": splitToolMatchList(draft.llmToolMatch)");
  });

  it("SpecificsStep hides the llmToolMatch text input when target=specific (auto-derived path)", () => {
    // The text input must only render under target=any so the operator
    // does not see a redundant field for the same data the Trigger step
    // already supplied.
    expect(src).toMatch(
      /draft\.lifecycle === "after_tool_use" && draft\.toolTarget === "specific"[\s\S]*?Tool match \(from Trigger step\)/,
    );
    expect(src).toMatch(
      /draft\.lifecycle === "after_tool_use" && draft\.toolTarget !== "specific"[\s\S]*?value=\{draft\.llmToolMatch\}/,
    );
  });

  it("SpecificsStep renders the read-only Tool chip on the auto-derived path", () => {
    // The chip surfaces the tool name so the operator can verify what the
    // critic will fire against without surfacing an editable input.
    expect(src).toContain("Tool: {draft.toolName.trim() || \"(none)\"}");
    expect(src).toContain("Auto-derived from the Trigger step's tool pick");
  });

  it("stepIsComplete allows advancing on after_tool_use+llm_criterion+specific without llmToolMatch typed", () => {
    // The completion gate must short-circuit when target=specific so the
    // operator does not have to fill llmToolMatch (which is auto-derived
    // from draft.toolName by customRulePayload).
    expect(src).toContain('draft.toolTarget === "specific"');
    expect(src).toMatch(
      /draft\.lifecycle !== "after_tool_use"[\s\S]*?draft\.toolTarget === "specific"[\s\S]*?splitToolMatchList\(draft\.llmToolMatch\)\.length > 0/,
    );
  });

  it("before_tool_use + target=specific keeps the documented 'no AND in tool_perm' restriction", () => {
    // F-UX4 only loosens combos the backend can actually save. The
    // before_tool_use + target=specific + (domain|path) AND combo is still
    // refused because backend tool_perm.py honors a single matcher key per
    // rule — no honest mapping today. PR-F-MUT1 adds ``prompt_injection``
    // alongside ``none`` (it's a mutator, not a deny gate), but the
    // domain/path AND combo stays excluded.
    expect(src).toMatch(
      /toolTarget === "specific"\) \{[\s\S]*?return \["none", "prompt_injection"\]/,
    );
  });
});


describe("AuthorWizard — PR-F-UX3 target merge into trigger + catalog dropdown", () => {
  it("StepKey union drops 'target'", () => {
    // The standalone Target step is gone; the field union must not
    // re-introduce the literal or downstream switch arms would silently
    // become unreachable code.
    expect(src).not.toMatch(/type StepKey = [^;]*?"target"/);
    expect(src).toMatch(
      /type StepKey =\s*"trigger" \| "condition" \| "specifics" \| "action" \| "name" \| "review"/,
    );
  });

  it("stepPlan returns the same 6-step list for EVERY lifecycle (no 7-step branch)", () => {
    // pre_final branch + Tier 2 branch + tool-bearing branch all return the
    // same 6 keys; the lifecycle branching only survives so the function
    // body stays explicit about each shape.
    const sixSteps = '["trigger", "condition", "specifics", "action", "name", "review"]';
    const occurrences = (src.match(new RegExp(sixSteps.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g")) ?? []).length;
    expect(occurrences).toBeGreaterThanOrEqual(3);
    // The F1.5 7-step list must NOT appear anywhere.
    expect(src).not.toContain(
      '["trigger", "target", "condition", "specifics", "action", "name", "review"]',
    );
  });

  it("the standalone TargetStep component is deleted", () => {
    expect(src).not.toContain("function TargetStep(");
    expect(src).not.toMatch(/<TargetStep\b/);
  });

  it("the render dispatch no longer keys off currentKey === 'target'", () => {
    expect(src).not.toMatch(/currentKey === "target"/);
  });

  it("lifecycleHasToolTarget is the single predicate gating the sub-fieldset", () => {
    // Single source of truth: TriggerStep AND stepIsComplete("trigger")
    // both consult lifecycleHasToolTarget so the tool-target axis can't
    // drift between the two surfaces.
    expect(src).toContain(
      'function lifecycleHasToolTarget(lifecycle: Lifecycle): boolean',
    );
    expect(src).toMatch(
      /lifecycleHasToolTarget[\s\S]*?return lifecycle === "before_tool_use" \|\| lifecycle === "after_tool_use"/,
    );
  });

  it("TriggerStep accepts a tools prop typed ToolItem[] (catalog-driven dropdown)", () => {
    expect(src).toContain("tools: ToolItem[]");
    expect(src).toMatch(/import\s*\{[\s\S]*?type ToolItem[\s\S]*?\}\s*from\s*"@\/lib\/customize-api"/);
  });

  it("AuthorWizard threads catalog.tools through to TriggerStep", () => {
    expect(src).toMatch(/<TriggerStep[\s\S]*?tools=\{catalog\.tools\}/);
  });

  it("Tool target sub-fieldset renders ONLY when lifecycleHasToolTarget(draft.lifecycle)", () => {
    expect(src).toContain("showToolTarget");
    expect(src).toMatch(
      /const showToolTarget = lifecycleHasToolTarget\(draft\.lifecycle\)/,
    );
    expect(src).toMatch(/showToolTarget \? \(\s*\n?\s*<fieldset/);
  });

  it("Tool target sub-fieldset surfaces Any tool + Specific tool radios", () => {
    expect(src).toContain("Tool target");
    expect(src).toContain("Any tool");
    expect(src).toContain("Specific tool");
  });

  it("ToolNameSelect is a native combobox (<input list> + <datalist>) sourced from the tools prop", () => {
    // PR-F-UX3 introduced the catalog-backed dropdown; PR-F-UX7 evolved
    // it from a hard <select> to a native combobox so the operator can
    // EITHER pick a known tool from the suggestion list OR type a
    // free-text fallback (e.g. a dynamically-registered tool not yet in
    // the catalog snapshot). The <input> binds the value; the datalist
    // supplies type-ahead options sourced from the same tools prop.
    expect(src).toContain("function ToolNameSelect(");
    expect(src).toMatch(/<input[\s\S]*?list=\{listId\}[\s\S]*?value=\{value\}/);
    expect(src).toContain('<datalist id={listId}>');
    expect(src).toMatch(/sorted\.map\(\(t\) => \(?\s*\n?\s*(?:\/\/[^\n]*\n\s*)*<option key=\{t\.name\}/);
    expect(src).toContain('data-testid="tool-name-combobox"');
    // The Trigger step renders the combobox when target=specific, not
    // the F1.5 TextField. The TextField call shape used by F1.5 is gone.
    expect(src).not.toMatch(
      /<TextField\s+value=\{draft\.toolName\}[\s\S]*?placeholder="shell_exec"/,
    );
  });

  it("ToolNameSelect tolerates a stale toolName that is no longer in the catalog", () => {
    // Round-trip safety: if a saved rule references a renamed/removed
    // tool, the wizard surfaces an honest "(not in catalog)" hint
    // beneath the input instead of silently snapping back to the
    // placeholder. PR-F-UX7 replaced the F-UX3 synthetic <option> with
    // a sibling hint span — the input itself already shows the raw
    // value, so the operator can edit it directly.
    expect(src).toContain("valueInCatalog");
    expect(src).toMatch(/not in catalog/);
  });

  it("stepIsComplete('trigger') gates on lifecycle + scope + (target axis when tool-bearing)", () => {
    // Merged completion gate: non-tool-bearing lifecycles need only
    // lifecycle + scope; tool-bearing lifecycles add the
    // any-or-(specific-with-non-empty-name) check.
    expect(src).toContain('case "trigger":');
    expect(src).toContain("!draft.lifecycle || !draft.scope");
    expect(src).toContain("lifecycleHasToolTarget(draft.lifecycle)");
    expect(src).toMatch(
      /draft\.toolTarget === "any"\s*\n?\s*\|\| \(draft\.toolTarget === "specific" && draft\.toolName\.trim\(\)\.length > 0\)/,
    );
    // The old standalone case is gone.
    expect(src).not.toMatch(/case "target":/);
  });

  it("reseedDownstream resets toolTarget+toolName when lifecycle moves to a non-tool lifecycle", () => {
    // Switching from before_tool_use to pre_final must drop any
    // toolTarget="specific" pick — otherwise the Review row + payload
    // emit would lie about a tool-name filter that the backend ignores at
    // pre_final.
    expect(src).toContain("reseedDownstream");
    expect(src).toMatch(
      /merged\.lifecycle === "pre_final"[\s\S]*?merged\.lifecycle === "on_user_prompt_submit"[\s\S]*?merged\.lifecycle === "on_subagent_stop"[\s\S]*?merged\.toolTarget = "any"[\s\S]*?merged\.toolName = ""/,
    );
  });

  it("TriggerStep counts three <fieldset>s (lifecycle + scope + tool target)", () => {
    // Sanity: the third sub-fieldset is present in the wizard source so
    // the F-UX3 collapse actually happened. The Trigger step is the only
    // step rendering <fieldset>s, so the file-wide count is 3.
    expect(src.match(/<fieldset/g)?.length).toBe(3);
  });
});


describe("AuthorWizard — F-MUT1 prompt_injection kind", () => {
  it("adds prompt_injection to the ConditionKind union", () => {
    expect(src).toMatch(/type ConditionKind[\s\S]*?\| "prompt_injection"/);
  });

  it("availableConditionKinds surfaces prompt_injection at on_user_prompt_submit", () => {
    // The on_user_prompt_submit branch now returns BOTH llm_criterion AND
    // prompt_injection so the operator can pick "append a section to the
    // system prompt" without losing the audit-critic option.
    expect(src).toMatch(
      /on_user_prompt_submit"\)[\s\S]*?return \["llm_criterion", "prompt_injection"\]/,
    );
  });

  it("availableConditionKinds keeps on_subagent_stop llm_criterion-only", () => {
    // Mutation on a turn that already emitted has no honest target — the
    // backend _LEGAL matrix leaves on_subagent_stop as llm_criterion-only.
    expect(src).toMatch(
      /on_subagent_stop"\)[\s\S]*?return \["llm_criterion"\]/,
    );
  });

  it("availableConditionKinds surfaces prompt_injection on before_tool_use (both target modes)", () => {
    // target=specific: ["none", "prompt_injection"]
    expect(src).toMatch(
      /toolTarget === "specific"\)[\s\S]*?return \["none", "prompt_injection"\]/,
    );
    // target=any: domain/path matchers + prompt_injection at the tail.
    expect(src).toMatch(
      /return \["domain", "domain_allowlist", "path", "path_allowlist", "prompt_injection"\]/,
    );
  });

  it("CONDITION_META exposes a prompt_injection entry labelled as a mutator", () => {
    expect(src).toMatch(/prompt_injection: \{[\s\S]*?label: "Append context \(mutator\)"/);
    expect(src).toMatch(/prompt_injection: \{[\s\S]*?Mutator/);
  });

  it("SpecificsStep branches on lifecycle to render the right picker", () => {
    expect(src).toMatch(
      /draft\.conditionKind === "prompt_injection"[\s\S]*?lifecycle === "on_user_prompt_submit"[\s\S]*?PromptInjectionSystemPromptPicker[\s\S]*?PromptInjectionToolArgPicker/,
    );
  });

  it("ships both prompt_injection pickers as named components", () => {
    expect(src).toContain("function PromptInjectionToolArgPicker");
    expect(src).toContain("function PromptInjectionSystemPromptPicker");
  });

  it("Draft + EMPTY carry the new pi* fields", () => {
    expect(src).toMatch(/piTargetArgKey:\s*string/);
    expect(src).toMatch(/piValue:\s*string/);
    expect(src).toMatch(/piConditionEnabled:\s*boolean/);
    expect(src).toMatch(/piConditionPattern:\s*string/);
    expect(src).toMatch(/piTargetArgKey:\s*""/);
    expect(src).toMatch(/piValue:\s*""/);
    expect(src).toMatch(/piConditionEnabled:\s*false/);
    expect(src).toMatch(/piConditionPattern:\s*""/);
  });

  it("customRuleKind routes prompt_injection to its own backend kind", () => {
    expect(src).toMatch(
      /draft\.conditionKind === "prompt_injection"\)\s*return "prompt_injection"/,
    );
  });

  it("customRuleAction forces audit for prompt_injection (backend _LEGAL matrix)", () => {
    expect(src).toMatch(
      /draft\.conditionKind === "prompt_injection"\)\s*return "audit"/,
    );
  });

  it("customRulePayload emits the on_user_prompt_submit shape (target=system_prompt)", () => {
    expect(src).toMatch(
      /draft\.lifecycle === "on_user_prompt_submit"[\s\S]*?mode: "append"[\s\S]*?target: "system_prompt"/,
    );
  });

  it("customRulePayload emits the before_tool_use shape (target_arg_key + optional condition)", () => {
    expect(src).toMatch(/target_arg_key: draft\.piTargetArgKey\.trim\(\)/);
    expect(src).toMatch(/condition\.tool = draft\.toolName\.trim\(\)/);
    expect(src).toMatch(/condition\.regex = draft\.piConditionPattern\.trim\(\)/);
  });

  it("conditionClause Review summary covers both prompt_injection surfaces", () => {
    expect(src).toMatch(
      /case "prompt_injection":[\s\S]*?on_user_prompt_submit"[\s\S]*?new system-prompt section/,
    );
    expect(src).toMatch(
      /case "prompt_injection":[\s\S]*?append "\$\{draft\.piValue\}" to tool arg/,
    );
  });
});


describe("AuthorWizard — F-MUT2 output_rewrite kind", () => {
  it("adds output_rewrite to the ConditionKind union", () => {
    expect(src).toMatch(/type ConditionKind[\s\S]*?\| "output_rewrite"/);
  });

  it("availableConditionKinds surfaces output_rewrite on after_tool_use (both target modes)", () => {
    // target=specific: ["none", "regex", "llm_criterion", "output_rewrite"]
    expect(src).toMatch(
      /toolTarget === "specific"\) \{[\s\S]*?return \["none", "regex", "llm_criterion", "output_rewrite"\]/,
    );
    // target=any: same list (the toolMatch.include filter rides on the
    // payload, not the wizard's top-level Target step).
    expect(src).toMatch(
      /return \["none", "regex", "llm_criterion", "output_rewrite"\]/,
    );
  });

  it("CONDITION_META exposes an output_rewrite entry labelled as a mutator", () => {
    expect(src).toMatch(
      /output_rewrite: \{[\s\S]*?label: "Rewrite tool output \(mutator\)"/,
    );
    expect(src).toMatch(/output_rewrite: \{[\s\S]*?redact/);
  });

  it("SpecificsStep renders a dedicated branch for output_rewrite", () => {
    expect(src).toMatch(
      /draft\.conditionKind === "output_rewrite"[\s\S]*?OutputRewriteRedactPicker/,
    );
  });

  it("ships the output_rewrite picker as a named component", () => {
    expect(src).toContain("function OutputRewriteRedactPicker");
  });

  it("Draft + EMPTY carry the new or* fields with safe defaults", () => {
    expect(src).toMatch(/orPattern:\s*string/);
    expect(src).toMatch(/orReplacement:\s*string/);
    expect(src).toMatch(/orScope:\s*"match_only"\s*\|\s*"full_output"/);
    expect(src).toMatch(/orIsRegex:\s*boolean/);
    expect(src).toMatch(/orPattern:\s*""/);
    expect(src).toMatch(/orReplacement:\s*""/);
    expect(src).toMatch(/orScope:\s*"match_only"/);
    expect(src).toMatch(/orIsRegex:\s*true/);
  });

  it("customRuleKind routes output_rewrite to its own backend kind", () => {
    expect(src).toMatch(
      /draft\.conditionKind === "output_rewrite"\)\s*return "output_rewrite"/,
    );
  });

  it("customRuleAction forces audit for output_rewrite (backend _LEGAL matrix)", () => {
    expect(src).toMatch(
      /draft\.conditionKind === "output_rewrite"\)\s*return "audit"/,
    );
  });

  it("customRulePayload emits the v1 redact shape with mode locked to 'redact'", () => {
    expect(src).toMatch(
      /draft\.conditionKind === "output_rewrite"[\s\S]*?mode: "redact"[\s\S]*?pattern: draft\.orPattern\.trim\(\)[\s\S]*?replacement: draft\.orReplacement/,
    );
  });

  it("customRulePayload auto-derives toolMatch.include from draft.toolName when target=specific", () => {
    expect(src).toMatch(
      /draft\.conditionKind === "output_rewrite"[\s\S]*?toolMatch = \{ include: \[draft\.toolName\.trim\(\)\] \}/,
    );
  });

  it("stepIsComplete gates the Specifics step on pattern + replacement", () => {
    expect(src).toMatch(
      /case "output_rewrite":[\s\S]*?draft\.orPattern\.trim\(\)\.length > 0[\s\S]*?draft\.orReplacement\.length > 0/,
    );
  });

  it("conditionClause Review summary covers the output_rewrite redact surface", () => {
    expect(src).toMatch(
      /case "output_rewrite":[\s\S]*?redact \$\{verb\}.*?in tool output/,
    );
  });
});


// ---------------------------------------------------------------------------
// PR-F-MUT3 — Mutator archetype card + auto-snap conditionKind
// ---------------------------------------------------------------------------


describe("AuthorWizard — F-MUT3 'Inject / Rewrite' archetype card", () => {
  it("extends the Archetype union with 'mutate'", () => {
    // The card is a friendly grouping for the two mutator conditionKinds
    // (prompt_injection + output_rewrite). The backend customRuleKind +
    // customRuleAction wiring already routes by conditionKind, so adding
    // 'mutate' costs nothing at save time.
    expect(src).toMatch(
      /type Archetype[\s\S]*?\| "mutate"/,
    );
  });

  it("availableArchetypes(before_tool_use) appends 'mutate' for the prompt_injection card", () => {
    // before_tool_use → prompt_injection (append to tool args). The card sits
    // last so the existing block / ask / audit picks stay first in the visual
    // order operators are used to.
    expect(src).toMatch(
      /lifecycle === "before_tool_use"\) return \["block", "ask", "audit", "mutate"\]/,
    );
  });

  it("availableArchetypes(after_tool_use) appends 'mutate' for the output_rewrite card", () => {
    // after_tool_use → output_rewrite (redact tool result). Sits beside the
    // existing 'strip' card; both are surface-level "modify the output" verbs
    // but 'strip' routes to dashboard_check action=override and 'mutate'
    // routes to the new output_rewrite kind.
    expect(src).toMatch(
      /lifecycle === "after_tool_use"\) return \["block", "audit", "strip", "mutate"\]/,
    );
  });

  it("availableArchetypes(on_user_prompt_submit) exposes mutate beside block + audit", () => {
    // prompt_injection (system-prompt section append) is wired here; the
    // mutate card is the operator's entry point. PR-F-LIFE4a added "block"
    // alongside (gate fan-out short-circuits the engine stream when a
    // block-action criterion fails). on_subagent_stop keeps its own set
    // (block + ask + audit) from F-LIFE1.
    expect(src).toMatch(
      /lifecycle === "on_user_prompt_submit"\) \{[\s\S]*?return \["block", "audit", "mutate"\]/,
    );
  });

  it("availableArchetypes hides 'mutate' on pre_final and on_subagent_stop (no mutator hook)", () => {
    // pre_final has no tool boundary or system-prompt slot. on_subagent_stop
    // fires after the child has already emitted — mutation has no honest
    // target. The wizard must not surface a card the operator cannot save.
    expect(src).toMatch(
      /lifecycle === "on_subagent_stop"\) \{[\s\S]*?return \["block", "ask", "audit"\]/,
    );
    // The default-branch fallback (used by pre_final + any unknown lifecycle)
    // remains the original 3 archetypes — no mutate.
    expect(src).toMatch(/return \["block", "ask", "audit"\];\s*\n\}/);
  });

  it("ARCHETYPE_META registers a 'mutate' entry labelled 'Inject / Rewrite (mutator)'", () => {
    expect(src).toMatch(/mutate:\s*\{[\s\S]*?id:\s*"mutate"/);
    expect(src).toMatch(
      /mutate:\s*\{[\s\S]*?label:\s*"Inject \/ Rewrite \(mutator\)"/,
    );
    // The description must explicitly say "Modifies traffic" so the operator
    // sees the same wording the trust badge tooltip will surface.
    expect(src).toMatch(/mutate:\s*\{[\s\S]*?Modifies traffic/);
  });

  it("reseedDownstream snaps conditionKind=output_rewrite when archetype=mutate + after_tool_use", () => {
    // The downstream auto-snap is what makes the card a single-click entry
    // point: picking 'mutate' on after_tool_use sets conditionKind to
    // output_rewrite so SpecificsStep renders the F-MUT2 redact picker
    // immediately on the next step.
    expect(src).toMatch(
      /merged\.archetype === "mutate"[\s\S]*?lifecycle === "after_tool_use"[\s\S]*?merged\.conditionKind = "output_rewrite"/,
    );
  });

  it("reseedDownstream snaps conditionKind=prompt_injection on before_tool_use + on_user_prompt_submit", () => {
    // Both lifecycle slots share the same backend kind (prompt_injection);
    // SpecificsStep already branches on lifecycle to render the tool-arg vs
    // system-prompt picker, so the snap target is the same kind.
    expect(src).toMatch(
      /merged\.archetype === "mutate"[\s\S]*?lifecycle === "before_tool_use"[\s\S]*?lifecycle === "on_user_prompt_submit"[\s\S]*?merged\.conditionKind = "prompt_injection"/,
    );
  });

  it("reseedDownstream promotes archetype to 'mutate' when conditionKind is a mutator kind (reverse path)", () => {
    // Operator may pick prompt_injection / output_rewrite via the
    // ConditionKindStep directly (skipping the archetype card). Reverse-snap
    // archetype to 'mutate' so the Action step + Review trust badge stay
    // honest about the rule shape (otherwise the badge would say Audit /
    // Block while the rule actually mutates traffic).
    expect(src).toMatch(
      /conditionKind === "prompt_injection"[\s\S]*?conditionKind === "output_rewrite"[\s\S]*?archetypes\.includes\("mutate"\)[\s\S]*?merged\.archetype = "mutate"/,
    );
  });

  it("archetypeVerb renders a mutator-honest sentence for the Review summary", () => {
    // The verb keys off lifecycle so after_tool_use says "rewrite the tool
    // output" and the inject lifecycles say "inject context into the agent's
    // next call". No vague "mutate" verb — the operator should see WHAT the
    // mutation does in plain English.
    expect(src).toMatch(
      /case "mutate":[\s\S]*?after_tool_use"[\s\S]*?rewrite the tool output/,
    );
    expect(src).toMatch(
      /case "mutate":[\s\S]*?inject context into the agent's next call/,
    );
  });

  it("customRuleKind continues to route by conditionKind (mutate archetype does not downcast)", () => {
    // The 'mutate' archetype is a friendly grouping — the backend kind
    // routing is still keyed on conditionKind so an operator who picked the
    // card lands on prompt_injection / output_rewrite at save time. This
    // matches the comment that the precedence-protected mutator branches
    // come BEFORE any lifecycle fallback in customRuleKind.
    expect(src).toMatch(
      /draft\.conditionKind === "prompt_injection"\)\s*return "prompt_injection"/,
    );
    expect(src).toMatch(
      /draft\.conditionKind === "output_rewrite"\)\s*return "output_rewrite"/,
    );
  });
});


// ---------------------------------------------------------------------------
// F-UX-EXTRA #1 — inline preview chips on Condition picker cards.
// F-UX-EXTRA #2 — auto-fill Policy ID with manual-edit preservation.
// ---------------------------------------------------------------------------


describe("AuthorWizard — F-UX-EXTRA #1 condition preview chips", () => {
  it("declares the CONDITION_PREVIEW_CHIPS lookup keyed by ConditionKind", () => {
    expect(src).toContain(
      "const CONDITION_PREVIEW_CHIPS: Record<ConditionKind, ReadonlyArray<string>>",
    );
  });

  it("registers representative chips for the most common condition kinds", () => {
    // Tokens match the canonical names RuntimeFieldChips inserts (no `$`
    // sigil) — keeps the preview faithful to what the interactive picker
    // writes into the pattern field at SpecificsStep.
    // llm_criterion shows tool + result (after-tool critic ergonomics).
    expect(src).toMatch(/llm_criterion:\s*\["tool", "result"\]/);
    // prompt_injection shows tool_input.command (the typical mutator slot).
    expect(src).toMatch(/prompt_injection:\s*\["tool_input\.command"\]/);
    // path / path_allowlist share tool_input.path (the matcher source).
    expect(src).toMatch(/path:\s*\["tool_input\.path"\]/);
    // none condition has no chips (no per-call check to preview).
    expect(src).toMatch(/none:\s*\[\]/);
  });

  it("ConditionKindStep threads the preview chips into each RadioCard", () => {
    // The RadioCard receives previewChips={chips} so the inline preview
    // renders beneath the description without an extra fetch.
    expect(src).toContain("const chips = CONDITION_PREVIEW_CHIPS[kind]");
    expect(src).toMatch(
      /<RadioCard[\s\S]*?previewChips=\{chips\}[\s\S]*?\/>/,
    );
  });
});


describe("AuthorWizard — F-UX-EXTRA #2 auto-fill Policy ID", () => {
  it("exposes a deriveRuleId helper that composes archetype + condition + lifecycle", () => {
    expect(src).toContain("function deriveRuleId(draft: Draft): string");
    // The three axes feed the slug; the joiner is "-" so the resulting
    // ID is lower-kebab and matches the existing validator regex.
    expect(src).toMatch(
      /archetypeSlug\(draft\.archetype\)[\s\S]*?conditionSlug\(draft\.conditionKind\)[\s\S]*?lifecycleSlug\(draft\.lifecycle\)/,
    );
    // Trim to 50 chars so the ID is short enough to read in the policy
    // list (the validator caps at 128 but visual scanning wants tighter).
    expect(src).toContain(".slice(0, 50)");
  });

  it("conditionSlug folds the longer enum names into compact tokens", () => {
    expect(src).toMatch(/"llm_criterion":\s*\n\s*return "critic"/);
    expect(src).toMatch(/"prompt_injection":\s*\n\s*return "prompt-inject"/);
    expect(src).toMatch(/"field_constraint":\s*\n\s*return "field"/);
    expect(src).toMatch(/"none":\s*\n\s*return "always"/);
  });

  it("NameStep tracks userEdited and only auto-fills when untouched", () => {
    // The hook initialises from the current draft.ruleId (non-empty means
    // the operator already typed — preserve their edit on remount).
    expect(src).toContain("useState<boolean>");
    expect(src).toContain("draft.ruleId.length > 0");
    // The effect only seeds the suggested value when the operator has
    // NOT typed; manual edits flip userEdited true and stop the auto-fill.
    expect(src).toMatch(/if \(userEdited\) return/);
    expect(src).toContain("setUserEdited(true)");
  });

  it("NameStep ships a Reset to suggested affordance that clears userEdited", () => {
    expect(src).toContain('data-testid="reset-policy-id"');
    expect(src).toContain("setUserEdited(false)");
    expect(src).toContain("Reset to suggested");
  });

  it("defaultIdHint mirrors the derived ID so placeholder matches auto-fill", () => {
    // Placeholder and auto-fill must agree so the operator sees the same
    // shape whether they look at the placeholder or wait for the seed.
    expect(src).toMatch(
      /function defaultIdHint[\s\S]*?const derived = deriveRuleId\(draft\)/,
    );
  });
});


// ---------------------------------------------------------------------------
// PR-F-UX7 — Trigger step inlining + tool name combobox.
//
// Per discovery, F-UX3 already collapsed the wizard from 7→6 steps by
// inlining the Tool target sub-fieldset inside TriggerStep, so the "6→5
// collapse" premise of the spec is stale (no standalone target step
// exists). The remaining F-UX7 work is the second half: convert the
// catalog-backed <select> ToolNameSelect into a native combobox
// (<input list="tool-name-options"> + <datalist>) so the operator can
// EITHER pick a known runtime tool from the suggestion list OR type a
// free-text fallback for a dynamically-registered tool not yet in the
// catalog snapshot. Step plan stays at 6.
// ---------------------------------------------------------------------------


describe("AuthorWizard — PR-F-UX7 tool name combobox", () => {
  it("step plan stays at a constant 6 entries (F-UX3 already collapsed 7→6)", () => {
    // F-UX7's spec text mentions "6→5" but the wizard was already at 6
    // before F-UX7 (no standalone "target" step existed since F-UX3).
    // The combobox change does not move steps; assert the plan stays at
    // 6 so a future refactor does not silently bring back the 7th step.
    const sixSteps = '["trigger", "condition", "specifics", "action", "name", "review"]';
    expect(src).toContain(sixSteps);
    // The F1.5 7-step list must still NOT appear (defense in depth with
    // the F-UX3 test below).
    expect(src).not.toContain(
      '["trigger", "target", "condition", "specifics", "action", "name", "review"]',
    );
    // The 5-step variant the spec hypothesised is also absent — the
    // wizard does not shrink to 5 because the tool target axis lives
    // inside Trigger as a sub-fieldset, not as a removed step.
    expect(src).not.toContain(
      '["trigger", "condition", "specifics", "name", "review"]',
    );
  });

  it("ToolNameSelect renders a native <input list=...> combobox bound to the catalog datalist", () => {
    // Native combobox pattern: <input type="text" list="..."> +
    // <datalist id="..."><option value="X"/></datalist>. Browsers
    // surface the datalist as a type-ahead suggestion list, and the
    // input still accepts arbitrary text so a free-text fallback works
    // on day 1 (no custom Popover needed).
    expect(src).toMatch(/<input[\s\S]*?type="text"[\s\S]*?list=\{listId\}/);
    expect(src).toMatch(/value=\{value\}[\s\S]*?onChange=\{\(e\) => onChange\(e\.target\.value\)\}/);
    expect(src).toContain('<datalist id={listId}>');
    expect(src).toContain('const listId = "tool-name-options"');
  });

  it("ToolNameSelect carries data-testid='tool-name-combobox' for browser tests", () => {
    // The spec calls out the testid by name so DOM-level tests can
    // target the combobox without coupling to layout classes.
    expect(src).toContain('data-testid="tool-name-combobox"');
  });

  it("ToolNameSelect accepts free-text outside the catalog (datalist is suggestion, not validation)", () => {
    // The <datalist> only suggests; the <input> binds the raw value
    // without filtering. The validator on Next-step click
    // (stepIsComplete("trigger")) only enforces non-empty when
    // toolTarget=specific, so any typed string passes through to the
    // backend tool_perm match.tool comparison as-is.
    expect(src).toMatch(/draft\.toolTarget === "specific" && draft\.toolName\.trim\(\)\.length > 0/);
    // The synthetic "(not in catalog)" <option> from F-UX3 is gone —
    // the input already renders the raw value, so the operator can
    // edit it directly.
    expect(src).not.toMatch(
      /<option value=\{value\}>\{value\} \(not in catalog\)<\/option>/,
    );
    // Free-text safety hint surfaces beneath the input so the operator
    // understands they typed something the runtime does not currently
    // expose.
    expect(src).toContain("saved as a free-text tool name");
  });

  it("ToolNameSelect's datalist enumerates catalog tools (no <select> wrapper)", () => {
    // Suggestion options come from the same sorted catalog the F-UX3
    // dropdown used; the only structural change is the wrapper element.
    expect(src).toMatch(/sorted\.map\(\(t\) => \(?\s*\n?\s*(?:\/\/[^\n]*\n\s*)*<option key=\{t\.name\} value=\{t\.name\}/);
    // The F-UX3 <select value={value}> wrapper is gone (the suggestion
    // list lives in <datalist> instead).
    expect(src).not.toMatch(/<select\s+value=\{value\}/);
  });

  it("ToolNameSelect surfaces the dangerous-tool hint via a sibling warning chip (NOT <option label>)", () => {
    // F-UX7 review pass: Chrome/Edge ignore <option label> on
    // <datalist> entries entirely, so a label-based dangerous signal
    // was invisible on the majority browser. The chip below the input
    // is browser-portable and screen-reader-visible via
    // aria-describedby.
    expect(src).toContain('data-testid="tool-name-dangerous-warning"');
    expect(src).toMatch(/matchedDangerous = sorted\.some\(/);
    // The stored value stays a clean bare tool name (no suffix mixed
    // into option text or value).
    expect(src).not.toMatch(/const hint = t\.dangerous/);
    expect(src).not.toContain('label={hint || undefined}');
  });

  it("ToolNameSelect wires aria-describedby for both warning hints", () => {
    // Screen-reader users get the (not in catalog) + dangerous warnings
    // alongside the Tool name label, not silently dropped.
    expect(src).toContain('aria-describedby={describedBy}');
    expect(src).toContain('id={notInCatalogId}');
    expect(src).toContain('id={dangerousId}');
  });
});
