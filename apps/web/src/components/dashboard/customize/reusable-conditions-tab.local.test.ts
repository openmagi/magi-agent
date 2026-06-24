import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./reusable-conditions-tab.tsx", import.meta.url),
  "utf8",
);


describe("ReusableConditionsTab — PR-F-UX5 unified verifier + condition list", () => {
  it("accepts builtinEntries as an additional prop (back-compat: optional)", () => {
    // F-UX5 spec: built-in verdict primitives merge into the same list as
    // user-authored conditions with an origin badge. Existing call sites
    // that only pass ``entries`` must keep working (user-only view).
    expect(src).toContain("builtinEntries?: NamedConditionEntry[]");
  });

  it("merges builtinEntries before user entries so built-ins surface first", () => {
    // Built-in inventory comes first so the operator sees ready-made
    // primitives before scrolling to their own rules. Spread-merge keeps
    // the caller's incoming sort intact within each half.
    expect(src).toContain("...(builtinEntries ?? [])");
    expect(src).toContain("...entries");
  });

  it("renders an origin badge: 'built-in' for catalog rows, 'user' for authored rows", () => {
    // The Verifier vs Condition distinction is origin-only in F-UX5; the
    // tab folds it into the badge rather than spawning a third tab.
    expect(src).toContain('"built-in"');
    expect(src).toContain('"user"');
  });

  it("user badge keeps the 'from policy' attribution; built-in shows the bare ref", () => {
    // Built-in rows have no owning Policy (verifier authoring is a code
    // surface, not a dashboard surface). The metadata line must NOT claim a
    // policy owner for built-in rows.
    expect(src).toMatch(/entry\.origin === "user"[\s\S]*?from policy/);
  });

  it("empty state covers BOTH halves being empty (no rule + no built-in)", () => {
    // Previously the empty-state copy referenced only user-authored
    // conditions. With the built-in half merged in, the copy must reflect
    // that nothing is in either inventory.
    expect(src).toMatch(/No verifiers or conditions yet/);
  });

  it("documents that verifier authoring is a code surface (F-UX5 principle 1)", () => {
    // The tab's header copy must surface the read-only invariant: built-in
    // verifiers are runtime code, not editable through this surface. User
    // conditions are editable through the originating policy.
    expect(src).toMatch(/runtime code|extending the runtime|code surface/i);
  });
});
