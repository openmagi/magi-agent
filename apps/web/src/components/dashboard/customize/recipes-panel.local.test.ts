import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./customize-hub.tsx", import.meta.url),
  "utf8",
);

describe("RecipesPanel — F-UX10 write-surface structural assertions", () => {
  it("imports the patchRecipeOverride helper from customize-api", () => {
    expect(src).toContain("patchRecipeOverride");
  });

  it("declares optimistic state buckets for recipes (enabled set / pending set / error)", () => {
    // Mirrors the BehaviorsPanel pattern — three independent buckets so the
    // toggle can render the optimistic flip, pending overlay, and revert
    // banner without leaking state across other tabs.
    expect(src).toContain("enabledRecipeIds");
    expect(src).toContain("recipePending");
    expect(src).toContain("recipeError");
  });

  it("seeds enabledRecipeIds from data.overrides.verification.recipes (allowlist source)", () => {
    expect(src).toContain("data?.overrides.verification.recipes");
  });

  it("handleToggleRecipe calls patchRecipeOverride and reconciles from the response", () => {
    expect(src).toContain("handleToggleRecipe");
    expect(src).toContain("patchRecipeOverride(agentFetch, id, enabled)");
    // Reconcile from authoritative server response — the optimistic state is
    // overwritten with overrides.verification.recipes after the PATCH lands.
    expect(src).toContain("overrides.verification.recipes");
  });

  it("reverts the optimistic toggle on PATCH failure", () => {
    // The catch arm must invert the optimistic state change (add↔delete) so
    // the user sees the row snap back to its prior state rather than a silent
    // disagreement with disk.
    expect(src).toContain("Failed to update recipe");
  });

  it("passes the toggle handler + state down to RecipesPanel", () => {
    expect(src).toMatch(/<RecipesPanel[\s\S]*?onToggle=\{handleToggleRecipe\}/);
    expect(src).toMatch(/<RecipesPanel[\s\S]*?enabledRecipeIds=\{enabledRecipeIds\}/);
    expect(src).toMatch(/<RecipesPanel[\s\S]*?pendingIds=\{recipePending\}/);
    expect(src).toMatch(/<RecipesPanel[\s\S]*?error=\{recipeError\}/);
  });

  it("renders a real role=switch toggle per recipe row (not a static read-only list)", () => {
    // RecipeToggle uses role="switch" so screen readers announce the
    // enable/disable affordance; this is the assertion that catches a
    // regression to the read-only pre-F-UX10 surface.
    expect(src).toContain('role="switch"');
    expect(src).toContain("RecipeToggle");
  });

  it("disables the toggle for unmapped recipes with an explanatory tooltip", () => {
    // Honest-degrade: when packIds is empty the toggle has no live effect.
    // The component must surface that with a disabled affordance AND a
    // tooltip so the operator does not assume the click was silently dropped.
    expect(src).toContain("UI label has no live mapping; toggling is a no-op");
    expect(src).toContain("toggleDisabled");
  });

  it("applies allowlist semantics in the checked computation", () => {
    // Empty enabledRecipeIds → every row reads enabled (legacy byte-identical).
    // Non-empty → only ids present in the set read enabled.
    expect(src).toContain("hasExplicitAllowlist");
    expect(src).toContain("enabledRecipeIds.has(r.id)");
  });

  it("removes the legacy 'write surface ships in a follow-up PR' caveat copy", () => {
    expect(src).not.toContain("write surface ships in a follow-up PR");
  });

  it("special-cases the first-disable from an empty allowlist by seeding peers", () => {
    // BLOCKER fix (2026-06-24): from a fresh install the persisted allowlist
    // is empty, which the backend treats as "no override → everything ON".
    // A naïve PATCH(id, false) is a silent no-op (set_verification_override
    // only remove()s when the id is already present). The handler must
    // detect this state and seed the allowlist with every OTHER mapped
    // recipe first so the row being disabled is the only one dropped.
    expect(src).toContain("firstDisable");
    expect(src).toContain("enabledRecipeIds.size === 0");
    // The seed pass enables peers (mapped recipes other than id) and skips
    // unmapped UI-only labels (packIds.length === 0) which have no live effect.
    expect(src).toMatch(/r\.id !== id/);
    expect(src).toMatch(/r\.packIds(?:\s*\?\.length|\)\s*&&\s*r\.packIds\.length)/);
    // The seed pass calls patchRecipeOverride(seedId, true) for each peer.
    expect(src).toContain("patchRecipeOverride(agentFetch, seedId, true)");
  });

  it("documents the seed semantics in the panel copy", () => {
    // The user-visible copy must explain the two states (no-override vs
    // explicit allowlist) and the first-disable seed behaviour so the
    // operator understands the toggle did not silently flip back.
    expect(src).toMatch(/first opt-out seeds the allowlist/);
  });

  it("reverts to an empty set on first-disable seed failure", () => {
    // When the seed pass throws (e.g. one of the peer PATCHes 404s), the
    // optimistic flip must revert back to the empty set the user started
    // from — NOT a partial allowlist that would silently disable the
    // peers whose PATCH did land before the failure. (The persisted state
    // on disk may still hold partial enables; the next data refresh will
    // reconcile it.)
    expect(src).toMatch(/if \(firstDisable\) return new Set\(\);/);
  });
});
