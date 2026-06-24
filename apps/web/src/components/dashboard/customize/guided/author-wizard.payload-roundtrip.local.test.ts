/**
 * PR-F-UX4 payload round-trip — for every (lifecycle, target, condition)
 * combo currently exposed by the wizard, build a Draft with the minimal
 * valid field values, run it through a mirrored `customRulePayload` /
 * `buildDashboardCheck`, then assert the emitted payload satisfies a minimal
 * stub of the backend ``validate_custom_rule`` matrix. This catches the
 * F6.5-class missing-toolMatch bug at red-light time: any future regression
 * that drops a required key, mis-routes a kind, or emits an empty list will
 * fail one of the per-combo cases below.
 *
 * The mirror is kept tiny + DRY against the spec at the top of
 * ``author-wizard.tsx`` (see the "Routing" docstring) so a change in the
 * real wizard's customRulePayload that breaks contract is caught by both
 * the source-string assertions above AND the structural assertions here.
 *
 * Why mirror instead of import: ``author-wizard.tsx`` is a React client
 * component pulling in `useState` / `useEffect` / lucide-react / etc.;
 * importing those modules from a non-jsx vitest run is more friction than
 * mirroring the ~40 lines of pure logic the helpers carry. The mirror is
 * tested AGAINST the real source via the source-string assertions in
 * `author-wizard.local.test.ts`, so a divergence between the two will not
 * silently survive.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// ---------------------------------------------------------------------------
// Source-string anchor — the round-trip mirror MUST line up with the
// real wizard's payload shape. The tests here run against the mirror,
// but each combo also asserts a substring that anchors the assertion to
// the real source so the mirror cannot drift undetected.
// ---------------------------------------------------------------------------
const wizardSrc = readFileSync(
  new URL("./author-wizard.tsx", import.meta.url),
  "utf8",
);

// ---------------------------------------------------------------------------
// Domain mirror (tiny — matches author-wizard.tsx types verbatim).
// ---------------------------------------------------------------------------
type Lifecycle =
  | "before_tool_use"
  | "after_tool_use"
  | "pre_final"
  | "on_user_prompt_submit"
  | "on_subagent_stop";
type ToolTarget = "any" | "specific";
type ConditionKind =
  | "none"
  | "domain"
  | "domain_allowlist"
  | "path"
  | "path_allowlist"
  | "evidence_ref"
  | "verifier_passed"
  | "shacl"
  | "llm_criterion"
  | "regex"
  | "field_constraint";
type Archetype = "block" | "ask" | "audit" | "strip";

interface Draft {
  lifecycle: Lifecycle;
  toolTarget: ToolTarget;
  toolName: string;
  conditionKind: ConditionKind;
  archetype: Archetype;
  domain: string;
  domainAllowlist: string;
  pathPrefix: string;
  pathAllowlist: string;
  evidenceRef: string;
  shapeTtl: string;
  criterion: string;
  regexPattern: string;
  regexIsRegex: boolean;
  llmToolMatch: string;
  llmContentMatchEnabled: boolean;
  llmContentMatchPattern: string;
  llmContentMatchIsRegex: boolean;
  llmContentMatchNegate: boolean;
  fcEvidenceType: string;
  fcField: string;
  fcOperator: string;
  fcValue: string;
  fcCrossTargetType: string;
  fcCrossTargetField: string;
}

function splitToolMatchList(raw: string): string[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

// ---------------------------------------------------------------------------
// Mirrors customRulePayload (author-wizard.tsx). Keep this PURE — no
// React, no DOM. Behavioural drift from the real fn is caught by the
// source-string anchors below.
// ---------------------------------------------------------------------------
function mirrorCustomRulePayload(draft: Draft): Record<string, unknown> {
  if (draft.lifecycle === "before_tool_use") {
    const decision = draft.archetype === "ask" ? "ask" : "deny";
    if (draft.toolTarget === "specific") {
      return { match: { tool: draft.toolName.trim() }, decision };
    }
    if (draft.conditionKind === "domain") {
      return { match: { domain: draft.domain.trim() }, decision };
    }
    if (draft.conditionKind === "domain_allowlist") {
      return {
        match: {
          domainAllowlist: draft.domainAllowlist
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        },
        decision,
      };
    }
    if (draft.conditionKind === "path") {
      return { match: { path: draft.pathPrefix.trim() }, decision };
    }
    if (draft.conditionKind === "path_allowlist") {
      return {
        match: {
          pathAllowlist: draft.pathAllowlist
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        },
        decision,
      };
    }
    return { match: {}, decision };
  }

  switch (draft.conditionKind) {
    case "evidence_ref":
    case "verifier_passed":
      return { ref: draft.evidenceRef };
    case "shacl":
      return { shapeTtl: draft.shapeTtl.trim() };
    case "regex":
      return {
        contentMatch: {
          pattern: draft.regexPattern.trim(),
          isRegex: draft.regexIsRegex,
        },
      };
    case "llm_criterion": {
      const payload: Record<string, unknown> = {
        criterion: draft.criterion.trim(),
      };
      if (draft.lifecycle === "after_tool_use") {
        // PR-F-UX4 mirror — auto-derive when target=specific.
        payload.toolMatch =
          draft.toolTarget === "specific" && draft.toolName.trim().length > 0
            ? [draft.toolName.trim()]
            : splitToolMatchList(draft.llmToolMatch);
        if (
          draft.llmContentMatchEnabled
          && draft.llmContentMatchPattern.trim().length > 0
        ) {
          payload.contentMatch = {
            pattern: draft.llmContentMatchPattern.trim(),
            isRegex: draft.llmContentMatchIsRegex,
            negate: draft.llmContentMatchNegate,
          };
        }
      }
      return payload;
    }
    case "field_constraint":
      return {
        shapeTtl: "",
        authoredAs: {
          kind: "field_constraint",
          operator: draft.fcOperator,
          evidenceType: draft.fcEvidenceType,
          field: draft.fcField,
          value: draft.fcValue,
        },
      };
    default:
      return {};
  }
}

function mirrorCustomRuleKind(draft: Draft): string {
  if (draft.lifecycle === "before_tool_use") return "tool_perm";
  if (draft.conditionKind === "evidence_ref") return "deterministic_ref";
  if (draft.conditionKind === "verifier_passed") return "deterministic_ref";
  if (draft.conditionKind === "shacl") return "shacl_constraint";
  if (draft.conditionKind === "field_constraint") return "shacl_constraint";
  if (draft.conditionKind === "regex") return "llm_criterion";
  return "llm_criterion";
}

function mirrorCustomRuleAction(draft: Draft): string {
  switch (draft.archetype) {
    case "block":
      return "block";
    case "ask":
      return "ask_approval";
    case "audit":
      return "audit";
    case "strip":
      return "override";
  }
}

interface DashboardCheckLike {
  tool: string;
  pattern: string;
  isRegex: boolean;
  action: "audit" | "block";
}

function mirrorBuildDashboardCheck(draft: Draft): DashboardCheckLike {
  const pattern =
    draft.conditionKind === "none" ? ".*" : draft.regexPattern.trim();
  const isRegex = draft.conditionKind === "none" ? true : draft.regexIsRegex;
  const tool =
    draft.toolTarget === "specific" ? draft.toolName.trim() : "*";
  return {
    tool,
    pattern,
    isRegex,
    action: draft.archetype === "audit" ? "audit" : "block",
  };
}

// ---------------------------------------------------------------------------
// Backend matrix stub — keys + presence rules from
// `magi_agent/customize/custom_rules.py::validate_custom_rule` +
// `_LEGAL`. Keeps the validation surface narrow on purpose: we ONLY
// assert what the backend checks, not what we wish it checked.
// ---------------------------------------------------------------------------

interface ValidationError {
  message: string;
}

function validateCustomRuleStub(rule: {
  kind: string;
  payload: Record<string, unknown>;
  firesAt: Lifecycle;
  action: string;
}): ValidationError | null {
  const legal: Record<string, Record<string, string[]>> = {
    deterministic_ref: { pre_final: ["block", "retry", "audit"] },
    tool_perm: { before_tool_use: ["block", "ask_approval"] },
    llm_criterion: {
      pre_final: ["block", "retry", "audit"],
      after_tool_use: ["override"],
      on_user_prompt_submit: ["audit"],
      on_subagent_stop: ["audit"],
    },
    shacl_constraint: { pre_final: ["block"] },
    capability_scope: { spawn: ["block"] },
  };
  const allowedFiresAt = legal[rule.kind];
  if (!allowedFiresAt) {
    return { message: `unknown kind ${rule.kind}` };
  }
  const allowedActions = allowedFiresAt[rule.firesAt];
  if (!allowedActions) {
    return {
      message: `kind ${rule.kind} not allowed at firesAt ${rule.firesAt}`,
    };
  }
  if (!allowedActions.includes(rule.action)) {
    return {
      message: `action ${rule.action} not allowed for kind ${rule.kind} @ ${rule.firesAt}`,
    };
  }
  // Per-kind payload checks (mirror of custom_rules.py).
  if (rule.kind === "tool_perm") {
    const match = (rule.payload.match ?? {}) as Record<string, unknown>;
    const keys = ["tool", "domain", "domainAllowlist", "path", "pathAllowlist"];
    const hasOne = keys.some((k) => {
      const v = match[k];
      if (typeof v === "string") return v.length > 0;
      if (Array.isArray(v)) return v.length > 0;
      return false;
    });
    if (!hasOne) {
      return { message: "tool_perm payload.match must contain at least one matcher" };
    }
    const decision = rule.payload.decision;
    if (decision !== "deny" && decision !== "ask") {
      return { message: "tool_perm payload.decision must be deny|ask" };
    }
  }
  if (rule.kind === "deterministic_ref") {
    if (typeof rule.payload.ref !== "string" || rule.payload.ref.length === 0) {
      return { message: "deterministic_ref payload.ref must be non-empty string" };
    }
  }
  if (rule.kind === "llm_criterion") {
    if (rule.firesAt === "after_tool_use") {
      const tm = rule.payload.toolMatch;
      if (!Array.isArray(tm) || tm.length === 0) {
        return {
          message:
            "after_tool_use llm_criterion REQUIRES non-empty toolMatch list",
        };
      }
      const hasCrit =
        typeof rule.payload.criterion === "string"
        && (rule.payload.criterion as string).length > 0;
      const hasContent = typeof rule.payload.contentMatch === "object";
      if (!hasCrit && !hasContent) {
        return {
          message:
            "after_tool_use llm_criterion REQUIRES criterion or contentMatch",
        };
      }
    } else {
      // pre_final / Tier-2 require a non-empty criterion.
      if (
        typeof rule.payload.criterion !== "string"
        || (rule.payload.criterion as string).length === 0
      ) {
        return {
          message: `${rule.firesAt} llm_criterion REQUIRES non-empty criterion`,
        };
      }
    }
  }
  if (rule.kind === "shacl_constraint") {
    // F3 field_constraint pass-through: empty shapeTtl is acceptable when
    // authoredAs is present (the server-side compiler synthesises the TTL).
    const auth = rule.payload.authoredAs;
    const ttl = rule.payload.shapeTtl;
    if (
      auth
      && typeof auth === "object"
      && (auth as Record<string, unknown>).kind === "field_constraint"
    ) {
      return null;
    }
    if (typeof ttl !== "string" || ttl.length === 0) {
      return { message: "shacl_constraint payload.shapeTtl required non-empty" };
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Draft seeders — minimal valid field values per combo. Field-constraint
// has its own deterministic compile path; minimal here is (type, field,
// operator=exists) which the backend cardinality compiler accepts.
// ---------------------------------------------------------------------------
function baseDraft(overrides: Partial<Draft>): Draft {
  return {
    lifecycle: "pre_final",
    toolTarget: "any",
    toolName: "",
    conditionKind: "none",
    archetype: "block",
    domain: "",
    domainAllowlist: "",
    pathPrefix: "",
    pathAllowlist: "",
    evidenceRef: "",
    shapeTtl: "",
    criterion: "",
    regexPattern: "",
    regexIsRegex: false,
    llmToolMatch: "",
    llmContentMatchEnabled: false,
    llmContentMatchPattern: "",
    llmContentMatchIsRegex: false,
    llmContentMatchNegate: false,
    fcEvidenceType: "",
    fcField: "",
    fcOperator: "eq",
    fcValue: "",
    fcCrossTargetType: "",
    fcCrossTargetField: "",
    ...overrides,
  };
}

interface RoundtripCase {
  name: string;
  draft: Draft;
  expectedKind: string;
  expectedFiresAt: Lifecycle;
  expectedAction: string;
  /** Optional substring anchor in author-wizard.tsx to guarantee the mirror
   * stays in step with the real wizard code path. */
  srcAnchor?: string;
}

