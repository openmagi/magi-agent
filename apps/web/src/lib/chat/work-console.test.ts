import { describe, expect, it } from "vitest";
import { deriveWorkConsoleRows } from "./work-console";
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
    subagents: [],
    taskBoard: null,
    fileProcessing: false,
    ...overrides,
  };
}

const subagent: SubagentActivity = {
  taskId: "blue",
  role: "explore",
  status: "running",
  detail: "Searching sources",
  startedAt: 1,
  updatedAt: 2,
};

const taskBoard: TaskBoardSnapshot = {
  receivedAt: 1,
  tasks: [
    {
      id: "research",
      title: "Research evidence",
      description: "Collect sources",
      status: "completed",
    },
    {
      id: "draft",
      title: "Draft answer",
      description: "Write the final response",
      status: "in_progress",
    },
  ],
};

const queued: QueuedMessage = {
  id: "q1",
  content: "Add a checkpoint summary",
  queuedAt: 1,
};

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

describe("deriveWorkConsoleRows", () => {
  it("prefers the UI language over inferred response language for chrome copy", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        responseLanguage: "ko",
        turnPhase: "executing",
        heartbeatElapsedMs: 81_000,
        activeTools: [{
          id: "tool-1",
          label: "Bash",
          status: "running",
          startedAt: 1,
          inputPreview: "npm test",
        }],
      }),
      queuedMessages: [queued],
      controlRequests: [request],
      uiLanguage: "en",
    });

    expect(rows.map((row) => row.label)).toEqual(
      expect.arrayContaining([
        "Running",
        "Checking the work",
        "Queued follow-up",
        "Needs approval",
      ]),
    );
    expect(rows.find((row) => row.label === "Running")?.detail).toBe("81s elapsed");
    expect(JSON.stringify(rows)).not.toContain("실행 중");
  });

  it("derives durable mission rows before short-lived task-board rows", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        turnPhase: "executing",
        missions: [{
          id: "mission-1",
          title: "Draft weekly research report",
          kind: "goal",
          status: "blocked",
          detail: "Waiting for approval to continue",
          updatedAt: 123,
        }],
        activeGoalMissionId: "mission-1",
        taskBoard,
      } as Partial<ChannelState>),
    });

    const missionRow = rows.find((row) => row.group === "mission");
    const taskIndex = rows.findIndex((row) => row.group === "task");
    const missionIndex = rows.findIndex((row) => row.group === "mission");

    expect(missionRow).toEqual(
      expect.objectContaining({
        id: "mission:mission-1",
        label: "Draft weekly research report",
        detail: "Waiting for approval to continue",
        status: "waiting",
        meta: "goal blocked",
      }),
    );
    expect(missionIndex).toBeGreaterThan(-1);
    expect(taskIndex).toBeGreaterThan(-1);
    expect(missionIndex).toBeLessThan(taskIndex);
    expect(JSON.stringify(rows)).not.toContain("payload");
  });

  it("renders structured patch previews as file-level change summaries", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        activeTools: [
          {
            id: "tool-1",
            label: "PatchApply",
            status: "running",
            startedAt: 1,
            patchPreview: {
              dryRun: false,
              changedFiles: ["src/app.ts", "src/new.ts"],
              createdFiles: ["src/new.ts"],
              deletedFiles: [],
              files: [
                {
                  path: "src/app.ts",
                  operation: "update",
                  hunks: 1,
                  addedLines: 1,
                  removedLines: 1,
                },
                {
                  path: "src/new.ts",
                  operation: "create",
                  hunks: 1,
                  addedLines: 2,
                  removedLines: 0,
                },
              ],
            },
          } as ToolActivity,
        ],
      }),
    });

    expect(rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Reviewing patch",
          detail: "2 files: src/app.ts, src/new.ts",
          snippet: "Update src/app.ts (+1/-1)\nCreate src/new.ts (+2/-0)",
        }),
      ]),
    );
    expect(JSON.stringify(rows)).not.toContain("oldSha256");
  });

  it("orders background agents before actions so long action lists do not bury agent status", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        activeTools: [
          {
            id: "tool-1",
            label: "FileRead",
            status: "running",
            startedAt: 1,
            inputPreview: JSON.stringify({ path: "book/FINAL_MANUSCRIPT.md" }),
          },
        ],
        subagents: [subagent],
      }),
    });

    const subagentIndex = rows.findIndex((row) => row.group === "subagent");
    const actionIndex = rows.findIndex((row) => row.group === "tool");

    expect(subagentIndex).toBeGreaterThan(-1);
    expect(actionIndex).toBeGreaterThan(-1);
    expect(subagentIndex).toBeLessThan(actionIndex);
  });
});
