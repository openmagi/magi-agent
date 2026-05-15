import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RunInspectorDock } from "./run-inspector-dock";
import type {
  ChannelState,
  ControlRequestRecord,
  QueuedMessage,
  SubagentActivity,
  TaskBoardSnapshot,
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
    taskBoard: null,
    fileProcessing: false,
    ...overrides,
  };
}

const openBoard: TaskBoardSnapshot = {
  receivedAt: 1,
  tasks: [
    {
      id: "task-1",
      title: "Task 1",
      description: "Investigate parity",
      status: "in_progress",
    },
    {
      id: "task-2",
      title: "Task 2",
      description: "Ship fix",
      status: "pending",
    },
  ],
};

const queued: QueuedMessage[] = [
  { id: "q1", content: "follow up", queuedAt: 1 },
];

const pendingPermissionRequest: ControlRequestRecord = {
  requestId: "cr-1",
  kind: "tool_permission",
  state: "pending",
  sessionKey: "agent:main:app:general",
  channelName: "general",
  source: "turn",
  prompt: "Allow Bash?",
  proposedInput: { command: "npm test" },
  createdAt: 1,
  expiresAt: 2,
};

const subagents: SubagentActivity[] = [
  {
    taskId: "blue",
    role: "explore",
    status: "running",
    detail: "Searching sources",
    startedAt: 1,
    updatedAt: 2,
  },
  {
    taskId: "red",
    role: "worker",
    status: "waiting",
    detail: "FileRead",
    startedAt: 1,
    updatedAt: 3,
  },
];

const longBoard: TaskBoardSnapshot = {
  receivedAt: 1,
  tasks: Array.from({ length: 24 }, (_, index) => ({
    id: `task-${index + 1}`,
    title: `Long task ${index + 1}`,
    description: `Detailed work item ${index + 1}`,
    status: index === 0 ? "in_progress" : "pending",
  })),
};

