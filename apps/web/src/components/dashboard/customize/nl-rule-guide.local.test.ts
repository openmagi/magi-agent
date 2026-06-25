import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./nl-rule-guide.tsx", import.meta.url),
  "utf8",
);

describe("NlRuleGuide — same mental model as AuthorWizard", () => {
  it("exposes the three policy axes — WHEN, WHAT, CONDITION", () => {
    expect(src).toContain('tag="WHEN"');
    expect(src).toContain('tag="WHAT"');
    expect(src).toContain('tag="CONDITION"');
  });

  it("lists supported phrasings (✓) and unsupported ones (✗) per axis", () => {
    expect(src).toContain('text-emerald-600');
    expect(src).toContain('text-secondary/60');
    expect(src).toContain('aria-hidden="true"');
  });

  it("flags non-wired lifecycle events honestly (Stop, UserPromptSubmit)", () => {
    expect(src).toContain("Stop");
    expect(src).toContain("file-hook only");
  });

  it("exposes 'no condition' as an after-tool first-class phrasing (replaces the dropped 'emit unconditionally' archetype)", () => {
    expect(src).toContain("no condition (after-tool only)");
    expect(src).not.toContain("backend pending");
  });

  it("annotates fetch matchers as 'network tools only' so users understand the constraint", () => {
    expect(src).toContain("network tools only");
  });

  it("ships clickable example chips for the four wired archetypes", () => {
    expect(src).toContain("EXAMPLES");
    expect(src).toContain('archetype: "block"');
    expect(src).toContain('archetype: "ask"');
    expect(src).toContain('archetype: "audit"');
    expect(src).toContain('archetype: "strip"');
  });

  it("clicking an example chip routes to the parent's onPickExample callback", () => {
    expect(src).toContain("onPickExample(ex.text)");
  });

  it("warns about clarifying questions when phrasing is ambiguous", () => {
    // The literal "clarifying questions" string is wrapped onto two source
    // lines, so match on the wrapped pair instead of demanding a single-line
    // assertion (Prettier line-wraps long JSX text).
    expect(src).toMatch(/clarifying\s+questions/);
  });

  it("is collapsible (default open) with aria-expanded", () => {
    expect(src).toContain("aria-expanded={open}");
    expect(src).toContain("useState(true)");
  });
});

describe("NlRuleGuide — F6 path / path_allowlist example phrasings", () => {
  it("ships the deny-under-/etc/ tool_perm path example", () => {
    expect(src).toContain("Before any tool call, deny writes under /etc/.");
  });

  it("ships the allow-only-/Users/me/proj/ tool_perm path_allowlist example", () => {
    expect(src).toContain(
      "Before any tool call, only allow edits under /Users/me/proj/.",
    );
  });
});

describe("NlRuleGuide — F3 field_constraint example phrasings", () => {
  it("ships at least three field_constraint examples covering eq/forEachExistsCovering/comparator phrasings", () => {
    expect(src).toContain(
      "On coding turns, block the final answer when TestRun.exitCode is not 0",
    );
    expect(src).toContain(
      "On coding turns, require that for each entry in GitDiff.changedFiles there exists a TestRun whose command contains that path",
    );
    expect(src).toContain(
      "On research turns, audit when SourceInspection.statusCode is greater than 399",
    );
  });
});


describe("NlRuleGuide — F6.5 llm_criterion + contentMatch combo example", () => {
  it("ships an after-tool example combining a regex pre-filter with an LLM critic verdict", () => {
    // F6.5 surfaces the deterministic regex pre-filter as the explicit
    // input-definition slot in front of an advisory critic. The example
    // chip should route the NL compiler to a llm_criterion CustomRule
    // (firesAt=after_tool_use) with a contentMatch payload.
    expect(src).toContain(
      "After fetch_url returns, when the result contains AKIA[0-9A-Z]{16}, audit when an LLM critic judges that the key is real.",
    );
  });
});


describe("NlRuleGuide — F-UX11 binary-verdict phrasing examples", () => {
  it("ships a pre_final binary-verdict block example mirroring the wizard GuidanceHintCard", () => {
    // F-UX11 surfaces the same Yes/No verdict shape in the NL guide so
    // an operator who arrives via the textarea sees the binary contract
    // before drafting a vague llm_criterion. The chip text must arrive
    // at the NL compiler verbatim via onPickExample.
    expect(src).toContain(
      "block at pre_final when the response does not cite a source for every claim",
    );
  });

  it("ships a shell_check pytest binary-verdict example covering the exit-code fallback", () => {
    // The shell_check verifier resolves the verdict from stdout JSON
    // first, exit code second. This example chip primes the operator
    // for the exit-code path (pytest exits non-zero on failure) which
    // is the most common verdict shape for off-the-shelf test runners.
    expect(src).toContain(
      "run shell pytest before committing; block if exit code is non-zero",
    );
  });
});
