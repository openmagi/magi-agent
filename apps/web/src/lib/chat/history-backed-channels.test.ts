import { describe, expect, it } from "vitest";

import { mergeChannelsWithHistory } from "./history-backed-channels";

describe("mergeChannelsWithHistory", () => {
  it("adds history-backed channels when app_channels rows are missing", () => {
    const channels = [
      {
        id: "quick",
        name: "quick-memo",
        display_name: "Quick Memo",
        position: 2,
        category: "General",
        created_at: "2026-04-27T16:23:25.000Z",
      },
      {
        id: "health",
        name: "health",
        display_name: "Health",
        position: 6,
        category: "Life",
        created_at: "2026-04-27T16:23:25.000Z",
      },
    ];

    const merged = mergeChannelsWithHistory(channels, [
      { channel_name: "general", created_at: "2026-03-26T14:16:29.000Z" },
      { channel_name: "schedule", created_at: "2026-05-04T17:12:35.000Z" },
      { channel_name: "fig-app-deal", created_at: "2026-04-22T13:26:08.000Z" },
      { channel_name: "quick-memo", created_at: "2026-05-01T00:00:00.000Z" },
    ]);

    expect(merged.map((channel) => channel.name)).toEqual([
      "general",
      "quick-memo",
      "schedule",
      "health",
      "fig-app-deal",
    ]);
    expect(merged.find((channel) => channel.name === "general")).toMatchObject({
      display_name: "General",
      position: 0,
      category: "General",
    });
    expect(merged.find((channel) => channel.name === "schedule")).toMatchObject({
      display_name: "Schedule",
      position: 5,
      category: "Life",
    });
    expect(merged.find((channel) => channel.name === "fig-app-deal")).toMatchObject({
      id: "history:fig-app-deal",
      display_name: "fig-app-deal",
      position: 7,
      category: "Restored",
    });
  });

  it("ignores invalid history channel names", () => {
    const merged = mergeChannelsWithHistory([], [
      { channel_name: "" },
      { channel_name: "bad space" },
      { channel_name: "general" },
    ]);

    expect(merged.map((channel) => channel.name)).toEqual(["general"]);
  });

  it("does not restore channels that have a channel-wide deletion tombstone", () => {
    const merged = mergeChannelsWithHistory(
      [],
      [
        { channel_name: "fig-app-deal", created_at: "2026-04-22T13:26:08.000Z" },
        { channel_name: "schedule", created_at: "2026-05-04T17:12:35.000Z" },
      ],
      [
        { channel_name: "fig-app-deal", client_msg_id: null },
      ],
    );

    expect(merged.map((channel) => channel.name)).toEqual(["schedule"]);
  });

  it("preserves stored channel model preference metadata", () => {
    const merged = mergeChannelsWithHistory(
      [
        {
          id: "creative",
          name: "creative",
          display_name: "Creative",
          position: 0,
          category: null,
          model_selection: "kimi_k2_5",
          router_type: "standard",
          created_at: "2026-05-12T00:00:00.000Z",
        },
      ],
      [],
    );

    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({
      name: "creative",
      model_selection: "kimi_k2_5",
      router_type: "standard",
    });
  });
});
