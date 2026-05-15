import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatMessages } from "./chat-messages";
import type {
  ChannelState,
  ChatMessage,
  QueuedMessage,
  ControlRequestRecord,
} from "@/lib/chat/types";

function baseChannelState(overrides: Partial<ChannelState> = {}): ChannelState {
  return {
    streaming: false,
    streamingText: "",
    thinkingText: "",
    error: null,
    hasTextContent: false,
    thinkingStartedAt: null,
    activeTools: [],
    taskBoard: null,
    fileProcessing: false,
    turnPhase: null,
    heartbeatElapsedMs: null,
    pendingInjectionCount: 0,
    ...overrides,
  };
}

describe("ChatMessages", () => {
  it("hides cached messages while the initial latest history page is loading", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[
          {
            id: "cached-old-message",
            role: "assistant",
            content: "cached stale answer",
            timestamp: 1_800_000_000_000,
          },
        ]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState()}
        loading
      />,
    );

    expect(html).not.toContain("cached stale answer");
    expect(html).not.toContain("Start a conversation");
    expect(html).toContain("chat-skeleton-line");
  });

  it("does not render the same long assistant answer twice when server history arrives late", () => {
    const content =
      "This assistant answer was streamed locally first, then arrived from server history later. ".repeat(3);
    const localMessages: ChatMessage[] = [
      {
        id: "assistant-1800000000000",
        role: "assistant",
        content,
        timestamp: 1_800_000_000_000,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "server-message-1",
        serverId: "server-message-1",
        role: "assistant",
        content,
        timestamp: 1_800_000_120_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={localMessages}
        serverMessages={serverMessages}
        channelState={baseChannelState()}
      />,
    );

    expect(html.split("This assistant answer was streamed locally first").length - 1).toBe(3);
  });

  it("dedupes a committed assistant copy that only adds a route meta preamble", () => {
    const content =
      "벤처 리포트 v2 재전송했어. 파일 링크와 핵심 변경사항을 정리한 최종 답변입니다. " +
      "운영사가 확인해야 할 조건과 내부 모니터링 항목까지 함께 포함했습니다. ".repeat(3);
    const localMessages: ChatMessage[] = [
      {
        id: "assistant-1800000000000",
        role: "assistant",
        content,
        timestamp: 1_800_000_000_000,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "server-message-1",
        serverId: "server-message-1",
        role: "assistant",
        content: `[META: intent=실행, domain=문서, complexity=단순, route=직접]\n\n${content}`,
        timestamp: 1_800_000_120_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={localMessages}
        serverMessages={serverMessages}
        channelState={baseChannelState()}
      />,
    );

    expect(html.split("벤처 리포트 v2 재전송했어").length - 1).toBe(1);
    expect(html).not.toContain("[META:");
  });

  it("dedupes local message with research evidence marker against stripped server copy", () => {
    const baseContent =
      "5개 종합 투자 리포트 PDF 전달 완료. " +
      "이번 턴 검증을 통해 5개 딥 IC 파일 전부 FileRead (이번 턴) → 5개 DocumentWrite PDF 변환 → 5개 FileDeliver 확인했습니다. ".repeat(2);
    const localMessages: ChatMessage[] = [
      {
        id: "assistant-1800000000000",
        role: "assistant",
        content: `${baseContent}\n<!-- clawy:research-evidence:v1:abc123 -->`,
        timestamp: 1_800_000_000_000,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "server-msg-1",
        serverId: "server-msg-1",
        role: "assistant",
        content: baseContent,
        timestamp: 1_800_000_120_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={localMessages}
        serverMessages={serverMessages}
        channelState={baseChannelState()}
      />,
    );

    expect(html.split("5개 종합 투자 리포트 PDF 전달 완료").length - 1).toBe(1);
  });

  it("keeps short repeated assistant messages so intentional repeats are not hidden", () => {
    const localMessages: ChatMessage[] = [
      {
        id: "assistant-1800000000000",
        role: "assistant",
        content: "OK",
        timestamp: 1_800_000_000_000,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "server-message-1",
        serverId: "server-message-1",
        role: "assistant",
        content: "OK",
        timestamp: 1_800_000_120_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={localMessages}
        serverMessages={serverMessages}
        channelState={baseChannelState()}
      />,
    );

    expect(html.split("OK").length - 1).toBe(2);
  });

  it("prefers clean server history over a cached assistant message with replacement characters", () => {
    const localMessages: ChatMessage[] = [
      {
        id: "assistant-1800000000000",
        role: "assistant",
        content: "저는 reactive 시스템이라 다음 메시지까지 저는 존\uFFFD\uFFFD\uFFFD하지 않는 상태예요.",
        timestamp: 1_800_000_000_000,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "server-message-1",
        serverId: "server-message-1",
        role: "assistant",
        content: "저는 reactive 시스템이라 다음 메시지까지 저는 존재하지 않는 상태예요.",
        timestamp: 1_800_000_002_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={localMessages}
        serverMessages={serverMessages}
        channelState={baseChannelState()}
      />,
    );

    expect(html).toContain("존재하지");
    expect(html).not.toContain("존\uFFFD\uFFFD\uFFFD하지");
  });

  it("does not duplicate structured live activity that is pinned in the run inspector", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          turnPhase: "planning",
        })}
      />,
    );

    expect(html).not.toContain("Planning next steps");
    expect(html).not.toContain("Start a conversation");
  });

  it("does not render the verbose activity timeline in the message transcript", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          activeTools: [
            {
              id: "tool-1",
              label: "FileRead",
              status: "running",
              startedAt: Date.now(),
            },
          ],
        })}
      />,
    );

    expect(html).not.toContain("Running FileRead");
  });

  it("renders one inline live work snapshot while tools are running", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          thinkingText: "private chain of thought",
          activeTools: [
            {
              id: "tool-1",
              label: "FileRead",
              status: "running",
              startedAt: 1,
              inputPreview: JSON.stringify({ path: "book/FINAL_MANUSCRIPT.md" }),
            },
          ],
        })}
      />,
    );

    expect(html.match(/data-chat-inline-run-status="true"/g)?.length).toBe(1);
    expect(html).toContain("Current Work");
    expect(html).toContain("Reviewing document");
    expect(html).toContain("book/FINAL_MANUSCRIPT.md");
    expect(html).not.toContain("private chain of thought");
  });

  it("keeps inline run status visible during streaming even when no tools are active", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "partial answer text",
          hasTextContent: true,
          activeTools: [],
        })}
      />,
    );

    expect(html).toContain('data-chat-inline-run-status="true"');
  });

  it("links the inline current run snapshot to the active mission ledger", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          activeGoalMissionId: "mission-1",
          missions: [{
            id: "mission-1",
            title: "Research competitor launches",
            kind: "goal",
            status: "running",
            updatedAt: 1,
          }],
          activeTools: [{
            id: "tool-1",
            label: "SpawnAgent",
            status: "running",
            startedAt: 1,
          }],
        })}
      />,
    );

    expect(html).toContain("Open mission ledger");
    expect(html).toContain('data-chat-open-mission-ledger="mission-1"');
    expect(html).toContain('aria-label="Open Mission Ledger for Research competitor launches"');
  });

  it("shows the concrete current request in the inline live work snapshot", () => {
    const goal = "Spawn 4 subagents, calculate 1+1, and send the result as markdown.";
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          currentGoal: goal,
          activeTools: [
            {
              id: "tool-1",
              label: "SpawnAgent",
              status: "running",
              startedAt: 1,
            },
          ],
        })}
      />,
    );

    expect(html).toContain(goal);
    expect(html).not.toContain("Working on your request");
  });

  it("renders the latest browser preview frame inside the inline live work snapshot", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          browserFrame: {
            action: "snapshot",
            url: "https://example.com",
            imageBase64: Buffer.from("inline-frame").toString("base64"),
            contentType: "image/png",
            capturedAt: 123,
          },
        })}
      />,
    );

    expect(html).toContain('data-chat-inline-browser-frame="true"');
    expect(html).toContain("Live browser");
    expect(html).toContain("https://example.com");
    expect(html).toContain("data:image/png;base64");
    expect(html).toContain('alt="Browser preview"');
  });

  it("streams a bounded inline live work log with recent completed and running tool details", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          activeTools: [
            {
              id: "old-tool",
              label: "FileRead",
              status: "done",
              startedAt: 1,
              inputPreview: JSON.stringify({ path: "old/report.md" }),
            },
            {
              id: "current-tool",
              label: "Bash",
              status: "running",
              startedAt: 2,
              inputPreview: "npm test",
            },
          ],
        })}
      />,
    );

    expect(html.match(/data-chat-inline-run-status="true"/g)?.length).toBe(1);
    expect(html).toContain("Reviewing document");
    expect(html).toContain("old/report.md");
    expect(html).toContain("Checking the work");
    expect(html).toContain("Running tests");
  });

  it("shows concrete helper assignment targets and skips generic helper iteration noise", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          responseLanguage: "ko",
          activeTools: [
            {
              id: "spawn-1",
              label: "SpawnAgent",
              status: "running",
              startedAt: 1,
              inputPreview:
                '{"persona":"skeptic-partner","prompt":"You are the SKEPTIC PARTNER.\\n\\nTask: 내외디스틸러리 TIPS LP 투자 건의 시장성과 리스크를 비판적으로 검토해줘.\\n\\nUse the provided context...',
            },
          ],
          subagents: [
            {
              taskId: "spawn-1",
              role: "explorer",
              status: "running",
              detail: "iteration 5",
              startedAt: 1,
              updatedAt: 2,
            },
          ],
        })}
      />,
    );

    expect(html).toContain("도우미 배정");
    expect(html).toContain("Task: 내외디스틸러리 TIPS LP 투자 건의 시장성과 리스크를 비판적으로 검토해줘.");
    expect(html).not.toContain("iteration 5");
  });

  it("keeps named background agents visible in the inline work card even with many live steps", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          responseLanguage: "en",
          activeTools: Array.from({ length: 6 }, (_, index) => ({
            id: `tool-${index + 1}`,
            label: "FileRead",
            status: "running",
            startedAt: index + 1,
            inputPreview: JSON.stringify({ path: `reports/source-${index + 1}.md` }),
          })),
          subagents: [
            {
              taskId: "agent-1",
              role: "explore",
              status: "running",
              detail: "Reading partner memo",
              startedAt: 1,
              updatedAt: 2,
            },
            {
              taskId: "agent-2",
              role: "review",
              status: "waiting",
              detail: "allow",
              startedAt: 1,
              updatedAt: 2,
            },
          ],
        })}
      />,
    );

    expect(html).toContain("Halley");
    expect(html).toContain("explorer");
    expect(html).toContain("Reading partner memo");
    expect(html).toContain("Meitner");
    expect(html).toContain("reviewer");
    expect(html).toContain("Checking permissions");
    expect(html).not.toContain(">allow<");
  });

  it("keeps inline run chrome in the selected UI language", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
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
        })}
        uiLanguage="en"
      />,
    );

    expect(html).toContain("Current Work");
    expect(html).toContain("Live");
    expect(html).toContain("Running");
    expect(html).toContain("1 action active");
    expect(html).not.toContain("현재 작업");
    expect(html).not.toContain("실시간");
  });

  it("shows appended model heartbeat progress as an inline public work transcript", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          responseLanguage: "en",
          turnPhase: "executing",
          heartbeatElapsedMs: 40_000,
          activeTools: [
            {
              id: "llm:turn-1:0",
              label: "ModelProgress",
              status: "done",
              startedAt: 1,
              inputPreview: JSON.stringify({
                stage: "started",
                label: "Thinking through next step",
                detail: "Reading context",
              }),
              outputPreview: "Still thinking (30s elapsed)",
            },
            {
              id: "llm:turn-1:0:heartbeat:30",
              label: "ModelProgress",
              status: "done",
              startedAt: 2,
              inputPreview: JSON.stringify({
                stage: "heartbeat",
                label: "Still working",
                detail: "Waiting for the next runtime update",
                elapsedMs: 30_000,
              }),
              outputPreview: "Still thinking (30s elapsed)",
            },
            {
              id: "llm:turn-1:0:heartbeat:40",
              label: "ModelProgress",
              status: "running",
              startedAt: 3,
              inputPreview: JSON.stringify({
                stage: "heartbeat",
                label: "Still working",
                detail: "Waiting for the next runtime update",
                elapsedMs: 40_000,
              }),
              outputPreview: "Still thinking (40s elapsed)",
            },
          ],
        })}
      />,
    );

    expect(html).toContain('data-chat-inline-run-status="true"');
    expect(html).toContain("Thinking through next step");
    expect(html.match(/Still working/g)?.length).toBeGreaterThanOrEqual(2);
    expect(html).toContain("30s elapsed");
    expect(html).toContain("40s elapsed");
  });

  it("renders generic public model wait stages in the inline transcript", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          responseLanguage: "ko",
          turnPhase: "executing",
          heartbeatElapsedMs: 50_000,
          activeTools: [
            {
              id: "llm:turn-1:0:heartbeat:30",
              label: "ModelProgress",
              status: "done",
              startedAt: 1,
              inputPreview: JSON.stringify({
                stage: "heartbeat",
                label: "요청 처리 중",
                detail: "공개 진행 로그를 갱신하고 있습니다",
                elapsedMs: 30_000,
              }),
            },
            {
              id: "llm:turn-1:0:heartbeat:40",
              label: "ModelProgress",
              status: "done",
              startedAt: 2,
              inputPreview: JSON.stringify({
                stage: "heartbeat",
                label: "다음 단계 준비 중",
                detail: "공개 진행 로그를 갱신하고 있습니다",
                elapsedMs: 40_000,
              }),
            },
            {
              id: "llm:turn-1:0:heartbeat:50",
              label: "ModelProgress",
              status: "running",
              startedAt: 3,
              inputPreview: JSON.stringify({
                stage: "heartbeat",
                label: "응답 구조 잡는 중",
                detail: "공개 진행 로그를 갱신하고 있습니다",
                elapsedMs: 50_000,
              }),
            },
          ],
        })}
      />,
    );

    expect(html).toContain("요청 처리 중");
    expect(html).toContain("다음 단계 준비 중");
    expect(html).toContain("응답 구조 잡는 중");
  });

  it("renders appended real-tool heartbeat stages in the inline transcript", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          responseLanguage: "ko",
          turnPhase: "executing",
          heartbeatElapsedMs: 40_000,
          activeTools: [
            {
              id: "tu_1",
              label: "FileRead",
              status: "running",
              startedAt: 1,
              inputPreview: JSON.stringify({
                path: "workspace/stock-framework-2026-05/CONTEXT.md",
              }),
            },
            {
              id: "tu_1:heartbeat:30",
              label: "ActivityProgress",
              status: "done",
              startedAt: 2,
              inputPreview: JSON.stringify({
                stage: "heartbeat",
                label: "자료 읽는 중",
                target: "workspace/stock-framework-2026-05/CONTEXT.md",
                detail: "FileRead",
                elapsedMs: 30_000,
              }),
              outputPreview: "Still running (30s elapsed)",
            },
            {
              id: "tu_1:heartbeat:40",
              label: "ActivityProgress",
              status: "done",
              startedAt: 3,
              inputPreview: JSON.stringify({
                stage: "heartbeat",
                label: "자료 읽는 중",
                target: "workspace/stock-framework-2026-05/CONTEXT.md",
                detail: "FileRead",
                elapsedMs: 40_000,
              }),
              outputPreview: "Still running (40s elapsed)",
            },
          ],
        })}
      />,
    );

    expect(html).toContain("자료 읽는 중");
    expect(html).toContain("40초째 작업 중");
    expect(html).toContain("workspace/stock-framework-2026-05/CONTEXT.md");
  });

  it("shows streaming text in normal bubble during committing phase", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "Here is the answer so far",
          turnPhase: "committing",
        })}
      />,
    );

    expect(html).toContain("Here is the answer so far");
    expect(html).not.toContain('data-chat-streaming-preview="true"');
  });

  it("renders attachment markers in live streaming assistant text during committing", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        botId="bot-1"
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: [
            "파일입니다.",
            "[attachment:00000000-0000-4000-8000-000000000111:report.pdf]",
          ].join("\n"),
          turnPhase: "committing",
        })}
      />,
    );

    expect(html).toContain("report.pdf");
    expect(html).not.toContain("[attachment:");
  });

  it("shows queued follow-up bubbles while the assistant is still streaming", () => {
    const queuedMessages: QueuedMessage[] = [
      {
        id: "queued-1",
        content: "Follow up after this",
        queuedAt: 1_800_000_000_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          turnPhase: "executing",
        })}
        queuedMessages={queuedMessages}
      />,
    );

    expect(html).toContain("Queued follow-ups");
    expect(html).toContain("Queued #1");
    expect(html).toContain("Waiting for current run");
    expect(html).toContain('data-chat-queued-card="true"');
    expect(html).toContain("Follow up after this");
  });

  it("keeps queued follow-up cards passive and exposes a separate cancel button", () => {
    const queuedMessages: QueuedMessage[] = [
      {
        id: "queued-1",
        content: "Do not cancel when I click the card body",
        queuedAt: 1_800_000_000_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          turnPhase: "executing",
        })}
        queuedMessages={queuedMessages}
      />,
    );

    expect(html).toMatch(/<div[^>]*data-chat-queued-card="true"/);
    expect(html).not.toMatch(/<button[^>]*data-chat-queued-card="true"/);
    expect(html).toContain('data-chat-queued-cancel="true"');
    expect(html).toContain('aria-label="Cancel queued follow-up #1"');
  });

  it("localizes queued follow-up cards with the selected UI language", () => {
    const queuedMessages: QueuedMessage[] = [
      {
        id: "queued-1",
        content: "현재 실행이 끝나면 이어서 해줘",
        queuedAt: 1_800_000_000_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          turnPhase: "executing",
        })}
        queuedMessages={queuedMessages}
        uiLanguage="ko"
      />,
    );

    expect(html).toContain("대기 중인 후속 메시지");
    expect(html).toContain("1개 대기");
    expect(html).toContain("대기 #1");
    expect(html).toContain("현재 실행 대기 중");
    expect(html).toContain('aria-label="대기 중인 후속 메시지 #1 취소"');
    expect(html).not.toContain("Queued follow-ups");
    expect(html).not.toContain("Waiting for current run");
  });

  it("interleaves mid-turn steering messages at the live assistant text position", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[
          {
            id: "injected-1800000001000",
            role: "user",
            content: "Please steer this part",
            timestamp: 1_800_000_001_000,
            injected: true,
            injectedAfterChars: "Alpha response. ".length,
          },
        ] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "Alpha response. Beta response.",
          turnPhase: "committing",
        })}
      />,
    );

    expect(html.indexOf("Alpha response.")).toBeLessThan(
      html.indexOf("Please steer this part"),
    );
    expect(html.indexOf("Please steer this part")).toBeLessThan(
      html.indexOf("Beta response."),
    );
  });

  it("shows streaming text as rolling preview during executing phase", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[]}
        serverMessages={[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "시험 구조를 파악했습니다. API를 확인하겠습니다. 문제 데이터를 수집합니다.",
          turnPhase: "executing",
          activeTools: [{ id: "t1", label: "WebSearch", status: "running", startedAt: Date.now() }],
        })}
      />,
    );

    expect(html).toContain('data-chat-streaming-preview="true"');
    expect(html).toContain("문제 데이터를 수집합니다.");
    expect(html).not.toContain('data-message-role="assistant"');
  });

  it("shows streaming text as normal bubble during committing phase", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[]}
        serverMessages={[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "최종 답변입니다.",
          turnPhase: "committing",
        })}
      />,
    );

    expect(html).not.toContain('data-chat-streaming-preview="true"');
    expect(html).toContain("최종 답변입니다.");
  });

  it("shows mid-turn steering messages even before assistant text starts streaming", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[
          {
            id: "injected-1800000001000",
            role: "user",
            content: "Use the new framework too",
            timestamp: 1_800_000_001_000,
            injected: true,
            injectedAfterChars: 0,
          },
        ] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "",
          thinkingStartedAt: 1_800_000_000_000,
          thinkingText: "Planning",
          turnPhase: "executing",
        })}
      />,
    );

    expect(html).toContain("Writing answer");
    expect(html).toContain("Use the new framework too");
    expect(html).toContain("mid-turn");
    expect(html).toContain("Delivered mid-turn to the running task");
  });

  it("keeps mid-turn steering visually inside the finalized assistant answer", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[
          {
            id: "injected-1800000001000",
            role: "user",
            content: "Please steer this part",
            timestamp: 1_800_000_001_000,
            injected: true,
            injectedAfterChars: "Alpha response. ".length,
          },
          {
            id: "assistant-1800000002000",
            role: "assistant",
            content: "Alpha response. Beta response.",
            timestamp: 1_800_000_002_000,
          },
        ] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState()}
      />,
    );

    expect(html.indexOf("Alpha response.")).toBeLessThan(
      html.indexOf("Please steer this part"),
    );
    expect(html.indexOf("Please steer this part")).toBeLessThan(
      html.indexOf("Beta response."),
    );
  });

  it("renders pending control requests", () => {
    const requests: ControlRequestRecord[] = [
      {
        requestId: "cr_1",
        kind: "tool_permission",
        state: "pending",
        sessionKey: "agent:main:app:general",
        channelName: "general",
        source: "turn",
        prompt: "Allow Bash?",
        proposedInput: { command: "npm test" },
        createdAt: 1,
        expiresAt: Date.now() + 60_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState()}
        controlRequests={requests}
      />,
    );

    expect(html).toContain("Allow Bash?");
    expect(html).toContain("npm test");
    expect(html).toContain("Approve");
    expect(html).toContain("Deny");
  });

  it("renders an export action in selection mode", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[
          {
            id: "msg-1",
            role: "user",
            content: "Share this",
            timestamp: 1,
          },
        ] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState()}
        selectionMode
        selectedMessages={new Set(["msg-1"])}
        onExportSelected={() => {}}
      />,
    );

    expect(html).toContain(">Export</button>");
    expect(html).toContain('<circle cx="18" cy="5" r="3"></circle>');
    expect(html).not.toContain('<line x1="12" y1="15" x2="12" y2="3"></line>');
    expect(html).toContain("Delete");
  });

  it("does not keep resolved control requests open in the transcript", () => {
    const requests: ControlRequestRecord[] = [
      {
        requestId: "cr_answered",
        kind: "user_question",
        state: "answered",
        decision: "answered",
        sessionKey: "agent:main:app:general",
        channelName: "general",
        source: "turn",
        prompt: "Which approach do you prefer?",
        proposedInput: {
          choices: [
            { id: "continue_testing", label: "Continue testing" },
          ],
        },
        answer: "continue_testing",
        createdAt: 1,
        expiresAt: Date.now() + 60_000,
        resolvedAt: Date.now(),
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState()}
        controlRequests={requests}
      />,
    );

    expect(html).not.toContain("Which approach do you prefer?");
    expect(html).not.toContain("answered");
    expect(html).not.toContain("continue_testing");
  });

  it("shows a server-only assistant message that has no local counterpart even when another assistant exists nearby", () => {
    // Scenario: bot responded at 4:05 PM but E2EE save failed, so only
    // push_messages has this message. A second assistant message at 4:05:08
    // exists locally. The first server message must NOT be filtered by
    // timestamp proximity against the second local assistant.
    const localMessages: ChatMessage[] = [
      {
        id: "user-1800000000000",
        role: "user",
        content: "첫 번째 질문",
        timestamp: 1_800_000_000_000,
      },
      {
        id: "assistant-1800000008000",
        role: "assistant",
        content: "두 번째 응답입니다.",
        timestamp: 1_800_000_008_000,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "push-msg-first-response",
        serverId: "push-msg-first-response",
        role: "assistant",
        content: "첫 번째 응답입니다.",
        timestamp: 1_800_000_005_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={localMessages}
        serverMessages={serverMessages}
        channelState={baseChannelState()}
      />,
    );

    expect(html).toContain("첫 번째 응답입니다.");
    expect(html).toContain("두 번째 응답입니다.");
  });

  it("shows a server-only assistant message between two user messages when E2EE save was lost", () => {
    // Real scenario from the bug report: user@4:03, bot-response@4:05 (lost from E2EE),
    // user@4:33, bot-response@4:34 (present locally).
    // The bot response at 4:05 is only in serverMessages (from push_messages).
    const localMessages: ChatMessage[] = [
      {
        id: "user-1800000000000",
        role: "user",
        content: "결손형태이며, 개인사업자는 4월14일~12월 31일 매출로 봐주시면 됩니다.",
        timestamp: 1_800_000_000_000,
      },
      {
        id: "user-1800001800000",
        role: "user",
        content: "pdf 첨부 누락됨 다시 보내줘",
        timestamp: 1_800_001_800_000,
      },
      {
        id: "assistant-1800001860000",
        role: "assistant",
        content: "PDF 다시 첨부했습니다! 확인해보세요.",
        timestamp: 1_800_001_860_000,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "push-msg-first-bot-response",
        serverId: "push-msg-first-bot-response",
        role: "assistant",
        content: "내외디스틸러리 재무제표를 분석하겠습니다.",
        timestamp: 1_800_000_120_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={localMessages}
        serverMessages={serverMessages}
        channelState={baseChannelState()}
      />,
    );

    expect(html).toContain("내외디스틸러리 재무제표를 분석하겠습니다.");
    expect(html).toContain("PDF 다시 첨부했습니다!");
  });
});
