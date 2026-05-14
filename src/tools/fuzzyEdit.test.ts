import { describe, expect, it } from "vitest";
import {
  fuzzyFindOldString,
  detectLazyComments,
  type FuzzyMatchResult,
} from "./fuzzyEdit.js";

describe("fuzzyFindOldString", () => {
  // ── Stage 1: exact match (regression) ──────────────────────
  it("returns exact match when old_string matches verbatim", () => {
    const content = "function foo() {\n  return 42;\n}\n";
    const result = fuzzyFindOldString(content, "  return 42;");
    expect(result).toMatchObject({ kind: "exact", offset: 17 });
  });

  // ── Stage 2: cherry-pick (line identity) ───────────────────
  it("matches 2-space vs 4-space indent via cherry-pick", () => {
    const content = "function foo() {\n    return bar;\n}\n";
    const oldString = "function foo() {\n  return bar;\n}";
    const result = fuzzyFindOldString(content, oldString);
    expect(result.kind).toBe("fuzzy");
    expect(result.stage).toBe("cherry_pick");
    expect(result.matchedText).toBeDefined();
  });

  it("matches tab vs spaces indent via cherry-pick", () => {
    const content = "function foo() {\n\treturn bar;\n}\n";
    const oldString = "function foo() {\n  return bar;\n}";
    const result = fuzzyFindOldString(content, oldString);
    expect(result.kind).toBe("fuzzy");
    expect(result.matchedText).toBeDefined();
  });

  // ── blank-stripped variant ───────────────────────────────────
  it("matches with extra blank lines via blank-stripped", () => {
    // Same indent, just extra blank lines — cherry-pick raw fails (line count differs),
    // blank-stripped removes blanks → line count matches → cherry_pick/blank_stripped
    const content = "function foo() {\n  return bar;\n  return baz;\n}\n";
    const oldString = "function foo() {\n\n  return bar;\n\n  return baz;\n\n}";
    const result = fuzzyFindOldString(content, oldString);
    expect(result.kind).toBe("fuzzy");
    expect(result.variant).toBe("blank_stripped");
  });

  // ── combined variant ───────────────────────────────────────
  it("matches indent drift + blank lines via combined preprocessing", () => {
    // Different indent AND extra blank lines.
    // cherry-pick raw: fails (indent + blank lines differ)
    // cherry-pick blank_stripped: matches (blank lines removed, trim comparison ignores indent)
    const content = "function foo() {\n    return bar();\n    const x = baz();\n}\n";
    const oldString = "function foo() {\n\n  return bar();\n\n  const x = baz();\n\n}";
    const result = fuzzyFindOldString(content, oldString);
    expect(result.kind).toBe("fuzzy");
    // blank_stripped brings line count to same, trim comparison handles indent → matches first
    expect(result.variant).toBe("blank_stripped");
  });

  // ── Stage 3: line-diff ─────────────────────────────────────
  it("matches trailing whitespace diff via line-diff", () => {
    const content = "function foo() {\n  return bar;\n}\n";
    const oldString = "function foo() {  \n  return bar;  \n}";
    const result = fuzzyFindOldString(content, oldString);
    expect(result.kind).toBe("fuzzy");
    expect(result.similarity).toBeGreaterThanOrEqual(0.95);
  });

  // ── Safety: ambiguous match ────────────────────────────────
  it("returns not_found when match is ambiguous (2 similar blocks)", () => {
    // Two structurally identical multi-line blocks separated by a non-blank line
    const content = [
      "    doFirst();",
      "    doSecond();",
      "    doThird();",
      "// separator",
      "    doFirst();",
      "    doSecond();",
      "    doThird();",
    ].join("\n");
    const oldString = [
      "  doFirst();",
      "  doSecond();",
      "  doThird();",
    ].join("\n");
    const result = fuzzyFindOldString(content, oldString);
    expect(result.kind).toBe("not_found");
  });

  // ── Completely wrong old_string ────────────────────────────
  it("returns not_found for completely wrong old_string", () => {
    const content = "function foo() {\n  return 42;\n}\n";
    const result = fuzzyFindOldString(
      content,
      "class Bar extends Component {\n  render() {}\n}",
    );
    expect(result.kind).toBe("not_found");
  });

  // ── Performance ────────────────────────────────────────────
  it("completes in < 200ms on a 10K-line file", () => {
    const lines = Array.from(
      { length: 10000 },
      (_, i) => `  const x${i} = ${i};`,
    );
    const content = lines.join("\n");
    const oldString = "  const x5000 = 5000;\n  const x5001 = 5001;";
    const start = performance.now();
    const result = fuzzyFindOldString(content, oldString);
    const elapsed = performance.now() - start;
    expect(result.kind).toBe("exact");
    expect(elapsed).toBeLessThan(200);
  });

  // ── replace_all with fuzzy (lower threshold) ───────────────
  it("finds all occurrences with replace_all fuzzy", () => {
    const content =
      "  log('a');\n  doStuff();\n  log('b');\n  doMore();\n  log('c');\n";
    // indent-drifted version
    const oldString = "    log('a');";
    const result = fuzzyFindOldString(content, oldString, {
      replaceAll: true,
    });
    // Only the first occurrence should match since 'a', 'b', 'c' differ
    expect(result.kind).toBe("fuzzy");
  });

  // ── Unicode content ────────────────────────────────────────
  it("handles Korean text correctly", () => {
    const content = "const msg = '안녕하세요';\nconsole.log(msg);\n";
    const oldString = "const msg = '안녕하세요';";
    const result = fuzzyFindOldString(content, oldString);
    expect(result.kind).toBe("exact");
    expect(result.offset).toBe(0);
  });

  it("handles emoji content correctly", () => {
    const content = "const emoji = '🎉🎊';\nreturn emoji;\n";
    const oldString = "const emoji = '🎉🎊';";
    const result = fuzzyFindOldString(content, oldString);
    expect(result.kind).toBe("exact");
  });

  // ── RelativeIndenter nested closures ───────────────────────
  it("handles nested closure indent drift via relative indentation", () => {
    const content = [
      "function outer() {",
      "  function inner() {",
      "    return 42;",
      "  }",
      "}",
    ].join("\n");
    const oldString = [
      "function outer() {",
      "    function inner() {",
      "        return 42;",
      "    }",
      "}",
    ].join("\n");
    const result = fuzzyFindOldString(content, oldString);
    expect(result.kind).toBe("fuzzy");
    expect(result.matchedText).toContain("function outer");
  });
});

