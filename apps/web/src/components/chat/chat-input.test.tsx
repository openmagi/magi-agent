import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  ChatInput,
  type ChatRecipeSelectionMode,
  buildSlashEntries,
  buildChatInputSendOptions,
  getSlashMatches,
  nextRecipeModeAfterSend,
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

  it("shows automatic live-run status while streaming", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} streaming />);
    expect(html).toContain('data-chat-live-run-toolbar="true"');
    expect(html).toContain("Live run");
    expect(html).toContain("Auto-steers when possible");
    expect(html).not.toContain('data-streaming-mode-option="queue"');
    expect(html).not.toContain('data-streaming-mode-option="steer"');
  });

  it("does not show live-run status while idle", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} />);
    expect(html).not.toContain('data-chat-live-run-toolbar="true"');
    expect(html).not.toContain("Auto-steers when possible");
  });

  it("buildChatInputSendOptions emits goalMode only when toggled on", () => {
    // 14f0c7f9 hard-coded goalMode:true on every send on the premise that
    // it would be always-on; that PR shipped without backend wiring, so
    // always-on was a no-op. Phase 1 of the goal-loop design
    // (clawy docs/plans/2026-06-21-magi-goal-loop-clean-break-judge-design.md)
    // restores opt-in until judge accuracy + latency are validated.
    expect(buildChatInputSendOptions("auto", undefined, undefined, false))
      .not.toHaveProperty("goalMode");
    expect(buildChatInputSendOptions("auto", undefined, undefined, true))
      .toMatchObject({ goalMode: true });
  });

  it("does not render recipe selector or fixture recipe labels by default", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} />);

    expect(html).not.toContain('data-chat-recipe-selector="true"');
    expect(html).not.toContain("Cited Source Preview");
    expect(html).not.toContain("Office Draft Review");
  });

  it("renders a compact default-off recipe selector in the composer controls", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        onSend={() => {}}
        availableRecipes={[{
          recipeId: "openmagi.research",
          label: "Cited Source Preview",
          version: "1",
        }]}
      />,
    );

    expect(html).toContain('data-chat-recipe-selector="true"');
    expect(html).toContain('data-chat-recipe-label="true"');
    expect(html).toContain('data-chat-recipe-mode-selector="true"');
    expect(html).toContain("Auto");
    expect(html).toContain("This turn only");
    expect(html).toContain("Session default");
    expect(html).toContain("Cited Source Preview");
  });

  it("hides the recipe selector when availability has no enabled safe options", () => {
    const disabledOnlyHtml = renderToStaticMarkup(
      <ChatInput
        onSend={() => {}}
        availableRecipes={[{
          recipeId: "openmagi.research",
          label: "Cited Source Preview",
          disabled: true,
        }]}
      />,
    );
    const unsafeOnlyHtml = renderToStaticMarkup(
      <ChatInput
        onSend={() => {}}
        availableRecipes={[{
          recipeId: "secret.recipe",
          label: "Secret Recipe",
        }]}
      />,
    );

    expect(disabledOnlyHtml).not.toContain('data-chat-recipe-selector="true"');
    expect(disabledOnlyHtml).not.toContain("Cited Source Preview");
    expect(unsafeOnlyHtml).not.toContain('data-chat-recipe-selector="true"');
    expect(unsafeOnlyHtml).not.toContain("Secret Recipe");
  });

  it("does not render unsafe recipe option labels", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        onSend={() => {}}
        availableRecipes={[{
          recipeId: "safe.recipe",
          label: "Bearer secret-token",
          description: "/srv/private/runtime.sqlite",
        }]}
      />,
    );

    expect(html).toContain("safe.recipe");
    expect(html).not.toContain("Bearer secret-token");
    expect(html).not.toContain("runtime.sqlite");
  });

  it("always builds goalMode send metadata (run-until-done is the default)", () => {
    expect(buildChatInputSendOptions()).toEqual({ goalMode: true });
    expect(buildChatInputSendOptions("auto")).toEqual({ goalMode: true });
  });

  it("includes explicit recipe metadata alongside the always-on goalMode", () => {
    const recipe = {
      recipeId: "openmagi.research",
      label: "Cited Source Preview",
      version: "1",
    };

    expect(buildChatInputSendOptions("auto", recipe)).toEqual({ goalMode: true });
    expect(buildChatInputSendOptions("this_turn", recipe)).toEqual({
      goalMode: true,
      explicitRecipeSelection: {
        mode: "this_turn",
        requiredRecipeRefs: [{
          recipeId: "openmagi.research",
          version: "1",
        }],
        allowAdditionalAutoRecipes: true,
      },
    });
    expect(buildChatInputSendOptions("session", recipe)).toEqual({
      goalMode: true,
      explicitRecipeSelection: {
        mode: "session",
        requiredRecipeRefs: [{
          recipeId: "openmagi.research",
          version: "1",
        }],
        allowAdditionalAutoRecipes: true,
      },
    });
  });

  it("keeps session recipe mode as persistent local composer state", () => {
    const selectedMode: ChatRecipeSelectionMode = "session";
    expect(nextRecipeModeAfterSend(selectedMode, undefined)).toBe("session");
    expect(nextRecipeModeAfterSend("this_turn", undefined)).toBe("auto");
    expect(nextRecipeModeAfterSend("this_turn", false)).toBe("this_turn");
  });

  it("keeps automatic text steering available when the follow-up queue is full", () => {
    const html = renderToStaticMarkup(
      <ChatInput onSend={() => {}} streaming queueFull canAttemptStreamingInject />,
    );
    expect(html).toContain("Auto-steers when possible");
    expect(html).not.toContain("Queue full - wait for the bot to finish");
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
    expect(html).toContain("실행 중");
    expect(html).toContain("가능하면 자동 조정");
    expect(html).toContain("답장 대상");
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
    expect(html).toContain("h-8");
    expect(html).toContain("w-8");
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

  it("keeps model controls in the bottom row while streaming", () => {
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
    const streamingControlsIndex = html.indexOf('data-chat-live-run-status="true"');
    const inputShellIndex = html.indexOf('data-chat-input-shell="true"');
    const actionsIndex = html.indexOf('data-chat-composer-actions="true"');
    const bottomAccessoryIndex = html.indexOf('data-composer-accessory="bottom-row"');

    expect(panelIndex).toBeGreaterThanOrEqual(0);
    expect(toolbarIndex).toBeGreaterThan(panelIndex);
    expect(streamingControlsIndex).toBeGreaterThan(toolbarIndex);
    expect(html).not.toContain('data-composer-accessory="streaming-toolbar"');
    expect(bottomAccessoryIndex).toBeGreaterThan(actionsIndex);
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
    expect(html).toContain("rounded-md");
    expect(html).not.toContain("sm:min-w-[18rem]");
  });

  it("does not render live-run mode buttons on mobile", () => {
    const html = renderToStaticMarkup(<ChatInput onSend={() => {}} streaming />);

    expect(html).not.toContain('data-streaming-mode-option="queue"');
    expect(html).not.toContain('data-streaming-mode-option="steer"');
    expect(html).toContain('data-chat-live-run-status="true"');
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

  it("surfaces installed custom skills in the bare '/' browse dropdown", () => {
    const entries = buildSlashEntries([
      {
        name: "custom-multibagger-screening",
        title: "Multibagger screening",
        description: "Screen stocks for multibagger potential.",
        tags: ["stocks"],
      },
    ]);

    const browse = getSlashMatches(entries, "");
    expect(
      browse.some((entry) => entry.command === "custom-multibagger-screening"),
    ).toBe(true);
  });
});
