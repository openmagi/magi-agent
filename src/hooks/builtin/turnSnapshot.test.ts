import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { makeTurnSnapshotHooks } from "./turnSnapshot.js";
import type { HookContext } from "../types.js";

let tmpDir: string;

function makeCtx(overrides?: Partial<HookContext>): HookContext {
  return {
    botId: "bot-1",
    userId: "user-1",
    sessionKey: "sess-1",
    turnId: "turn-1",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    agentModel: "claude-opus-4-7",
    abortSignal: new AbortController().signal,
    deadlineMs: 5000,
    ...overrides,
  } as HookContext;
}

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "turn-snap-hook-"));
  await fs.writeFile(path.join(tmpDir, "file.txt"), "initial\n");
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe("turnSnapshot hooks", () => {
  it("registers two hooks with correct names and points", () => {
    const hooks = makeTurnSnapshotHooks({ workspaceRoot: tmpDir, enabled: true });
    expect(hooks.start.name).toBe("builtin:turn-snapshot-start");
    expect(hooks.start.point).toBe("beforeTurnStart");
    expect(hooks.end.name).toBe("builtin:turn-snapshot-end");
    expect(hooks.end.point).toBe("afterTurnEnd");
  });

  it("both hooks are non-blocking", () => {
    const hooks = makeTurnSnapshotHooks({ workspaceRoot: tmpDir, enabled: true });
    expect(hooks.start.blocking).toBe(false);
    expect(hooks.end.blocking).toBe(false);
  });

  it("start hook stores SHA for later retrieval by end hook", async () => {
    const hooks = makeTurnSnapshotHooks({ workspaceRoot: tmpDir, enabled: true });
    const ctx = makeCtx();

    await hooks.start.handler({}, ctx);

    await fs.writeFile(path.join(tmpDir, "file.txt"), "changed\n");

    await hooks.end.handler({}, ctx);

    const snaps = await hooks.service.listTurnSnapshots({ sessionKey: "sess-1" });
    expect(snaps.length).toBeGreaterThan(0);
  });

  it("skips when disabled", async () => {
    const hooks = makeTurnSnapshotHooks({ workspaceRoot: tmpDir, enabled: false });
    const ctx = makeCtx();

    await hooks.start.handler({}, ctx);
    await fs.writeFile(path.join(tmpDir, "file.txt"), "changed\n");
    await hooks.end.handler({}, ctx);

    const snaps = await hooks.service.listTurnSnapshots({});
    expect(snaps.length).toBe(0);
  });

  it("end hook is resilient to missing start SHA", async () => {
    const hooks = makeTurnSnapshotHooks({ workspaceRoot: tmpDir, enabled: true });
    const ctx = makeCtx();

    await fs.writeFile(path.join(tmpDir, "file.txt"), "changed\n");
    await expect(hooks.end.handler({}, ctx)).resolves.not.toThrow();
  });
});
