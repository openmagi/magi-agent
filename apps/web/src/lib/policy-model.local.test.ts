import { describe, expect, it } from "vitest";

import {
  extractBuiltinJudgmentRefs,
  extractEvidenceTypes,
  extractNamedConditions,
  trustClassForPolicy,
  unifyRuleRows,
  type RuleRow,
  type PolicyConditionKind,
} from "./policy-model";


function buildCatalog(): Parameters<typeof unifyRuleRows>[0]["catalog"] {
  return {
    verification: {
      recipes: [],
      harnessPresets: [
        {
          id: "coding-verification",
          title: "Coding Verification",
          category: "coding",
          domain: "coding",
          hookPoints: ["pre_final"],
          description: "Require fresh test-pass evidence before final answer.",
          tier: "deterministic",
          optMethod: "opt-out",
          defaultEnabled: true,
          enforcement: "enforcing",
          supportedModes: ["deterministic"],
        },
        {
          id: "dangerous-patterns",
          title: "Dangerous Patterns",
          category: "security",
          domain: "always-on",
          hookPoints: ["before_tool_use"],
          description: "Block dangerous shell commands.",
          tier: "always-on",
          optMethod: null,
          defaultEnabled: true,
          enforcement: "always-on",
          supportedModes: ["deterministic"],
        },
      ],
      hooks: [],
      customRuleMenu: [],
      // PR-F-UX5 — evidence vs verdict split surfaced as two catalog fields;
      // legacy customRuleMenu retained as the union for back-compat.
      evidenceMenu: [],
      judgmentMenu: [],
    },
    tools: [],
    controlPlane: [],
    builtinPolicies: [],
  };
}


function buildOverrides(): Parameters<typeof unifyRuleRows>[0]["overrides"] {
  return {
    verification: {
      recipes: [],
      harness_presets: [],
      preset_overrides: { "coding-verification": false },
      hooks: {},
      modes: {},
      custom_rules: [
        {
          id: "cr_block_shell",
          scope: "always",
          enabled: true,
          firesAt: "before_tool_use",
          action: "block",
          what: {
            kind: "tool_perm",
            payload: { match: { tool: "shell_exec" }, decision: "deny" },
          },
        },
      ],
      seam_specs: [
        {
          id: "seam_a",
          spec_version: "0.1",
          actions: [
            { op: "modify_seam", preset_id: "coding-verification", wiring: "opt_in" },
          ],
        },
      ],
    },
    tools: {},
    user_rules: "",
    control_plane: {},
    builtin_policies: {},
  };
}


describe("unifyRuleRows — merges all four backend stores into a single RuleRow[]", () => {
  it("includes one entry per built-in preset, one per custom rule, one per dashboard check, one per SeamSpec action", () => {
    const policies = unifyRuleRows({
      catalog: buildCatalog(),
      overrides: buildOverrides(),
      dashboardChecks: [
        {
          id: "blk-secrets",
          label: "Block AWS access keys",
          scope: "always",
          enabled: true,
          trigger: { tool: "fetch_url", match: { pattern: "AKIA[0-9A-Z]{16}", isRegex: true } },
          action: "block",
        },
      ],
    });
    // 2 presets + 1 custom rule + 1 seam action + 1 dashboard check = 5
    expect(policies).toHaveLength(5);
    const sources = policies.map((p) => p.source);
    expect(sources).toContain("preset_seam");
    expect(sources).toContain("custom_rule");
    expect(sources).toContain("seam_spec");
    expect(sources).toContain("dashboard_check");
  });

  it("respects preset_overrides — coding-verification toggled OFF surfaces as disabled", () => {
    const policies = unifyRuleRows({
      catalog: buildCatalog(),
      overrides: buildOverrides(),
      dashboardChecks: [],
    });
    const coding = policies.find(
      (p) => p.source === "preset_seam" && p.name === "Coding Verification",
    );
    expect(coding?.state).toBe("disabled");
  });

  it("renders security presets with state=always-on and togglable=false", () => {
    const policies = unifyRuleRows({
      catalog: buildCatalog(),
      overrides: buildOverrides(),
      dashboardChecks: [],
    });
    const security = policies.find((p) => p.name === "Dangerous Patterns");
    expect(security?.state).toBe("always-on");
    expect(security?.togglable).toBe(false);
    expect(security?.deletable).toBe(false);
  });

  it("marks user policies as togglable + deletable", () => {
    const policies = unifyRuleRows({
      catalog: buildCatalog(),
      overrides: buildOverrides(),
      dashboardChecks: [],
    });
    const custom = policies.find((p) => p.source === "custom_rule");
    expect(custom?.togglable).toBe(true);
    expect(custom?.deletable).toBe(true);
  });

  it("renders SeamSpec actions as one row per action with togglable=false", () => {
    const policies = unifyRuleRows({
      catalog: buildCatalog(),
      overrides: buildOverrides(),
      dashboardChecks: [],
    });
    const seam = policies.find((p) => p.source === "seam_spec");
    expect(seam?.togglable).toBe(false);
    expect(seam?.deletable).toBe(true);
  });
});


