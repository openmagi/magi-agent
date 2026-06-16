import { describe, expect, it } from "vitest";

import { reconcileChannelsWithLocalOrder } from "./channel-order";
import type { Channel } from "./types";

function channel(name: string, position: number, extra: Partial<Channel> = {}): Channel {
  return {
    id: `ch-${name}`,
    name,
    display_name: null,
    position,
    category: "General",
    created_at: "2026-05-20T00:00:00.000Z",
    ...extra,
  };
}

describe("channel order reconciliation", () => {
  it("keeps the locally persisted channel order when a stale server fetch returns old positions", () => {
    const local = [
      channel("stock", 0, { category: "Finance" }),
      channel("general", 1),
      channel("random", 2),
    ];
    const staleServer = [
      channel("general", 0, { memory_mode: "read_only" }),
      channel("random", 1),
      channel("stock", 2, { display_name: "Stock", memory_mode: "normal" }),
    ];

    const reconciled = reconcileChannelsWithLocalOrder(staleServer, local);

    expect(reconciled.map((ch) => ch.name)).toEqual(["stock", "general", "random"]);
    expect(reconciled.map((ch) => ch.position)).toEqual([0, 1, 2]);
    expect(reconciled[0]).toMatchObject({
      name: "stock",
      category: "Finance",
      display_name: "Stock",
      memory_mode: "normal",
    });
    expect(reconciled[1]).toMatchObject({
      name: "general",
      memory_mode: "read_only",
    });
  });

  it("appends new server channels after the local order without resurrecting deleted local channels", () => {
    const local = [
      channel("stock", 0),
      channel("old-local-only", 1),
      channel("general", 2),
    ];
    const server = [
      channel("general", 0),
      channel("new-channel", 1, { category: "Other" }),
      channel("stock", 2),
    ];

    const reconciled = reconcileChannelsWithLocalOrder(server, local);

    expect(reconciled.map((ch) => ch.name)).toEqual(["stock", "general", "new-channel"]);
    expect(reconciled.map((ch) => ch.position)).toEqual([0, 1, 2]);
    expect(reconciled[2]?.category).toBe("Other");
  });
});