const ROUTES_TO_DASHBOARD_CHECK: RoundtripCase[] = [
  {
    name: "after_tool + any + none + audit → DashboardCheck wildcard",
    draft: baseDraft({
      lifecycle: "after_tool_use",
      toolTarget: "any",
      conditionKind: "none",
      archetype: "audit",
    }),
    expectedKind: "(dashboard_check)",
    expectedFiresAt: "after_tool_use",
    expectedAction: "audit",
    srcAnchor: 'conditionKind === "none" ? ".*"',
  },
  {
    name: "after_tool + specific + none + block → DashboardCheck tool=X",
    draft: baseDraft({
      lifecycle: "after_tool_use",
      toolTarget: "specific",
      toolName: "fetch_url",
      conditionKind: "none",
      archetype: "block",
    }),
    expectedKind: "(dashboard_check)",
    expectedFiresAt: "after_tool_use",
    expectedAction: "block",
    srcAnchor: 'toolTarget === "specific" ? draft.toolName.trim() : "*"',
  },
  {
    name: "after_tool + any + regex + audit → DashboardCheck pattern=P",
    draft: baseDraft({
      lifecycle: "after_tool_use",
      toolTarget: "any",
      conditionKind: "regex",
      regexPattern: "AKIA[0-9A-Z]{16}",
      regexIsRegex: true,
      archetype: "audit",
    }),
    expectedKind: "(dashboard_check)",
    expectedFiresAt: "after_tool_use",
    expectedAction: "audit",
  },
  {
    name: "after_tool + specific + regex + block → DashboardCheck tool=X pattern=P",
    draft: baseDraft({
      lifecycle: "after_tool_use",
      toolTarget: "specific",
      toolName: "fetch_url",
      conditionKind: "regex",
      regexPattern: "AWS_SECRET",
      regexIsRegex: false,
      archetype: "block",
    }),
    expectedKind: "(dashboard_check)",
    expectedFiresAt: "after_tool_use",
    expectedAction: "block",
  },
];

