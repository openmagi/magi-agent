/**
 * BackgroundTaskRegistry — T2-10 unit tests.
 *
 * Covers:
 *  - create() returns a running record and persists to disk
 *  - update() merges a patch and re-persists
 *  - list({status}) filters correctly
 *  - persistence round-trip: fresh registry sees prior records after
 *    hydrate() (simulating a pod restart)
 *  - stop() aborts the registered AbortController
 *  - get()/stop() on unknown taskId return null/false
 */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { BackgroundTaskRegistry } from "./BackgroundTaskRegistry.js";

async function tmpRoot(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), "bgreg-"));
}

describe("BackgroundTaskRegistry", () => {
  let root: string;

  beforeEach(async () => {
    root = await tmpRoot();
  });
  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("create() returns a running record and writes it to disk", async () => {
    const reg = new BackgroundTaskRegistry(root);
    const rec = await reg.create({
      taskId: "t1",
      parentTurnId: "turn_a",
      sessionKey: "agent:main:x:1",
      persona: "researcher",
      prompt: "do a thing",
    });
    expect(rec.status).toBe("running");
    expect(rec.startedAt).toBeGreaterThan(0);

    const filePath = path.join(root, "core-agent", "bg-tasks", "t1.json");
    const raw = await fs.readFile(filePath, "utf8");
    const parsed = JSON.parse(raw);
    expect(parsed.taskId).toBe("t1");
    expect(parsed.persona).toBe("researcher");
  });

  it("update() merges a patch and re-persists", async () => {
    const reg = new BackgroundTaskRegistry(root);
    await reg.create({
      taskId: "t2",
      parentTurnId: "turn_b",
      sessionKey: "sess_b",
      persona: "coder",
      prompt: "code",
    });
    const updated = await reg.update("t2", {
      status: "completed",
      resultText: "done",
      toolCallCount: 3,
    });
    expect(updated?.status).toBe("completed");
    expect(updated?.resultText).toBe("done");

    const reloaded = await reg.get("t2");
    expect(reloaded?.toolCallCount).toBe(3);
    expect(reloaded?.status).toBe("completed");
  });

  it("attachResult persists attempts and artifact inventory for TaskGet recovery", async () => {
    const reg = new BackgroundTaskRegistry(root);
    await reg.create({
      taskId: "partial",
      parentTurnId: "turn_partial",
      sessionKey: "sess_partial",
      persona: "writer",
      prompt: "write",
      spawnDir: "/workspace/.spawn/partial",
    });

    await reg.attachResult("partial", {
      status: "failed",
      resultText: "",
      toolCallCount: 7,
      attempts: 1,
      error: "aborted",
      artifacts: {
        spawnDir: "/workspace/.spawn/partial",
        fileCount: 4,
        handedOffArtifacts: [],
      },
    });

    const reloaded = await reg.get("partial");
    expect(reloaded?.toolCallCount).toBe(7);
    expect(reloaded?.attempts).toBe(1);
    expect(reloaded?.artifacts?.fileCount).toBe(4);
    expect(reloaded?.artifacts?.spawnDir).toBe("/workspace/.spawn/partial");
  });

  it("list({status}) filters correctly and sorts newest-first", async () => {
    const reg = new BackgroundTaskRegistry(root);
    await reg.create({
      taskId: "a",
      parentTurnId: "t",
      sessionKey: "s",
      persona: "p",
      prompt: "a",
    });
    // Small delay so startedAt strictly differs.
    await new Promise((r) => setTimeout(r, 2));
    await reg.create({
      taskId: "b",
      parentTurnId: "t",
      sessionKey: "s",
      persona: "p",
      prompt: "b",
    });
    await reg.update("a", { status: "completed", finishedAt: Date.now() });

    const running = await reg.list({ status: "running" });
    expect(running.tasks.map((r) => r.taskId)).toEqual(["b"]);

    const completed = await reg.list({ status: "completed" });
    expect(completed.tasks.map((r) => r.taskId)).toEqual(["a"]);

    const all = await reg.list();
    expect(all.tasks[0]?.taskId).toBe("b"); // newest first
  });

  it("persistence round-trip — fresh registry sees prior records after hydrate()", async () => {
    const first = new BackgroundTaskRegistry(root);
    await first.create({
      taskId: "persist",
      parentTurnId: "t",
      sessionKey: "s",
      persona: "p",
      prompt: "survive me",
    });
    await first.update("persist", { status: "completed", resultText: "x" });

    // Simulate a pod restart — new instance, same root.
    const second = new BackgroundTaskRegistry(root);
    await second.hydrate();
    const rec = await second.get("persist");
    expect(rec).toBeDefined();
    expect(rec?.status).toBe("completed");
    expect(rec?.resultText).toBe("x");
  });

  it("stop() triggers the registered AbortController", async () => {
    const reg = new BackgroundTaskRegistry(root);
    const controller = new AbortController();
    await reg.create({
      taskId: "to_stop",
      parentTurnId: "t",
      sessionKey: "s",
      persona: "p",
      prompt: "",
      abortController: controller,
    });
    expect(controller.signal.aborted).toBe(false);

    const stopped = await reg.stop("to_stop", "user_requested");
    expect(stopped).toBe(true);
    expect(controller.signal.aborted).toBe(true);

    const rec = await reg.get("to_stop");
    expect(rec?.status).toBe("aborted");
    expect(rec?.error).toBe("stopped: user_requested");

    // Second stop is a no-op — record already terminal.
    const again = await reg.stop("to_stop");
    expect(again).toBe(false);
  });

  it("get()/stop() on unknown taskId return null/false", async () => {
    const reg = new BackgroundTaskRegistry(root);
    const rec = await reg.get("nope");
    expect(rec).toBeNull();
    const stopped = await reg.stop("nope");
    expect(stopped).toBe(false);
  });

  describe("pending notifications (#81)", () => {
    it("enqueueNotification + drainForSession returns FIFO order", async () => {
      const reg = new BackgroundTaskRegistry(root);
      reg.enqueueNotification({
        taskId: "n1",
        sessionKey: "s-a",
        kind: "spawn",
        summary: "first",
        ts: 1,
      });
      reg.enqueueNotification({
        taskId: "n2",
        sessionKey: "s-a",
        kind: "cron",
        summary: "second",
        ts: 2,
      });
      reg.enqueueNotification({
        taskId: "n3",
        sessionKey: "s-a",
        kind: "agent",
        summary: "third",
        ts: 3,
      });
      const drained = reg.drainForSession("s-a");
      expect(drained.map((n) => n.taskId)).toEqual(["n1", "n2", "n3"]);
      // Queue is empty after drain.
      expect(reg.drainForSession("s-a")).toEqual([]);
    });

    it("drainForSession returns [] for unknown session", () => {
      const reg = new BackgroundTaskRegistry(root);
      expect(reg.drainForSession("never-used")).toEqual([]);
    });

    it("per-session isolation — draining one session leaves the other intact", () => {
      const reg = new BackgroundTaskRegistry(root);
      reg.enqueueNotification({
        taskId: "a1",
        sessionKey: "sess-A",
        kind: "spawn",
        summary: "A1",
        ts: 1,
      });
      reg.enqueueNotification({
        taskId: "b1",
        sessionKey: "sess-B",
        kind: "spawn",
        summary: "B1",
        ts: 2,
      });
      const a = reg.drainForSession("sess-A");
      expect(a.map((n) => n.taskId)).toEqual(["a1"]);
      const b = reg.drainForSession("sess-B");
      expect(b.map((n) => n.taskId)).toEqual(["b1"]);
    });

    it("attachResult auto-enqueues a spawn notification for the owner session", async () => {
      const reg = new BackgroundTaskRegistry(root);
      await reg.create({
        taskId: "auto-notify",
        parentTurnId: "turn_c",
        sessionKey: "sess-C",
        persona: "p",
        prompt: "do it",
      });
      await reg.attachResult("auto-notify", {
        status: "completed",
        resultText: "all done",
        toolCallCount: 2,
      });
      const drained = reg.drainForSession("sess-C");
      expect(drained).toHaveLength(1);
      expect(drained[0]?.taskId).toBe("auto-notify");
      expect(drained[0]?.kind).toBe("spawn");
      expect(drained[0]?.output).toBe("all done");
      expect(drained[0]?.summary).toContain("completed");
    });

    it("attachResult records failure summary with error text", async () => {
      const reg = new BackgroundTaskRegistry(root);
      await reg.create({
        taskId: "fail-notify",
        parentTurnId: "turn_d",
        sessionKey: "sess-D",
        persona: "p",
        prompt: "do it",
      });
      await reg.attachResult("fail-notify", {
        status: "failed",
        error: "kaboom",
      });
      const drained = reg.drainForSession("sess-D");
      expect(drained).toHaveLength(1);
      expect(drained[0]?.summary).toContain("failed");
      expect(drained[0]?.summary).toContain("kaboom");
      expect(drained[0]?.output).toBeUndefined();
    });
  });

  it("recordProgress() appends bounded progress entries", async () => {
    const reg = new BackgroundTaskRegistry(root);
    await reg.create({
      taskId: "prog",
      parentTurnId: "t",
      sessionKey: "s",
      persona: "p",
      prompt: "",
    });
    for (let i = 0; i < 3; i++) {
      await reg.recordProgress("prog", `step ${i}`);
    }
    const rec = await reg.get("prog");
    expect(rec?.progress?.length).toBe(3);
    expect(rec?.progress?.[0]?.label).toBe("step 0");
    expect(rec?.progress?.[2]?.label).toBe("step 2");
  });
});
