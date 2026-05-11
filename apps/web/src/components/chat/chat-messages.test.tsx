import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatMessages } from "./chat-messages";
import type { ChannelState, ChatMessage, QueuedMessage } from "@/lib/chat/types";

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

describe("ChatMessages", () => {
  it("localizes queued follow-up cards with a dedicated cancel affordance", () => {
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
        channelState={channelState({
          streaming: true,
          turnPhase: "executing",
          responseLanguage: "ko",
        })}
        queuedMessages={queuedMessages}
      />,
    );

    expect(html).toContain("대기 중인 후속 메시지");
    expect(html).toContain("1개 대기");
    expect(html).toContain("대기 #1");
    expect(html).toContain("현재 실행 대기 중");
    expect(html).toContain('aria-label="대기 중인 후속 메시지 #1 취소"');
    expect(html).toContain('data-chat-queued-cancel="true"');
    expect(html).not.toContain("Queued follow-ups");
    expect(html).not.toContain("Waiting for current run");
  });
});
