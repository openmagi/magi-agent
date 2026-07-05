import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

import { createStreamingTextSmoother } from "./chat-client";

describe("createStreamingTextSmoother.drain (synchronous flush at tool boundaries)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("synchronously emits all pending buffered text without advancing timers", () => {
    const received: string[] = [];
    const smoother = createStreamingTextSmoother((delta) => received.push(delta), {
      initialChars: 1,
      charsPerTick: 1,
      tickMs: 50,
    });
    smoother.push("Looking into it now");
    // Only the initial char has been emitted; the rest is paced on the timer.
    const beforeDrain = received.join("");
    expect(beforeDrain.length).toBeLessThan("Looking into it now".length);

    // drain() emits everything remaining RIGHT NOW, before any timer fires.
    smoother.drain();
    expect(received.join("")).toBe("Looking into it now");
  });

  it("is a no-op when nothing is buffered", () => {
    const received: string[] = [];
    const smoother = createStreamingTextSmoother((delta) => received.push(delta), {
      initialChars: 100,
      charsPerTick: 100,
      tickMs: 50,
    });
    smoother.push("short");
    // initialChars covers the whole string, so nothing is pending.
    expect(received.join("")).toBe("short");
    smoother.drain();
    expect(received.join("")).toBe("short");
  });

  it("guarantees text ordering before a following synchronous event", () => {
    // Models the wire-order != store-order fix: text is pushed (paced), then a
    // tool boundary fires synchronously. Draining first means the store sees the
    // full text BEFORE the tool.
    const order: string[] = [];
    const smoother = createStreamingTextSmoother((delta) => order.push(`text:${delta}`), {
      initialChars: 1,
      charsPerTick: 1,
      tickMs: 50,
    });
    smoother.push("abcdef");
    // Tool boundary: drain, then record the tool.
    smoother.drain();
    order.push("tool:call-1");

    const joined = order.join("|");
    // Every text emission precedes the tool marker.
    const toolIndex = order.indexOf("tool:call-1");
    const lastTextIndex = order.map((o) => o.startsWith("text:")).lastIndexOf(true);
    expect(lastTextIndex).toBeLessThan(toolIndex);
    expect(joined).toContain("tool:call-1");
    // Reconstructed text equals the pushed text.
    const text = order.filter((o) => o.startsWith("text:")).map((o) => o.slice(5)).join("");
    expect(text).toBe("abcdef");
  });
});
