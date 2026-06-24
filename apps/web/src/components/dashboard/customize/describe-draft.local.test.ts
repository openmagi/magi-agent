import { describe, expect, it } from "vitest";

import { describeDraft } from "./describe-draft";


function base(): Parameters<typeof describeDraft>[0] {
  return {
    kind: "deterministic_ref",
    scope: "always",
    ref: "",
    refLabel: "",
    matchType: "tool",
    matchValue: "",
    decision: "deny",
    criterion: "",
    toolMatch: "",
    contentPattern: "",
    contentIsRegex: false,
    contentNegate: false,
    shaclMode: "nl",
    shaclPreviewOk: false,
    rawTtlHasContent: false,
  };
}


describe("describeDraft — plain-English live preview of the add-form draft", () => {
  it("returns null for an empty deterministic_ref draft (no ref picked yet)", () => {
    expect(describeDraft(base())).toBeNull();
  });

  it("renders 'Every turn' for scope=always", () => {
    const line = describeDraft({
      ...base(),
      ref: "evidence:git-diff",
      refLabel: "Git diff was recorded",
    });
    expect(line).toContain("Every turn");
    expect(line).toContain("Git diff was recorded");
  });

  it("renders 'On {scope} turns' for non-always scopes", () => {
    const line = describeDraft({
      ...base(),
      scope: "coding",
      ref: "evidence:test-run",
      refLabel: "Tests were run",
    });
    expect(line).toContain("On coding turns");
  });

  it("describes tool_perm deny by tool name", () => {
    const line = describeDraft({
      ...base(),
      kind: "tool_perm",
      matchType: "tool",
      matchValue: "shell_exec",
    });
    expect(line).toContain("Before the agent calls a tool");
    expect(line).toContain("deny");
    expect(line).toContain('"shell_exec"');
  });

  it("describes tool_perm ask-approval by domain", () => {
    const line = describeDraft({
      ...base(),
      kind: "tool_perm",
      matchType: "domain",
      matchValue: "example.com",
      decision: "ask",
    });
    expect(line).toContain("require human approval");
    expect(line).toContain("example.com");
  });

  it("describes llm_criterion with the quoted criterion", () => {
    const line = describeDraft({
      ...base(),
      kind: "llm_criterion",
      criterion: "the answer cites at least one source",
    });
    expect(line).toContain("LLM critic");
    expect(line).toContain('"the answer cites at least one source"');
  });

  it("returns null for shacl_constraint when no shape compiled yet", () => {
    expect(describeDraft({ ...base(), kind: "shacl_constraint" })).toBeNull();
  });

  it("describes shacl_constraint once a shape is ready", () => {
    const line = describeDraft({
      ...base(),
      kind: "shacl_constraint",
      shaclPreviewOk: true,
    });
    expect(line).toContain("SHACL shape");
  });

  it("describes after_tool with both regex and LLM criterion", () => {
    const line = describeDraft({
      ...base(),
      kind: "after_tool",
      toolMatch: "fetch_url",
      contentPattern: "AKIA[0-9A-Z]{16}",
      contentIsRegex: true,
      criterion: "the response contains credentials",
    });
    expect(line).toContain("After fetch_url returns");
    expect(line).toContain("regex");
    expect(line).toContain("AKIA[0-9A-Z]{16}");
    expect(line).toContain("OR");
    expect(line).toContain("LLM critic");
  });

  // -------------------------------------------------------------------------
  // F4 — capability_scope (subagent toolset narrowing at spawn time)
  // -------------------------------------------------------------------------

  it("returns null for an empty capability_scope draft (no denyTools, no maxPermissionClass)", () => {
    expect(
      describeDraft({
        ...base(),
        kind: "capability_scope",
        denyTools: [],
        maxPermissionClass: null,
      }),
    ).toBeNull();
  });

  it("describes capability_scope with denyTools only", () => {
    const line = describeDraft({
      ...base(),
      kind: "capability_scope",
      denyTools: ["shell_exec"],
      maxPermissionClass: null,
    });
    expect(line).toContain("Subagents");
    expect(line).toContain("shell_exec");
  });

  it("describes capability_scope with maxPermissionClass only", () => {
    const line = describeDraft({
      ...base(),
      kind: "capability_scope",
      denyTools: [],
      maxPermissionClass: "readonly",
    });
    expect(line).toContain("Subagents");
    expect(line).toContain("readonly");
  });

  it("describes capability_scope with both denyTools and maxPermissionClass", () => {
    const line = describeDraft({
      ...base(),
      kind: "capability_scope",
      denyTools: ["shell_exec", "fs_write"],
      maxPermissionClass: "safe_write",
    });
    expect(line).toContain("Subagents");
    expect(line).toContain("shell_exec");
    expect(line).toContain("fs_write");
    expect(line).toContain("safe_write");
  });
});
