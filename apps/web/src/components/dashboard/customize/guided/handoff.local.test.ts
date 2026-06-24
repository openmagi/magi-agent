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
