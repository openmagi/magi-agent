import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./add-rule-modal.tsx", import.meta.url),
  "utf8",
);

describe("AddRuleModal — single Add-rule entry point", () => {
  it("exposes exactly four authoring choices that map to the four backing primitives", () => {
    expect(src).toContain('"block-answer"');
    expect(src).toContain('"restrict-tool"');
    expect(src).toContain('"filter-result"');
    expect(src).toContain('"rewire-builtin"');
  });

  it("names the underlying primitive for each choice so the routing is transparent", () => {
    expect(src).toContain("Custom Rule (pre-final)");
    expect(src).toContain("Custom Rule (before-tool)");
    expect(src).toContain("Custom Check (after-tool");
    expect(src).toContain("SeamSpec (Advanced)");
  });

  it("renders as an aria-labelled modal dialog", () => {
    expect(src).toContain('role="dialog"');
    expect(src).toContain('aria-modal="true"');
    expect(src).toContain('aria-label="Add a rule"');
  });
});
