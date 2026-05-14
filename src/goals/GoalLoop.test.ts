import { describe, expect, it } from "vitest";
import {
  buildGoalContinuationMessage,
  canContinueGoal,
  goalLoopMaxTurns,
  goalRequestFromMessage,
  parseGoalSpecResult,
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

describe("parseGoalSpecResult", () => {
  it("uses compact structured title, objective, and completion criteria", () => {
    expect(
      parseGoalSpecResult(
        JSON.stringify({
          title: "내외디스틸러리 TIPS 투자심의",
          objective: "내외디스틸러리의 1억원 TIPS LP 투자 여부를 검토한다.",
          completionCriteria: [
            "시장 전망과 회사 리스크 검토",
            "재무제표 기반 투자 판단",
            "최종 IC 보고서 작성",
          ],
        }),
        "raw long request",
      ),
    ).toEqual({
      title: "내외디스틸러리 TIPS 투자심의",
      objective: "내외디스틸러리의 1억원 TIPS LP 투자 여부를 검토한다.",
      completionCriteria: [
        "시장 전망과 회사 리스크 검토",
        "재무제표 기반 투자 판단",
        "최종 IC 보고서 작성",
      ],
    });
  });

  it("falls back without exposing an unbounded raw request as the title", () => {
    const raw =
      "이 자료들을 기반으로 내외디스틸러리에 대한 TIPS LP 투자(1억원) 건에 대해 투심위를 열어줘. ".repeat(
        20,
      );

    const spec = parseGoalSpecResult("not json", raw);

    expect(spec.title.length).toBeLessThanOrEqual(80);
    expect(spec.title).not.toBe(raw);
    expect(spec.objective.length).toBeLessThanOrEqual(500);
    expect(spec.objective).not.toBe(raw.trim());
    expect(spec.completionCriteria).toEqual([
      "Deliver a clear completion update for this goal.",
    ]);
  });
});

describe("goalLoopMaxTurns", () => {
  it("defaults to thirty continuation turns", () => {
    expect(goalLoopMaxTurns({})).toBe(30);
  });

  it("clamps env overrides to a bounded range", () => {
    expect(goalLoopMaxTurns({ MAGI_GOAL_MAX_TURNS: "0" })).toBe(1);
    expect(goalLoopMaxTurns({ MAGI_GOAL_MAX_TURNS: "12" })).toBe(12);
    expect(goalLoopMaxTurns({ MAGI_GOAL_MAX_TURNS: "30" })).toBe(30);
    expect(goalLoopMaxTurns({ MAGI_GOAL_MAX_TURNS: "200" })).toBe(50);
    expect(goalLoopMaxTurns({ MAGI_GOAL_MAX_TURNS: "not-a-number" })).toBe(30);
  });
});

describe("buildGoalContinuationMessage", () => {
  it("builds a synthetic continuation message with durable goal metadata", () => {
    const message = buildGoalContinuationMessage({
      objective: "Ship the launch memo",
      missionId: "mission-1",
      missionRunId: "run-1",
      turnsUsed: 2,
      maxTurns: 30,
      previousAssistantText: "I drafted the outline.",
      reason: "Need to write the final memo",
    });

    expect(message.text).toContain("Continue working toward this goal");
    expect(message.text).toContain("Ship the launch memo");
    expect(message.text).toContain("Need to write the final memo");
    expect(message.text).toContain("Completion criteria");
    expect(message.metadata).toMatchObject({
      goalMode: true,
      goalContinuation: true,
      goalObjective: "Ship the launch memo",
      goalCompletionCriteria: ["Deliver a clear completion update for this goal."],
      missionId: "mission-1",
      missionRunId: "run-1",
      goalTurnsUsed: 2,
      goalMaxTurns: 30,
      missionKind: "goal",
    });
    expect(String(message.metadata?.systemPromptAddendum)).toContain(
      "autonomous goal continuation",
    );
  });
});