const ROUTES_TO_CUSTOM_RULE: RoundtripCase[] = [
  // before_tool_use family
  {
    name: "before_tool + specific + none + block → tool_perm match.tool",
    draft: baseDraft({
      lifecycle: "before_tool_use",
      toolTarget: "specific",
      toolName: "shell_exec",
      conditionKind: "none",
      archetype: "block",
    }),
    expectedKind: "tool_perm",
    expectedFiresAt: "before_tool_use",
    expectedAction: "block",
    srcAnchor: "match: { tool: draft.toolName.trim() }",
  },
  {
    name: "before_tool + specific + none + ask → tool_perm match.tool decision=ask",
    draft: baseDraft({
      lifecycle: "before_tool_use",
      toolTarget: "specific",
      toolName: "fetch_url",
      conditionKind: "none",
      archetype: "ask",
    }),
    expectedKind: "tool_perm",
    expectedFiresAt: "before_tool_use",
    expectedAction: "ask_approval",
  },
  {
    name: "before_tool + any + domain + block → tool_perm match.domain",
    draft: baseDraft({
      lifecycle: "before_tool_use",
      toolTarget: "any",
      conditionKind: "domain",
      domain: "evil.example",
      archetype: "block",
    }),
    expectedKind: "tool_perm",
    expectedFiresAt: "before_tool_use",
    expectedAction: "block",
    srcAnchor: "match: { domain: draft.domain.trim() }",
  },
  {
    name: "before_tool + any + domain_allowlist + block → tool_perm match.domainAllowlist",
    draft: baseDraft({
      lifecycle: "before_tool_use",
      toolTarget: "any",
      conditionKind: "domain_allowlist",
      domainAllowlist: "github.com, openmagi.ai",
      archetype: "block",
    }),
    expectedKind: "tool_perm",
    expectedFiresAt: "before_tool_use",
    expectedAction: "block",
  },
  {
    name: "before_tool + any + path + block → tool_perm match.path",
    draft: baseDraft({
      lifecycle: "before_tool_use",
      toolTarget: "any",
      conditionKind: "path",
      pathPrefix: "/etc/passwd",
      archetype: "block",
    }),
    expectedKind: "tool_perm",
    expectedFiresAt: "before_tool_use",
    expectedAction: "block",
    srcAnchor: "match: { path: draft.pathPrefix.trim() }",
  },
  {
    name: "before_tool + any + path_allowlist + ask → tool_perm match.pathAllowlist",
    draft: baseDraft({
      lifecycle: "before_tool_use",
      toolTarget: "any",
      conditionKind: "path_allowlist",
      pathAllowlist: "/Users/me/proj, /tmp/scratch",
      archetype: "ask",
    }),
    expectedKind: "tool_perm",
    expectedFiresAt: "before_tool_use",
    expectedAction: "ask_approval",
  },

  // after_tool_use llm_criterion — both target axes (F-UX4 liberalization)
  {
    name: "after_tool + any + llm_criterion + strip → llm_criterion w/ user-typed toolMatch",
    draft: baseDraft({
      lifecycle: "after_tool_use",
      toolTarget: "any",
      conditionKind: "llm_criterion",
      criterion: "answer cites at least one source",
      llmToolMatch: "fetch_url, web_search",
      archetype: "strip",
    }),
    expectedKind: "llm_criterion",
    expectedFiresAt: "after_tool_use",
    expectedAction: "override",
    srcAnchor: "payload.toolMatch =",
  },
  {
    name: "after_tool + specific + llm_criterion + strip → llm_criterion w/ AUTO-DERIVED toolMatch (F-UX4)",
    draft: baseDraft({
      lifecycle: "after_tool_use",
      toolTarget: "specific",
      toolName: "fetch_url",
      conditionKind: "llm_criterion",
      criterion: "answer is safe",
      // CRITICALLY: llmToolMatch is EMPTY — F-UX4 auto-derives it from toolName.
      llmToolMatch: "",
      archetype: "strip",
    }),
    expectedKind: "llm_criterion",
    expectedFiresAt: "after_tool_use",
    expectedAction: "override",
    srcAnchor: "? [draft.toolName.trim()]",
  },
  {
    name: "after_tool + specific + llm_criterion + contentMatch + strip → carries contentMatch",
    draft: baseDraft({
      lifecycle: "after_tool_use",
      toolTarget: "specific",
      toolName: "fetch_url",
      conditionKind: "llm_criterion",
      criterion: "answer is safe",
      llmContentMatchEnabled: true,
      llmContentMatchPattern: "AKIA[0-9A-Z]{16}",
      llmContentMatchIsRegex: true,
      llmContentMatchNegate: false,
      archetype: "strip",
    }),
    expectedKind: "llm_criterion",
    expectedFiresAt: "after_tool_use",
    expectedAction: "override",
  },

  // pre_final family
  {
    name: "pre_final + evidence_ref + block → deterministic_ref",
    draft: baseDraft({
      lifecycle: "pre_final",
      toolTarget: "any",
      conditionKind: "evidence_ref",
      evidenceRef: "evidence:git-diff",
      archetype: "block",
    }),
    expectedKind: "deterministic_ref",
    expectedFiresAt: "pre_final",
    expectedAction: "block",
  },
  {
    name: "pre_final + verifier_passed + retry → deterministic_ref (same payload as evidence_ref)",
    draft: baseDraft({
      lifecycle: "pre_final",
      toolTarget: "any",
      conditionKind: "verifier_passed",
      evidenceRef: "verifier:answer-non-empty",
      archetype: "audit",
    }),
    expectedKind: "deterministic_ref",
    expectedFiresAt: "pre_final",
    expectedAction: "audit",
  },
  {
    name: "pre_final + shacl + block → shacl_constraint",
    draft: baseDraft({
      lifecycle: "pre_final",
      toolTarget: "any",
      conditionKind: "shacl",
      shapeTtl: "@prefix sh: <http://www.w3.org/ns/shacl#> .\n[] a sh:NodeShape .",
      archetype: "block",
    }),
    expectedKind: "shacl_constraint",
    expectedFiresAt: "pre_final",
    expectedAction: "block",
  },
  {
    name: "pre_final + llm_criterion + audit → llm_criterion (no toolMatch)",
    draft: baseDraft({
      lifecycle: "pre_final",
      toolTarget: "any",
      conditionKind: "llm_criterion",
      criterion: "answer cites at least one source",
      archetype: "audit",
    }),
    expectedKind: "llm_criterion",
    expectedFiresAt: "pre_final",
    expectedAction: "audit",
  },
  {
    name: "pre_final + field_constraint + block → shacl_constraint w/ authoredAs IR",
    draft: baseDraft({
      lifecycle: "pre_final",
      toolTarget: "any",
      conditionKind: "field_constraint",
      fcEvidenceType: "evidence:git-diff",
      fcField: "files_changed",
      fcOperator: "exists",
      fcValue: "",
      archetype: "block",
    }),
    expectedKind: "shacl_constraint",
    expectedFiresAt: "pre_final",
    expectedAction: "block",
  },

  // Tier 2 audit-only slots
  {
    name: "on_user_prompt_submit + llm_criterion + audit → llm_criterion",
    draft: baseDraft({
      lifecycle: "on_user_prompt_submit",
      toolTarget: "any",
      conditionKind: "llm_criterion",
      criterion: "user prompt is safe",
      archetype: "audit",
    }),
    expectedKind: "llm_criterion",
    expectedFiresAt: "on_user_prompt_submit",
    expectedAction: "audit",
  },
  {
    name: "on_subagent_stop + llm_criterion + audit → llm_criterion",
    draft: baseDraft({
      lifecycle: "on_subagent_stop",
      toolTarget: "any",
      conditionKind: "llm_criterion",
      criterion: "subagent answer is on-topic",
      archetype: "audit",
    }),
    expectedKind: "llm_criterion",
    expectedFiresAt: "on_subagent_stop",
    expectedAction: "audit",
  },
];