describe("extractEvidenceTypes — auto-derived from policy list", () => {
  it("collects evidence refs from custom_rule deterministic_ref payloads", () => {
    const policies: RuleRow[] = [
      {
        id: "custom_rule:r1",
        name: "r1",
        description: "",
        origin: "user",
        source: "custom_rule",
        state: "enabled",
        when: { scope: "coding", firesAt: "pre_final" },
        condition: {
          kind: "evidence_ref",
          summary: "Requires evidence: evidence:test-run",
          payload: { ref: "evidence:test-run" },
        },
        action: "block",
        togglable: true,
        editable: true,
        deletable: true,
        rawSource: {
          kind: "custom_rule",
          rule: {
            id: "r1",
            scope: "coding",
            enabled: true,
            firesAt: "pre_final",
            action: "block",
            what: { kind: "deterministic_ref", payload: { ref: "evidence:test-run" } },
          },
        },
      },
    ];
    const entries = extractEvidenceTypes(policies);
    const target = entries.find((e) => e.ref === "evidence:test-run");
    expect(target).toBeDefined();
    expect(target?.consumedBy).toEqual(["custom_rule:r1"]);
    expect(target?.origin).toBe("user");
  });

  it("returns an empty list when no policies reference evidence", () => {
    const entries = extractEvidenceTypes([]);
    expect(entries).toEqual([]);
  });

  it("does NOT derive fake preset:<id> entries from preset_seam policies (F2.5)", () => {
    // Regression: a prior implementation invented a `preset:<id>` evidence
    // entry per built-in preset_seam policy under the comment "Surface the
    // preset id itself as a known 'rule' name so users see the inventory".
    // Presets are POLICIES (gates), not evidence emitters; the false
    // derivation made the Evidence sub-tab a near-duplicate of the
    // Policies sub-tab (38/38 matching counts) with CONSUMED-BY-0 /
    // PRODUCED-BY-0 on every row. The real catalog of emit-able types
    // comes from /v1/app/customize/evidence/live-catalog (F2); this
    // function is now the per-ref consumer index only.
    const policies: RuleRow[] = [
      {
        id: "preset_seam:answer-quality",
        name: "Answer Quality",
        description: "",
        origin: "builtin",
        source: "preset_seam",
        state: "disabled",
        when: { scope: "delivery", firesAt: "pre_final" },
        condition: { kind: "preset", summary: "" },
        action: "block",
        togglable: true,
        editable: false,
        deletable: false,
        rawSource: {
          kind: "preset_seam",
          preset: {
            id: "answer-quality",
            label: "Answer Quality",
            description: "",
            category: "delivery",
            enabled: false,
            mode: "block",
          } as unknown as RuleRow["rawSource"]["preset"],
        },
      },
    ];
    const entries = extractEvidenceTypes(policies);
    expect(entries.find((e) => e.ref === "preset:answer-quality")).toBeUndefined();
    expect(entries).toEqual([]);
  });
});


