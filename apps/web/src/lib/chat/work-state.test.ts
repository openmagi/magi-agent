import { describe, expect, it } from "vitest";
import { deriveWorkStateSummary } from "./work-state";
import type {
  ChannelState,
  ControlRequestRecord,
  QueuedMessage,
  SubagentActivity,
  TaskBoardSnapshot,
  ToolActivity,
} from "./types";

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

const taskBoard: TaskBoardSnapshot = {
  receivedAt: 1,
  tasks: [
    {
      id: "research",
      title: "Research sources",
      description: "Collect evidence",
      status: "completed",
    },
    {
      id: "draft",
      title: "Draft report",
      description: "Write the main answer",
      status: "in_progress",
    },
    {
      id: "review",
      title: "Review claims",
      description: "Check weak assertions",
      status: "pending",
      dependsOn: ["research"],
    },
  ],
};

describe("deriveWorkStateSummary", () => {
  it("summarizes an active task board without inventing a percentage", () => {
    const summary = deriveWorkStateSummary({
      channelState: channelState({
        streaming: true,
        turnPhase: "executing",
        taskBoard,
      }),
    });

    expect(summary).toEqual({
      title: "Current Work",
      goal: "Draft report",
      status: "Running",
      progress: "1/3 tasks complete",
      now: "Draft report",
      next: "Review claims",
    });
  });

  it("prioritizes pending control requests over active work", () => {
    const request: ControlRequestRecord = {
      requestId: "cr-1",
      kind: "tool_permission",
      state: "pending",
      sessionKey: "agent:main:app:general",
      channelName: "general",
      source: "turn",
      prompt: "Allow Bash?",
      createdAt: 1,
      expiresAt: 2,
    };
    const tool: ToolActivity = {
      id: "tool-1",
      label: "Bash",
      status: "running",
      startedAt: 1,
    };

    const summary = deriveWorkStateSummary({
      channelState: channelState({
        streaming: true,
        turnPhase: "executing",
        activeTools: [tool],
      }),
      controlRequests: [request],
    });

    expect(summary.status).toBe("Needs approval");
    expect(summary.now).toBe("Waiting for tool permission");
    expect(summary.next).toBe("Allow Bash?");
  });

  it("shows queued follow-up as the next step while preserving current execution", () => {
    const queued: QueuedMessage[] = [
      {
        id: "q1",
        content: "Add a checkpoint summary",
        queuedAt: 1,
      },
    ];
    const tool: ToolActivity = {
      id: "tool-1",
      label: "FileRead",
      status: "running",
      startedAt: 1,
    };

    const summary = deriveWorkStateSummary({
      channelState: channelState({
        streaming: true,
        turnPhase: "executing",
        activeTools: [tool],
      }),
      queuedMessages: queued,
    });

    expect(summary.status).toBe("Running");
    expect(summary.progress).toBe("1 action active");
    expect(summary.now).toBe("FileRead");
    expect(summary.next).toBe("Add a checkpoint summary");
  });

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

  it("surfaces active background agents as visible work", () => {
    const subagent: SubagentActivity = {
      taskId: "blue",
      role: "explore",
      status: "running",
      detail: "Searching sources",
      startedAt: 1,
      updatedAt: 2,
    };

    const summary = deriveWorkStateSummary({
      channelState: channelState({
        streaming: true,
        turnPhase: "executing",
        subagents: [subagent],
      }),
    });

    expect(summary.status).toBe("Running");
    expect(summary.progress).toBe("1 background agent active");
    expect(summary.now).toBe("Searching sources");
  });

  it("localizes generated run summary text to the response language", () => {
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
    });

    expect(summary).toEqual({
      title: "현재 작업",
      goal: "요청 처리 중",
      status: "실행 중",
      progress: "1개 작업 실행 중",
      now: "Bash",
    });
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

  it("marks reconnecting and error states explicitly", () => {
    expect(
      deriveWorkStateSummary({
        channelState: channelState({
          streaming: true,
          reconnecting: true,
          turnPhase: "executing",
        }),
      }).status,
    ).toBe("Reconnecting");

    expect(
      deriveWorkStateSummary({
        channelState: channelState({
          streaming: true,
          error: "tool failed",
          turnPhase: "executing",
        }),
      }).status,
    ).toBe("Blocked");
  });

  it("does not use raw long user requests as the displayed work goal", () => {
    const rawGoal = "이 자료들을 기반으로 내외디스틸러리에 대한 TIPS LP 투자(1억원) 건에 대해 투심위를 열어줘. ".repeat(6);

    const summary = deriveWorkStateSummary({
      channelState: channelState({
        streaming: true,
        turnPhase: "executing",
        currentGoal: rawGoal,
        responseLanguage: "ko",
      }),
    });

    expect(summary.goal).toBe("요청 처리 중");
    expect(summary.goal).not.toBe(rawGoal.trim());
  });
});
