import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  ChatInput,
  buildSlashEntries,
  buildChatInputSendOptions,
  getSlashMatches,
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

  it("does not show queue and steering modes while idle", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} />);
    expect(html).not.toContain("Queue after run");
    expect(html).not.toContain("Steer current run");
  });

  it("renders the Run until done toggle", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} />);
    expect(html).toContain("Run until done");
    expect(html).toContain('data-chat-goal-toggle="true"');
    expect(html).toContain('aria-pressed="false"');
    expect(html).not.toContain('type="checkbox"');
  });

  it("builds goalMode send metadata only when toggled on", () => {
    expect(buildChatInputSendOptions(false)).toBeUndefined();
    expect(buildChatInputSendOptions(true)).toEqual({ goalMode: true });
  });

  it("treats Run until done as a one-shot send option", () => {
    expect(nextRunUntilDoneAfterSend(true, undefined)).toBe(false);
    expect(nextRunUntilDoneAfterSend(true, true)).toBe(false);
    expect(nextRunUntilDoneAfterSend(true, false)).toBe(true);
    expect(nextRunUntilDoneAfterSend(false, undefined)).toBe(false);
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
    expect(html).toContain("Queue full");
    expect(html).toContain("Clear queue");
  });

  it("localizes composer chrome with the selected UI language", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        onSend={() => {}}
        streaming
        uiLanguage="ko"
        queuedCount={2}
        queueFull
        onCancelQueue={() => {}}
        onCancel={() => {}}
        replyingTo={{ messageId: "msg-1", role: "user", preview: "이전 메시지" }}
        onCancelReply={() => {}}
      />,
    );

    expect(html).toContain("현재 실행 후 전송 대기");
    expect(html).toContain("2개 대기");
    expect(html).toContain("대기열 가득 참");
    expect(html).toContain("실행 후 전송");
    expect(html).toContain("현재 실행 조정");
    expect(html).toContain("답장 대상");
    expect(html).toContain("완료까지 실행");
    expect(html).toContain('placeholder="메시지..."');
    expect(html).toContain('aria-label="중단"');
    expect(html).not.toContain("Queued after current run");
    expect(html).not.toContain("Run until done");
    expect(html).not.toContain('placeholder="Message..."');
  });

  it("renders the streaming stop control as a touch-safe button", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} streaming onCancel={() => {}} />);

    expect(html).toContain('data-chat-stop-button="true"');
    expect(html).toContain('type="button"');
    expect(html).toContain('aria-label="Stop"');
    expect(html).toContain("min-h-11");
    expect(html).toContain("min-w-11");
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
    expect(html).toContain('data-chat-composer-panel="true"');
    expect(html).toContain('data-chat-composer-actions="true"');
    expect(html).toContain('data-composer-accessory="bottom-row"');
    expect(html).not.toContain("sm:absolute");
    expect(html).not.toContain("sm:pr-[18rem]");
    expect(html).toContain('data-testid="model-picker"');
    expect(html).toContain('data-chat-input-field="true"');
  });

  it("renders the composer as a low-elevation bottom dock", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        onSend={() => {}}
        composerAccessory={<span data-testid="model-picker">Model picker</span>}
      />,
    );

    expect(html).toContain('data-chat-composer-dock="true"');
    expect(html).toContain("rounded-2xl");
    expect(html).toContain("shadow-[0_2px_12px");
    expect(html).not.toContain("chat-input-glow");
    expect(html).not.toContain("backdrop-blur");
  });

  it("keeps live-run composer controls inside a compact toolbar above the textarea", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        onSend={() => {}}
        streaming
        onCancel={() => {}}
        composerAccessory={<span data-testid="model-picker">Model picker</span>}
      />,
    );

    const panelIndex = html.indexOf('data-chat-composer-panel="true"');
    const toolbarIndex = html.indexOf('data-chat-composer-toolbar="true"');
    void html.indexOf('data-chat-composer-meta-row="true"');
    const streamingControlsIndex = html.indexOf('data-streaming-composer-controls="true"');
    const inputShellIndex = html.indexOf('data-chat-input-shell="true"');
    const actionsIndex = html.indexOf('data-chat-composer-actions="true"');

    expect(panelIndex).toBeGreaterThanOrEqual(0);
    expect(toolbarIndex).toBeGreaterThan(panelIndex);
    expect(streamingControlsIndex).toBeGreaterThan(toolbarIndex);
    expect(html).toContain('data-composer-accessory="streaming-toolbar"');
    expect(html).not.toContain('data-composer-accessory="bottom-row"');
    expect(inputShellIndex).toBeGreaterThanOrEqual(0);
    expect(streamingControlsIndex).toBeLessThan(inputShellIndex);
    expect(actionsIndex).toBeGreaterThan(inputShellIndex);
    expect(html).toContain('data-chat-stop-button="true"');
  });

  it("uses a quiet live-run toolbar instead of nested floating cards", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        onSend={() => {}}
        streaming
        composerAccessory={<span data-testid="model-picker">Model picker</span>}
      />,
    );

    expect(html).toContain("bg-black/[0.04]");
    expect(html).toContain("rounded-lg");
    expect(html).not.toContain("sm:min-w-[18rem]");
  });

  it("uses mobile-first touch sizing for live-run mode buttons", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} streaming />);

    expect(html).toContain('data-streaming-mode-option="queue"');
    expect(html).toContain('data-streaming-mode-option="steer"');
    expect(html).toContain("grid-cols-2");
    expect(html).toContain("min-h-8");
    expect(html).toContain("touch-manipulation");
  });

  it("lets the bottom controls wrap before crowding the model picker", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        onSend={() => {}}
        composerAccessory={<span data-testid="model-picker">Model picker</span>}
      />,
    );

    expect(html).toContain('data-chat-composer-controls="true"');
    expect(html).toContain('data-chat-goal-toggle="true"');
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

  it("adds installed custom skills to slash autocomplete and searches their metadata", () => {
    const entries = buildSlashEntries([
      {
        name: "custom-deal-review",
        title: "투자심의 리뷰",
        description: "TIPS LP 투자 메모를 검토합니다.",
        tags: ["investment", "tips"],
      },
    ]);

    const byTitle = getSlashMatches(entries, "투자");
    expect(byTitle[0]).toMatchObject({
      command: "custom-deal-review",
      label: "투자심의 리뷰",
      category: "custom",
    });

    const byTag = getSlashMatches(entries, "tips");
    expect(byTag[0]?.command).toBe("custom-deal-review");
  });
});
