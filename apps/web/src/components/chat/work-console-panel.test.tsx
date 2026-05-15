import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WorkConsolePanel } from "./work-console-panel";
import type {
  ChannelState,
  ControlRequestRecord,
  MissionActivity,
  QueuedMessage,
  SubagentActivity,
  TaskBoardSnapshot,
  ToolActivity,
} from "@/lib/chat/types";

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
  label: "FileWrite",
  status: "running",
  startedAt: 1,
  inputPreview: JSON.stringify({
    path: "book/FINAL_MANUSCRIPT.md",
    content: "Opening paragraph\nSecond paragraph",
  }),
};

const subagent: SubagentActivity = {
  taskId: "reviewer",
  role: "review",
  status: "waiting",
  detail: "Reviewing the patch",
  startedAt: 1,
  updatedAt: 2,
};

const taskBoard: TaskBoardSnapshot = {
  receivedAt: 1,
  tasks: [
    {
      id: "task-1",
      title: "Build work console",
      description: "Render public activity",
      status: "in_progress",
    },
  ],
};

const queued: QueuedMessage = {
  id: "q1",
  content: "After this, summarize the tradeoffs",
  queuedAt: 1,
};

const request: ControlRequestRecord = {
  requestId: "cr-1",
  kind: "plan_approval",
  state: "pending",
  sessionKey: "agent:main:app:general",
  source: "plan",
  prompt: "Approve implementation plan?",
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

describe("WorkConsolePanel", () => {
  it("renders live public work state without private thinking text", () => {
    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          thinkingText: "private chain-of-thought",
          turnPhase: "executing",
          heartbeatElapsedMs: 42_000,
          activeTools: [tool],
          subagents: [subagent],
          taskBoard,
        })}
        queuedMessages={[queued]}
        controlRequests={[request]}
      />,
    );

    expect(html).toContain("Work in progress");
    expect(html).toContain("Running");
    expect(html).toContain("42s elapsed");
    expect(html).toContain('data-work-console-section-tone="status"');
    expect(html).toContain('data-work-console-section-tone="actions"');
    expect(html).toContain('data-work-console-section-tone="queue"');
    expect(html).toContain('data-work-console-status-row="true"');
    expect(html).toContain("Creating document");
    expect(html).toContain("book/FINAL_MANUSCRIPT.md");
    expect(html).toContain("Opening paragraph");
    expect(html).toContain("Second paragraph");
    expect(html).toContain('data-work-console-action-row="true"');
    expect(html).toContain('data-work-console-row-status="running"');
    expect(html).not.toContain("FileWrite");
    expect(html).toContain("Halley");
    expect(html).toContain("reviewer");
    expect(html).toContain("Build work console");
    expect(html).toContain("Queued follow-up");
    expect(html).toContain("After this, summarize the tradeoffs");
    expect(html).toContain('data-work-console-queue-row="true"');
    expect(html).toContain("waiting");
    expect(html).toContain("Needs approval");
    expect(html).toContain("Approve implementation plan?");
    expect(html).not.toContain("private chain-of-thought");
  });

  it("keeps actions in a capped bottom-following scroll region", () => {
    const tools: ToolActivity[] = Array.from({ length: 12 }, (_, index) => ({
      id: `tool-${index + 1}`,
      label: index === 4 ? "Grep" : "Bash",
      status: "running",
      startedAt: index + 1,
      inputPreview: JSON.stringify({
        command: `echo action-${index + 1}`,
      }),
    }));

    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
          activeTools: tools,
          subagents: [subagent],
        })}
      />,
    );

    expect(html.indexOf("Helpers")).toBeLessThan(html.indexOf("Current steps"));
    expect(html).toContain('data-work-console-group="tool"');
    expect(html).toContain('data-work-console-actions-scroll="bottom"');
    expect(html).toContain("max-h-[44vh]");
    expect(html).toContain("overflow-y-auto");
    expect(html).not.toContain("Grep");
  });

  it("renders compact live markdown draft preview in the work panel", () => {
    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          documentDraft: {
            id: "tu_doc",
            filename: "docs/report.md",
            format: "md",
            status: "streaming",
            contentPreview: "# Draft\nOpening paragraph",
            contentLength: 25,
            truncated: false,
            updatedAt: 123,
          },
        })}
      />,
    );

    expect(html).toContain('data-work-console-document-draft="true"');
    expect(html).toContain("Writing document");
    expect(html).toContain("docs/report.md");
    expect(html).toContain("25 chars");
    expect(html).toContain("# Draft");
    expect(html).toContain("Opening paragraph");
  });

  it("marks live rows for smooth motion instead of abrupt replacement", () => {
    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
          heartbeatElapsedMs: 42_000,
          activeTools: [tool],
        })}
      />,
    );

    expect(html).toContain('data-work-console-motion="true"');
    expect(html).toContain("work-console-row-motion");
    expect(html).toContain("work-console-text-motion");
    expect(html).toContain("work-console-running-dot");
    expect(html).toContain("--work-console-row-delay");
  });

  it("suppresses live detail groups when the chat already renders the inline work stream", () => {
    const goal = "Draft the final manuscript with supporting evidence.";
    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
          heartbeatElapsedMs: 42_000,
          currentGoal: goal,
          activeTools: [tool],
          subagents: [subagent],
          taskBoard,
        })}
        queuedMessages={[queued]}
        controlRequests={[request]}
        suppressInlineRunDetails
      />,
    );

    expect(html).toContain("Work in progress");
    expect(html).toContain("Running");
    expect(html).toContain("42s elapsed");
    expect(html).toContain("Goal");
    expect(html).toContain(goal);
    expect(html).toContain('data-work-console-section-tone="status"');
    expect(html).not.toContain('data-work-console-section-tone="actions"');
    expect(html).not.toContain('data-work-console-section-tone="agents"');
    expect(html).not.toContain('data-work-console-section-tone="queue"');
    expect(html).not.toContain("Creating document");
    expect(html).not.toContain("book/FINAL_MANUSCRIPT.md");
    expect(html).not.toContain("Halley");
    expect(html).not.toContain("Build work console");
    expect(html).not.toContain("After this, summarize the tradeoffs");
    expect(html).not.toContain("Approve implementation plan?");
  });

  it("does not show a raw long user request as the right-panel goal", () => {
    const rawGoal = [
      "이 자료들을 기반으로 내외디스틸러리에 대한 TIPS LP 투자(1억원) 건에 대해 투심위를 열어줘.",
      "Opus 4.6 서브에이전트로 각각 낙관적 파트너와 회의적 파트너가 의견을 내고 GPT5.5 리뷰까지 진행해.",
      "최종 IC 보고서까지 작성해줘.",
    ].join(" ");
    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
          currentGoal: rawGoal,
          missions: [
            {
              id: "mission-1",
              title: "내외디스틸러리 TIPS 투자심의",
              kind: "goal",
              status: "running",
              detail: "시장 전망, 재무제표, 최종 IC 보고서 기준으로 진행 중",
              updatedAt: 123,
            },
          ],
          activeGoalMissionId: "mission-1",
        })}
        suppressInlineRunDetails
      />,
    );

    expect(html).toContain("내외디스틸러리 TIPS 투자심의");
    expect(html).not.toContain(rawGoal);
    expect(html).not.toContain("Opus 4.6 서브에이전트");
  });

  it("renders helper results without raw SpawnAgent JSON", () => {
    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
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
        })}
      />,
    );

    expect(html).toContain("Helper reported result");
    expect(html).toContain("Calculate 1 + 1. Respond with only the numeric result.");
    expect(html).toContain("Result: 2");
    expect(html).toContain("Reason: Deterministic sum of 1 and 1 via Calculation tool yields 2.");
    expect(html).not.toContain("taskId");
    expect(html).not.toContain("spawnDir");
    expect(html).not.toContain("MODEL:");
  });

  it("renders durable missions as their own work-console section", () => {
    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
          missions: [mission],
          activeGoalMissionId: "mission-1",
          taskBoard,
        })}
      />,
    );

    expect(html).toContain("Missions");
    expect(html).toContain("Draft weekly research report");
    expect(html).toContain("Waiting for approval to continue");
    expect(html).toContain("goal blocked");
    expect(html).toContain('data-work-console-group="mission"');
    expect(html).toContain('data-work-console-section-tone="mission"');
    expect(html).toContain('data-work-console-mission-row="true"');
    expect(html.indexOf("Missions")).toBeLessThan(html.indexOf("Plan"));
  });

  it("renders calculation results without raw JSON", () => {
    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
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
        })}
      />,
    );

    expect(html).toContain("Calculated total");
    expect(html).toContain("2 rows checked");
    expect(html).toContain("Result: 2");
    expect(html).not.toContain("numericCount");
    expect(html).not.toContain("operation");
    expect(html).not.toContain("{&quot;");
  });

  it("does not render raw JSON for file, browser, or command step previews", () => {
    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
          activeTools: [
            {
              id: "tool-1",
              label: "FileRead",
              status: "done",
              startedAt: 1,
              outputPreview: JSON.stringify({
                path: "skills/tossplace-pos/SKILL.md",
                fileSha256: "not-a-real-file-sha",
                contentSha256: "not-a-real-content-sha",
                content: "---\nname: tossplace-pos",
              }),
            },
            {
              id: "tool-2",
              label: "Browser",
              status: "running",
              startedAt: 2,
              outputPreview: JSON.stringify({
                action: "create_session",
                sessionId: "browser-session-fixture",
                cdpEndpoint: "ws://browser-worker.clawy-system:9222/cdp/browser-session-fixture",
              }),
            },
            {
              id: "tool-3",
              label: "Bash",
              status: "done",
              startedAt: 3,
              inputPreview: JSON.stringify({ command: "cat merchants.json" }),
              outputPreview: JSON.stringify({
                exitCode: 0,
                stdout:
                  '{"count":4,"merchants":[{"merchantId":"439438","merchantName":"Canary test store"}]}',
                stderr: "",
              }),
            },
          ],
        })}
      />,
    );

    expect(html).toContain("Reviewing document");
    expect(html).toContain("skills/tossplace-pos/SKILL.md");
    expect(html).toContain("Opening browser");
    expect(html).toContain("Starting browser session");
    expect(html).toContain("Working in workspace");
    expect(html).not.toContain("{&quot;");
    expect(html).not.toContain("fileSha256");
    expect(html).not.toContain("contentSha256");
    expect(html).not.toContain("sessionId");
    expect(html).not.toContain("cdpEndpoint");
    expect(html).not.toContain("merchantId");
  });

  it("renders a live browser preview frame without browser transport internals", () => {
    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
          browserFrame: {
            action: "open",
            url: "https://example.com/dashboard",
            imageBase64: Buffer.from("tiny-frame").toString("base64"),
            contentType: "image/png",
            capturedAt: 123,
          },
          activeTools: [
            {
              id: "tool-1",
              label: "Browser",
              status: "running",
              startedAt: 1,
              inputPreview: JSON.stringify({ action: "open", url: "https://example.com/dashboard" }),
            },
          ],
        })}
      />,
    );

    expect(html).toContain("Live browser");
    expect(html).toContain("https://example.com/dashboard");
    expect(html).toContain("data:image/png;base64");
    expect(html).toContain('data-browser-frame-expand-trigger="true"');
    expect(html).toContain('aria-label="Open larger browser preview"');
    expect(html).toContain('aria-haspopup="dialog"');
    expect(html).not.toContain("cdpEndpoint");
    expect(html).not.toContain("sessionId");
  });

  it("does not render truncated JSON-looking payloads from internal work tools", () => {
    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
          activeTools: [
            {
              id: "tool-1",
              label: "CodeWorkspace",
              status: "done",
              startedAt: 1,
              outputPreview:
                '{"path":"code/naeoe-tips-ic/CONTEXT.md","fileSha256":"not-a-real-file-sha","contentSha256":"not-a-real-content-sha","content":"# 내외디스털리 TIPS LP 투자 심사...',
            },
            {
              id: "tool-2",
              label: "Browser",
              status: "running",
              startedAt: 2,
              outputPreview:
                '{"action":"create_session","sessionId":"browser-session-fixture","cdpEndpoint":"ws://browser-worker.clawy-system:9222/cdp/browser-session-fixture...',
            },
            {
              id: "tool-3",
              label: "TaskGet",
              status: "running",
              startedAt: 3,
              outputPreview:
                '{"taskId":"spawn_mow6u189_6midf1sy","parentTurnId":"01KR2G4K8Y9XB18C35YND10H8F","sessionKey":"agent:main:app:ch-moi9105m:24","persona":"skeptic-partner","prompt":"You are the SKEPTIC PARTNER...',
            },
          ],
        })}
      />,
    );

    expect(html).toContain("Reviewing document");
    expect(html).toContain("code/naeoe-tips-ic/CONTEXT.md");
    expect(html).toContain("Opening browser");
    expect(html).toContain("Starting browser session");
    expect(html).not.toContain("TaskGet");
    expect(html).not.toContain("{&quot;");
    expect(html).not.toContain("fileSha256");
    expect(html).not.toContain("contentSha256");
    expect(html).not.toContain("sessionId");
    expect(html).not.toContain("cdpEndpoint");
    expect(html).not.toContain("SKEPTIC PARTNER");
  });

  it("renders background agents as a compact boxed chip roster", () => {
    const subagents: SubagentActivity[] = Array.from({ length: 5 }, (_, index) => ({
      taskId: `agent-${index + 1}`,
      role: `arithmetic-worker-${index + 1}`,
      status: "running",
      detail: `iteration ${index + 1}`,
      startedAt: index + 1,
      updatedAt: index + 2,
    }));

    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
          subagents,
        })}
      />,
    );

    expect(html).toContain('data-work-console-section-tone="agents"');
    expect(html).toContain('data-work-console-section-density="compact"');
    expect(html).toContain('data-work-console-agent-roster="compact"');
    expect(html).toContain('data-work-console-agent-layout="grid"');
    expect(html).toContain("grid-cols-2");
    expect(html).toContain('data-work-console-agent-chip="true"');
    expect(html).toContain("5 agents");
    expect(html).toContain("Halley");
    expect(html).toContain("arithmetic-worker-1");
    expect(html).not.toContain("iteration 1");
    expect(html).not.toContain("data-work-console-subagent-row");
  });

  it("renders an idle state when no run is active", () => {
    const html = renderToStaticMarkup(
      <WorkConsolePanel
        channelState={channelState()}
        queuedMessages={[]}
        controlRequests={[]}
      />,
    );

    expect(html).toContain("Idle");
    expect(html).toContain("Live agent work will appear here.");
  });
});
