import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { ControlEventLedger } from "../control/ControlEventLedger.js";
import { ControlRequestStore } from "../control/ControlRequestStore.js";
import { PlanLifecycle } from "./PlanLifecycle.js";
import type { PermissionMode } from "../Session.js";

async function makeLifecycle() {
  const rootDir = await fs.mkdtemp(path.join(os.tmpdir(), "plan-lifecycle-"));
  const sessionKey = "agent:main:app:general:1";
  const ledger = new ControlEventLedger({ rootDir, sessionKey });
  const requests = new ControlRequestStore({ ledger });
  let mode: PermissionMode = "auto";
  const hidden: string[] = [];
  const lifecycle = new PlanLifecycle({
    sessionKey,
    channelName: "general",
    controlEvents: ledger,
    controlRequests: requests,
    getPermissionMode: () => mode,
    setPermissionMode: (next) => {
      mode = next;
    },
    exitPlanMode: () => {
      mode = "auto";
    },
    enqueueHiddenContext: (message) => hidden.push(message),
  });
  return { rootDir, sessionKey, lifecycle, requests, getMode: () => mode, hidden };
}

describe("PlanLifecycle", () => {
  it("keeps plan mode after plan submission until approval resolves", async () => {
    const fx = await makeLifecycle();
    const events: unknown[] = [];

    await fx.lifecycle.enterPlanMode({ turnId: "turn-1" });
    expect(fx.getMode()).toBe("plan");

    const exitResult = await fx.lifecycle.submitPlan({
      turnId: "turn-1",
      plan: "## Plan\n- inspect\n- implement\n- verify",
      emitAgentEvent: (event) => events.push(event),
    });

    expect(exitResult).toMatchObject({
      planApproved: false,
      state: "awaiting_approval",
      planId: expect.any(String),
      requestId: expect.any(String),
    });
    expect(fx.getMode()).toBe("plan");
    expect(events[0]).toMatchObject({
      type: "plan_ready",
      planId: exitResult.planId,
      requestId: exitResult.requestId,
    });

    const projection = await fx.lifecycle.project();
    expect(projection.activePlan).toMatchObject({
      state: "awaiting_approval",
      planId: exitResult.planId,
      requestId: exitResult.requestId,
    });

    const resolved = await fx.lifecycle.resolveControlRequest(
      exitResult.requestId,
      { decision: "approved" },
    );
    expect(resolved).toMatchObject({ state: "approved" });
    expect(fx.getMode()).toBe("auto");
    expect(await fx.lifecycle.project()).toMatchObject({
      activePlan: {
        state: "verification_pending",
        planId: exitResult.planId,
        requestId: exitResult.requestId,
      },
    });
  });

  it("persists awaiting approval across lifecycle reload", async () => {
    const fx = await makeLifecycle();
    await fx.lifecycle.enterPlanMode({ turnId: "turn-1" });
    const exitResult = await fx.lifecycle.submitPlan({
      turnId: "turn-1",
      plan: "## Plan\n- durable",
    });

    const resumed = await PlanLifecycle.load(fx.rootDir, fx.sessionKey);

    expect(resumed.activePlan()).toMatchObject({
      state: "awaiting_approval",
      planId: exitResult.planId,
      requestId: exitResult.requestId,
    });
  });

  it("keeps plan mode and stores corrective feedback when rejected", async () => {
    const fx = await makeLifecycle();
    await fx.lifecycle.enterPlanMode({ turnId: "turn-1" });
    const exitResult = await fx.lifecycle.submitPlan({
      turnId: "turn-1",
      plan: "## Plan\n- risky change",
    });

    const resolved = await fx.lifecycle.resolveControlRequest(
      exitResult.requestId,
      { decision: "denied", feedback: "Narrow the blast radius first." },
    );

    expect(resolved).toMatchObject({ state: "denied" });
    expect(fx.getMode()).toBe("plan");
    expect(fx.hidden[0]).toContain("Narrow the blast radius first.");
    expect(fx.lifecycle.activePlan()).toMatchObject({
      state: "rejected",
      feedback: "Narrow the blast radius first.",
    });
  });
});
