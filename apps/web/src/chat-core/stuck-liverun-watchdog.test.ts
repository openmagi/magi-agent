import { describe, expect, it } from "vitest";
import {
  STUCK_LIVERUN_SILENCE_MS,
  shouldReconcileStuckLiveRun,
  stuckLiveRunResolvedChannelState,
} from "./stuck-liverun-watchdog";
import { deriveAgentActivityItems } from "./agent-activity";
import type { ToolActivity } from "./types";

describe("stuck live-run watchdog decision", () => {
  const base = { streaming: true, now: 200_000, lastFrameAt: 200_000 - 91_000 };

  it("fires when a live run has been silent past the window (SSE connection gone)", () => {
    // No frame (not even a heartbeat) for > 90s while still streaming: the
    // connection is dead even though the fetch reader never observed the close.
    expect(shouldReconcileStuckLiveRun(base)).toBe(true);
  });

  it("does NOT fire while frames still arrive", () => {
    // A heartbeat 5s ago keeps the run alive; a merely slow turn must not
    // reconcile.
    expect(
      shouldReconcileStuckLiveRun({ ...base, lastFrameAt: 200_000 - 5_000 }),
    ).toBe(false);
  });

  it("does NOT fire exactly at the boundary minus one millisecond", () => {
    expect(
      shouldReconcileStuckLiveRun({
        streaming: true,
        now: 200_000,
        lastFrameAt: 200_000 - (STUCK_LIVERUN_SILENCE_MS - 1),
      }),
    ).toBe(false);
  });

  it("fires exactly at the silence boundary", () => {
    expect(
      shouldReconcileStuckLiveRun({
        streaming: true,
        now: 200_000,
        lastFrameAt: 200_000 - STUCK_LIVERUN_SILENCE_MS,
      }),
    ).toBe(true);
  });

  it("does NOT fire for a normal completing / idle turn (streaming false)", () => {
    expect(
      shouldReconcileStuckLiveRun({ ...base, streaming: false }),
    ).toBe(false);
  });

  it("does NOT fire once the turn has a terminal phase", () => {
    expect(
      shouldReconcileStuckLiveRun({ ...base, turnPhase: "committed" }),
    ).toBe(false);
    expect(
      shouldReconcileStuckLiveRun({ ...base, turnPhase: "aborted" }),
    ).toBe(false);
  });

  it("does NOT fire while a recovery poll is already running", () => {
    expect(
      shouldReconcileStuckLiveRun({ ...base, reconnecting: true }),
    ).toBe(false);
  });

  it("does NOT fire without a frame baseline", () => {
    expect(
      shouldReconcileStuckLiveRun({ streaming: true, now: 200_000, lastFrameAt: null }),
    ).toBe(false);
    expect(
      shouldReconcileStuckLiveRun({ streaming: true, now: 200_000 }),
    ).toBe(false);
  });

  it("respects a caller-supplied silence override", () => {
    expect(
      shouldReconcileStuckLiveRun({
        streaming: true,
        now: 200_000,
        lastFrameAt: 200_000 - 46_000,
        silenceMs: 45_000,
      }),
    ).toBe(true);
  });
});

describe("stuck live-run reconcile clears the panel", () => {
  it("resolved state flips the live run to idle and drops the truncated bubble", () => {
    const patch = stuckLiveRunResolvedChannelState();
    expect(patch.streaming).toBe(false);
    expect(patch.streamingText).toBe("");
    expect(patch.reconnecting).toBe(false);
    expect(patch.turnPhase).toBeNull();
    expect(patch.heartbeatElapsedMs).toBeNull();
    expect(patch.activeTools).toEqual([]);
    expect(patch.subagents).toEqual([]);
    expect(patch.liveTranscriptItems).toEqual([]);
    expect(patch.lastFrameAt).toBeNull();
  });

  it("clears the stuck 'Processing' agent chips after reconcile", () => {
    // Before: a stuck run shows running chips (phase + heartbeat + a tool).
    const runningTool: ToolActivity = {
      id: "tool-1",
      label: "WebSearch",
      status: "running",
      startedAt: 1_000,
    };
    const before = deriveAgentActivityItems({
      live: true,
      startedAt: 1_000,
      now: 200_000,
      turnPhase: "executing",
      heartbeatElapsedMs: 90_000,
      activities: [runningTool],
    });
    expect(before.some((row) => row.status === "running")).toBe(true);

    // After reconcile: the resolved patch drives the derivation with live=false
    // and the transient drivers cleared, so no running chip survives.
    const patch = stuckLiveRunResolvedChannelState();
    const after = deriveAgentActivityItems({
      live: patch.streaming === true,
      startedAt: null,
      now: 200_000,
      turnPhase: patch.turnPhase ?? null,
      heartbeatElapsedMs: patch.heartbeatElapsedMs ?? null,
      activities: patch.activeTools ?? [],
    });
    expect(after.some((row) => row.status === "running")).toBe(false);
    expect(after).toEqual([]);
  });
});
