import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./interview-message.tsx", import.meta.url),
  "utf8",
);

describe("InterviewMessage — F-UX6 per-question answer affordances", () => {
  it("renders the question text + a per-`expects` answer affordance", () => {
    expect(src).toContain("question.question");
    expect(src).toContain("AnswerAffordance");
  });

  it("imports the InterviewQuestion + ArchitectExpects types from customize-api", () => {
    expect(src).toContain("InterviewQuestion");
    expect(src).toContain("ArchitectExpects");
  });

  it("renders an enum radio chip group for lifecycle + scope expects", () => {
    expect(src).toContain('expects === "lifecycle"');
    expect(src).toContain('expects === "scope"');
    expect(src).toContain("LIFECYCLE_OPTIONS");
    expect(src).toContain("SCOPE_OPTIONS");
    expect(src).toContain('role="radiogroup"');
  });

  it("lists the six canonical lifecycle slots in LIFECYCLE_OPTIONS", () => {
    expect(src).toContain('"pre_final"');
    expect(src).toContain('"before_tool_use"');
    expect(src).toContain('"after_tool_use"');
    expect(src).toContain('"spawn"');
    expect(src).toContain('"on_user_prompt_submit"');
    expect(src).toContain('"on_subagent_stop"');
  });

  it("lists the six canonical scope buckets in SCOPE_OPTIONS", () => {
    expect(src).toContain('"always"');
    expect(src).toContain('"coding"');
    expect(src).toContain('"research"');
    expect(src).toContain('"delivery"');
    expect(src).toContain('"memory"');
    expect(src).toContain('"task"');
  });

  it("renders chip pickers for inventory-bearing expects (evidence_ref / verifier_ref / field / tool_name)", () => {
    expect(src).toContain('expects === "evidence_ref"');
    expect(src).toContain('expects === "verifier_ref"');
    expect(src).toContain('expects === "field"');
    expect(src).toContain('expects === "tool_name"');
    expect(src).toContain("ChipPicker");
  });

  it("falls back to a freeform input when the inventory is empty (never dead-ends)", () => {
    expect(src).toContain("FreeformInput");
    // The Send button calls onAnswer with the trimmed text.
    expect(src).toContain("onAnswer(v)");
  });
});
