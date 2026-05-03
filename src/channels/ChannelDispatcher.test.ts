import { describe, it, expect, vi } from "vitest";
import { dispatchInbound } from "./ChannelDispatcher.js";
import type { Agent } from "../Agent.js";
import type { ChannelAdapter, InboundMessage } from "./ChannelAdapter.js";
import type { UserMessage } from "../util/types.js";

describe("dispatchInbound", () => {
  it("sends route metadata exactly once to native channels", async () => {
    const agent = {
      resetCounters: {
        get: vi.fn(async () => 0),
      },
      getOrCreateSession: vi.fn(async () => ({
        runTurn: vi.fn(async (_message: UserMessage, sse) => {
          sse.agent({
            type: "text_delta",
            delta: "[META: intent=실행, domain=문서작성, complexity=complex, route=subagent]",
          });
          sse.agent({ type: "text_delta", delta: "\n지금 바로 시작합니다." });
          sse.agent({
            type: "text_delta",
            delta: "[META: route=direct]백그라운드 없이 직접 씁니다.",
          });
          sse.agent({
            type: "turn_end",
            turnId: "turn-1",
            status: "committed",
            stopReason: "end_turn",
          });
        }),
      })),
    } as unknown as Agent;
    const adapter = {
      kind: "telegram",
      sendTyping: vi.fn(async () => {}),
      send: vi.fn(async () => {}),
      start: vi.fn(async () => {}),
      stop: vi.fn(async () => {}),
      onInboundMessage: vi.fn(),
      sendDocument: vi.fn(async () => {}),
      sendPhoto: vi.fn(async () => {}),
    } satisfies ChannelAdapter;
    const inbound: InboundMessage = {
      channel: "telegram",
      chatId: "chat-1",
      userId: "user-1",
      text: "긴 글 써줘",
      messageId: "msg-1",
      raw: {},
    };

    await dispatchInbound(agent, adapter, inbound);

    expect(adapter.send).toHaveBeenCalledWith(
      expect.objectContaining({
        text:
          "[META: intent=실행, domain=문서작성, complexity=complex, route=subagent]\n" +
          "지금 바로 시작합니다.백그라운드 없이 직접 씁니다.",
      }),
    );
  });

  it("passes downloaded inbound attachments into the Session turn", async () => {
    let capturedMessage: UserMessage | null = null;
    const agent = {
      resetCounters: {
        get: vi.fn(async () => 0),
      },
      getOrCreateSession: vi.fn(async () => ({
        runTurn: vi.fn(async (message: UserMessage) => {
          capturedMessage = message;
        }),
      })),
    } as unknown as Agent;
    const adapter = {
      kind: "telegram",
      sendTyping: vi.fn(async () => {}),
      send: vi.fn(async () => {}),
      start: vi.fn(async () => {}),
      stop: vi.fn(async () => {}),
      onInboundMessage: vi.fn(),
      sendDocument: vi.fn(async () => {}),
      sendPhoto: vi.fn(async () => {}),
    } satisfies ChannelAdapter;
    const inbound: InboundMessage = {
      channel: "telegram",
      chatId: "chat-1",
      userId: "user-1",
      text: "",
      messageId: "msg-1",
      attachments: [
        {
          kind: "file",
          name: "report.pdf",
          mimeType: "application/pdf",
          localPath: "/workspace/telegram-downloads/report.pdf",
          sizeBytes: 123,
        },
      ],
      raw: {},
    };

    await dispatchInbound(agent, adapter, inbound);

    expect(capturedMessage?.attachments).toEqual(inbound.attachments);
  });
});
