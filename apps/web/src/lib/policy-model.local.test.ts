import { describe, expect, it } from "vitest";

import {
  extractEvidenceTypes,
  extractNamedConditions,
  unifyPolicies,
  type Policy,
} from "./policy-model";


function buildCatalog(): Parameters<typeof unifyPolicies>[0]["catalog"] {
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
    },
    tools: [],
  };
}


function buildOverrides(): Parameters<typeof unifyPolicies>[0]["overrides"] {
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
  };
}


describe("unifyPolicies — merges all four backend stores into a single Policy[]", () => {
  it("includes one entry per built-in preset, one per custom rule, one per dashboard check, one per SeamSpec action", () => {
    const policies = unifyPolicies({
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
    const policies = unifyPolicies({
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
    const policies = unifyPolicies({
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
    const policies = unifyPolicies({
      catalog: buildCatalog(),
      overrides: buildOverrides(),
      dashboardChecks: [],
    });
    const custom = policies.find((p) => p.source === "custom_rule");
    expect(custom?.togglable).toBe(true);
    expect(custom?.deletable).toBe(true);
  });

  it("renders SeamSpec actions as one row per action with togglable=false", () => {
    const policies = unifyPolicies({
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
    const policies: Policy[] = [
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
});


describe("extractNamedConditions — user-defined reusable condition payloads", () => {
  it("captures shacl_constraint / llm_criterion / regex / tool_perm conditions", () => {
    const policies = unifyPolicies({
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
    const policies = unifyPolicies({
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