describe("extractNamedConditions — user-defined reusable condition payloads", () => {
  it("captures shacl_constraint / llm_criterion / regex / tool_perm conditions", () => {
    const policies = unifyRuleRows({
      catalog: buildCatalog(),
      overrides: buildOverrides(),
      dashboardChecks: [
        {
          id: "blk-secrets",
          label: "Block AWS access keys",
          scope: "always",
          enabled: true,
          trigger: { tool: "fetch_url", match: { pattern: "AKIA[0-9A-Z]{16}", isRegex: true } },
          action: "block",
        },
      ],
    });
    const conditions = extractNamedConditions(policies);
    const kinds = conditions.map((c) => c.kind);
    expect(kinds).toContain("tool_perm");
    expect(kinds).toContain("regex");
  });

  it("skips built-in policies (they have implicit conditions)", () => {
    const policies = unifyRuleRows({
      catalog: buildCatalog(),
      overrides: buildOverrides(),
      dashboardChecks: [],
    });
    const conditions = extractNamedConditions(policies);
    // Only user-origin conditions, never built-in preset conditions.
    for (const c of conditions) {
      expect(c.origin).toBe("user");
    }
  });
});


// ---------------------------------------------------------------------------
// PR-F5 — trust-class derivation
// ---------------------------------------------------------------------------


/** Build a minimal :class:`Policy` with the condition kind under test. The
 *  rest of the fields are filled with safe defaults so the kind branch is
 *  the only thing under test. */
function buildPolicy(args: {
  kind: PolicyConditionKind;
  action?: string;
  state?: RuleRow["state"];
  source?: RuleRow["source"];
}): RuleRow {
  const source = args.source ?? "custom_rule";
  return {
    id: `${source}:fixture`,
    name: "fixture",
    description: "",
    origin: source === "preset_seam" ? "builtin" : "user",
    source,
    state: args.state ?? "enabled",
    when: { scope: "always", firesAt: "pre_final" },
    condition: { kind: args.kind, summary: "" },
    action: args.action ?? "block",
    togglable: true,
    editable: true,
    deletable: true,
    rawSource: { kind: "custom_rule", rule: {
      id: "fixture",
      scope: "always",
      enabled: true,
      firesAt: "pre_final",
      action: "block",
      what: { kind: args.kind, payload: {} },
    } },
  };
}


// ---------------------------------------------------------------------------
// PR-F-UX5 — extractBuiltinJudgmentRefs derives Conditions-tab rows from
// the catalog's judgmentMenu so built-in verifier primitives appear alongside
// user-authored named conditions under an origin badge.
// ---------------------------------------------------------------------------


describe("extractBuiltinJudgmentRefs — derives built-in entries from catalog.judgmentMenu", () => {
  function buildCatalogWithJudgmentMenu(
    items: Array<{
      ref: string;
      label: string;
      evidenceType: string;
      tier: string;
      firesAt: string;
      allowedActions: string[];
    }>,
  ): Parameters<typeof extractBuiltinJudgmentRefs>[0] {
    const cat = buildCatalog();
    cat.verification.judgmentMenu = items;
    return cat;
  }

  it("returns an entry per judgmentMenu item with origin=builtin", () => {
    const cat = buildCatalogWithJudgmentMenu([
      {
        ref: "verifier:dev-coding:test-evidence",
        label: "Tests pass after a code change",
        evidenceType: "TestRun",
        tier: "deterministic",
        firesAt: "pre_final",
        allowedActions: ["block", "retry", "audit"],
      },
      {
        ref: "fact_grounding",
        label: "Factual values are grounded",
        evidenceType: "FactGrounding",
        tier: "deterministic",
        firesAt: "pre_final",
        allowedActions: ["block", "retry", "audit"],
      },
    ]);
    const out = extractBuiltinJudgmentRefs(cat);
    expect(out).toHaveLength(2);
    for (const entry of out) {
      expect(entry.origin).toBe("builtin");
      // Conditions tab uses ownerPolicyName as the inline reference; for a
      // built-in row it should be the bare ref so the operator can copy it.
      expect(entry.ownerPolicyName).toBe(entry.payload?.ref);
    }
    expect(out.map((e) => e.payload?.ref)).toEqual([
      "verifier:dev-coding:test-evidence",
      "fact_grounding",
    ]);
  });

  it("returns an empty list when the judgmentMenu is empty", () => {
    const cat = buildCatalogWithJudgmentMenu([]);
    expect(extractBuiltinJudgmentRefs(cat)).toEqual([]);
  });

  it("each entry carries kind=evidence_ref (storage shape parity with deterministic_ref)", () => {
    // Backend persists both wizard kinds (evidence_ref + verifier_passed) as
    // ``deterministic_ref``; the frontend renames it ``evidence_ref`` at the
    // adapter boundary. Built-in judgment rows mirror that to keep the
    // Conditions tab labelling consistent.
    const cat = buildCatalogWithJudgmentMenu([
      {
        ref: "verifier:research-source-evidence",
        label: "At least one source was actually inspected",
        evidenceType: "SourceLedger",
        tier: "deterministic",
        firesAt: "pre_final",
        allowedActions: ["block", "retry", "audit"],
      },
    ]);
    const [entry] = extractBuiltinJudgmentRefs(cat);
    expect(entry.kind).toBe("evidence_ref");
  });
});


