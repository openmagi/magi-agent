import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./add-rule-modal.tsx", import.meta.url),
  "utf8",
);

describe("AddRulePicker — in-page Add-rule entry point", () => {
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

  it("renders as an in-page section, not an overlay modal", () => {
    // Earlier shape was role=dialog with fixed inset-0 — the new shape is
    // a plain section the parent mounts inline so the user's eye stays
    // anchored where the Add-rule button lived.
    expect(src).not.toContain('role="dialog"');
    expect(src).not.toContain('aria-modal="true"');
    expect(src).not.toContain("fixed inset-0");
    expect(src).toContain('aria-label="Pick a rule kind"');
  });

  it("ships an X-style cancel control via onCancel", () => {
    expect(src).toContain("onCancel");
    expect(src).toContain('aria-label="Close add rule picker"');
  });

  it("keeps the AddRuleModal name as a deprecated back-compat wrapper", () => {
    expect(src).toContain("AddRuleModal");
    expect(src).toContain("@deprecated");
  });
});
