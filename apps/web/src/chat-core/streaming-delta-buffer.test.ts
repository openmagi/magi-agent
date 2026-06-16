import { describe, expect, it, vi } from "vitest";
import { createStreamingDeltaBuffer } from "./streaming-delta-buffer";

describe("createStreamingDeltaBuffer", () => {
  it("coalesces multi-character text and thinking deltas into one scheduled flush", () => {
    const scheduled: (() => void)[] = [];
    const flush = vi.fn();
    const buffer = createStreamingDeltaBuffer(flush, {
      schedule: (callback) => {
        scheduled.push(callback);
        return 1;
      },
      cancel: vi.fn(),
    });

    buffer.appendText("he");
    buffer.appendText("ll");
    buffer.appendText("o!");
    buffer.appendThinking("thinking");

    expect(flush).not.toHaveBeenCalled();
    expect(scheduled).toHaveLength(1);

    scheduled[0]?.();

    expect(flush).toHaveBeenCalledTimes(1);
    expect(flush).toHaveBeenCalledWith({
      textDelta: "hello!",
      thinkingDelta: "thinking",
    });
  });

  it("batches single-character visible text deltas onto the scheduled frame", () => {
    const scheduled: (() => void)[] = [];
    const flush = vi.fn();
    const buffer = createStreamingDeltaBuffer(flush, {
      schedule: (callback) => {
        scheduled.push(callback);
        return scheduled.length;
      },
      cancel: vi.fn(),
    });

    buffer.appendText("안");
    buffer.appendText("녕");

    expect(scheduled).toHaveLength(1);
    expect(flush).not.toHaveBeenCalled();

    scheduled[0]?.();

    expect(flush).toHaveBeenCalledTimes(1);
    expect(flush).toHaveBeenCalledWith({
      textDelta: "안녕",
      thinkingDelta: "",
    });
  });

  it("flushes pending deltas synchronously before finalization", () => {
    const scheduled: (() => void)[] = [];
    const cancel = vi.fn();
    const flush = vi.fn();
    const buffer = createStreamingDeltaBuffer(flush, {
      schedule: (callback) => {
        scheduled.push(callback);
        return 7;
      },
      cancel,
    });

    buffer.appendText("final");
    buffer.flush();
    scheduled[0]?.();

    expect(cancel).toHaveBeenCalledWith(7);
    expect(flush).toHaveBeenCalledTimes(1);
    expect(flush).toHaveBeenCalledWith({
      textDelta: "final",
      thinkingDelta: "",
    });
  });

  it("clears pending deltas and cancels scheduled work", () => {
    const scheduled: (() => void)[] = [];
    const cancel = vi.fn();
    const flush = vi.fn();
    const buffer = createStreamingDeltaBuffer(flush, {
      schedule: (callback) => {
        scheduled.push(callback);
        return 9;
      },
      cancel,
    });

    buffer.appendText("discard");
    buffer.clear();
    scheduled[0]?.();

    expect(cancel).toHaveBeenCalledWith(9);
    expect(flush).not.toHaveBeenCalled();
  });
});