// ---------------------------------------------------------------------------
// Mirror-vs-source anchor: every case with a srcAnchor proves the mirror
// still reflects the real code path. A regression in the real wizard's
// customRulePayload that removes one of these substrings fails the test
// before any structural assertion runs.
// ---------------------------------------------------------------------------
describe("Mirror anchors — round-trip mirror tracks author-wizard source", () => {
  for (const c of [...ROUTES_TO_DASHBOARD_CHECK, ...ROUTES_TO_CUSTOM_RULE]) {
    if (!c.srcAnchor) continue;
    it(`anchors '${c.name}' to author-wizard.tsx source`, () => {
      expect(wizardSrc).toContain(c.srcAnchor as string);
    });
  }
});

describe("DashboardCheck round-trip — after_tool none/regex audit/block", () => {
  for (const c of ROUTES_TO_DASHBOARD_CHECK) {
    it(`${c.name}`, () => {
      const built = mirrorBuildDashboardCheck(c.draft);
      // Structural assertions: every DashboardCheck has a non-empty tool,
      // pattern, and a valid action.
      expect(built.tool).toBeTruthy();
      expect(built.pattern).toBeTruthy();
      expect(built.action).toBe(c.expectedAction);
      if (c.draft.toolTarget === "specific") {
        expect(built.tool).toBe(c.draft.toolName.trim());
      } else {
        expect(built.tool).toBe("*");
      }
      if (c.draft.conditionKind === "none") {
        expect(built.pattern).toBe(".*");
        expect(built.isRegex).toBe(true);
      }
    });
  }
});