describe("trustClassForPolicy — verified mapping table (PR-F5)", () => {
  // ---- Deterministic kinds ------------------------------------------------
  it("maps evidence_ref → deterministic (frontend rename of backend deterministic_ref)", () => {
    expect(trustClassForPolicy(buildPolicy({ kind: "evidence_ref" }))).toBe(
      "deterministic",
    );
  });

  it("maps shacl_constraint → deterministic (also covers field_constraint, lifted at transport)", () => {
    expect(trustClassForPolicy(buildPolicy({ kind: "shacl_constraint" }))).toBe(
      "deterministic",
    );
  });

  it("maps tool_perm → deterministic", () => {
    expect(trustClassForPolicy(buildPolicy({ kind: "tool_perm" }))).toBe(
      "deterministic",
    );
  });

  it("maps seam_action → deterministic (built-in preset rewire)", () => {
    expect(
      trustClassForPolicy(
        buildPolicy({ kind: "seam_action", source: "seam_spec" }),
      ),
    ).toBe("deterministic");
  });

  // ---- Advisory kind ------------------------------------------------------
  it("maps llm_criterion → advisory (LLM critic, results vary)", () => {
    expect(trustClassForPolicy(buildPolicy({ kind: "llm_criterion" }))).toBe(
      "advisory",
    );
  });

  // ---- regex (dashboard_check) — action distinguisher ---------------------
  it("maps regex action=block → deterministic (current dashboard_check)", () => {
    expect(
      trustClassForPolicy(
        buildPolicy({
          kind: "regex",
          action: "block",
          source: "dashboard_check",
        }),
      ),
    ).toBe("deterministic");
  });

  it("maps regex action=audit → deterministic (observability only)", () => {
    expect(
      trustClassForPolicy(
        buildPolicy({
          kind: "regex",
          action: "audit",
          source: "dashboard_check",
        }),
      ),
    ).toBe("deterministic");
  });

  it("maps regex action=override → hybrid (forward-compat: deterministic match + transform)", () => {
    // No backend action emits "override" today (the type is the closed set
    // { block, audit }); the branch is wired so a forthcoming strip /
    // redact / override action lights up Hybrid automatically.
    expect(
      trustClassForPolicy(
        buildPolicy({
          kind: "regex",
          action: "override",
          source: "dashboard_check",
        }),
      ),
    ).toBe("hybrid");
  });

  // ---- none kind (built-in preset_seam) -----------------------------------
  it("maps none + state=preview → preview (shipped-but-not-wired preset)", () => {
    expect(
      trustClassForPolicy(
        buildPolicy({
          kind: "none",
          state: "preview",
          source: "preset_seam",
        }),
      ),
    ).toBe("preview");
  });

  it("maps none + state=enabled → deterministic (built-in enforcing preset)", () => {
    expect(
      trustClassForPolicy(
        buildPolicy({
          kind: "none",
          state: "enabled",
          source: "preset_seam",
        }),
      ),
    ).toBe("deterministic");
  });

  it("maps none + state=always-on → deterministic (security preset)", () => {
    expect(
      trustClassForPolicy(
        buildPolicy({
          kind: "none",
          state: "always-on",
          source: "preset_seam",
        }),
      ),
    ).toBe("deterministic");
  });
});
