import { describe, it, expect, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { ControlEventLedger } from "../control/ControlEventLedger.js";
import { projectControlEvents } from "../control/ControlProjection.js";
import { createChildAgentHarness } from "./ChildAgentHarness.js";

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

async function makeLedger(): Promise<ControlEventLedger> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "child-harness-"));
  roots.push(root);
  return new ControlEventLedger({
    rootDir: root,
    sessionKey: "agent:main:test:child-harness",
  });
}

describe("ChildAgentHarness", () => {
  it("persists child_started and child_progress control events", async () => {
    const ledger = await makeLedger();
    const harness = createChildAgentHarness({
      taskId: "child-1",
      parentTurnId: "turn-parent",
      prompt: "do work",
      emitControlEvent: (event) => ledger.append(event),
    });

    await harness.started();
    await harness.progress("reading files");

    const events = await ledger.readAll();
    expect(events.map((event) => event.type)).toEqual([
      "child_started",
      "child_progress",
    ]);
    const projection = projectControlEvents(events);
    expect(projection.childAgents["child-1"]).toMatchObject({
      taskId: "child-1",
      state: "running",
      parentTurnId: "turn-parent",
      lastEventSeq: 2,
    });
  });

  it("persists exactly one terminal child_completed event", async () => {
    const ledger = await makeLedger();
    const harness = createChildAgentHarness({
      taskId: "child-1",
      parentTurnId: "turn-parent",
      prompt: "do work",
      emitControlEvent: (event) => ledger.append(event),
    });

    await harness.started();
    await harness.completed({ status: "ok", finalText: "done", toolCallCount: 2 });
    await harness.completed({ status: "ok", finalText: "done again", toolCallCount: 3 });

    const completed = (await ledger.readAll()).filter(
      (event) => event.type === "child_completed",
    );
    expect(completed).toHaveLength(1);
    expect(completed[0]).toMatchObject({
      taskId: "child-1",
      summary: { status: "ok", finalText: "done", toolCallCount: 2 },
    });
  });

  it("projects a running child after parent resume", async () => {
    const ledger = await makeLedger();
    const harness = createChildAgentHarness({
      taskId: "child-1",
      parentTurnId: "turn-parent",
      prompt: "do work",
      emitControlEvent: (event) => ledger.append(event),
    });

    await harness.started();
    await harness.progress("still running");
    await harness.toolRequest({ requestId: "tu-1", toolName: "Bash" });

    const restartedLedger = new ControlEventLedger({
      rootDir: path.dirname(path.dirname(ledger.filePath)),
      sessionKey: "agent:main:test:child-harness",
    });
    const projection = projectControlEvents(await restartedLedger.readAll());
    expect(projection.childAgents["child-1"]).toMatchObject({
      state: "running",
      lastEventSeq: 3,
    });
  });

  it("does not fail child work when the control event sink is temporarily unavailable", async () => {
    const harness = createChildAgentHarness({
      taskId: "child-1",
      parentTurnId: "turn-parent",
      prompt: "do work",
      emitControlEvent: () => {
        throw new Error("ledger unavailable");
      },
    });

    await expect(harness.started()).resolves.toBeUndefined();
    await expect(harness.progress("still running")).resolves.toBeUndefined();
    await expect(harness.completed({ status: "ok" })).resolves.toBeUndefined();
  });
});
