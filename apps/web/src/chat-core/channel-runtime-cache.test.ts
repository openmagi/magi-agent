import { describe, expect, it } from "vitest";
import {
  BACKGROUND_RUNTIME_CHANNEL_LIMIT,
  HISTORY_CACHE_TTL_MS,
  SERVER_MESSAGE_CACHE_TTL_MS,
  activeRuntimeChannelNames,
  hasCachedChannelDisplay,
  shouldRefreshByTtl,
  shouldUseCachedChannelDisplay,
} from "./channel-runtime-cache";
import type { ChannelState, ChatMessage } from "./types";

function message(id: string): ChatMessage {
  return {
    id,
    role: "user",
    content: id,
    timestamp: 1,
  };
}

function idleState(partial: Partial<ChannelState> = {}): ChannelState {
  return {
    streaming: false,
    streamingText: "",
    thinkingText: "",
    error: null,
    ...partial,
  };
}

describe("channel runtime cache policy", () => {
  it("detects cached channel display from local messages, server messages, streaming, or reconnecting state", () => {
    expect(hasCachedChannelDisplay({
      channel: "local",
      localMessages: { local: [message("local")] },
      serverMessages: {},
      channelStates: {},
    })).toBe(true);

    expect(hasCachedChannelDisplay({
      channel: "server",
      localMessages: {},
      serverMessages: { server: [message("server")] },
      channelStates: {},
    })).toBe(true);

    expect(hasCachedChannelDisplay({
      channel: "streaming",
      localMessages: {},
      serverMessages: {},
      channelStates: { streaming: idleState({ streaming: true }) },
    })).toBe(true);

    expect(hasCachedChannelDisplay({
      channel: "reconnecting",
      localMessages: {},
      serverMessages: {},
      channelStates: { reconnecting: idleState({ reconnecting: true }) },
    })).toBe(true);

    expect(hasCachedChannelDisplay({
      channel: "empty",
      localMessages: {},
      serverMessages: {},
      channelStates: { empty: idleState() },
    })).toBe(false);
  });

  it("uses cached display only when display state exists and history is fresh", () => {
    const now = 10_000;
    const localMessages = { fresh: [message("fresh")] };

    expect(shouldUseCachedChannelDisplay({
      channel: "fresh",
      localMessages,
      serverMessages: {},
      channelStates: {},
      historyLoadedAt: { fresh: now - HISTORY_CACHE_TTL_MS + 1 },
      now,
    })).toBe(true);

    expect(shouldUseCachedChannelDisplay({
      channel: "fresh",
      localMessages,
      serverMessages: {},
      channelStates: {},
      historyLoadedAt: { fresh: now - HISTORY_CACHE_TTL_MS },
      now,
    })).toBe(false);

    expect(shouldUseCachedChannelDisplay({
      channel: "missing-display",
      localMessages,
      serverMessages: {},
      channelStates: {},
      historyLoadedAt: { "missing-display": now },
      now,
    })).toBe(false);
  });

  it("returns active runtime channel names with active channel first and a capped result", () => {
    const states: Record<string, ChannelState> = {
      background: idleState({
        subagents: [{
          taskId: "bg",
          role: "background",
          status: "running",
          startedAt: 1,
          updatedAt: 1,
        }],
      }),
      inactive: idleState({
        subagents: [{
          taskId: "done",
          role: "background",
          status: "done",
          startedAt: 1,
          updatedAt: 1,
        }],
      }),
      reconnecting: idleState({ reconnecting: true }),
      visible: idleState({ streaming: true }),
      waiting: idleState({
        subagents: [{
          taskId: "wait",
          role: "research",
          status: "waiting",
          startedAt: 1,
          updatedAt: 1,
        }],
      }),
    };

    expect(activeRuntimeChannelNames(states, "visible")).toEqual([
      "visible",
      "background",
      "reconnecting",
      "waiting",
    ]);

    expect(activeRuntimeChannelNames(states, "reconnecting", 2)).toEqual([
      "reconnecting",
      "background",
    ]);

    expect(activeRuntimeChannelNames(states, "inactive").length).toBeLessThanOrEqual(
      BACKGROUND_RUNTIME_CHANNEL_LIMIT,
    );
  });

  it("blocks repeated refreshes before ttl and allows refresh after ttl", () => {
    const now = 20_000;

    expect(shouldRefreshByTtl(undefined, now, SERVER_MESSAGE_CACHE_TTL_MS)).toBe(true);
    expect(shouldRefreshByTtl(now - SERVER_MESSAGE_CACHE_TTL_MS + 1, now, SERVER_MESSAGE_CACHE_TTL_MS)).toBe(false);
    expect(shouldRefreshByTtl(now - SERVER_MESSAGE_CACHE_TTL_MS, now, SERVER_MESSAGE_CACHE_TTL_MS)).toBe(true);
  });
});