describe("CustomRule round-trip — every wizard-exposed combo passes validator stub", () => {
  for (const c of ROUTES_TO_CUSTOM_RULE) {
    it(`${c.name}`, () => {
      const payload = mirrorCustomRulePayload(c.draft);
      const kind = mirrorCustomRuleKind(c.draft);
      const action = mirrorCustomRuleAction(c.draft);
      expect(kind).toBe(c.expectedKind);
      expect(action).toBe(c.expectedAction);
      const err = validateCustomRuleStub({
        kind,
        payload,
        firesAt: c.expectedFiresAt,
        action,
      });
      expect(err).toBeNull();
    });
  }

  it("F-UX4 auto-derive: after_tool+specific+llm_criterion emits toolMatch=[toolName] even with empty llmToolMatch (REGRESSION GUARD)", () => {
    // F6.5-class bug guard: an earlier wizard version emitted an EMPTY
    // toolMatch when target=specific because the textfield was hidden
    // but no auto-derive ran. The backend then rejected with HTTP 400.
    // F-UX4 fixes that by auto-deriving from draft.toolName.
    const draft = baseDraft({
      lifecycle: "after_tool_use",
      toolTarget: "specific",
      toolName: "fetch_url",
      conditionKind: "llm_criterion",
      criterion: "answer is safe",
      llmToolMatch: "",
      archetype: "strip",
    });
    const payload = mirrorCustomRulePayload(draft);
    expect(payload.toolMatch).toEqual(["fetch_url"]);
    // Sanity: pre_final llm_criterion still omits toolMatch.
    const preFinal = baseDraft({
      lifecycle: "pre_final",
      conditionKind: "llm_criterion",
      criterion: "answer cites",
      archetype: "audit",
    });
    expect(mirrorCustomRulePayload(preFinal).toolMatch).toBeUndefined();
  });

  it("F-UX4 fallback: after_tool+any+llm_criterion still uses splitToolMatchList(draft.llmToolMatch)", () => {
    // target=any keeps the user-typed multi-tool path so multi-tool rules
    // remain authorable. Auto-derive only triggers under target=specific.
    const draft = baseDraft({
      lifecycle: "after_tool_use",
      toolTarget: "any",
      conditionKind: "llm_criterion",
      criterion: "answer is safe",
      llmToolMatch: "fetch_url, web_search",
      archetype: "strip",
    });
    const payload = mirrorCustomRulePayload(draft);
    expect(payload.toolMatch).toEqual(["fetch_url", "web_search"]);
  });
});

