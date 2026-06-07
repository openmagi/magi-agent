import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ChatSidebar } from "./chat-sidebar";
import type { Channel } from "@/lib/chat/types";

vi.mock("next/navigation", () => ({
  useParams: () => ({ botId: "bot-1" }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/i18n", () => ({
  useI18n: () => ({ locale: "en" }),
}));

const channels: Channel[] = [
  {
    id: "channel-1",
    name: "general",
    display_name: null,
    position: 0,
    category: "General",
  },
  {
    id: "channel-2",
    name: "research",
    display_name: "Research",
    position: 1,
    category: "General",
    memory_mode: "read_only",
  },
  {
    id: "channel-3",
    name: "private",
    display_name: "Private",
    position: 2,
    category: "General",
    memory_mode: "incognito",
  },
];

const baseProps = {
  channels,
  activeChannel: "general",
  botName: "OpenMagi_Bot",
  botStatus: "active",
  editing: false,
  customCategories: [],
  refreshing: false,
  mobileOpen: false,
  onChannelSelect: vi.fn(),
  onDeleteChannel: vi.fn(),
  onCreateChannel: vi.fn(),
  onCreateCategory: vi.fn(),
  onDeleteCategory: vi.fn(),
  onRefreshChannels: vi.fn(),
  onToggleEdit: vi.fn(),
  onCancelEdit: vi.fn(),
  onMobileClose: vi.fn(),
  onReorderChannels: vi.fn(),
  onRenameChannel: vi.fn(),
  onRenameCategory: vi.fn(),
};

describe("ChatSidebar", () => {
  it("keeps bot creation inside the switcher menu without a duplicate header button", () => {
    const html = renderToStaticMarkup(
      <ChatSidebar
        {...baseProps}
        currentBotId="bot-1"
        bots={[
          { id: "bot-1", name: "OpenMagi_Bot", status: "active" },
          { id: "bot-2", name: "Research_Bot", status: "active" },
        ]}
        maxBots={5}
      />,
    );

    expect(html).toContain("aria-label=\"Switch bot\"");
    expect(html).not.toContain("aria-label=\"Add bot\"");
  });

  it("delegates channel delete confirmation to the parent handler", () => {
    const source = readFileSync(new URL("./chat-sidebar.tsx", import.meta.url), "utf8");
    const channelRow = source.match(/function SortableChannel[\s\S]*?\/\* ---------- Main component/m)?.[0] ?? "";

    expect(channelRow).toContain("<TrashIcon onClick={() => onDelete(channel.name)} />");
    expect(channelRow).not.toContain("confirm(");
  });

  it("does not show empty legacy default categories for a general-only bot in edit mode", () => {
    const html = renderToStaticMarkup(
      <ChatSidebar
        {...baseProps}
        editing
      />,
    );

    expect(html).toContain("General");
    expect(html).not.toContain("Info");
    expect(html).not.toContain("Life");
    expect(html).not.toContain("Finance");
    expect(html).not.toContain("Study");
    expect(html).not.toContain("People");
    expect(html).not.toContain("Tasks");
  });

  it("shows memory modes as compact badges outside the channel name", () => {
    const html = renderToStaticMarkup(
      <ChatSidebar
        {...baseProps}
      />,
    );

    expect(html).toContain("># Research<");
    expect(html).toContain("># Private<");
    expect(html).not.toContain("Research · Read-only memory");
    expect(html).not.toContain("Private · No memory");
    expect(html).toContain(">Read-only<");
    expect(html).toContain(">No mem<");
    expect(html).toContain("aria-label=\"Read-only memory\"");
    expect(html).toContain("aria-label=\"No memory\"");
  });

  it("keeps the edit controls outside the scrollable channel list", () => {
    const html = renderToStaticMarkup(
      <ChatSidebar
        {...baseProps}
      />,
    );
    const editBarIndex = html.indexOf('data-chat-sidebar-edit-bar="true"');
    const channelNavIndex = html.indexOf('data-chat-sidebar-channel-nav="true"');
    const editBar = html.match(/<div[^>]*data-chat-sidebar-edit-bar="true"[^>]*>/)?.[0] ?? "";

    expect(editBarIndex).toBeGreaterThanOrEqual(0);
    expect(channelNavIndex).toBeGreaterThan(editBarIndex);
    expect(editBar).not.toContain("sticky");
    expect(editBar).not.toContain("-mt-3");
  });
});
