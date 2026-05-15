import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("SidebarNav personal KB placement", () => {
  it("shows CLI as a bot-scoped guide between chat and overview", () => {
    const source = readFileSync(new URL("./sidebar-nav.tsx", import.meta.url), "utf8");
    const botNavItems = source.match(/const botNavItems = \[[\s\S]*?\];/)?.[0] ?? "";

    expect(botNavItems).toContain('href: `${botPrefix}/cli`');
    expect(botNavItems).toContain("label: t.dashboard.cli");
    expect(botNavItems).not.toContain('href: `${botPrefix}/benchmarks`');
    expect(botNavItems.indexOf('href: `${botPrefix}/chat`')).toBeLessThan(
      botNavItems.indexOf('href: `${botPrefix}/cli`'),
    );
    expect(botNavItems.indexOf('href: `${botPrefix}/cli`')).toBeLessThan(
      botNavItems.indexOf('href: `${botPrefix}/overview`'),
    );
  });

  it("defines the dashboard CLI nav label for every supported locale", () => {
    const locales = ["en", "ko", "ja", "zh", "es"] as const;

    for (const locale of locales) {
      const source = readFileSync(
        new URL(`../../lib/i18n/locales/${locale}.ts`, import.meta.url),
        "utf8",
      );
      const dashboardBlock = source.match(/dashboard: \{[\s\S]*?deletedBot:/)?.[0] ?? "";

      expect(dashboardBlock).toContain("cli:");
    }
  });

  it("keeps Knowledge Base in the account section instead of the bot-scoped section", () => {
    const source = readFileSync(new URL("./sidebar-nav.tsx", import.meta.url), "utf8");
    const botNavItems = source.match(/const botNavItems = \[[\s\S]*?\];/)?.[0] ?? "";
    const accountNavItems = source.match(/const accountNavItems = \[[\s\S]*?\];/)?.[0] ?? "";

    expect(botNavItems).not.toContain("knowledge");
    expect(accountNavItems).toContain('href: "/dashboard/knowledge"');
    expect(accountNavItems).toContain("label: t.dashboard.knowledge");
  });

  it("preserves admin view-as on account pages and bot switcher links", () => {
    const source = readFileSync(new URL("./sidebar-nav.tsx", import.meta.url), "utf8");
    const accountNavItems = source.match(/const accountNavItems = \[[\s\S]*?\];/)?.[0] ?? "";
    const buildHref = source.match(/function buildHref\(base: string\): string \{[\s\S]*?\n  \}/)?.[0] ?? "";

    expect(accountNavItems).toContain('href: "/dashboard/billing"');
    expect(buildHref).toContain('!base.startsWith("/dashboard/admin")');
    expect(buildHref).not.toContain("/dashboard/billing");
    expect(source).toContain("href={buildHref(botHref)}");
  });

  it("fetches viewed user sidebar data with the real admin token", () => {
    const source = readFileSync(new URL("./sidebar-nav.tsx", import.meta.url), "utf8");

    expect(source).toContain('import { usePrivy } from "@privy-io/react-auth";');
    expect(source).toContain("const { getAccessToken } = usePrivy();");
    expect(source).toContain("const token = await getAccessToken();");
    expect(source).toContain('headers: { Authorization: `Bearer ${token}` }');
    expect(source).not.toContain("useAuthFetch");
  });

  it("does not show Pipelines as a dashboard navigation tab", () => {
    const source = readFileSync(new URL("./sidebar-nav.tsx", import.meta.url), "utf8");
    const botNavItems = source.match(/const botNavItems = \[[\s\S]*?\];/)?.[0] ?? "";

    expect(botNavItems).not.toContain("/pipelines");
    expect(botNavItems).not.toContain("Pipelines");
  });

  it("opens the dashboard language menu upward so it stays selectable at the bottom of the sidebar", () => {
    const source = readFileSync(new URL("./sidebar-nav.tsx", import.meta.url), "utf8");

    expect(source).toContain('<LanguageSwitcher menuPlacement="top" />');
  });

  it("keeps deleted bot tombstones selectable for historical usage without counting them against plan limits", () => {
    const source = readFileSync(new URL("./sidebar-nav.tsx", import.meta.url), "utf8");

    expect(source).toContain('const activeBots = bots.filter((bot) => bot.status !== "deleted");');
    expect(source).toContain("const canAddBot = activeBots.length < maxBots;");
    expect(source).toContain('bot.status === "deleted" ? `/dashboard/${bot.id}/usage`');
    expect(source).toContain("t.dashboard.deletedBot");
  });
});
