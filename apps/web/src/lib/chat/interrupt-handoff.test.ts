import { describe, expect, it, vi } from "vitest";
import {
  buildEscCancelDecision,
  cancelActiveTurnWithQueueHandoff,
} from "./interrupt-handoff";

describe("cancelActiveTurnWithQueueHandoff", () => {
  it("preserves and drains the queue after an accepted handoff interrupt", async () => {
    const calls: string[] = [];

    const result = await cancelActiveTurnWithQueueHandoff({
      hasQueued: () => true,
      cancelStream: (options) => calls.push(`cancel:${options.preserveQueue}`),
      interrupt: async (handoffRequested) => {
        calls.push(`interrupt:${handoffRequested}`);
        return { accepted: true, handoffRequested, status: 200 };
      },
      drainQueue: () => calls.push("drain"),
    });

    expect(calls).toEqual(["interrupt:true", "cancel:true", "drain"]);
    expect(result).toEqual({
      handoffRequested: true,
      interruptAccepted: true,
      drained: true,
    });
  });

  it("starts the runtime interrupt before locally aborting the stream", async () => {
    const calls: string[] = [];

    await cancelActiveTurnWithQueueHandoff({
      hasQueued: () => false,
      cancelStream: (options) => calls.push(`cancel:${options.preserveQueue}`),
      interrupt: async (handoffRequested) => {
        calls.push(`interrupt:${handoffRequested}`);
        return { accepted: true, handoffRequested, status: 200 };
      },
      drainQueue: () => calls.push("drain"),
    });

    expect(calls).toEqual(["interrupt:false", "cancel:false"]);
  });

  it("promotes queued follow-up before interrupt handoff drains", async () => {
    const calls: string[] = [];

    const result = await cancelActiveTurnWithQueueHandoff({
      hasQueued: () => true,
      promoteQueuedForHandoff: () => calls.push("promote"),
      cancelStream: (options) => calls.push(`cancel:${options.preserveQueue}`),
      interrupt: async (handoffRequested) => {
        calls.push(`interrupt:${handoffRequested}`);
        return { accepted: true, handoffRequested, status: 200 };
      },
      drainQueue: () => calls.push("drain"),
    });

    expect(calls).toEqual(["promote", "interrupt:true", "cancel:true", "drain"]);
    expect(result.drained).toBe(true);
  });

  it("does not drain when there is no queued follow-up", async () => {
    const calls: string[] = [];

    const result = await cancelActiveTurnWithQueueHandoff({
      hasQueued: () => false,
      cancelStream: (options) => calls.push(`cancel:${options.preserveQueue}`),
      interrupt: async (handoffRequested) => {
        calls.push(`interrupt:${handoffRequested}`);
        return { accepted: true, handoffRequested, status: 200 };
      },
      drainQueue: () => calls.push("drain"),
    });

    expect(calls).toEqual(["interrupt:false", "cancel:false"]);
    expect(result).toEqual({
      handoffRequested: false,
      interruptAccepted: true,
      drained: false,
    });
  });

  it("drains when the runtime reports no active turn because the queue can start normally", async () => {
    const drainQueue = vi.fn();

    const result = await cancelActiveTurnWithQueueHandoff({
      hasQueued: () => true,
      cancelStream: vi.fn(),
      interrupt: async (handoffRequested) => ({
        accepted: false,
        handoffRequested,
        status: 409,
        reason: "no_active_turn",
      }),
      drainQueue,
    });

    expect(drainQueue).toHaveBeenCalledTimes(1);
    expect(result.drained).toBe(true);
    expect(result.interruptAccepted).toBe(false);
  });

  it("keeps the queue intact when interrupt fails before the runtime accepted handoff", async () => {
    const drainQueue = vi.fn();

    const result = await cancelActiveTurnWithQueueHandoff({
      hasQueued: () => true,
      cancelStream: vi.fn(),
      interrupt: async (handoffRequested) => ({
        accepted: false,
        handoffRequested,
        status: 0,
        reason: "network_error",
      }),
      drainQueue,
    });

    expect(drainQueue).not.toHaveBeenCalled();
    expect(result.drained).toBe(false);
  });

  it("arms first ESC when no queue exists", () => {
    expect(
      buildEscCancelDecision({ hasQueued: false, armedUntil: null, now: 1_000 }),
    ).toEqual({
      action: "arm",
      nextArmedUntil: 6_000,
    });
  });

  it("hard cancels second ESC within the arm window", () => {
    expect(
      buildEscCancelDecision({ hasQueued: false, armedUntil: 6_000, now: 2_000 }),
    ).toEqual({
      action: "cancel",
      nextArmedUntil: null,
    });
  });

  it("cancels immediately when queue exists", () => {
    expect(
      buildEscCancelDecision({ hasQueued: true, armedUntil: null, now: 1_000 }),
    ).toEqual({
      action: "cancel",
      nextArmedUntil: null,
    });
  });
});
