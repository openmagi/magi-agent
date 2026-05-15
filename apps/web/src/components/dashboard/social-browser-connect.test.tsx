import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { SocialBrowserConnect } from "./social-browser-connect";

vi.mock("@/hooks/use-auth-fetch", () => ({
  useAuthFetch: () => vi.fn(),
}));

vi.mock("@/lib/i18n", () => ({
  useMessages: () => ({
    settingsPage: {
      socialBrowserTitle: "Social browser",
      socialBrowserDescription: "One-time Instagram/X browser session",
      socialBrowserPasswordNotice: "No password storage.",
      socialBrowserInstagram: "Instagram",
      socialBrowserX: "X",
      socialBrowserStart: "Open",
      socialBrowserClose: "Close",
      socialBrowserRefresh: "Refresh",
      socialBrowserScreenshotAlt: "Social browser screenshot",
      socialBrowserKeyboardHint: "Focus preview",
    },
  }),
}));

describe("SocialBrowserConnect", () => {
  it("renders provider start controls and the password notice", () => {
    const html = renderToStaticMarkup(<SocialBrowserConnect />);

    expect(html).toContain("Social browser");
    expect(html).toContain("Open Instagram");
    expect(html).toContain("Open X");
    expect(html).toContain("No password storage.");
  });
});
