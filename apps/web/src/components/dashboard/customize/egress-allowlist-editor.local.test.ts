import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { isValidAllowlistPattern } from "./egress-allowlist-pattern";

const src = readFileSync(
  new URL("./egress-allowlist-editor.tsx", import.meta.url),
  "utf8",
);

describe("egress-allowlist-editor: pattern validity (mirrors backend grammar)", () => {
  it("accepts an exact host", () => {
    expect(isValidAllowlistPattern("api.github.com")).toBe(true);
    expect(isValidAllowlistPattern("github.com")).toBe(true);
  });

  it("accepts a single-suffix wildcard", () => {
    expect(isValidAllowlistPattern("*.github.com")).toBe(true);
  });

  it("lowercases before validating", () => {
    expect(isValidAllowlistPattern("API.GitHub.COM")).toBe(true);
  });

  it("rejects ports, paths, scheme, userinfo, and whitespace", () => {
    for (const bad of [
      "",
      "not a host",
      "api.github.com:443",
      "https://api.github.com",
      "api.github.com/path",
      "user@api.github.com",
      "*",
    ]) {
      expect(isValidAllowlistPattern(bad)).toBe(false);
    }
  });

  it("rejects an over-long label / hostname", () => {
    expect(isValidAllowlistPattern("a".repeat(64) + ".com")).toBe(false);
    expect(isValidAllowlistPattern("a." + "b".repeat(253) + ".com")).toBe(false);
  });
});

describe("egress-allowlist-editor: presentational contract", () => {
  it("is a presentational component (parent owns the fetch via callbacks)", () => {
    expect(src).toContain("onSaveAllowlist");
    expect(src).toContain("onSaveMode");
    // No direct fetch inside the component -- persistence is a prop callback.
    expect(src).not.toContain("await fetch(");
  });

  it("offers audit and block modes", () => {
    expect(src).toContain('"audit"');
    expect(src).toContain('"block"');
  });

  it("states the wildcard-not-apex and best-effort limits (card honesty)", () => {
    expect(src).toContain("does not match the bare apex");
    expect(src).toContain("first-hop and best-effort");
  });
});
