import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { StepModel } from "./step-model";

const textProxy = new Proxy<Record<string, string>>(
  {},
  {
    get: (_target, prop) => (typeof prop === "string" ? prop : ""),
  },
);

vi.mock("@/lib/analytics", () => ({
  trackOnboardingModelSelect: vi.fn(),
}));

vi.mock("@/lib/i18n", () => ({
  useMessages: () => ({
    onboarding: {
      ...textProxy,
      haiku: "Claude Haiku 4.5",
      gpt55Pro: "GPT-5.5 Pro",
      modelTitle: "Choose Model",
      modelSubtitle: "Choose how your bot thinks.",
    },
    settingsPage: textProxy,
  }),
}));

describe("StepModel", () => {
  it("starts with compact router choices instead of individual model noise", () => {
    const html = renderToStaticMarkup(<StepModel onNext={() => undefined} />);

    expect(html).toContain("Standard Router");
    expect(html).toContain("Premium Router");
    expect(html).toContain("Advanced");
    expect(html).not.toContain("Claude Haiku 4.5");
    expect(html).not.toContain("GPT-5.5 Pro");
  });
});
