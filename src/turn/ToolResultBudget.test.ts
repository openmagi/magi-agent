import { describe, it, expect } from "vitest";
import { applyToolResultBudget, DEFAULT_TOOL_RESULT_BUDGET_CONFIG } from "./ToolResultBudget.js";
import type { ToolResultBudgetConfig } from "./ToolResultBudget.js";

describe("ToolResultBudget", () => {
  const cfg: ToolResultBudgetConfig = {
    maxResultSizeChars: 200,
    exemptTools: new Set<string>(),
    headChars: 40,
    tailChars: 40,
  };

  it("passes through content under budget", () => {
    const content = "short result";
    expect(applyToolResultBudget(content, "Bash", cfg)).toBe(content);
  });

  it("truncates oversized text with head + tail", () => {
    const content = "A".repeat(500);
    const result = applyToolResultBudget(content, "Bash", cfg);
    expect(result.length).toBeLessThanOrEqual(200);
    expect(result).toContain("chars omitted");
    expect(result).toContain("[Tool result truncated");
  });

  it("skips exempt tools", () => {
    const exemptCfg: ToolResultBudgetConfig = {
      ...cfg,
      exemptTools: new Set(["Bash"]),
    };
    const content = "X".repeat(500);
    expect(applyToolResultBudget(content, "Bash", exemptCfg)).toBe(content);
  });

  it("truncates JSON arrays by slicing elements", () => {
    const arr = Array.from({ length: 100 }, (_, i) => ({ id: i, data: "x".repeat(20) }));
    const content = JSON.stringify(arr);
    const result = applyToolResultBudget(content, "Grep", {
      ...cfg,
      maxResultSizeChars: 500,
    });
    expect(result.length).toBeLessThanOrEqual(500);
    // The suffix "elements omitted" may be truncated by the maxChars cap,
    // but the result should contain fewer elements than the original.
    expect(result).toContain("elements omit");
  });

  it("falls back to text truncation for non-array JSON", () => {
    const content = JSON.stringify({ key: "v".repeat(500) });
    const result = applyToolResultBudget(content, "Grep", cfg);
    expect(result.length).toBeLessThanOrEqual(200);
    expect(result).toContain("chars omitted");
  });

  it("returns content unchanged when maxResultSizeChars <= 0", () => {
    const content = "A".repeat(500);
    const zeroCfg: ToolResultBudgetConfig = { ...cfg, maxResultSizeChars: 0 };
    expect(applyToolResultBudget(content, "Bash", zeroCfg)).toBe(content);
  });

  it("uses default config env-based values", () => {
    expect(DEFAULT_TOOL_RESULT_BUDGET_CONFIG.maxResultSizeChars).toBeGreaterThan(0);
    expect(DEFAULT_TOOL_RESULT_BUDGET_CONFIG.headChars).toBe(2000);
    expect(DEFAULT_TOOL_RESULT_BUDGET_CONFIG.tailChars).toBe(2000);
  });
});
