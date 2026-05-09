import { describe, expect, it } from "vitest";
import {
  buildGoalContinuationMessage,
  canContinueGoal,
  goalLoopMaxTurns,
  goalRequestFromMessage,
  type GoalLoopState,
} from "./GoalLoop.js";

function state(overrides: Partial<GoalLoopState> = {}): GoalLoopState {
  return {
    missionId: "mission-1",
    objective: "Ship the report",
    turnsUsed: 0,
    maxTurns: 5,
    paused: false,
    cancelled: false,
    ...overrides,
  };
}

describe("canContinueGoal", () => {
  it("continues while under the turn budget", () => {
    expect(canContinueGoal(state({ turnsUsed: 4, maxTurns: 5 }))).toBe(true);
  });

  it("stops at the max turn budget", () => {
    expect(canContinueGoal(state({ turnsUsed: 5, maxTurns: 5 }))).toBe(false);
  });

  it("stops when paused or cancelled", () => {
    expect(canContinueGoal(state({ paused: true }))).toBe(false);
    expect(canContinueGoal(state({ cancelled: true }))).toBe(false);
  });
});

describe("goalRequestFromMessage", () => {
  it("detects /goal and strips the command from the visible turn text", () => {
    expect(goalRequestFromMessage({ text: "/goal Draft the launch memo" })).toEqual({
      objective: "Draft the launch memo",
      text: "Draft the launch memo",
    });
  });

  it("detects composer goalMode without rewriting normal text", () => {
    expect(
      goalRequestFromMessage({ text: "Draft the launch memo", goalMode: true }),
    ).toEqual({
      objective: "Draft the launch memo",
      text: "Draft the launch memo",
    });
  });

  it("ignores empty /goal requests", () => {
    expect(goalRequestFromMessage({ text: "/goal   " })).toBeNull();
  });
});

describe("goalLoopMaxTurns", () => {
  it("defaults to five continuation turns", () => {
    expect(goalLoopMaxTurns({})).toBe(5);
  });

  it("clamps env overrides to a bounded range", () => {
    expect(goalLoopMaxTurns({ CORE_AGENT_GOAL_MAX_TURNS: "0" })).toBe(1);
    expect(goalLoopMaxTurns({ CORE_AGENT_GOAL_MAX_TURNS: "12" })).toBe(12);
    expect(goalLoopMaxTurns({ CORE_AGENT_GOAL_MAX_TURNS: "200" })).toBe(20);
    expect(goalLoopMaxTurns({ CORE_AGENT_GOAL_MAX_TURNS: "not-a-number" })).toBe(5);
  });
});

describe("buildGoalContinuationMessage", () => {
  it("builds a synthetic continuation message with durable goal metadata", () => {
    const message = buildGoalContinuationMessage({
      objective: "Ship the launch memo",
      missionId: "mission-1",
      missionRunId: "run-1",
      turnsUsed: 2,
      maxTurns: 5,
      previousAssistantText: "I drafted the outline.",
      reason: "Need to write the final memo",
    });

    expect(message.text).toContain("Continue working toward this goal");
    expect(message.text).toContain("Ship the launch memo");
    expect(message.text).toContain("Need to write the final memo");
    expect(message.metadata).toMatchObject({
      goalMode: true,
      goalContinuation: true,
      goalObjective: "Ship the launch memo",
      missionId: "mission-1",
      missionRunId: "run-1",
      goalTurnsUsed: 2,
      goalMaxTurns: 5,
      missionKind: "goal",
    });
    expect(String(message.metadata?.systemPromptAddendum)).toContain(
      "autonomous goal continuation",
    );
  });
});
