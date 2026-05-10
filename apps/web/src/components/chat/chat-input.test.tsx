import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  ChatInput,
  buildChatInputSendOptions,
  nextRunUntilDoneAfterSend,
  shouldCancelStopOnPointerDown,
  shouldSendComposerOnEnter,
} from "./chat-input";

describe("ChatInput", () => {
  it("exposes pptx in the file picker accept list", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} />);
    expect(html).toContain(".pptx");
  });

  it("exposes xlsx and xls in the file picker accept list", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} />);
    expect(html).toContain(".xlsx");
    expect(html).toContain(".xls");
  });

  it("shows queue and steering modes while streaming", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} streaming />);
    expect(html).toContain("Queue after run");
    expect(html).toContain("Steer current run");
  });

  it("localizes composer chrome with the selected UI language", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        onSend={() => {}}
        streaming
        uiLanguage="ko"
        queuedCount={1}
        queueFull
        onCancelQueue={() => {}}
      />,
    );

    expect(html).toContain("현재 실행 후 대기");
    expect(html).toContain("현재 실행 조정");
    expect(html).toContain("메시지...");
    expect(html).toContain("대기열 가득 참");
    expect(html).toContain("대기열 비우기");
    expect(html).toContain("완료까지 실행");
    expect(html).not.toContain("Queue after run");
    expect(html).not.toContain("Steer current run");
    expect(html).not.toContain("Message...");
  });

  it("does not show queue and steering modes while idle", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} />);
    expect(html).not.toContain("Queue after run");
    expect(html).not.toContain("Steer current run");
  });

  it("keeps the steering selector available when the follow-up queue is full", () => {
    const html = renderToStaticMarkup(
      <ChatInput onSend={() => {}} streaming streamingMode="steer" queueFull />,
    );
    expect(html).toContain('aria-pressed="true" title="Send now as a text-only steering update"');
  });

  it("renders a prominent queued follow-up strip near the composer", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        onSend={() => {}}
        streaming
        queuedCount={2}
        queueFull
        onCancelQueue={() => {}}
      />,
    );

    expect(html).toContain('data-chat-queue-strip="true"');
    expect(html).toContain("Queued after current run");
    expect(html).toContain("2 waiting");
    expect(html).toContain("Will send automatically when this run finishes.");
    expect(html).toContain("Queue full");
    expect(html).toContain("Clear queue");
  });

  it("renders the streaming stop control as a touch-safe button", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} streaming onCancel={() => {}} />);

    expect(html).toContain('data-chat-stop-button="true"');
    expect(html).toContain('type="button"');
    expect(html).toContain('aria-label="Stop"');
    expect(html).toContain("touch-manipulation");
  });

  it("cancels immediately for mobile stop pointer starts", () => {
    expect(shouldCancelStopOnPointerDown("touch")).toBe(true);
    expect(shouldCancelStopOnPointerDown("pen")).toBe(true);
    expect(shouldCancelStopOnPointerDown("mouse")).toBe(false);
  });

  it("renders composer accessories in the bottom row below textarea", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        onSend={() => {}}
        composerAccessory={<span data-testid="model-picker">Model picker</span>}
      />,
    );

    expect(html).toContain('data-chat-input-shell="true"');
    expect(html).toContain('data-composer-accessory="bottom-row"');
    expect(html).not.toContain(`sm:${"absolute"}`);
    expect(html).not.toContain(`sm:${"pr-[18rem]"}`);
    expect(html).toContain('data-testid="model-picker"');
    expect(html).toContain('data-chat-input-field="true"');
  });

  it("builds one-shot goal mission send options", () => {
    expect(buildChatInputSendOptions(false)).toBeUndefined();
    expect(buildChatInputSendOptions(true)).toEqual({ goalMode: true });
    expect(nextRunUntilDoneAfterSend(true, false)).toBe(true);
    expect(nextRunUntilDoneAfterSend(true, true)).toBe(false);
    expect(nextRunUntilDoneAfterSend(false, false)).toBe(false);
  });

  it("does not send on bare Enter on mobile web", () => {
    expect(shouldSendComposerOnEnter({ key: "Enter", shiftKey: false }, { mobileWeb: true })).toBe(false);
  });

  it("still sends on bare Enter on desktop web", () => {
    expect(shouldSendComposerOnEnter({ key: "Enter", shiftKey: false }, { mobileWeb: false })).toBe(true);
  });

  it("does not send while text composition is active", () => {
    expect(
      shouldSendComposerOnEnter(
        { key: "Enter", shiftKey: false, nativeEvent: { isComposing: true } },
        { mobileWeb: false },
      ),
    ).toBe(false);
  });
});
