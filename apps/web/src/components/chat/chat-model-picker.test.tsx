import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ChatModelPicker } from "./chat-model-picker";

vi.mock("@/hooks/use-auth-fetch", () => ({
  useAuthFetch: () => vi.fn(),
}));

describe("ChatModelPicker", () => {
  it("renders a compact model selector for platform-credit bots", () => {
    const html = renderToStaticMarkup(
      <ChatModelPicker
        botId="bot_123"
        modelSelection="sonnet"
        apiKeyMode="platform_credits"
        subscriptionPlan="pro"
      />,
    );

    expect(html).toContain("aria-label=\"Model\"");
    expect(html).toContain("Claude Sonnet 4.5");
    expect(html).toContain("aria-label=\"Router tier\"");
    expect(html).toContain('data-chat-model-picker="true"');
    expect(html).toContain("h-10");
    expect(html).toContain("min-w-0");
    expect(html).toContain("justify-between");
  });

  it("uses an adaptive full-width shell for narrow composer rows", () => {
    const html = renderToStaticMarkup(
      <ChatModelPicker
        botId="bot_123"
        modelSelection="opus"
        apiKeyMode="platform_credits"
        subscriptionPlan="pro"
        routerType="standard"
      />,
    );

    expect(html).toContain('data-chat-model-picker="true"');
    expect(html).toContain("w-full");
    expect(html).toContain("sm:w-auto");
    expect(html).toContain("max-w-[calc(100vw-2rem)]");
  });

  it("does not render for BYOK bots because those changes still need settings", () => {
    const html = renderToStaticMarkup(
      <ChatModelPicker
        botId="bot_123"
        modelSelection="sonnet"
        apiKeyMode="byok"
        subscriptionPlan="pro"
      />,
    );

    expect(html).toBe("");
  });

  it("renders selected local beta models for eligible platform-credit plans", () => {
    const proHtml = renderToStaticMarkup(
      <ChatModelPicker
        botId="bot_123"
        modelSelection="sonnet"
        apiKeyMode="platform_credits"
        subscriptionPlan="pro"
      />,
    );
    const maxHtml = renderToStaticMarkup(
      <ChatModelPicker
        botId="bot_123"
        modelSelection="local_gemma_fast"
        apiKeyMode="platform_credits"
        subscriptionPlan="max"
      />,
    );

    expect(proHtml).not.toContain("Gemma 4 Fast (beta)");
    expect(maxHtml).toContain("Gemma 4 Fast (beta)");
  });

  it("keeps advanced picker controls on one shrinkable composer row", () => {
    const html = renderToStaticMarkup(
      <ChatModelPicker
        botId="bot_123"
        modelSelection="gemini_3_1_pro"
        apiKeyMode="platform_credits"
        subscriptionPlan="pro"
      />,
    );

    expect(html).toContain('data-chat-model-picker="true"');
    expect(html).toContain("flex-nowrap");
    expect(html).not.toContain("flex-wrap");
    expect(html).toContain("min-w-0");
  });

  it("renders as a flat composer control group instead of a nested card", () => {
    const html = renderToStaticMarkup(
      <ChatModelPicker
        botId="bot_123"
        modelSelection="opus"
        apiKeyMode="platform_credits"
        subscriptionPlan="pro"
      />,
    );

    expect(html).toContain('data-chat-model-picker="true"');
    expect(html).toContain("bg-transparent");
    expect(html).toContain("border-transparent");
    expect(html).toContain("shadow-none");
    expect(html).not.toContain("bg-black/[0.03]");
  });
});
