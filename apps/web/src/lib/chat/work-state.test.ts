import { describe, expect, it } from "vitest";
import { deriveWorkStateSummary } from "./work-state";
import type { ChannelState, ToolActivity } from "./types";

function channelState(overrides: Partial<ChannelState> = {}): ChannelState {
  return {
    streaming: false,
    streamingText: "",
    thinkingText: "",
    error: null,
    hasTextContent: false,
    thinkingStartedAt: null,
    turnPhase: null,
    heartbeatElapsedMs: null,
    pendingInjectionCount: 0,
    activeTools: [],
    taskBoard: null,
    fileProcessing: false,
    ...overrides,
  };
}

describe("deriveWorkStateSummary", () => {
  it("uses the current user request as the fallback goal for tool-only work", () => {
    const tool: ToolActivity = {
      id: "tool-1",
      label: "Bash",
      status: "running",
      startedAt: 1,
    };

    const summary = deriveWorkStateSummary({
      channelState: channelState({
        streaming: true,
        currentGoal: "Spawn 4 subagents and cross-validate 1+1.",
        turnPhase: "executing",
        activeTools: [tool],
      }),
    });

    expect(summary.goal).toBe("Spawn 4 subagents and cross-validate 1+1.");
  });

  it("uses active goal missions as the visible work goal", () => {
    const summary = deriveWorkStateSummary({
      channelState: channelState({
        streaming: true,
        turnPhase: "executing",
        missions: [{
          id: "mission-1",
          title: "Draft weekly research report",
          kind: "goal",
          status: "running",
          updatedAt: 123,
        }],
        activeGoalMissionId: "mission-1",
      }),
    });

    expect(summary.goal).toBe("Draft weekly research report");
  });

  it("prefers the UI language over the inferred response language for chrome copy", () => {
    const summary = deriveWorkStateSummary({
      channelState: channelState({
        streaming: true,
        responseLanguage: "ko",
        turnPhase: "executing",
        activeTools: [
          {
            id: "tool-1",
            label: "Bash",
            status: "running",
            startedAt: 1,
          },
        ],
      }),
      uiLanguage: "en",
    });

    expect(summary).toEqual({
      title: "Current Work",
      goal: "Working on your request",
      status: "Running",
      progress: "1 action active",
      now: "Bash",
    });
  });
});
