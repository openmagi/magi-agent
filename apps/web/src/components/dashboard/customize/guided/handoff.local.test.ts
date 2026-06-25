/**
 * Tests for the wizard → NL handoff serializer (PR-F-HANDOFF).
 *
 * The serializer must:
 *   1. Emit a first-person opening sentence honoring the populated axes.
 *   2. OMIT empty fields (never render "lifecycle: (empty)").
 *   3. Surface the currently-open step + a hint at the last-touched field.
 *   4. Stay under PRIMER_MAX_CHARS (1000) for sanity.
 *   5. Reuse the populated subset, not the full Draft union.
 */

import { describe, expect, it } from "vitest";

import {
  PRIMER_MAX_CHARS,
  serializeDraftToPrimer,
  type HandoffDraft,
} from "./handoff";


function makeDraft(overrides: Partial<HandoffDraft> = {}): HandoffDraft {
  return {
    lifecycle: "",
    scope: "always",
    toolTarget: "any",
    toolName: "",
    conditionKind: "none",
    archetype: "audit",
    ...overrides,
  };
}


describe("serializeDraftToPrimer — opening sentence", () => {
  it("renders an opening with at least the archetype when no other axis is set", () => {
    // makeDraft seeds archetype="audit" so a fresh draft still has one
    // axis populated. The serializer must surface it in the opening
    // sentence rather than falling back to the empty placeholder.
    const out = serializeDraftToPrimer(makeDraft(), "trigger");
    expect(out).toContain("I started authoring a policy");
    expect(out).toContain('"audit" action');
  });

  it("falls back to the guided-wizard placeholder when every axis is unset", () => {
    const empty: HandoffDraft = {
      lifecycle: "",
      scope: "always",
      toolTarget: "any",
      toolName: "",
      conditionKind: "none",
      archetype: "",
    };
    const out = serializeDraftToPrimer(empty, "trigger");
    expect(out).toContain("guided wizard");
  });

  it("includes lifecycle when populated", () => {
    const out = serializeDraftToPrimer(
      makeDraft({ lifecycle: "before_tool_use" }),
      "trigger",
    );
    expect(out).toContain('"before_tool_use"');
    expect(out).toContain("lifecycle");
  });

  it("includes scope only when not 'always' (the default)", () => {
    const withCoding = serializeDraftToPrimer(
      makeDraft({ scope: "coding" }),
      "trigger",
    );
    expect(withCoding).toContain("coding turns");
    const withAlways = serializeDraftToPrimer(
      makeDraft({ scope: "always" }),
      "trigger",
    );
    expect(withAlways).not.toContain("scoped to always");
  });

  it("includes the conditionKind only when not 'none' (no decision yet)", () => {
    const withShacl = serializeDraftToPrimer(
      makeDraft({ conditionKind: "shacl" }),
      "specifics",
    );
    expect(withShacl).toContain('"shacl" condition');
    const noneOnly = serializeDraftToPrimer(
      makeDraft({ conditionKind: "none" }),
      "trigger",
    );
    expect(noneOnly).not.toContain('"none" condition');
  });

  it("describes a specific tool target when the operator picked one", () => {
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "before_tool_use",
        toolTarget: "specific",
        toolName: "FileWrite",
      }),
      "trigger",
    );
    expect(out).toContain('"FileWrite"');
  });

  it("falls back when the operator picked 'specific' but no tool name", () => {
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "before_tool_use",
        toolTarget: "specific",
        toolName: "",
      }),
      "trigger",
    );
    expect(out).toContain("specific tool (no name picked yet)");
  });

  it("notes 'any tool' for tool-bearing lifecycles only", () => {
    const beforeToolAny = serializeDraftToPrimer(
      makeDraft({ lifecycle: "before_tool_use", toolTarget: "any" }),
      "trigger",
    );
    expect(beforeToolAny).toContain("any tool");
    const preFinalAny = serializeDraftToPrimer(
      makeDraft({ lifecycle: "pre_final", toolTarget: "any" }),
      "trigger",
    );
    expect(preFinalAny).not.toContain("any tool");
  });
});


