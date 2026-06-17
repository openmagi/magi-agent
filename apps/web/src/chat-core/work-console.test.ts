import { describe, expect, it } from "vitest";
import { deriveWorkConsoleRows } from "./work-console";
import { pythonAdkForbiddenPrivateMarkers } from "./fixtures/python-adk-public-events";
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

  it("labels detached background shell work as a background task", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        subagents: [{
          taskId: "shell_bg_1",
          role: "bash",
          status: "running",
          detail: "Background command running",
          startedAt: Date.now() - 10_000,
          updatedAt: Date.now(),
        }],
      }),
    });

    expect(rows).toContainEqual(expect.objectContaining({
      id: "subagent:shell_bg_1",
      group: "subagent",
      label: "Background task",
      detail: "Background command running",
      status: "running",
      meta: expect.stringContaining("bash"),
    }));
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

  it("keeps committed-but-live turns running until the terminal event settles the stream", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        turnPhase: "committed",
        heartbeatElapsedMs: 12_000,
      }),
    });

    expect(rows).toContainEqual(expect.objectContaining({
      id: "phase",
      label: "Finalizing",
      status: "running",
      detail: "12s elapsed",
    }));
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

  it("derives safe host/path web progress details without leaking URL secrets", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        activeTools: [
          {
            id: "web-1",
            label: "WebSearch",
            status: "running",
            startedAt: 1,
            inputPreview: JSON.stringify({
              query: "openmagi docs",
              progress: {
                sourceUrl:
                  "https://docs.example.test/research/openmagi/2026/details?session=fixture#raw",
              },
            }),
            outputPreview: JSON.stringify({
              results: [
                {
                  title: "Safe result",
                  sourceUrl:
                    "https://docs.example.test/research/openmagi/2026/details?token=fixture",
                },
              ],
              detail:
                "Checking https://docs.example.test/research/openmagi/2026/details?token=fixture",
            }),
          },
        ],
      }),
    });

    const action = rows.find((row) => row.group === "tool");

    expect(action).toEqual(
      expect.objectContaining({
        label: "Searching the web",
        detail: "docs.example.test/research/openmagi/2026",
        snippet:
          "Query: openmagi docs\nURL: docs.example.test/research/openmagi/2026\nDetail: Checking docs.example.test/research/openmagi/2026\n1 result",
      }),
    );
    expect(JSON.stringify(rows)).not.toContain("session=fixture");
    expect(JSON.stringify(rows)).not.toContain("token=fixture");
    expect(JSON.stringify(rows)).not.toContain("https://docs.example.test");
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

  it("does not expose unsafe helper task ids in row ids", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        subagents: [
          {
            taskId: "agent:main:app:ch-moi9105m:24:spawn_secret_token",
            role: "reviewer",
            status: "running",
            detail: "Reviewing sources",
            startedAt: Date.now() - 10_000,
            updatedAt: Date.now(),
          },
        ],
      }),
    });

    expect(rows).toContainEqual(
      expect.objectContaining({
        id: "subagent:agent-1",
        label: "Halley",
        detail: "Reviewing sources",
      }),
    );
    expect(JSON.stringify(rows)).not.toContain("agent:main:app");
    expect(JSON.stringify(rows)).not.toContain("spawn_secret_token");
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

  it("derives deterministic rows from Python ADK replay state without private payloads", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        turnPhase: "executing",
        heartbeatElapsedMs: 1_200,
        runtimeTraces: [{
          turnId: "turn-python-public-1",
          phase: "retry_scheduled",
          severity: "warning",
          title: "Retry scheduled",
          detail: "Retry after bounded failure",
          reasonCode: "provider_transient_failure",
          attempt: 2,
          maxAttempts: 3,
          retryable: true,
          receivedAt: 1,
        }],
        activeTools: [
          {
            id: "py_tool_clock",
            label: "Clock running",
            status: "done",
            startedAt: 1,
            inputPreview: "timezone: UTC",
            outputPreview: "Public time checked",
            durationMs: 8,
          },
          {
            id: "py_tool_patch",
            label: "PatchApply",
            status: "done",
            startedAt: 2,
            outputPreview: "Patch preview ready",
            patchPreview: {
              dryRun: true,
              changedFiles: ["src/public.ts"],
              createdFiles: [],
              deletedFiles: [],
              files: [{
                path: "src/public.ts",
                operation: "update",
                hunks: 1,
                addedLines: 2,
                removedLines: 0,
              }],
            },
          },
        ],
        subagents: [{
          taskId: "child-python-ok",
          role: "reviewer",
          status: "done",
          detail: "Searching",
          startedAt: 1,
          updatedAt: 2,
        }],
        taskBoard: {
          receivedAt: 1,
          tasks: [{
            id: "task-python-1",
            title: "Check Python parity",
            description: "Replay public event rows",
            status: "in_progress",
          }],
        },
      }),
      controlRequests: [{
        requestId: "req-python-tool",
        kind: "tool_permission",
        state: "pending",
        sessionKey: "agent:main:app:general",
        channelName: "general",
        source: "turn",
        prompt: "Approve patch preview?",
        createdAt: 11,
        expiresAt: 71,
      }],
    });

    expect(rows).toEqual(expect.arrayContaining([
      expect.objectContaining({
        id: "trace:turn-python-public-1:1:retry_scheduled",
        label: "Retrying with verifier guidance",
        detail: "Retry after bounded failure",
        status: "running",
        meta: "provider_transient_failure 2/3",
      }),
      expect.objectContaining({
        id: "tool:py_tool_clock",
        label: "Clock running",
        snippet: "Public time checked",
        status: "done",
      }),
      expect.objectContaining({
        id: "tool:py_tool_patch",
        label: "Previewing patch",
        detail: "1 file: src/public.ts",
        snippet: "Update src/public.ts (+2/-0)",
      }),
      expect.objectContaining({
        id: "subagent:child-python-ok",
        group: "subagent",
        status: "done",
      }),
      expect.objectContaining({
        id: "task:task-python-1",
        label: "Check Python parity",
      }),
      expect.objectContaining({
        id: "control:req-python-tool",
        label: "Needs approval",
      }),
    ]));
    const renderedRows = JSON.stringify(rows);
    for (const marker of pythonAdkForbiddenPrivateMarkers) {
      expect(renderedRows).not.toContain(marker);
    }
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

  it("shows deterministic runtime status rows from sanitized public state", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        determinism: {
          workflowId: "workflow.public",
          workflowVersion: "1.0.0",
          governed: true,
          effectivePolicySnapshotDigest: `sha256:${"1".repeat(64)}`,
          ledgerHeadDigest: `sha256:${"2".repeat(64)}`,
          checkpointId: "checkpoint-1",
          projectionMode: "structured_claims_only",
          outputAllowed: false,
          blockedReasonCodes: ["unsupported_claim"],
          appliedRecipes: [{
            recipeId: "invoice.cited-brief",
            version: "1.0.0",
            role: "primary",
            governed: true,
            sourceDigest: `sha256:${"3".repeat(64)}`,
          }],
          recipeSelection: {
            status: "explicit_applied",
            selectionSource: "explicit",
            requestedRecipeRefs: [{ recipeId: "invoice.cited-brief", version: "1.0.0" }],
            appliedRecipeRefs: [{ recipeId: "invoice.cited-brief", version: "1.0.0" }],
            omittedRecipeRefs: [],
            omissionReasons: [],
            policySnapshotDigest: `sha256:${"3".repeat(64)}`,
            turnBlocked: false,
            fallbackUsed: false,
          },
          verificationGates: [{
            gateId: "citation.opened_snapshot",
            stage: "before_output_projection",
            status: "passed",
            validatorTrustClass: "deterministic",
            reasonCodes: [],
            evidenceRefs: [`evidence:sha256:${"4".repeat(64)}`],
            policyDecisionId: "policy-2",
            checkedAt: 1760000000000,
          }],
          guardrails: [{
            guardrailId: "claim-citation-gate",
            stage: "before_output_projection",
            status: "blocked",
            reasonCodes: ["unsupported_claim"],
            validatorTrustClass: "deterministic",
            policyDecisionId: "policy-1",
            evidenceRefs: [],
          }],
          fallbackReasonCode: "projection_blocked",
          fallbackAuthority: "typescript",
        },
      }),
    });

    expect(rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "determinism:policy",
          group: "trace",
          label: "Policy snapshot",
          detail: "workflow.public v1.0.0 · governed · sha256:111111…111111",
          status: "info",
        }),
        expect.objectContaining({
          id: "determinism:ledger",
          label: "Evidence ledger",
          detail: "sha256:222222…222222",
          status: "info",
        }),
        expect.objectContaining({
          id: "determinism:checkpoint",
          label: "Checkpoint",
          detail: "checkpoint-1",
        }),
        expect.objectContaining({
          id: "determinism:projection",
          label: "Projection",
          detail: "structured_claims_only blocked: unsupported_claim",
          status: "waiting",
        }),
        expect.objectContaining({
          id: "determinism:recipe-selection",
          label: "Recipe request",
          detail: "explicit applied · explicit",
          status: "done",
        }),
        expect.objectContaining({
          id: "determinism:recipe:invoice.cited-brief",
          label: "Recipe: invoice.cited-brief",
          detail: "1.0.0 · primary · governed",
          status: "info",
          meta: "sha256:333333…333333",
        }),
        expect.objectContaining({
          id: "determinism:verification:citation.opened_snapshot",
          label: "Verification: citation.opened_snapshot",
          detail: "before_output_projection",
          status: "done",
          meta: "deterministic · policy-2",
        }),
        expect.objectContaining({
          id: "determinism:guardrail:claim-citation-gate:policy-1",
          label: "Guardrail: claim-citation-gate",
          detail: "unsupported_claim",
          status: "error",
          meta: "before_output_projection · deterministic",
        }),
        expect.objectContaining({
          id: "determinism:fallback",
          label: "Fallback",
          detail: "projection_blocked",
          status: "waiting",
          meta: "typescript",
        }),
      ]),
    );
    expect(JSON.stringify(rows)).not.toContain("evidence:sha256");
  });

  it("renders blocked explicit recipe selection as a visible failure row", () => {
    const rows = deriveWorkConsoleRows({
      channelState: channelState({
        streaming: true,
        determinism: {
          recipeSelection: {
            status: "explicit_blocked",
            selectionSource: "explicit",
            requestedRecipeRefs: [{ recipeId: "openmagi.research", version: "1" }],
            appliedRecipeRefs: [],
            omittedRecipeRefs: [{ recipeId: "openmagi.research", version: "1" }],
            omissionReasons: ["recipe_policy_blocked"],
            policySnapshotDigest: `sha256:${"5".repeat(64)}`,
            turnBlocked: true,
            fallbackUsed: false,
            nextAction: "choose_available_recipe",
          },
        },
      }),
    });

    expect(rows).toContainEqual(expect.objectContaining({
      id: "determinism:recipe-selection",
      label: "Recipe request",
      detail: "explicit blocked · explicit · recipe_policy_blocked · turn blocked · choose_available_recipe",
      status: "error",
      meta: "sha256:555555…555555",
    }));
    expect(JSON.stringify(rows)).not.toMatch(/general_chat.*success/i);
  });
});
