import React from "react";
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { getLanguageSwitcherMenuPositionClass, LanguageSwitcher } from "./language-switcher";

vi.mock("@/lib/i18n", () => ({
  LOCALES: ["en", "ko", "es"],
  LOCALE_LABELS: {
    en: "English",
    ko: "한국어",
    es: "Español",
  },
  useI18n: () => ({
    locale: "ko",
    setLocale: vi.fn(),
  }),
}));

describe("LanguageSwitcher", () => {
  it("keeps the menu below the trigger by default", () => {
    expect(getLanguageSwitcherMenuPositionClass("bottom")).toContain("top-full");
  });

  it("can open the menu above the trigger for footer or sidebar placements", () => {
    expect(getLanguageSwitcherMenuPositionClass("top")).toContain("bottom-full");
  });

  it("renders an upward menu when requested", () => {
    const html = renderToStaticMarkup(<LanguageSwitcher menuPlacement="top" defaultOpen />);

    expect(html).toContain("bottom-full");
    expect(html).not.toContain("top-full");
    expect(html).toContain("English");
    expect(html).toContain("Español");
  });
});