describe("serializeDraftToPrimer — populated-field clause", () => {
  it("omits the 'I had filled in' clause entirely when no payload fields are populated", () => {
    const out = serializeDraftToPrimer(
      makeDraft({ lifecycle: "pre_final" }),
      "trigger",
    );
    expect(out).not.toContain("I had filled in");
  });

  it("enumerates populated fields and skips empty ones (honest-degrade)", () => {
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "pre_final",
        conditionKind: "shacl",
        shapeTtl: "magi:Shape a sh:NodeShape .",
        criterion: "",
        regexPattern: "",
      }),
      "specifics",
    );
    expect(out).toContain("I had filled in");
    expect(out).toContain("SHACL shape:");
    expect(out).toContain("magi:Shape a sh:NodeShape");
    expect(out).not.toContain("LLM criterion:");
    expect(out).not.toContain("regex pattern:");
  });

  it("clips very long values to a single-line excerpt", () => {
    const longTtl = "a".repeat(300);
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "pre_final",
        conditionKind: "shacl",
        shapeTtl: longTtl,
      }),
      "specifics",
    );
    expect(out).toContain("...");
    expect(out.indexOf("a".repeat(200))).toBe(-1);
  });

  it("captures the field-constraint picker subset when authored", () => {
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "pre_final",
        conditionKind: "field_constraint",
        fcEvidenceType: "TestRun",
        fcField: "exitCode",
        fcOperator: "eq",
        fcValue: "0",
      }),
      "specifics",
    );
    expect(out).toContain("field-constraint evidence type: TestRun");
    expect(out).toContain("field-constraint field: exitCode");
    expect(out).toContain("field-constraint operator: eq");
    expect(out).toContain("field-constraint value: 0");
  });

  it("captures the mutator picker subset (prompt_injection)", () => {
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "before_tool_use",
        toolTarget: "specific",
        toolName: "Bash",
        conditionKind: "prompt_injection",
        archetype: "mutate",
        piTargetArgKey: "command",
        piValue: " --dry-run",
      }),
      "specifics",
    );
    expect(out).toContain("prompt-injection arg key: command");
    expect(out).toContain("prompt-injection value: --dry-run");
  });
});


describe("serializeDraftToPrimer — stuck clause", () => {
  it("names the currently-open step", () => {
    const out = serializeDraftToPrimer(
      makeDraft({ lifecycle: "pre_final", conditionKind: "shacl" }),
      "specifics",
    );
    expect(out).toContain("I got stuck at the specifics step");
  });

  it("adds a field hint for shacl when stuck on specifics", () => {
    const out = serializeDraftToPrimer(
      makeDraft({ lifecycle: "pre_final", conditionKind: "shacl" }),
      "specifics",
    );
    expect(out).toContain("SHACL shape textarea");
  });

  it("adds a field hint for llm_criterion when stuck on specifics", () => {
    const out = serializeDraftToPrimer(
      makeDraft({ lifecycle: "pre_final", conditionKind: "llm_criterion" }),
      "specifics",
    );
    expect(out).toContain("LLM criterion sentence");
  });

  it("hints at the tool name picker when the operator chose 'specific' but no name", () => {
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "before_tool_use",
        toolTarget: "specific",
        toolName: "",
      }),
      "trigger",
    );
    expect(out).toContain("tool name picker");
  });
});


describe("serializeDraftToPrimer — closing + length cap", () => {
  it("ends with a 'Please help me finish' ask", () => {
    const out = serializeDraftToPrimer(makeDraft(), "trigger");
    expect(out).toMatch(/Please help me finish/);
  });

  it("stays under PRIMER_MAX_CHARS even with a huge SHACL paste", () => {
    const huge = "x".repeat(5000);
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "pre_final",
        conditionKind: "shacl",
        shapeTtl: huge,
        description: huge,
      }),
      "specifics",
    );
    expect(out.length).toBeLessThanOrEqual(PRIMER_MAX_CHARS);
  });
});


