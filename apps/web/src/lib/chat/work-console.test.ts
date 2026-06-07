import { describe, expect, it } from "vitest";
import { deriveWorkConsoleRows } from "./work-console";
import type {
  ChannelState,
  ControlRequestRecord,
  MissionActivity,
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

const tool: ToolActivity = {
  id: "tool-1",
  label: "Bash",
  status: "running",
  startedAt: 1,
  inputPreview: "npm test",
};

const subagent: SubagentActivity = {
  taskId: "blue",
  role: "explore",
  status: "running",
  detail: "Searching sources",
  startedAt: Date.now() - 10_000,
  updatedAt: Date.now(),
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

const mission: MissionActivity = {
  id: "mission-1",
  title: "Draft weekly research report",
  kind: "goal",
  status: "blocked",
  detail: "Waiting for approval to continue",
  updatedAt: 123,
};

describe("deriveWorkConsoleRows", () => {
  it("derives public rows for tools, subagents, tasks, queued steering, and controls", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        turnPhase: "executing",
        heartbeatElapsedMs: 81_000,
        activeTools: [tool],
        subagents: [subagent],
        taskBoard,
      }),
      queuedMessages: [queued],
      controlRequests: [request],
    });

    expect(rows.map((row) => row.label)).toEqual(
      expect.arrayContaining([
        "Running",
        "Checking the work",
        "Halley",
        "Research evidence",
        "Draft answer",
        "Queued follow-up",
        "Needs approval",
      ]),
    );
    expect(rows.find((row) => row.label === "Running")?.detail).toContain("81s");
    expect(rows.find((row) => row.label === "Checking the work")?.detail).toBe(
      "Running tests",
    );
    expect(rows.find((row) => row.label === "Halley")?.detail).toContain("Searching sources");
    expect(rows.find((row) => row.label === "Queued follow-up")?.detail).toBe(
      "Add a checkpoint summary",
    );
    expect(rows.find((row) => row.label === "Needs approval")?.detail).toBe("Allow Bash?");
  });

  it("localizes live progress labels to the current response language", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        responseLanguage: "ko",
        turnPhase: "executing",
        heartbeatElapsedMs: 81_000,
        activeTools: [
          {
            id: "tool-1",
            label: "Bash",
            status: "running",
            startedAt: 1,
            inputPreview: "npm test",
          },
          {
            id: "tool-2",
            label: "Calculation",
            status: "done",
            startedAt: 2,
            outputPreview: JSON.stringify({
              operation: "sum",
              result: 2,
              rowCount: 2,
            }),
          },
          {
            id: "tool-3",
            label: "SpawnAgent",
            status: "done",
            startedAt: 3,
            inputPreview: JSON.stringify({
              prompt: "1+1을 계산해.",
            }),
            outputPreview: JSON.stringify({
              status: "ok",
              finalText: "RESULT: 2\nREASONING: 1과 1을 더하면 2입니다.",
            }),
          },
        ],
      }),
    });

    expect(rows.map((row) => row.label)).toEqual(
      expect.arrayContaining([
        "실행 중",
        "작업 확인",
        "합계 계산 완료",
        "도우미 결과",
      ]),
    );
    expect(rows.find((row) => row.label === "실행 중")?.detail).toBe("81초 경과");
    expect(rows.find((row) => row.label === "작업 확인")?.detail).toBe("테스트 실행 중");
    expect(rows.find((row) => row.label === "합계 계산 완료")?.detail).toBe("2행 확인");
    expect(rows.find((row) => row.label === "도우미 결과")?.snippet).toBe(
      "결과: 2\n이유: 1과 1을 더하면 2입니다.",
    );
  });

  it("prefers the UI language over the inferred response language for chrome copy", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        responseLanguage: "ko",
        turnPhase: "executing",
        heartbeatElapsedMs: 81_000,
        activeTools: [tool],
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
        missions: [mission],
        activeGoalMissionId: "mission-1",
        taskBoard,
      }),
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

  it("derives runtime verifier trace rows before tool rows", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        responseLanguage: "ko",
        runtimeTraces: [
          {
            turnId: "turn-1",
            phase: "verifier_blocked",
            severity: "warning",
            title: "Runtime verifier blocked completion",
            detail: "The draft promised work without tool evidence.",
            reasonCode: "GOAL_PROGRESS_EXECUTE_NEXT",
            attempt: 1,
            maxAttempts: 3,
            retryable: true,
            requiredAction: "Call the required tool before answering.",
            receivedAt: 10,
          },
        ],
        activeTools: [tool],
      }),
    });

    const traceRow = rows.find((row) => row.group === "trace");
    const traceIndex = rows.findIndex((row) => row.group === "trace");
    const toolIndex = rows.findIndex((row) => row.group === "tool");

    expect(traceRow).toMatchObject({
      label: "런타임 검증 차단",
      detail: "Call the required tool before answering.",
      snippet: "The draft promised work without tool evidence.",
      status: "waiting",
      meta: "GOAL_PROGRESS_EXECUTE_NEXT 1/3",
    });
    expect(traceIndex).toBeGreaterThan(-1);
    expect(toolIndex).toBeGreaterThan(-1);
    expect(traceIndex).toBeLessThan(toolIndex);
  });

  it("does not expose private thinking text", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        turnPhase: "planning",
        thinkingText: "private chain of thought",
        activeTools: [
          {
            id: "tool-1",
            label: "Search",
            status: "running",
            startedAt: 1,
            outputPreview: "public tool output",
          },
        ],
      }),
    });

    expect(JSON.stringify(rows)).not.toContain("private chain of thought");
    expect(JSON.stringify(rows)).toContain("public tool output");
  });

  it("adds useful public file targets and bounded snippets to tool rows", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        activeTools: [
          {
            id: "tool-1",
            label: "FileWrite",
            status: "running",
            startedAt: 1,
            inputPreview: JSON.stringify({
              path: "book/FINAL_MANUSCRIPT.md",
              content: "Opening paragraph\nSecond paragraph",
            }),
          },
          {
            id: "tool-2",
            label: "FileEdit",
            status: "done",
            startedAt: 2,
            inputPreview: JSON.stringify({
              file_path: "book/chapter-1.md",
              old_string: "too robotic",
              new_string: "more natural",
            }),
          },
        ],
      }),
    });

    expect(rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Creating document",
          detail: "book/FINAL_MANUSCRIPT.md",
          snippet: "Opening paragraph\nSecond paragraph",
        }),
        expect.objectContaining({
          label: "Updating document",
          detail: "book/chapter-1.md",
          snippet: "Replace: too robotic -> more natural",
        }),
      ]),
    );
    expect(rows.find((row) => row.label === "Creating document")?.meta).toBeUndefined();
    expect(rows.find((row) => row.label === "Updating document")?.meta).toBeUndefined();
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

  it("omits low-signal technical search actions from user-facing rows", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        activeTools: [
          {
            id: "tool-1",
            label: "Grep",
            status: "running",
            startedAt: 1,
            inputPreview: JSON.stringify({ pattern: "TODO", path: "book" }),
          },
          {
            id: "tool-2",
            label: "Glob",
            status: "done",
            startedAt: 2,
            inputPreview: JSON.stringify({ pattern: "**/*.md" }),
          },
          {
            id: "tool-3",
            label: "FileRead",
            status: "running",
            startedAt: 3,
            inputPreview: JSON.stringify({ path: "book/FINAL_MANUSCRIPT.md" }),
          },
        ],
      }),
    });

    expect(rows.some((row) => row.label === "Grep" || row.label === "Glob")).toBe(false);
    expect(rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Reviewing document",
          detail: "book/FINAL_MANUSCRIPT.md",
        }),
      ]),
    );
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

  it("turns low-signal subagent permission details into public status copy", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        subagents: [
          {
            taskId: "permission-check",
            role: "explore",
            status: "waiting",
            detail: "allow",
            startedAt: Date.now() - 1000,
            updatedAt: Date.now(),
          },
        ],
      }),
    });

    const agent = rows.find((row) => row.group === "subagent");

    expect(agent).toEqual(
      expect.objectContaining({
        label: "Halley",
        detail: "Checking permissions",
        status: "waiting",
        meta: "explorer",
      }),
    );
    expect(JSON.stringify(rows)).not.toContain("allow");
  });

  it("does not repeat generic action previews in both detail and snippet", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        activeTools: [
          {
            id: "tool-1",
            label: "Custom progress",
            status: "running",
            startedAt: 1,
            inputPreview: "allow",
          },
        ],
      }),
    });

    const action = rows.find((row) => row.group === "tool");

    expect(action).toEqual(
      expect.objectContaining({
        label: "Custom progress",
        snippet: "allow",
      }),
    );
    expect(action?.detail).toBeUndefined();
  });

  it("omits low-signal subagent heartbeat actions from user-facing rows", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        activeTools: [
          {
            id: "tool-1",
            label: "Subagent running",
            status: "running",
            startedAt: 1,
            inputPreview: "iteration 2",
          },
          {
            id: "tool-2",
            label: "Subagent tool decision",
            status: "done",
            startedAt: 2,
            inputPreview: "allow",
          },
          {
            id: "tool-3",
            label: "SpawnAgent",
            status: "running",
            startedAt: 3,
            inputPreview: JSON.stringify({
              persona: "calculator-gemini-pro",
              prompt: "Calculate 1 + 1. Respond with only the numeric result.",
            }),
          },
        ],
      }),
    });

    expect(rows.some((row) => row.label === "Subagent running")).toBe(false);
    expect(rows.some((row) => row.label === "Subagent tool decision")).toBe(false);
    expect(rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Assigning helper",
          detail: "Calculate 1 + 1. Respond with only the numeric result.",
        }),
      ]),
    );
    expect(JSON.stringify(rows)).not.toContain("calculator-gemini-pro");
  });

  it("omits low-signal helper polling actions from user-facing rows", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        activeTools: [
          {
            id: "tool-1",
            label: "TaskGet",
            status: "running",
            startedAt: 1,
            inputPreview:
              '{"taskId":"spawn_mow6u189_6midf1sy","parentTurnId":"01KR2G4K8Y9XB18C35YND10H8F","sessionKey":"agent:main:app:ch-moi9105m:24","persona":"skeptic-partner","prompt":"You are the SKEPTIC PARTNER...',
          },
          {
            id: "tool-2",
            label: "FileRead",
            status: "running",
            startedAt: 2,
            inputPreview: JSON.stringify({ path: "book/FINAL_MANUSCRIPT.md" }),
          },
        ],
      }),
    });

    expect(rows.some((row) => row.label === "TaskGet")).toBe(false);
    expect(JSON.stringify(rows)).not.toContain("spawn_mow6u189");
    expect(JSON.stringify(rows)).not.toContain("SKEPTIC PARTNER");
    expect(rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Reviewing document",
          detail: "book/FINAL_MANUSCRIPT.md",
        }),
      ]),
    );
  });

  it("renders completed SpawnAgent output as a plain helper result instead of raw JSON", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        activeTools: [
          {
            id: "tool-1",
            label: "SpawnAgent",
            status: "done",
            startedAt: 1,
            inputPreview: JSON.stringify({
              persona: "calculator-gpt",
              prompt: "Calculate 1 + 1. Respond with only the numeric result.",
            }),
            outputPreview: JSON.stringify({
              taskId: "spawn_motzdgo9_mzbmo5cy",
              status: "ok",
              finalText:
                "MODEL: gpt-5.5-pro\nRESULT: 2\nREASONING: Deterministic sum of 1 and 1 via Calculation tool yields 2.",
              spawnDir: "/home/ocuser/.openclaw/spawns/spawn_motzdgo9_mzbmo5cy",
            }),
          },
        ],
      }),
    });

    const action = rows.find((row) => row.group === "tool");

    expect(action).toEqual(
      expect.objectContaining({
        label: "Helper reported result",
        detail: "Calculate 1 + 1. Respond with only the numeric result.",
        snippet:
          "Result: 2\nReason: Deterministic sum of 1 and 1 via Calculation tool yields 2.",
      }),
    );
    expect(JSON.stringify(rows)).not.toContain("taskId");
    expect(JSON.stringify(rows)).not.toContain("spawnDir");
    expect(JSON.stringify(rows)).not.toContain("MODEL:");
  });

  it("renders Calculation output as a plain result instead of raw JSON", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        activeTools: [
          {
            id: "tool-1",
            label: "Calculation",
            status: "done",
            startedAt: 1,
            outputPreview: JSON.stringify({
              operation: "sum",
              field: "v",
              result: 2,
              rowCount: 2,
              numericCount: 2,
              ignoredCount: 0,
              sum: 2,
            }),
          },
        ],
      }),
    });

    const action = rows.find((row) => row.group === "tool");

    expect(action).toEqual(
      expect.objectContaining({
        label: "Calculated total",
        detail: "2 rows checked",
        snippet: "Result: 2",
      }),
    );
    expect(JSON.stringify(rows)).not.toContain('"operation"');
    expect(JSON.stringify(rows)).not.toContain('"numericCount"');
  });

  it("renders miscellaneous structured tool output as plain facts instead of raw JSON", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        activeTools: [
          {
            id: "tool-1",
            label: "Clock",
            status: "done",
            startedAt: 1,
            outputPreview: JSON.stringify({
              timestampMs: 1_778_064_000_000,
              iso: "2026-05-06T12:00:00.000Z",
              timezone: "America/New_York",
              localDate: "2026-05-06",
              localTime: "08:00:00",
            }),
          },
          {
            id: "tool-2",
            label: "DocumentWrite",
            status: "done",
            startedAt: 2,
            outputPreview: JSON.stringify({
              artifactId: "art_doc_123",
              workspacePath: "reports/audit-report.docx",
              filename: "audit-report.docx",
            }),
          },
          {
            id: "tool-3",
            label: "Revenue",
            status: "done",
            startedAt: 3,
            outputPreview: JSON.stringify({
              status: "ok",
              count: 3,
              workspacePath: "reports/revenue.csv",
              filename: "revenue.csv",
              internalId: "rev_123",
            }),
          },
        ],
      }),
    });

    expect(rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Checked current time",
          detail: "America/New_York",
          snippet: "2026-05-06 08:00:00",
        }),
        expect.objectContaining({
          label: "Created document",
          detail: "audit-report.docx",
          snippet: "reports/audit-report.docx",
        }),
        expect.objectContaining({
          label: "Revenue",
          detail: "revenue.csv",
          snippet: "Status: ok\nCount: 3\nPath: reports/revenue.csv",
        }),
      ]),
    );
    expect(rows.map((row) => row.snippet).join("\n")).not.toContain("{");
    expect(JSON.stringify(rows)).not.toContain("artifactId");
    expect(JSON.stringify(rows)).not.toContain("internalId");
  });

  it("returns a stable idle state when nothing is running", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState(),
    });

    expect(rows).toEqual([
      {
        id: "idle",
        group: "status",
        label: "Idle",
        detail: "Live agent work will appear here.",
        status: "info",
      },
    ]);
  });
});
