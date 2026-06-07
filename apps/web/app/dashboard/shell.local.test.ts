import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("local dashboard shell", () => {
  it("opens the full chat workspace as the default dashboard surface", () => {
    const dashboardPage = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");
    const chatIndex = readFileSync(new URL("./[botId]/chat/page.tsx", import.meta.url), "utf8");
    const legacyChatIndex = readFileSync(new URL("./chat/page.tsx", import.meta.url), "utf8");
    const channelLayout = readFileSync(new URL("./[botId]/chat/[channel]/layout.tsx", import.meta.url), "utf8");
    const channelPage = readFileSync(new URL("./[botId]/chat/[channel]/page.tsx", import.meta.url), "utf8");

    expect(dashboardPage).toContain('router.replace("/dashboard/local/chat/general")');
    expect(chatIndex).toContain('chat/general`');
    expect(legacyChatIndex).toContain('router.replace("/dashboard/local/chat/general")');
    expect(channelLayout).toContain('channel: "general"');
    expect(channelPage).toContain('channel: "general"');
    expect(dashboardPage).not.toContain("/dashboard/local/overview");
    expect(chatIndex).not.toContain("chat/default");
    expect(legacyChatIndex).not.toContain("chat/default");
    expect(channelLayout).not.toContain('channel: "default"');
    expect(channelPage).not.toContain('channel: "default"');
  });

  it("uses a polished local console frame instead of a bare page wrapper", () => {
    const layout = readFileSync(new URL("./layout.tsx", import.meta.url), "utf8");

    expect(layout).toContain("LocalRuntimeHeader");
    expect(layout).toContain("Magi Agent Console");
    expect(layout).toContain("bg-[radial-gradient");
    expect(layout).toContain("border-b border-black/5");
  });

  it("renders icon-backed local navigation without hosted account sections", () => {
    const sidebar = readFileSync(
      new URL("../../src/components/dashboard/sidebar-nav.tsx", import.meta.url),
      "utf8",
    );

    expect(sidebar).toContain("MessageSquare");
    expect(sidebar).toContain("LayoutDashboard");
    expect(sidebar).toContain("Settings");
    expect(sidebar).toContain("Sparkles");
    expect(sidebar).toContain("Local workspace");
    expect(sidebar).not.toContain("accountNavItems");
    expect(sidebar).not.toContain("accountSection");
  });
});