// ---------------------------------------------------------------------------
// PR-F-EXEC3 — Operator-defined shell draft primer rendering.
// ---------------------------------------------------------------------------


describe("serializeDraftToPrimer — F-EXEC3 shell draft primer", () => {
  it("captures the inline shell_command draft (source + script + timeout + env)", () => {
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "after_tool_use",
        conditionKind: "shell_command",
        archetype: "shell",
        shSource: "inline",
        shInline: "curl -fsS https://notify.example/slack",
        shTimeoutSeconds: 60,
        shEnvVars: "SLACK_TOKEN",
        shShell: "bash",
      }),
      "specifics",
    );
    // Source kind surfaces as a key:value.
    expect(out).toContain("shell source: inline");
    // The script body rides through (clipped to 120 chars by pushIf).
    expect(out).toContain(
      "shell script: curl -fsS https://notify.example/slack",
    );
    // Non-default timeout surfaces.
    expect(out).toContain("shell timeout: 60s");
    // Env-var allowlist surfaces.
    expect(out).toContain("shell env-var allowlist: SLACK_TOKEN");
    // The conditionKind opening clause names the shell shape.
    expect(out).toContain('"shell_command" condition');
  });

  it("captures the file-sourced shell_check verifier draft", () => {
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "pre_final",
        conditionKind: "shell_check",
        archetype: "shell",
        shSource: "file",
        shPath: "/usr/local/bin/run-tests.sh",
        shTimeoutSeconds: 300,
        shShell: "sh",
      }),
      "specifics",
    );
    expect(out).toContain("shell source: file");
    expect(out).toContain("shell script path: /usr/local/bin/run-tests.sh");
    expect(out).toContain("shell timeout: 300s");
    expect(out).toContain("shell interpreter: sh");
    expect(out).toContain('"shell_check" condition');
  });

  it("omits the default timeout (30s) + default interpreter (bash) for honest-degrade", () => {
    // The wizard's EMPTY draft seeds shTimeoutSeconds=30 + shShell="bash";
    // those defaults should NOT surface in the primer because they carry
    // no operator intent — only non-default values are honest signals to
    // forward to the NL compose surface.
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "before_tool_use",
        conditionKind: "shell_command",
        archetype: "shell",
        shSource: "inline",
        shInline: "echo hi",
        shTimeoutSeconds: 30,
        shShell: "bash",
      }),
      "specifics",
    );
    expect(out).not.toContain("shell timeout: 30s");
    expect(out).not.toContain("shell interpreter: bash");
    // The script body still rides through (load-bearing).
    expect(out).toContain("shell script: echo hi");
  });

  it("clips a very long inline script body to a single-line excerpt", () => {
    const longScript = "echo " + "x".repeat(300);
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "pre_final",
        conditionKind: "shell_check",
        archetype: "shell",
        shSource: "inline",
        shInline: longScript,
      }),
      "specifics",
    );
    expect(out).toContain("...");
    // Honest-degrade: the primer must not echo the full 300-char paste.
    expect(out.indexOf("x".repeat(200))).toBe(-1);
  });

  it("emits the shell-command stuck hint on the specifics step", () => {
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "after_tool_use",
        conditionKind: "shell_command",
        archetype: "shell",
      }),
      "specifics",
    );
    expect(out).toContain("shell command picker");
  });

  it("emits the shell-verifier stuck hint on the specifics step", () => {
    const out = serializeDraftToPrimer(
      makeDraft({
        lifecycle: "pre_final",
        conditionKind: "shell_check",
        archetype: "shell",
      }),
      "specifics",
    );
    expect(out).toContain("shell verifier picker");
  });
});