describe("RunInspectorDock", () => {
  it("renders nothing when there is no active run state", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState()}
        queuedMessages={[]}
        controlRequests={[]}
      />,
    );

    expect(html).toBe("");
  });

  it("pins open task board and queued follow-up summary", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({ taskBoard: openBoard })}
        queuedMessages={queued}
        controlRequests={[]}
      />,
    );

    expect(html).toContain("Current run");
    expect(html).toContain("follow up");
    expect(html).toContain("Task 1");
    expect(html).toContain("Task 2");
  });

  it("renders a work state summary above current run details", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
          taskBoard: openBoard,
        })}
        queuedMessages={queued}
        controlRequests={[]}
      />,
    );

    expect(html).toContain("Current Work");
    expect(html).toContain("Goal");
    expect(html).toContain("Task 1");
    expect(html).toContain("Status");
    expect(html).toContain("Running");
    expect(html).toContain("Progress");
    expect(html).toContain("0/2 tasks complete");
    expect(html).toContain("Now");
    expect(html).toContain("Next");
    expect(html).toContain("follow up");
  });

  it("localizes generated current-run chrome to the response language", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: true,
          responseLanguage: "ko",
          turnPhase: "executing",
          heartbeatElapsedMs: 42_000,
          activeTools: [
            {
              id: "tool-1",
              label: "Bash",
              status: "running",
              startedAt: 1,
            },
          ],
        })}
        queuedMessages={queued}
        controlRequests={[]}
      />,
    );

    expect(html).toContain("현재 실행");
    expect(html).toContain("현재 작업");
    expect(html).toContain("목표");
    expect(html).toContain("실행 중");
    expect(html).toContain("1개 실행 중");
    expect(html).toContain("1개 대기");
    expect(html).not.toContain("Current run");
    expect(html).not.toContain("active action");
  });

  it("keeps generated current-run chrome in the selected UI language", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: true,
          responseLanguage: "ko",
          turnPhase: "executing",
          heartbeatElapsedMs: 42_000,
          activeTools: [
            {
              id: "tool-1",
              label: "Bash",
              status: "running",
              startedAt: 1,
            },
          ],
        })}
        queuedMessages={queued}
        controlRequests={[]}
        uiLanguage="en"
      />,
    );

    expect(html).toContain("Current run");
    expect(html).toContain("Current Work");
    expect(html).toContain("Running");
    expect(html).toContain("1 active action");
    expect(html).toContain("1 waiting");
    expect(html).not.toContain("현재 실행");
    expect(html).not.toContain("현재 작업");
  });

  it("surfaces pending control requests near the composer", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState()}
        queuedMessages={[]}
        controlRequests={[pendingPermissionRequest]}
      />,
    );

    expect(html).toContain("Needs approval");
    expect(html).toContain("Allow Bash?");
  });

  it("shows active tool work without raw thinking text", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: true,
          thinkingText: "private notes",
          activeTools: [
            {
              id: "tool-1",
              label: "FileRead",
              status: "running",
              startedAt: 1,
            },
          ],
        })}
        queuedMessages={[]}
        controlRequests={[]}
      />,
    );

    expect(html).toContain("Running FileRead");
    expect(html).not.toContain("private notes");
  });

  it("renders the latest browser preview frame in the current run panel", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: true,
          browserFrame: {
            action: "click",
            url: "https://example.com/app",
            imageBase64: Buffer.from("frame").toString("base64"),
            contentType: "image/png",
            capturedAt: 123,
          },
        })}
        queuedMessages={[]}
        controlRequests={[]}
      />,
    );

    expect(html).toContain("Live browser");
    expect(html).toContain("https://example.com/app");
    expect(html).toContain("data:image/png;base64");
    expect(html).toContain('data-browser-frame-expand-trigger="true"');
    expect(html).toContain('aria-label="Open larger browser preview"');
    expect(html).toContain('aria-haspopup="dialog"');
  });

  it("shows live text document draft in the current run panel", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          documentDraft: {
            id: "tu_doc",
            filename: "notes/live.txt",
            format: "txt",
            status: "streaming",
            contentPreview: "Line one\nLine two",
            contentLength: 17,
            truncated: false,
            updatedAt: 123,
          },
        })}
        queuedMessages={[]}
        controlRequests={[]}
      />,
    );

    expect(html).toContain('data-run-inspector-document-draft="true"');
    expect(html).toContain("Writing document");
    expect(html).toContain("notes/live.txt");
    expect(html).toContain("17 chars");
    expect(html).toContain("Line one");
    expect(html).toContain("Line two");
  });

  it("renders inspected sources and claim citation gate status", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          inspectedSources: [{
            sourceId: "src_1",
            kind: "web_fetch",
            uri: "https://example.com/report",
            title: "Example Report",
            inspectedAt: 123,
          }],
          citationGate: {
            ruleId: "claim-citation-gate",
            verdict: "violation",
            detail: "2 uncited claims",
            checkedAt: 456,
          },
        })}
        queuedMessages={[]}
        controlRequests={[]}
      />,
    );

    expect(html).toContain("Research evidence");
    expect(html).toContain("Sources");
    expect(html).toContain("src_1");
    expect(html).toContain("Example Report");
    expect(html).toContain("example.com/report");
    expect(html).toContain("Citation coverage");
    expect(html).toContain("2 uncited claims");
  });

  it("renders named background subagents in the current run panel", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: true,
          subagents,
        })}
        queuedMessages={[]}
        controlRequests={[]}
      />,
    );

    expect(html).toContain("Background agents");
    expect(html).toContain("Halley");
    expect(html).toContain("explorer");
    expect(html).toContain("running");
    expect(html).toContain("Meitner");
    expect(html).toContain("worker");
    expect(html).toContain("waiting");
    expect(html).toContain("FileRead");
  });

  it("localizes background subagent status labels to Korean for Korean response runs", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: true,
          responseLanguage: "ko",
          subagents,
        })}
        queuedMessages={[]}
        controlRequests={[]}
      />,
    );

    expect(html).toContain("작업 중");
    expect(html).toContain("승인 대기");
    expect(html).not.toContain("running");
    expect(html).not.toContain("waiting");
  });

  it("defaults background subagent status labels to English when response language is unavailable", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: false,
          subagents,
        })}
        queuedMessages={[]}
        controlRequests={[]}
      />,
    );

    expect(html).toContain("running");
    expect(html).toContain("waiting");
    expect(html).not.toContain("작업 중");
    expect(html).not.toContain("승인 대기");
  });

  it("keeps detached background subagents visible after the parent stream ends", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: false,
          subagents: [subagents[0]],
        })}
        queuedMessages={[]}
        controlRequests={[]}
      />,
    );

    expect(html).toContain("Current run");
    expect(html).toContain("1 background agent");
    expect(html).toContain("Searching sources");
  });

  it("bounds long current run details in a scrollable region with a hide control", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: true,
          thinkingStartedAt: 1,
          turnPhase: "executing",
          taskBoard: longBoard,
          subagents,
        })}
        queuedMessages={queued}
        controlRequests={[pendingPermissionRequest]}
      />,
    );

    expect(html).toContain('aria-label="Hide current run"');
    expect(html).toContain("max-h-[min(50vh,34rem)]");
    expect(html).toContain("overflow-y-auto");
    expect(html).toContain("Long task 24");
  });

  it("can render compact summary mode without duplicating right-inspector details", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
          taskBoard: openBoard,
          subagents,
          activeTools: [
            {
              id: "tool-1",
              label: "FileRead",
              status: "running",
              startedAt: 1,
              inputPreview: "{\"path\":\"book/FINAL_MANUSCRIPT.md\"}",
            },
          ],
        })}
        queuedMessages={queued}
        controlRequests={[]}
        compactDetails
      />,
    );

    expect(html).toContain("Current run");
    expect(html).toContain("Current Work");
    expect(html).toContain("0/2 tasks complete");
    expect(html).toContain("follow up");
    expect(html).not.toContain("Background agents");
    expect(html).not.toContain("Searching sources");
    expect(html).not.toContain("Task 2");
    expect(html).not.toContain("Running FileRead");
  });

  it("highlights queued follow-ups in the current run details", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
        })}
        queuedMessages={queued}
        controlRequests={[]}
      />,
    );

    expect(html).toContain('data-run-inspector-queue-card="true"');
    expect(html).toContain("Queued after current run");
    expect(html).toContain("1 waiting");
    expect(html).toContain("follow up");
  });

  it("can render a hidden current run as a compact restore control", () => {
    const html = renderToStaticMarkup(
      <RunInspectorDock
        channelState={channelState({
          streaming: true,
          thinkingStartedAt: 1,
          turnPhase: "executing",
          taskBoard: longBoard,
        })}
        queuedMessages={[]}
        controlRequests={[]}
        defaultHidden
      />,
    );

    expect(html).toContain("Show current run");
    expect(html).not.toContain("Long task 24");
  });
});
