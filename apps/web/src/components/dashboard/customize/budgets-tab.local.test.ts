import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./budgets-tab.tsx", import.meta.url),
  "utf8",
);

describe("BudgetsTab — PR-F7 structural assertions", () => {
  it("exports the BudgetsTab component", () => {
    expect(src).toContain("export function BudgetsTab");
  });

  it("renders all three F7 budget fields with their MAGI_* env names", () => {
    expect(src).toContain("maxToolCallsPerTurn");
    expect(src).toContain("maxStepsBrakeHard");
    expect(src).toContain("loopGuardHardThreshold");
  });

  it("surfaces the runtime default hint for each budget so the operator never guesses", () => {
    expect(src).toContain("Runtime default: 64");
    expect(src).toContain("Runtime default: 5");
    expect(src).toContain("Runtime default: unset");
  });

  it("renders the env-name badge from the server-provided envMap (not a hardcoded literal)", () => {
    // We render `envMap[f.key]` rather than hardcoding MAGI_TOOL_MAX_CALLS_PER_TURN
    // in the component — this keeps the UI vocabulary in lockstep with the
    // backend BUDGET_ENV_MAP (the single source of truth).
    expect(src).toContain("envMap[f.key]");
    // Defensive: the test stays informative if someone tries to hardcode the
    // env name and forget the map plumbing. We do not assert MAGI_* presence
    // here because the description copy or hints could legitimately mention
    // them in prose without violating the contract.
  });

  it("flags the 'operator env wins' precedence when env value differs from the dashboard save", () => {
    expect(src).toContain("operator env wins");
    expect(src).toContain("envOverridesSave");
  });

  it("surfaces the unset-env hint so the user knows the save takes effect next turn", () => {
    expect(src).toContain("Env unset");
    expect(src).toContain("next turn");
  });

  it("uses positive-int input semantics (numeric inputMode, coercion drops non-digits)", () => {
    expect(src).toContain('inputMode="numeric"');
    expect(src).toContain("[^0-9]");
  });

  it("disables the Save button until the form is dirty", () => {
    expect(src).toContain("!dirty");
    expect(src).toContain("disabled={!dirty");
  });

  it("calls onSave with a typed VerificationBudgets payload", () => {
    expect(src).toContain("onSave(_toBudgets(values))");
    expect(src).toContain("VerificationBudgets");
  });

  it("renders an error banner with a Retry callback when load/save fails", () => {
    expect(src).toContain("onReload");
    expect(src).toContain("Retry");
  });

  it("uses the Gauge icon (matches the customize-hub sub-nav)", () => {
    expect(src).toContain("Gauge");
    expect(src).toContain('from "lucide-react"');
  });
});