describe("detectLazyComments", () => {
  it("detects // ... existing code", () => {
    const result = detectLazyComments("// ... existing code");
    expect(result).not.toBeNull();
    expect(result!.line).toBe(1);
  });

  it("detects # ... rest of implementation", () => {
    const result = detectLazyComments("# ... rest of implementation");
    expect(result).not.toBeNull();
  });

  it("detects bare // ...", () => {
    const result = detectLazyComments("code();\n// ...");
    expect(result).not.toBeNull();
    expect(result!.line).toBe(2);
  });

  it("does NOT detect // ... in a README-like context", () => {
    // This is a legitimate usage pattern — see docs
    const result = detectLazyComments("// ... (see docs for full API reference)");
    // 'docs' is not in our trigger word list, so not detected
    expect(result).toBeNull();
  });

  it("does NOT detect ...args spread syntax", () => {
    const result = detectLazyComments("function foo(...args) { return args; }");
    expect(result).toBeNull();
  });

  it("detects /* ... unchanged */", () => {
    const result = detectLazyComments("/* ... unchanged */");
    expect(result).not.toBeNull();
  });

  it("does NOT detect // ... inside a string literal", () => {
    const result = detectLazyComments('const s = "// ... existing code";');
    expect(result).toBeNull();
  });

  it("detects {/* ... */} JSX comment", () => {
    const result = detectLazyComments("{/* ... existing components */}");
    expect(result).not.toBeNull();
  });

  it("detects # ... code pattern (Python/Shell)", () => {
    const result = detectLazyComments("# ... remaining code");
    expect(result).not.toBeNull();
  });
});
