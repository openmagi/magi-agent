import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./author-wizard.tsx", import.meta.url),
  "utf8",
);

describe("AuthorWizard — variable-length policy authoring (F1.5)", () => {
  it("declares step plan as a function of lifecycle (pre_final=6, tool-bearing=7)", () => {
    // F1.5 separates tool targeting from per-call condition. pre_final has
    // no tool layer so it stays 6 steps; tool-bearing lifecycles gain a
    // dedicated "Target" step (Any tool / Specific tool) for a total of 7.
    expect(src).toContain("function stepPlan(lifecycle: Lifecycle): StepKey[]");
    expect(src).toMatch(
      /pre_final[\s\S]*?\["trigger", "condition", "specifics", "action", "name", "review"\]/,
    );
    expect(src).toContain(
      '["trigger", "target", "condition", "specifics", "action", "name", "review"]',
    );
  });

  it("ships one step body per axis: trigger / target / condition / specifics / archetype / name / review", () => {
    expect(src).toContain("TriggerStep");
    expect(src).toContain("TargetStep");
    expect(src).toContain("ConditionKindStep");
    expect(src).toContain("SpecificsStep");
    expect(src).toContain("ArchetypeStep");
    expect(src).toContain("NameStep");
    expect(src).toContain("ReviewStep");
  });

  it("TargetStep is keyed off the dynamic step plan (currentKey === 'target')", () => {
    // Target step is rendered conditionally on the StepKey enum, not a
    // hardcoded index, so pre_final can skip it.
    expect(src).toMatch(/currentKey === "target"[\s\S]*?TargetStep/);
  });

  it("Target step surfaces Any tool / Specific tool radio + tool-name input", () => {
    expect(src).toContain("Which tool(s) does this policy apply to?");
    expect(src).toContain("Any tool");
    expect(src).toContain("Specific tool");
    expect(src).toContain('toolTarget === "specific"');
  });

  it("TriggerStep renders TWO radio fieldsets (lifecycle + scope)", () => {
    expect(src.match(/<fieldset/g)?.length).toBe(2);
    expect(src).toContain("Lifecycle event");
    expect(src).toContain("Turn scope");
  });

  it("drops the disabled 'emit' archetype (audit+(no condition) covers the same outcome)", () => {
    expect(src).not.toContain("Coming soon");
    expect(src).not.toContain("Megaphone");
    expect(src).not.toMatch(/id:\s*"emit"/);
    expect(src).toContain('type Archetype = "block" | "ask" | "audit" | "strip"');
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

  it("before_tool_use + target=specific exposes ONLY 'none' (per-tool unconditional)", () => {
    // Backend tool_perm has no AND between tool name and url-shape matchers,
    // so per-tool rules can only fire unconditionally per call. The wizard
    // shrinks the option list to match.
    expect(src).toMatch(
      /toolTarget === "specific"[\s\S]*?return \["none"\]/,
    );
  });

  it("before_tool_use + target=any omits 'none' (no wildcard matcher in backend)", () => {
    // tool_perm has no wildcard, so 'no condition' with target=any has no
    // honest backend mapping. The option is omitted instead of synthesised.
    // F6 expanded the matcher list to include path + path_allowlist (the
    // backend tool_perm matcher already supports both).
    expect(src).toMatch(
      /target=any: tool_perm has no wildcard[\s\S]*?return \["domain", "domain_allowlist", "path", "path_allowlist"\]/,
    );
  });

  it("after_tool_use + target=specific omits 'llm_criterion' (use target=any + llmToolMatch field instead)", () => {
    expect(src).toMatch(
      /SpecificsStep already exposes its own `llmToolMatch`[\s\S]*?return \["none", "regex"\]/,
    );
  });

  it("after_tool_use + target=any offers none / regex / llm_criterion", () => {
    expect(src).toMatch(/return \["none", "regex", "llm_criterion"\]/);
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
    // in the backend.
    expect(src).toMatch(
      /return \["domain", "domain_allowlist", "path", "path_allowlist"\]/,
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
    expect(src).toContain("payload.toolMatch = splitToolMatchList(draft.llmToolMatch)");
    expect(src).toMatch(
      /draft\.lifecycle === "after_tool_use"[\s\S]*?payload\.toolMatch = splitToolMatchList/,
    );
  });

  it("customRulePayload OMITS toolMatch on pre_final (no tool layer)", () => {
    // pre_final llm_criterion stays byte-identical to pre-fix: the backend
    // validator does not require toolMatch outside after_tool_use, and
    // pre_final has no tool to match against.
    // Verified structurally: the toolMatch emit sits inside the
    // `if (draft.lifecycle === "after_tool_use")` block — the only branch
    // assigning payload.toolMatch.
    const matches = src.match(/payload\.toolMatch = /g) ?? [];
    expect(matches.length).toBe(1);
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
    expect(src).toContain("payload.toolMatch = splitToolMatchList(draft.llmToolMatch)");
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

  it("Tier 2 entries describe themselves as audit-only", () => {
    // Backend matrix restricts the two new slots to llm_criterion + audit;
    // the description must telegraph that contract so the operator sees
    // up-front that block isn't an option here.
    expect(src).toMatch(
      /id: "on_user_prompt_submit"[\s\S]*?Audit-only/,
    );
    expect(src).toMatch(
      /id: "on_subagent_stop"[\s\S]*?Audit-only/,
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

  it("availableArchetypes(Tier 2) returns ONLY audit", () => {
    // Mirrors the backend matrix: block/retry would change the surrounding
    // runtime contract (byte-identical prompt assembly / already-emitted
    // child output) and is deferred to a later PR.
    expect(src).toMatch(
      /lifecycle === "on_user_prompt_submit" \|\| lifecycle === "on_subagent_stop"[\s\S]*?return \["audit"\]/,
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
    expect(src).toMatch(
      /lifecycle !== "pre_final"[\s\S]*?lifecycle !== "on_user_prompt_submit"[\s\S]*?lifecycle !== "on_subagent_stop"[\s\S]*?Target/,
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
