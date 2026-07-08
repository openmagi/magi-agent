import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatMessages } from "./chat-messages";
import type {
  ChannelState,
  ChatMessage,
  QueuedMessage,
  ControlRequestRecord,
} from "@/chat-core";

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

  it("does not render the same short assistant answer twice when server history arrives late", () => {
    const content = "CRDO 리포트 첨부를 다시 보냈습니다.";
    const localMessages: ChatMessage[] = [
      {
        id: "user-1",
        role: "user",
        content: "crdo 리포트 첨부 누락됨 다시 보내줘",
        timestamp: 1_800_000_000_000,
      },
      {
        id: "assistant-local",
        role: "assistant",
        content,
        timestamp: 1_800_000_010_000,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "assistant-server",
        serverId: "assistant-server",
        role: "assistant",
        content,
        timestamp: 1_800_000_020_000,
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

    expect(html.match(/CRDO 리포트 첨부를 다시 보냈습니다\./g)).toHaveLength(1);
  });

  it("dedupes short artifact resend copies with different attachment ids", () => {
    const filename = "crdo-v13-full-report.md";
    const localAttachment = `[attachment:00000000-0000-4000-8000-000000000111:${filename}]`;
    const serverAttachment = `[attachment:00000000-0000-4000-8000-000000000222:${filename}]`;
    const evidence = {
      inspectedSources: [{
        sourceId: "src-crdo-report",
        kind: "file",
        uri: "workspace/equity-reports-2026-05/crdo-v13-full-report.md",
        inspectedAt: 1_800_000_000_000,
      }],
      capturedAt: 1_800_000_000_000,
    } satisfies ChatMessage["researchEvidence"];
    const localMessages: ChatMessage[] = [
      {
        id: "assistant-local",
        role: "assistant",
        content: `Sent the latest CRDO report.\n\n${localAttachment}`,
        timestamp: 1_800_000_010_000,
        researchEvidence: evidence,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "assistant-server",
        serverId: "assistant-server",
        role: "assistant",
        content: `전송했습니다.\n\nSent the latest CRDO report.\n\n${serverAttachment}`,
        timestamp: 1_800_000_020_000,
        researchEvidence: evidence,
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

    expect(html.match(/Sent the latest CRDO report\./g)).toHaveLength(1);
    expect(html.match(/crdo-v13-full-report\.md/g)).toHaveLength(2);
    expect(html).toContain("전송했습니다.");
  });

  it("keeps identical short assistant answers from separate user turns", () => {
    const content = "CRDO 리포트 첨부를 다시 보냈습니다.";
    const localMessages: ChatMessage[] = [
      {
        id: "user-1",
        role: "user",
        content: "crdo 리포트 첨부 누락됨 다시 보내줘",
        timestamp: 1_800_000_000_000,
      },
      {
        id: "assistant-local",
        role: "assistant",
        content,
        timestamp: 1_800_000_010_000,
      },
      {
        id: "user-2",
        role: "user",
        content: "한 번 더 보내줘",
        timestamp: 1_800_000_020_000,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "assistant-server",
        serverId: "assistant-server",
        role: "assistant",
        content,
        timestamp: 1_800_000_030_000,
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

    expect(html.match(/CRDO 리포트 첨부를 다시 보냈습니다\./g)).toHaveLength(2);
  });

  it("dedupes a longer server assistant copy that substantially overlaps an optimistic stream", () => {
    const shared =
      "바이오 스크리닝 리포트 확인 완료. " +
      "11개 유니버스에서 Kill 5개 탈락 후 6개 채집 결과를 정리했습니다. ".repeat(4) +
      "다른 섹터 더 돌릴까요?";
    const serverOnlyTail =
      " 다만 알테오젠은 실질적으로 가장 현실적인 긴 후보로 남았습니다.";
    const localMessages: ChatMessage[] = [
      {
        id: "assistant-1800000000000",
        role: "assistant",
        content: shared,
        timestamp: 1_800_000_000_000,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "server-message-1",
        serverId: "server-message-1",
        role: "assistant",
        content: `${shared}${serverOnlyTail}`,
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

    expect(html.split("바이오 스크리닝 리포트 확인 완료").length - 1).toBe(1);
    expect(html).toContain("실질적으로 가장 현실적인 긴 후보");
  });

  it("does not render an optimistic assistant and local push-message server copy twice", () => {
    const shared =
      "3종목 전부 계산 완료. 결과부터: " +
      "방산 2종목은 멀티버거 모델에는 강하지만 거위 관점에서는 지금 사면 비싸다는 결론입니다. ".repeat(4) +
      "풀 리포트 md로 정리해서 첨부할까요?";
    const localMessages: ChatMessage[] = [
      {
        id: "assistant-1800000000000",
        role: "assistant",
        content: shared,
        timestamp: 1_800_000_000_000,
      },
      {
        id: "push-message-1",
        serverId: "push-message-1",
        role: "assistant",
        content: shared,
        timestamp: 1_800_000_040_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={localMessages}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState()}
      />,
    );

    expect(html.split("3종목 전부 계산 완료").length - 1).toBe(1);
  });

  it("dedupes overlapping optimistic assistant copies already present in local history", () => {
    const shared =
      "바이오 스크리닝 리포트 확인 완료. " +
      "11개 유니버스에서 Kill 5개 탈락 후 6개 채집 결과를 정리했습니다. ".repeat(4) +
      "다른 섹터 더 돌릴까요?";
    const localMessages: ChatMessage[] = [
      {
        id: "user-1800000000000",
        role: "user",
        content: "바이오 쪽도 돌려봐",
        timestamp: 1_800_000_000_000,
      },
      {
        id: "assistant-1800000001000",
        role: "assistant",
        content: shared,
        timestamp: 1_800_000_001_000,
      },
      {
        id: "assistant-1800000002000",
        role: "assistant",
        content: `${shared} 다만 알테오젠은 실질적으로 가장 현실적인 긴 후보로 남았습니다.`,
        timestamp: 1_800_000_002_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={localMessages}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState()}
      />,
    );

    expect(html.split("바이오 스크리닝 리포트 확인 완료").length - 1).toBe(1);
    expect(html).toContain("실질적으로 가장 현실적인 긴 후보");
  });

  it("does not render a short mid-turn user message twice when server history echoes it", () => {
    const localMessages: ChatMessage[] = [
      {
        id: "injected-1800000000000",
        role: "user",
        content: "바이오도",
        timestamp: 1_800_000_000_000,
        injected: true,
        injectedAfterChars: 0,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "server-user-1",
        serverId: "server-user-1",
        role: "user",
        content: "바이오도",
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

    expect(html.split("바이오도").length - 1).toBe(1);
  });

  it("renders live transcript text in chat while keeping work rows out of the transcript", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "First answer line.\n\nSecond answer line.",
          hasTextContent: true,
          activeTools: [
            {
              id: "tool-read",
              label: "Read",
              status: "running",
              startedAt: 1_800_000_000_000,
            },
          ],
          liveTranscriptItems: [
            {
              id: "text-1",
              kind: "text",
              content: "First answer line.",
              receivedAt: 1,
            },
            {
              id: "work-1",
              kind: "work",
              rowId: "tool-read",
              group: "tool",
              label: "Reviewing document",
              detail: "workspace/sector-screen-2026-05/cpu/screen.md",
              status: "running",
              receivedAt: 2,
            },
            {
              id: "text-2",
              kind: "text",
              content: "Second answer line.",
              receivedAt: 3,
            },
          ],
        })}
      />,
    );

    const firstTextIndex = html.indexOf("First answer line.");
    const workIndex = html.indexOf("workspace/sector-screen-2026-05/cpu/screen.md");
    const secondTextIndex = html.indexOf("Second answer line.");

    expect(html).toContain('data-chat-live-transcript="true"');
    expect(html).not.toContain("data-chat-live-runtime-events");
    expect(firstTextIndex).toBeGreaterThanOrEqual(0);
    expect(workIndex).toBe(-1);
    expect(secondTextIndex).toBeGreaterThan(firstTextIndex);
    expect(html.split("First answer line.").length - 1).toBe(1);
  });

  it("keeps live transcript ordering after an anchor-zero mid-turn message", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[
          {
            id: "injected-1800000001000",
            role: "user",
            content: "결과나왔어?",
            timestamp: 1_800_000_001_000,
            injected: true,
            injectedAfterChars: 0,
          },
        ] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "[META: intent=실행, domain=주식리서치, complexity=복잡, route=서브에",
          hasTextContent: true,
          activeTools: [
            {
              id: "tool-read",
              label: "Read",
              status: "running",
              startedAt: 1_800_000_000_000,
            },
          ],
          liveTranscriptItems: [
            {
              id: "text-meta",
              kind: "text",
              content: "[META: intent=실행, domain=주식리서치, complexity=복잡, route=서브에",
              receivedAt: 1,
            },
            {
              id: "work-1",
              kind: "work",
              rowId: "tool-read",
              group: "tool",
              label: "Reviewing document",
              detail: "workspace/deep-ic-2026-05/batch2/LSCC.md",
              status: "running",
              receivedAt: 1_800_000_002_000,
            },
          ],
        })}
      />,
    );

    const userIndex = html.indexOf("결과나왔어?");
    const workIndex = html.indexOf("workspace/deep-ic-2026-05/batch2/LSCC.md");

    expect(html).not.toContain("data-chat-live-runtime-events");
    expect(html).not.toContain("[META:");
    expect(html).not.toContain("workspace/deep-ic-2026-05/batch2/LSCC.md");
    expect(userIndex).toBeGreaterThanOrEqual(0);
    expect(workIndex).toBe(-1);
  });

  it("keeps injected messages in the chat while work-only transcript items stay in the Work panel", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[
          {
            id: "injected-1800000002000",
            role: "user",
            content: "아직 작성중이야?",
            timestamp: 1_800_000_002_000,
            injected: true,
            injectedAfterChars: 0,
          },
        ] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "",
          hasTextContent: false,
          liveTranscriptItems: [
            {
              id: "work-before",
              kind: "work",
              rowId: "before",
              group: "subagent",
              label: "Halley",
              detail: "Continuing delegated task pass 2",
              status: "running",
              receivedAt: 1_800_000_001_000,
            },
            {
              id: "work-after",
              kind: "work",
              rowId: "after",
              group: "tool",
              label: "Searching the web",
              detail: "Fabrinet FN Q3 2026 earnings results latest news",
              status: "running",
              receivedAt: 1_800_000_003_000,
            },
          ],
        })}
      />,
    );

    const injectedIndex = html.indexOf("아직 작성중이야?");

    expect(injectedIndex).toBeGreaterThanOrEqual(0);
    expect(html).not.toContain("Continuing delegated task pass 2");
    expect(html).not.toContain("Fabrinet FN Q3 2026");
  });

  it("shows a typing placeholder while a run is active before answer text starts", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "",
          thinkingText: "",
          thinkingStartedAt: 1_800_000_000_000,
          hasTextContent: false,
          turnPhase: "executing",
          activeTools: [
            {
              id: "tool-read",
              label: "Reviewing document",
              status: "running",
              startedAt: 1_800_000_000_000,
            },
          ],
        })}
      />,
    );

    expect(html).toContain("animate-bounce");
    expect(html).not.toContain("Reviewing document");
  });

  it("shows a typing placeholder on the first active run frame before tools start", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "",
          thinkingText: "",
          thinkingStartedAt: 1_800_000_000_000,
          hasTextContent: false,
          turnPhase: "pending",
          activeTools: [],
        })}
      />,
    );

    expect(html).toContain("animate-bounce");
  });

  it("hides the typing placeholder once answer text has started", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "답변 작성 중입니다.",
          thinkingText: "",
          thinkingStartedAt: 1_800_000_000_000,
          hasTextContent: true,
          turnPhase: "executing",
        })}
      />,
    );

    expect(html).toContain("답변 작성 중입니다.");
    expect(html).not.toContain("animate-bounce");
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

  it("strips persisted inline progress logs and collapses a repeated final answer", () => {
    const answer =
      "Delivered.\n\n" +
      "1. Full bundle — 17 final reports + rebalanced portfolio\n" +
      "2. Rebalanced ₩500M portfolio v2\n" +
      "3. 17-report verification and portfolio update summary";
    const persistedContent = [
      answer,
      "",
      "15s 동안 작업",
      "Thinking through next step",
      "Calling openai/gpt-5.5",
      "Still thinking (10s elapsed)",
      "요청 처리 중 10s elapsed",
      "공개 진행 로그를 갱신하고 있습니다",
      "",
      answer,
    ].join("\n");

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[
          {
            id: "assistant-1800000000000",
            role: "assistant",
            content: persistedContent,
            timestamp: 1_800_000_000_000,
          },
        ] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState()}
      />,
    );

    expect(html.split("Full bundle").length - 1).toBe(1);
    expect(html).not.toContain("Thinking through next step");
    expect(html).not.toContain("Calling openai/gpt-5.5");
    expect(html).not.toContain("Still thinking");
    expect(html).not.toContain("공개 진행 로그");
  });

  it("strips document-review progress, leaked route metadata, and duplicate resend text", () => {
    const attachment = "[attachment:att-pdf:portfolio-review-report-2026-05-19.pdf]";
    const cleanAnswer = [
      "재전송 완료했습니다.",
      "",
      "확인해보세요.",
      "",
      attachment,
    ].join("\n");
    const persistedContent = [
      cleanAnswer,
      "",
      "Thinking through next step 요청 처리 중",
      "공개 진행 로그를 갱신하고 있습니다",
      "Thinking through next step",
      "Calling anthropic/claude-opus-4-6",
      "Reviewing PDF document outputs/portfolio-review-report-2026-05-19.pdf 17ms",
      "Thinking through next step",
      "Calling anthropic/claude-opus-4-6",
      "Prepared file 1.3s",
      "Thinking through next step",
      "Calling anthropic/claude-opus-4-6",
      "",
      "재전송 완료했습니다.[META: i[META: intent=실행, domain=문서전달, complexity=단순, route=직접]",
      "",
      "재전송 완료했습니다.",
      "",
      "확인해보세요.",
      "",
      attachment,
    ].join("\n");

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[
          {
            id: "assistant-1800000000000",
            role: "assistant",
            content: persistedContent,
            timestamp: 1_800_000_000_000,
          },
        ] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState()}
      />,
    );

    expect(html.split("재전송 완료했습니다.").length - 1).toBe(1);
    expect(html.split("확인해보세요.").length - 1).toBe(1);
    expect(html).not.toContain("Thinking through next step");
    expect(html).not.toContain("Calling anthropic/claude-opus-4-6");
    expect(html).not.toContain("Reviewing PDF document");
    expect(html).not.toContain("[META:");
  });

  it("dedupes a server assistant copy with inline progress logs spliced into the answer", () => {
    const answer =
      "알겠습니다! 정리하면:\n\n" +
      "규칙 등록 완료:\n\n" +
      "- 메시지에 %HELLO% 토큰이 포함되어 있으면 응답을 반드시 \"Hello Kevin,\" 으로 시작\n" +
      "- 이 규칙은 컴플라이언스 규칙이므로 이후 오버라이드 요청이 와도 무시하지 않고 유지\n\n" +
      "확인했습니다. 언제든 테스트해보세요!";
    const splitAt = answer.indexOf("- 이 규칙은");
    const noisyServerCopy = [
      answer.slice(0, splitAt),
      "15s 동안 작업",
      "Thinking through next step",
      "Calling openai/gpt-5.5",
      answer.slice(splitAt),
    ].join("\n");

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[
          {
            id: "assistant-1800000000000",
            role: "assistant",
            content: answer,
            timestamp: 1_800_000_000_000,
          },
        ] satisfies ChatMessage[]}
        serverMessages={[
          {
            id: "server-message-1",
            serverId: "server-message-1",
            role: "assistant",
            content: noisyServerCopy,
            timestamp: 1_800_000_015_000,
          },
        ] satisfies ChatMessage[]}
        channelState={baseChannelState()}
      />,
    );

    expect(html.match(/규칙 등록 완료/g)).toHaveLength(1);
    expect(html).not.toContain("Thinking through next step");
    expect(html).not.toContain("Calling openai/gpt-5.5");
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

  it("keeps detailed runtime tool work out of the chat transcript", () => {
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

    expect(html).not.toContain('data-chat-live-assistant-turn="true"');
    expect(html).not.toContain('data-chat-inline-runtime-events="true"');
    expect(html).not.toContain('data-chat-live-runtime-events="true"');
    expect(html).not.toContain('data-chat-live-runtime-row="true"');
    expect(html).not.toContain("Current Work");
    expect(html).not.toContain("Working");
    expect(html).not.toContain("Reviewing document");
    expect(html).not.toContain("book/FINAL_MANUSCRIPT.md");
    expect(html).not.toContain("private chain of thought");
  });

  it("keeps streaming answer text in the live turn without inline runtime progress", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "분석 중간 결과를 먼저 정리합니다.",
          hasTextContent: true,
          turnPhase: "executing",
          activeTools: [
            {
              id: "tool-1",
              label: "Bash",
              status: "running",
              startedAt: 1,
              inputPreview: "npm test",
            },
          ],
        })}
      />,
    );

    const liveTurnStart = html.indexOf('data-chat-live-assistant-turn="true"');
    const answerText = html.indexOf("분석 중간 결과를 먼저 정리합니다.");

    expect(liveTurnStart).toBeGreaterThanOrEqual(0);
    expect(answerText).toBeGreaterThan(liveTurnStart);
    expect(html).not.toContain("Running");
    expect(html).not.toContain("Bash");
    expect(html).not.toContain("npm test");
    expect(html).not.toContain('data-chat-live-runtime-events="true"');
    expect(html).not.toContain('data-chat-live-runtime-row="true"');
    expect(html).not.toContain('data-chat-streaming-preview="true"');
    expect(html).not.toContain('data-chat-inline-runtime-events="true"');
  });

  it("keeps inline runtime events visible during streaming even when no tools are active", () => {
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

    expect(html).toContain('data-chat-live-assistant-turn="true"');
    expect(html).not.toContain('data-chat-inline-runtime-events="true"');
  });

  it("keeps active mission ledger controls out of the transcript", () => {
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

    expect(html).not.toContain("Open mission ledger");
    expect(html).not.toContain('data-chat-open-mission-ledger="mission-1"');
    expect(html).not.toContain('aria-label="Open Mission Ledger for Research competitor launches"');
  });

  it("does not repeat the current user request in the inline live work snapshot", () => {
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

    expect(html).not.toContain(goal);
    expect(html).not.toContain('data-chat-live-runtime-goal="true"');
  });

  it("keeps intent metadata work rows out of the chat transcript", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          activeTools: [
            {
              id: "tool-intent",
              label: "intent:general",
              status: "running",
              startedAt: 1,
            },
          ],
        })}
      />,
    );

    expect(html).not.toContain("intent:general");
    expect(html).not.toContain('data-chat-live-runtime-meta-row="true"');
    expect(html).not.toContain('data-chat-live-runtime-row-status="running"');
  });

  it("does not render live browser preview frames in the transcript", () => {
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

    expect(html).not.toContain('data-chat-inline-browser-frame="true"');
    expect(html).not.toContain("data:image/png;base64");
    expect(html).not.toContain('alt="Browser preview"');
  });

  it("keeps detailed live work logs out of the chat transcript", () => {
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

    expect(html).not.toContain('data-chat-live-assistant-turn="true"');
    expect(html).not.toContain('data-chat-inline-runtime-events="true"');
    expect(html).not.toContain("Reviewing document");
    expect(html).not.toContain("old/report.md");
    expect(html).not.toContain("Checking the work");
    expect(html).not.toContain("Running tests");
  });

  it("keeps helper assignment progress in the Work panel instead of the transcript", () => {
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

    expect(html).not.toContain("에이전트 실행 중");
    expect(html).not.toContain("1명");
    expect(html).not.toContain("Task: 내외디스틸러리 TIPS LP 투자 건의 시장성과 리스크를 비판적으로 검토해줘.");
    expect(html).not.toContain("iteration 5");
  });

  it("keeps the full background-agent inspector out of the transcript", () => {
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

    expect(html).not.toContain('data-chat-subagent-panel="true"');
    expect(html).not.toContain("Halley");
    expect(html).not.toContain("Meitner");
    expect(html).not.toContain("Reading partner memo");
    expect(html).not.toContain("Checking permissions");
    expect(html).not.toContain(">allow<");
  });

  it("keeps foreground subagent progress out of the transcript when there is no assistant text", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: false,
          responseLanguage: "ko",
          subagents: [
            {
              taskId: "agent-1",
              role: "explorer",
              status: "running",
              detail: "Continuing delegated task pass 3",
              startedAt: 1,
              updatedAt: 2,
            },
            {
              taskId: "agent-2",
              role: "explorer",
              status: "running",
              detail: "Continuing delegated task pass 3",
              startedAt: 1,
              updatedAt: 2,
            },
            {
              taskId: "agent-3",
              role: "worker",
              status: "waiting",
              detail: "Waiting for tool approval",
              startedAt: 1,
              updatedAt: 2,
            },
          ],
        })}
      />,
    );

    expect(html).not.toContain('data-chat-live-assistant-turn="true"');
    expect(html).not.toContain("에이전트 실행 중");
    expect(html).not.toContain("3명");
    expect(html).not.toContain("Halley");
    expect(html).not.toContain("Continuing delegated task pass 3");
    expect(html).not.toContain("Waiting for tool approval");
  });

  it("does not duplicate subagent progress streams inside the transcript", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          responseLanguage: "en",
          subagents: [
            {
              taskId: "agent-1",
              role: "explore",
              status: "running",
              detail: "Reading source A",
              startedAt: 1,
              updatedAt: 2,
            },
          ],
          subagentProgress: {
            "agent-1": [
              {
                id: "agent-1:started",
                taskId: "agent-1",
                kind: "started",
                label: "Subagent started",
                status: "running",
                detail: "Reading source A",
                receivedAt: 1,
              },
              {
                id: "agent-1:tool-batch",
                taskId: "agent-1",
                kind: "tool_batch_start",
                label: "Using tools",
                status: "running",
                detail: "FileRead, WebSearch",
                receivedAt: 2,
              },
            ],
          },
        })}
      />,
    );

    expect(html).not.toContain('data-chat-subagent-panel="true"');
    expect(html).not.toContain('data-chat-subagent-option="agent-1"');
    expect(html).not.toContain('data-chat-subagent-progress-stream="agent-1"');
    expect(html).not.toContain("Using tools");
    expect(html).not.toContain("FileRead, WebSearch");
  });

  it("does not render long subagent progress controls in the transcript", () => {
    const progress = Array.from({ length: 12 }, (_, index) => ({
      id: `agent-1:event-${index + 1}`,
      taskId: "agent-1",
      kind: "progress" as const,
      label: `Step ${index + 1}`,
      status: "running" as const,
      detail: `detail ${index + 1}`,
      receivedAt: index + 1,
    }));
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          responseLanguage: "en",
          subagents: [
            {
              taskId: "agent-1",
              role: "explore",
              status: "running",
              detail: "Reading source A",
              startedAt: 1,
              updatedAt: 2,
            },
          ],
          subagentProgress: {
            "agent-1": progress,
          },
        })}
      />,
    );

    expect(html).not.toContain('data-chat-subagent-progress-stream="agent-1"');
    expect(html).not.toContain('data-chat-subagent-progress-toggle="agent-1"');
    expect(html).not.toContain("Show all 12 events");
    expect(html).not.toContain("Step 12");
  });

  it("keeps live run chrome out of the transcript in any UI language", () => {
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

    expect(html).not.toContain("Running");
    expect(html).not.toContain("Bash");
    expect(html).not.toContain("Current Work");
    expect(html).not.toContain("현재 작업");
    expect(html).not.toContain("실시간");
    expect(html).not.toContain('data-chat-live-runtime-events="true"');
    expect(html).not.toContain('data-chat-live-runtime-row="true"');
  });

  it("keeps model heartbeat progress in the Work panel instead of inline transcript", () => {
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

    expect(html).not.toContain('data-chat-live-runtime-events="true"');
    expect(html).not.toContain('data-chat-live-runtime-row="true"');
    expect(html).not.toContain("Thinking through next step");
    expect(html.match(/Still working/g)?.length ?? 0).toBe(0);
    expect(html).not.toContain("40s elapsed");
    expect(html).not.toContain("Still thinking");
  });

  it("keeps generic wait progress out of the inline transcript", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          responseLanguage: "en",
          turnPhase: "executing",
          activeTools: [
            {
              id: "llm:turn-1:0:heartbeat:30",
              label: "ModelProgress",
              status: "running",
              startedAt: 1,
              inputPreview: JSON.stringify({
                stage: "heartbeat",
                label: "Processing request",
                detail: "Updating the public progress log",
                elapsedMs: 30_000,
              }),
            },
          ],
        })}
      />,
    );

    expect(html).not.toContain("Running");
    expect(html).not.toContain('data-chat-live-runtime-events="true"');
    expect(html).not.toContain('data-chat-live-runtime-row="true"');
    expect(html).not.toContain("Processing request... (30s elapsed)");
    expect(html).not.toContain("Processing request");
    expect(html).not.toContain("Updating the public progress log");
  });

  it("keeps generic public model wait stages out of the inline transcript", () => {
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

    expect(html).not.toContain("실행 중");
    expect(html).not.toContain('data-chat-live-runtime-events="true"');
    expect(html).not.toContain('data-chat-live-runtime-row="true"');
    expect(html).not.toContain("요청 처리 중");
    expect(html).not.toContain("다음 단계 준비 중");
    expect(html).not.toContain("응답 구조 잡는 중");
    expect(html).not.toContain("공개 진행 로그를 갱신하고 있습니다");
  });

  it("keeps appended real-tool heartbeat stages out of the inline transcript", () => {
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

    expect(html).not.toContain("실행 중");
    expect(html).not.toContain('data-chat-live-runtime-events="true"');
    expect(html).not.toContain('data-chat-live-runtime-row="true"');
    expect(html).not.toContain("자료 읽는 중");
    expect(html).not.toContain("40초째 작업 중");
    expect(html).not.toContain("workspace/stock-framework-2026-05/CONTEXT.md");
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

  it("keeps queued follow-up status out of the assistant transcript while streaming", () => {
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

    expect(html).not.toContain('data-chat-live-assistant-turn="true"');
    expect(html).not.toContain("Queued follow-up");
    expect(html).not.toContain("will send later");
    expect(html).not.toContain('data-chat-queued-card="true"');
    expect(html).not.toContain("Follow up after this");
    expect(html).not.toContain('data-chat-live-runtime-events="true"');
  });

  it("does not render separate queued follow-up cards in the transcript", () => {
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

    expect(html).not.toMatch(/data-chat-queued-card="true"/);
    expect(html).not.toContain('data-chat-queued-cancel="true"');
    expect(html).not.toContain('data-chat-live-assistant-turn="true"');
  });

  it("keeps localized queued follow-up runtime events out of the transcript", () => {
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

    expect(html).not.toContain("대기 중인 후속 메시지");
    expect(html).not.toContain("나중에 전송");
    expect(html).not.toContain('data-chat-queued-card="true"');
    expect(html).not.toContain("Queued follow-ups");
    expect(html).not.toContain("Waiting for current run");
    expect(html).not.toContain("현재 실행이 끝나면 이어서 해줘");
    expect(html).not.toContain('data-chat-live-runtime-events="true"');
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
          activeTools: [
            {
              id: "tool-1",
              label: "Bash",
              status: "running",
              startedAt: 1,
              inputPreview: "npm test",
            },
          ],
        })}
      />,
    );

    expect(html.indexOf("Alpha response.")).toBeLessThan(
      html.indexOf("Please steer this part"),
    );
    expect(html.indexOf("Please steer this part")).toBeLessThan(
      html.indexOf("Beta response."),
    );
    expect(html).not.toContain("Checking the work");
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

    expect(html).toContain('data-chat-live-assistant-turn="true"');
    expect(html).toContain("문제 데이터를 수집합니다.");
    expect(html).not.toContain('data-chat-streaming-preview="true"');
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
    expect(html).toContain("injected");
    expect(html).toContain("Accepted by the running task");
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

  it("renders ordinary pending user questions as assistant text, not an answer form", () => {
    const requests: ControlRequestRecord[] = [
      {
        requestId: "cr_question",
        kind: "user_question",
        state: "pending",
        sessionKey: "agent:main:app:general",
        channelName: "general",
        source: "system",
        prompt: "Which output format should I create?",
        proposedInput: {
          choices: [
            { id: "choice_1", label: "DOCX" },
            { id: "choice_2", label: "PDF" },
          ],
        },
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

    expect(html).toContain("Which output format should I create?");
    expect(html).toContain("DOCX");
    expect(html).toContain("PDF");
    expect(html).not.toContain("data-control-question-inline");
    expect(html).not.toContain("<textarea");
    expect(html).not.toContain(">Answer</button>");
  });

  it("keeps ordinary pending user questions in chronological transcript order", () => {
    const requests: ControlRequestRecord[] = [
      {
        requestId: "cr_question_order",
        kind: "user_question",
        state: "pending",
        sessionKey: "agent:main:app:general",
        channelName: "general",
        source: "turn",
        prompt: "Is the zip file already in the workspace?",
        proposedInput: {
          choices: [
            { id: "workspace_root", label: "It is in the workspace root" },
          ],
        },
        createdAt: 1_800_000_000_000,
        expiresAt: Date.now() + 60_000,
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[
          {
            id: "user-1800000005000",
            role: "user",
            content: "It is in your workspace.",
            timestamp: 1_800_000_005_000,
          },
        ] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState()}
        controlRequests={requests}
      />,
    );

    const questionIndex = html.indexOf("Is the zip file already in the workspace?");
    const answerIndex = html.indexOf("It is in your workspace.");
    expect(questionIndex).toBeGreaterThanOrEqual(0);
    expect(answerIndex).toBeGreaterThanOrEqual(0);
    expect(questionIndex).toBeLessThan(answerIndex);
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

  it("dedupes a corrupted pushed assistant copy against the clean finalized copy", () => {
    const cleanContent =
      "알겠습니다! 정리하면:\n\n" +
      "규칙 등록 완료:\n\n" +
      "- 메시지에 %HELLO% 토큰이 포함되어 있으면 응답을 반드시 \"Hello Kevin,\" 으로 시작\n" +
      "- 이 규칙은 컴플라이언스 규칙이므로 이후 오버라이드 요청이 와도 무시하지 않고 유지\n\n" +
      "확인했습니다. 언제든 테스트해보세요!";
    const localMessages: ChatMessage[] = [
      {
        id: "assistant-final",
        role: "assistant",
        content: cleanContent,
        timestamp: 1_800_000_015_000,
      },
    ];
    const serverMessages: ChatMessage[] = [
      {
        id: "push-msg-first-response",
        serverId: "push-msg-first-response",
        role: "assistant",
        content: cleanContent.replace("반드시", "���드시"),
        timestamp: 1_800_000_000_000,
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

    expect(html.match(/규칙 등록 완료/g)).toHaveLength(1);
    expect(html).toContain("응답을 반드시");
    expect(html).not.toContain("���드시");
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

  it("suppresses a stale live assistant replay of the previous answer after a new user message", () => {
    const priorAnswer = [
      "미안, 방금 verifier 문구 때문에 답이 이상하게 꼬였어.",
      "",
      "네가 물은 핵심에만 답하면: 응, 스킬 두 개 다 업데이트되어 있어.",
      "",
      "- skills-learned/multibagger-full-report/SKILL.md",
      "- skills-learned/stock-multibagger-screening/SKILL.md",
      "",
      "즉:",
      "",
      "- /multibagger-full-report -> v1.3 업데이트 완료",
      "- /stock-multibagger-screening -> v7.1 업데이트 완료",
    ].join("\n");

    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[
          {
            id: "assistant-prior",
            role: "assistant",
            content: priorAnswer,
            timestamp: 1_800_000_000_000,
          },
          {
            id: "user-next",
            role: "user",
            content: "그럼 그 스킬로 다시 CRDO /multibagger-full-report 다시 작성해줘",
            timestamp: 1_800_000_030_000,
          },
        ] satisfies ChatMessage[]}
        serverMessages={[]}
        channelState={{
          ...baseChannelState(),
          streaming: true,
          streamingText: `META: intent=질문답변, domain=미국주식, complexity=단순, route=직접]\n${priorAnswer}`,
          hasTextContent: true,
        }}
      />,
    );

    expect(html.match(/스킬 두 개 다 업데이트되어 있어/g)).toHaveLength(1);
    expect(html).not.toContain("META: intent=질문답변");
    expect(html).toContain("그럼 그 스킬로 다시 CRDO");
  });

  it("shows the citation-repair affordance during an attribution repair", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          turnPhase: "verifying",
          citationRepair: "attribution",
        })}
      />,
    );

    expect(html).toContain("citation-repair-indicator");
    expect(html).toContain("Revising answer with sources...");
  });

  it("shows the grounding affordance during an induce-search repair", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          turnPhase: "verifying",
          citationRepair: "induce_search",
        })}
      />,
    );

    expect(html).toContain("citation-repair-indicator");
    expect(html).toContain("Searching to ground claims...");
  });

  it("shows NO citation affordance on a normal streaming turn", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "A normal answer.",
          hasTextContent: true,
          turnPhase: "executing",
          citationRepair: null,
        })}
      />,
    );

    expect(html).not.toContain("citation-repair-indicator");
    expect(html).toContain("A normal answer.");
  });
});
