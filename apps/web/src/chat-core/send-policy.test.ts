import { describe, expect, it } from "vitest";
import {
  canInjectMidTurn,
  getStreamingSendMode,
  isStreamingComposerBlockedByQueue,
} from "./send-policy";

describe("streaming send policy", () => {
  it("injects text-only messages during an active stream by default", () => {
    expect(getStreamingSendMode({ hasFiles: false })).toBe("inject");
  });

  it("queues attachment messages during an active stream", () => {
    expect(getStreamingSendMode({ hasFiles: true })).toBe("queue");
  });

  it("queues KB-context messages during an active stream", () => {
    expect(
      getStreamingSendMode({
        hasFiles: false,
        hasKbContext: true,
      }),
    ).toBe("queue");
  });

  it("reports whether automatic mid-turn injection is available for the current composer payload", () => {
    expect(canInjectMidTurn({ hasFiles: false, hasKbContext: false })).toBe(true);
    expect(canInjectMidTurn({ hasFiles: true, hasKbContext: false })).toBe(false);
    expect(canInjectMidTurn({ hasFiles: false, hasKbContext: true })).toBe(false);
  });

  it("does not block text-only automatic injection just because the follow-up queue is full", () => {
    expect(isStreamingComposerBlockedByQueue({ queueFull: true, canAttemptInject: false })).toBe(true);
    expect(isStreamingComposerBlockedByQueue({ queueFull: true, canAttemptInject: true })).toBe(false);
    expect(isStreamingComposerBlockedByQueue({ queueFull: false, canAttemptInject: false })).toBe(false);
  });
});
