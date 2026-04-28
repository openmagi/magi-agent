import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { CronScheduler, type CronRecord } from "./CronScheduler.js";

describe("CronScheduler", () => {
  let root: string;
  let clock: number;
  const now = () => clock;

  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "cron-sched-"));
    clock = new Date("2026-04-20T10:00:00").getTime();
  });
  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  async function waitForCondition(
    predicate: () => boolean,
    timeoutMs = 200,
  ): Promise<void> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (predicate()) return;
      await new Promise((resolve) => setTimeout(resolve, 1));
    }
    throw new Error("timed out waiting for condition");
  }

  it("create persists cron + computes nextFireAt", async () => {
    const s = new CronScheduler(root, { now });
    const r = await s.create({
      botId: "b1",
      userId: "u1",
      expression: "@hourly",
      prompt: "hi",
      deliveryChannel: { type: "app", channelId: "daily-update" },
      durable: true,
    });
    expect(r.cronId).toMatch(/^[0-9A-Z]{26}$/);
    expect(r.enabled).toBe(true);
    expect(r.nextFireAt).toBeGreaterThan(clock);
    const indexRaw = await fs.readFile(path.join(root, "core-agent", "crons", "index.json"), "utf8");
    const index = JSON.parse(indexRaw);
    expect(index[0].cronId).toBe(r.cronId);
  });

  it("hydrate picks up persisted crons", async () => {
    const s1 = new CronScheduler(root, { now });
    await s1.create({
      botId: "b1",
      userId: "u1",
      expression: "@hourly",
      prompt: "hi",
      deliveryChannel: { type: "app", channelId: "ch" },
      durable: true,
    });
    const s2 = new CronScheduler(root, { now });
    await s2.hydrate();
    expect(s2.list()).toHaveLength(1);
  });

  it("tick fires due crons and updates lastFiredAt + nextFireAt", async () => {
    const s = new CronScheduler(root, { now });
    const r = await s.create({
      botId: "b1",
      userId: "u1",
      expression: "*/15 * * * *",
      prompt: "probe",
      deliveryChannel: { type: "telegram", channelId: "tg-1" },
    });
    const firedWith: CronRecord[] = [];
    s.setFireHandler(async (c) => {
      firedWith.push({ ...c });
    });
    clock = r.nextFireAt + 1000;
    await s.tick();
    expect(firedWith).toHaveLength(1);
    expect(firedWith[0]?.cronId).toBe(r.cronId);
    expect(firedWith[0]?.deliveryChannel.type).toBe("telegram");
    const updated = s.get(r.cronId)!;
    expect(updated.lastFiredAt).toBe(clock);
    expect(updated.nextFireAt).toBeGreaterThan(clock);
    expect(updated.consecutiveFailures).toBe(0);
  });

  it("does not overlap ticks while a previous tick is still firing", async () => {
    const s = new CronScheduler(root, { now });
    const r = await s.create({
      botId: "b1",
      userId: "u1",
      expression: "*/15 * * * *",
      prompt: "probe",
      deliveryChannel: { type: "telegram", channelId: "tg-1" },
    });
    let fireCount = 0;
    const releases: Array<() => void> = [];
    s.setFireHandler(async () => {
      fireCount += 1;
      await new Promise<void>((resolve) => releases.push(resolve));
    });

    clock = r.nextFireAt + 1000;
    const firstTick = s.tick();
    await waitForCondition(() => releases.length === 1);
    const overlappingTick = s.tick();
    await new Promise((resolve) => setTimeout(resolve, 5));

    expect(fireCount).toBe(1);

    releases.splice(0).forEach((release) => release());
    await Promise.all([firstTick, overlappingTick]);
    expect(fireCount).toBe(1);
  });

  it("handler throw increments consecutiveFailures + auto-disables", async () => {
    const s = new CronScheduler(root, { now, maxConsecutiveFailures: 3 });
    const r = await s.create({
      botId: "b1",
      userId: "u1",
      expression: "*/1 * * * *",
      prompt: "fail",
      deliveryChannel: { type: "app", channelId: "ch" },
    });
    s.setFireHandler(async () => {
      throw new Error("kaboom");
    });
    for (let i = 0; i < 3; i++) {
      clock = s.get(r.cronId)!.nextFireAt + 500;
      await s.tick();
    }
    const after = s.get(r.cronId)!;
    expect(after.enabled).toBe(false);
    expect(after.consecutiveFailures).toBe(3);
  });

  it("disabled crons are skipped in tick", async () => {
    const s = new CronScheduler(root, { now });
    const r = await s.create({
      botId: "b1",
      userId: "u1",
      expression: "*/5 * * * *",
      prompt: "x",
      deliveryChannel: { type: "app", channelId: "ch" },
    });
    await s.update(r.cronId, { enabled: false });
    let fireCount = 0;
    s.setFireHandler(async () => {
      fireCount += 1;
    });
    clock = r.nextFireAt + 1000;
    await s.tick();
    expect(fireCount).toBe(0);
  });

  it("update(expression) recomputes nextFireAt", async () => {
    const s = new CronScheduler(root, { now });
    const r = await s.create({
      botId: "b1",
      userId: "u1",
      expression: "@hourly",
      prompt: "x",
      deliveryChannel: { type: "app", channelId: "ch" },
    });
    const prev = r.nextFireAt;
    const upd = await s.update(r.cronId, { expression: "*/1 * * * *" });
    expect(upd.nextFireAt).toBeLessThanOrEqual(prev);
  });

  it("delete removes cron + persists", async () => {
    const s = new CronScheduler(root, { now });
    const r = await s.create({
      botId: "b1",
      userId: "u1",
      expression: "@hourly",
      prompt: "x",
      deliveryChannel: { type: "app", channelId: "ch" },
    });
    expect(await s.delete(r.cronId)).toBe(true);
    expect(s.list()).toHaveLength(0);
  });

  it("invalid expression at create throws before persist", async () => {
    const s = new CronScheduler(root, { now });
    await expect(
      s.create({
        botId: "b1",
        userId: "u1",
        expression: "bogus cron",
        prompt: "x",
        deliveryChannel: { type: "app", channelId: "ch" },
      }),
    ).rejects.toThrow();
    expect(s.list()).toHaveLength(0);
  });
});
