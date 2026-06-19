import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ChatModelPicker } from "./chat-model-picker";

vi.mock("@/hooks/use-auth-fetch", () => ({
  useAuthFetch: () => vi.fn(),
}));

describe("ChatModelPicker", () => {
  it("renders a single flat model dropdown for local (BYOK) serve", () => {
    const html = renderToStaticMarkup(
      <ChatModelPicker
        botId="local"
        modelSelection="kimi_k2_5"
        persistMode="local"
      />,
    );

    expect(html).toContain('data-chat-model-picker="true"');
    expect(html).toContain("aria-label=\"Model\"");
    expect(html).toContain("Kimi K2.6 (Fireworks AI)");
    // Local serve has no platform router tiers, so only the model dropdown shows.
    expect(html).not.toContain("aria-label=\"Router tier\"");
  });

  it("labels the default smart-routing selection in local mode", () => {
    const html = renderToStaticMarkup(
      <ChatModelPicker
        botId="local"
        modelSelection="clawy_smart_routing"
        persistMode="local"
      />,
    );

    expect(html).toContain("Smart Routing");
    expect(html).not.toContain("clawy_smart_routing");
  });

  it("uses an adaptive full-width shell for narrow composer rows", () => {
    const html = renderToStaticMarkup(
      <ChatModelPicker
        botId="local"
        modelSelection="opus"
        persistMode="local"
      />,
    );

    expect(html).toContain('data-chat-model-picker="true"');
    expect(html).toContain("w-full");
    expect(html).toContain("sm:w-auto");
    expect(html).toContain("max-w-[calc(100vw-2rem)]");
  });

  it("keeps picker controls on one shrinkable composer row", () => {
    const html = renderToStaticMarkup(
      <ChatModelPicker
        botId="local"
        modelSelection="gemini_3_1_pro"
        persistMode="local"
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
        botId="local"
        modelSelection="opus"
        persistMode="local"
      />,
    );

    expect(html).toContain('data-chat-model-picker="true"');
    expect(html).toContain("bg-transparent");
    expect(html).toContain("border-transparent");
    expect(html).toContain("shadow-none");
    expect(html).not.toContain("bg-black/[0.03]");
  });

  it("does not surface hosted-only smart-routing or codex options", () => {
    const html = renderToStaticMarkup(
      <ChatModelPicker
        botId="local"
        modelSelection="opus"
        persistMode="local"
      />,
    );

    // OSS has no smart-router backend or Codex OAuth — these options must not
    // appear in the picker since they would silently fail at the runtime.
    expect(html).not.toContain("Smart Routing"); // selected="opus" → no fallback row
    expect(html).not.toContain("Open Magi Router");
    expect(html).not.toContain("GPT Smart Routing");
    expect(html).not.toContain("Codex");
  });
});
