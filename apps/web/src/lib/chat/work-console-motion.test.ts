import { describe, expect, it } from "vitest";
import { smoothedHeartbeatElapsedMs, workConsoleRowDelayMs } from "./work-console-motion";

describe("work console motion helpers", () => {
  it("advances heartbeat elapsed with the local display clock between server updates", () => {
    expect(smoothedHeartbeatElapsedMs(42_000, 1_000, 1_900)).toBe(42_000);
    expect(smoothedHeartbeatElapsedMs(42_000, 1_000, 2_000)).toBe(43_000);
    expect(smoothedHeartbeatElapsedMs(42_000, 1_000, 4_450)).toBe(45_000);
  });

  it("does not invent elapsed time before the first heartbeat", () => {
    expect(smoothedHeartbeatElapsedMs(null, 1_000, 5_000)).toBeNull();
    expect(smoothedHeartbeatElapsedMs(undefined, 1_000, 5_000)).toBeNull();
  });

  it("caps row animation staggering so long action lists stay responsive", () => {
    expect(workConsoleRowDelayMs(0)).toBe(0);
    expect(workConsoleRowDelayMs(2)).toBe(120);
    expect(workConsoleRowDelayMs(99)).toBe(240);
  });
});