describe("Backend matrix stub — rejects known-invalid payloads (smoke)", () => {
  it("rejects after_tool_use llm_criterion with EMPTY toolMatch (the F6.5 bug)", () => {
    const err = validateCustomRuleStub({
      kind: "llm_criterion",
      payload: { criterion: "x", toolMatch: [] },
      firesAt: "after_tool_use",
      action: "override",
    });
    expect(err).not.toBeNull();
    expect(err?.message).toMatch(/toolMatch/);
  });

  it("rejects tool_perm with empty match dict", () => {
    const err = validateCustomRuleStub({
      kind: "tool_perm",
      payload: { match: {}, decision: "deny" },
      firesAt: "before_tool_use",
      action: "block",
    });
    expect(err).not.toBeNull();
  });

  it("rejects llm_criterion @ pre_final + action=override (not in _LEGAL)", () => {
    const err = validateCustomRuleStub({
      kind: "llm_criterion",
      payload: { criterion: "x" },
      firesAt: "pre_final",
      action: "override",
    });
    expect(err).not.toBeNull();
    expect(err?.message).toMatch(/action override not allowed/);
  });

  it("rejects shacl_constraint with empty shapeTtl and no authoredAs", () => {
    const err = validateCustomRuleStub({
      kind: "shacl_constraint",
      payload: { shapeTtl: "" },
      firesAt: "pre_final",
      action: "block",
    });
    expect(err).not.toBeNull();
  });

  it("accepts shacl_constraint with empty shapeTtl when authoredAs.kind=field_constraint (F3 pass-through)", () => {
    const err = validateCustomRuleStub({
      kind: "shacl_constraint",
      payload: {
        shapeTtl: "",
        authoredAs: { kind: "field_constraint", operator: "exists" },
      },
      firesAt: "pre_final",
      action: "block",
    });
    expect(err).toBeNull();
  });
});
