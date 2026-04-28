import { describe, expect, it } from "vitest";
import {
  DebugWorkflow,
  isDebugRelevantTurn,
} from "./DebugWorkflow.js";

describe("DebugWorkflow", () => {
  it("classifies bug and failure turns conservatively", () => {
    expect(isDebugRelevantTurn("The regression is still failing in production")).toBe(true);
    expect(isDebugRelevantTurn("오류 원인 찾아서 고쳐줘")).toBe(true);
    expect(isDebugRelevantTurn("Add a new dashboard widget")).toBe(false);
  });

  it("tracks investigation, patch, hypothesis, and verification checkpoints per turn", () => {
    const workflow = new DebugWorkflow();
    workflow.classifyTurn("session-1", "turn-1", "The tests are failing after yesterday's change");
    workflow.recordInspection("session-1", "turn-1", "FileRead");
    workflow.recordHypothesis("session-1", "turn-1", "Likely cause is the stale cache key.");
    workflow.recordPatch("session-1", "turn-1", "FileEdit");
    workflow.recordVerification("session-1", "turn-1", "npm test");

    expect(workflow.getTurnState("session-1", "turn-1")).toMatchObject({
      classified: true,
      investigated: true,
      hypothesized: true,
      patched: true,
      verified: true,
      warnings: [],
    });
  });

  it("reports a compact status summary", () => {
    const workflow = new DebugWorkflow();
    workflow.classifyTurn("session-1", "turn-1", "debug this regression");
    workflow.recordInspection("session-1", "turn-1", "Grep");

    expect(workflow.status()).toEqual({
      enabled: true,
      activeTurns: 1,
      latest: {
        sessionKey: "session-1",
        turnId: "turn-1",
        classified: true,
        investigated: true,
        hypothesized: false,
        patched: false,
        verified: false,
        warnings: [],
      },
    });
  });
});
