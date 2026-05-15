import React from "react";
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { NavBar } from "./nav-bar";

vi.mock("@privy-io/react-auth", () => ({
  usePrivy: () => ({
    ready: true,
    authenticated: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/i18n", () => ({
  LOCALE_LABELS: {
    ko: "한국어",
  },
  useI18n: () => ({
    locale: "ko",
    setLocale: vi.fn(),
  }),
  useMessages: () => ({
    nav: {
      logIn: "로그인",
      logOut: "로그아웃",
      dashboard: "대시보드",
      signUp: "무료로 시작",
      downloadDesktop: "데스크탑 앱",
      alreadyHaveAccount: "이미 계정이 있으신가요?",
    },
  }),
}));

vi.mock("@/lib/analytics", () => ({
  trackAuthClick: vi.fn(),
}));

describe("NavBar", () => {
  it("renders a custom primary CTA link when an href is provided", () => {
    const CustomNavBar = NavBar as React.ComponentType<{
      primaryCtaHref?: string;
      primaryCtaLabel?: string;
      englishOnly?: boolean;
    }>;

    const html = renderToStaticMarkup(
      <CustomNavBar primaryCtaHref="#tax-assistant" primaryCtaLabel="세무 자동화 시작" />,
    );

    expect(html).toContain('href="#tax-assistant"');
    expect(html).toContain("세무 자동화 시작");
  });

  it("links to the desktop download page from the public navigation", () => {
    const html = renderToStaticMarkup(<NavBar />);

    expect(html).toContain('href="/desktop"');
    expect(html).toContain("데스크탑 앱");
    expect(html).not.toContain('href="/desktop"><button');
  });

  it("links to the docs hub from public navigation", () => {
    const html = renderToStaticMarkup(<NavBar englishOnly />);

    expect(html).toContain('href="/docs"');
    expect(html).toContain("Docs");
  });

  it("can render English-only navigation for the public landing page", () => {
    const html = renderToStaticMarkup(<NavBar primaryCtaLabel="Open Magi Cloud" englishOnly />);

    expect(html).toContain("Desktop app");
    expect(html).toContain("English");
    expect(html).toContain("Log in");
    expect(html).not.toContain("데스크탑 앱");
    expect(html).not.toContain("한국어");
  });

  it("renders the Open Magi mark with a stable icon and text lockup", () => {
    const html = renderToStaticMarkup(<NavBar />);

    expect(html).toContain("openmagi-logo-lockup.png");
    expect(html).toContain('alt="Open Magi"');
  });

  it("renders the desktop app link as a quiet navigation item on desktop", () => {
    const html = renderToStaticMarkup(<NavBar />);

    expect(html).toContain('href="/desktop"');
    expect(html).toContain("text-sm text-secondary hover:text-foreground transition-colors px-3 py-1.5");
    expect(html).not.toContain('href="/desktop"><button');
  });
});
