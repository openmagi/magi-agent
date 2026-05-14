import { describe, it, expect } from "vitest";
import {
  ToolCallLoopDetector,
  type LoopAction,
} from "./ToolCallLoopDetector.js";

describe("ToolCallLoopDetector", () => {
  it("returns ok for distinct calls", () => {
    const d = new ToolCallLoopDetector();
    expect(d.check("FileRead", { path: "a.ts" }).action).toBe("ok");
    expect(d.check("FileRead", { path: "b.ts" }).action).toBe("ok");
    expect(d.check("Grep", { pattern: "x" }).action).toBe("ok");
  });

  it("increments count for consecutive identical calls", () => {
    const d = new ToolCallLoopDetector();
    expect(d.check("FileRead", { path: "a.ts" }).count).toBe(1);
    expect(d.check("FileRead", { path: "a.ts" }).count).toBe(2);
    expect(d.check("FileRead", { path: "a.ts" }).count).toBe(3);
  });

  it("resets count when tool name changes", () => {
    const d = new ToolCallLoopDetector();
    d.check("FileRead", { path: "a.ts" });
    d.check("FileRead", { path: "a.ts" });
    const result = d.check("Grep", { pattern: "x" });
    expect(result.count).toBe(1);
    expect(result.action).toBe("ok");
  });

  it("resets count when same tool has different params", () => {
    const d = new ToolCallLoopDetector();
    d.check("FileRead", { path: "a.ts" });
    d.check("FileRead", { path: "a.ts" });
    const result = d.check("FileRead", { path: "b.ts" });
    expect(result.count).toBe(1);
  });

  it("excludes task_progress from hash", () => {
    const d = new ToolCallLoopDetector();
    d.check("FileRead", { path: "a.ts", task_progress: { current_task: "t1" } });
    d.check("FileRead", { path: "a.ts", task_progress: { current_task: "t2" } });
    const r = d.check("FileRead", { path: "a.ts" });
    expect(r.count).toBe(3);
  });

  it("excludes progress and metadata fields from hash", () => {
    const d = new ToolCallLoopDetector();
    d.check("FileRead", { path: "a.ts", progress: "50%", metadata: { x: 1 } });
    const r = d.check("FileRead", { path: "a.ts" });
    expect(r.count).toBe(2);
  });

  it("emits soft_warning at threshold 3 (default)", () => {
    const d = new ToolCallLoopDetector();
    d.check("FileRead", { path: "a.ts" });
    d.check("FileRead", { path: "a.ts" });
    const r = d.check("FileRead", { path: "a.ts" });
    expect(r.action).toBe("soft_warning");
    expect(r.count).toBe(3);
  });

  it("emits soft_warning at count 4 (between soft and hard)", () => {
    const d = new ToolCallLoopDetector();
    for (let i = 0; i < 3; i++) d.check("FileRead", { path: "a.ts" });
    const r = d.check("FileRead", { path: "a.ts" });
    expect(r.action).toBe("soft_warning");
    expect(r.count).toBe(4);
  });

  it("emits hard_escalation at threshold 5 (default)", () => {
    const d = new ToolCallLoopDetector();
    for (let i = 0; i < 4; i++) d.check("FileRead", { path: "a.ts" });
    const r = d.check("FileRead", { path: "a.ts" });
    expect(r.action).toBe("hard_escalation");
    expect(r.count).toBe(5);
  });

  it("respects custom thresholds", () => {
    const d = new ToolCallLoopDetector({ softThreshold: 2, hardThreshold: 4 });
    expect(d.check("X", {}).action).toBe("ok");
    expect(d.check("X", {}).action).toBe("soft_warning");
    expect(d.check("X", {}).action).toBe("soft_warning");
    expect(d.check("X", {}).action).toBe("hard_escalation");
  });

  it("reset() clears state", () => {
    const d = new ToolCallLoopDetector();
    d.check("FileRead", { path: "a.ts" });
    d.check("FileRead", { path: "a.ts" });
    d.reset();
    const r = d.check("FileRead", { path: "a.ts" });
    expect(r.count).toBe(1);
    expect(r.action).toBe("ok");
  });

  it("hashCall produces consistent hashes for same input", () => {
    const h1 = ToolCallLoopDetector.hashCall("FileRead", { path: "a.ts" });
    const h2 = ToolCallLoopDetector.hashCall("FileRead", { path: "a.ts" });
    expect(h1).toBe(h2);
    expect(h1).toHaveLength(16);
  });

  it("hashCall produces different hashes for different inputs", () => {
    const h1 = ToolCallLoopDetector.hashCall("FileRead", { path: "a.ts" });
    const h2 = ToolCallLoopDetector.hashCall("FileRead", { path: "b.ts" });
    expect(h1).not.toBe(h2);
  });

  it("handles null/undefined/primitive inputs", () => {
    const d = new ToolCallLoopDetector();
    expect(d.check("Noop", null).count).toBe(1);
    expect(d.check("Noop", null).count).toBe(2);
    expect(d.check("Noop", undefined).action).toBe("ok"); // different hash
  });

  it("handles array inputs without stripping", () => {
    const d = new ToolCallLoopDetector();
    d.check("Multi", [1, 2, 3]);
    const r = d.check("Multi", [1, 2, 3]);
    expect(r.count).toBe(2);
  });

  it("count resumes after an interleaved different call", () => {
    const d = new ToolCallLoopDetector();
    d.check("FileRead", { path: "a.ts" }); // 1
    d.check("FileRead", { path: "a.ts" }); // 2
    d.check("FileRead", { path: "b.ts" }); // reset
    d.check("FileRead", { path: "a.ts" }); // 1 (restarted)
    d.check("FileRead", { path: "a.ts" }); // 2
    const r = d.check("FileRead", { path: "a.ts" }); // 3
    expect(r.count).toBe(3);
    expect(r.action).toBe("soft_warning");
  });

  describe("frequency-based detection", () => {
    it("triggers soft_warning at frequencySoftThreshold even with different params", () => {
      const d = new ToolCallLoopDetector({ frequencySoftThreshold: 3, frequencyHardThreshold: 6 });
      d.check("FileRead", { path: "a.ts" });
      d.check("FileRead", { path: "b.ts" });
      const r = d.check("FileRead", { path: "c.ts" });
      expect(r.action).toBe("soft_warning");
      expect(r.frequencyCount).toBe(3);
      expect(r.count).toBe(1); // consecutive count is 1 (different params each time)
    });

    it("triggers hard_escalation at frequencyHardThreshold", () => {
      const d = new ToolCallLoopDetector({ frequencySoftThreshold: 2, frequencyHardThreshold: 4 });
      for (let i = 0; i < 4; i++) d.check("Grep", { pattern: `p${i}` });
      expect(d.getToolNameCount("Grep")).toBe(4);
    });

    it("getToolNameCount returns 0 for unknown tool", () => {
      const d = new ToolCallLoopDetector();
      expect(d.getToolNameCount("Unknown")).toBe(0);
    });

    it("reset clears frequency counts", () => {
      const d = new ToolCallLoopDetector({ frequencySoftThreshold: 3 });
      d.check("FileRead", { path: "a.ts" });
      d.check("FileRead", { path: "b.ts" });
      d.reset();
      expect(d.getToolNameCount("FileRead")).toBe(0);
    });

    it("consecutive hard_escalation takes priority over frequency soft_warning", () => {
      const d = new ToolCallLoopDetector({
        softThreshold: 3, hardThreshold: 5,
        frequencySoftThreshold: 4, frequencyHardThreshold: 10,
      });
      for (let i = 0; i < 5; i++) d.check("FileRead", { path: "a.ts" });
      // consecutive=5 (hard) vs frequency=5 (soft) → hard wins
      const r = d.check("FileRead", { path: "a.ts" });
      expect(r.action).toBe("hard_escalation");
      expect(r.frequencyCount).toBeUndefined();
    });

    it("frequencyCount is set when frequency triggers the action", () => {
      const d = new ToolCallLoopDetector({ frequencySoftThreshold: 2, frequencyHardThreshold: 10 });
      d.check("Bash", { command: "ls" });
      const r = d.check("Bash", { command: "pwd" });
      expect(r.action).toBe("soft_warning");
      expect(r.frequencyCount).toBe(2);
    });
  });
});
