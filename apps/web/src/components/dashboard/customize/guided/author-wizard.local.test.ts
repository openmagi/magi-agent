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

  it("after_tool_use + target=specific omits 'llm_criterion' (no per-tool filter today)", () => {
    expect(src).toMatch(
      /llm_criterion has no per-tool filter[\s\S]*?return \["none", "regex"\]/,
    );
  });

  it("after_tool_use + target=any offers none / regex / llm_criterion", () => {
    expect(src).toMatch(/return \["none", "regex", "llm_criterion"\]/);
  });

  it("pre_final ignores target and returns evidence_ref / shacl / llm_criterion (+ field_constraint per F3)", () => {
    // F3 appends `field_constraint` as the preferred deterministic SHACL
    // option for pre_final; existing kinds preserved.
    expect(src).toMatch(
      /pre_final[\s\S]*?return \["evidence_ref", "shacl", "llm_criterion", "field_constraint"\]/,
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
