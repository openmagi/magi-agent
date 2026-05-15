import { describe, expect, it } from "vitest";
import {
  canSteerMidTurn,
  getStreamingSendMode,
  isStreamingComposerBlockedByQueue,
} from "./send-policy";

describe("streaming send policy", () => {
  it("queues normal messages during an active stream by default", () => {
    expect(getStreamingSendMode({ hasFiles: false })).toBe("queue");
  });

  it("queues attachment messages during an active stream", () => {
    expect(getStreamingSendMode({ hasFiles: true })).toBe("queue");
  });

  it("queues KB-context messages during an active stream", () => {
    expect(
      getStreamingSendMode({
        hasFiles: false,
        hasKbContext: true,
        requestedMode: "steer",
      }),
    ).toBe("queue");
  });

  it("requires explicit steering mode before using mid-turn injection", () => {
    expect(getStreamingSendMode({ hasFiles: false, requestedMode: "steer" })).toBe("inject");
  });

  it("reports whether mid-turn steering is available for the current composer payload", () => {
    expect(canSteerMidTurn({ hasFiles: false, hasKbContext: false })).toBe(true);
    expect(canSteerMidTurn({ hasFiles: true, hasKbContext: false })).toBe(false);
    expect(canSteerMidTurn({ hasFiles: false, hasKbContext: true })).toBe(false);
  });

  it("does not block text-only steering just because the follow-up queue is full", () => {
    expect(isStreamingComposerBlockedByQueue({ queueFull: true, mode: "queue" })).toBe(true);
    expect(isStreamingComposerBlockedByQueue({ queueFull: true, mode: "steer" })).toBe(false);
    expect(isStreamingComposerBlockedByQueue({ queueFull: false, mode: "queue" })).toBe(false);
  });
});
