/**
 * Tests for workspaceAwarenessInjector (Layer 2 meta-cognitive
 * scaffolding).
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  _clearWorkspaceAwarenessCache,
  buildWorkspaceSnapshot,
  makeWorkspaceAwarenessHook,
} from "./workspaceAwarenessInjector.js";
import type { HookContext } from "../types.js";

function makeCtx(overrides: Partial<HookContext> = {}): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm: {} as never,
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
    ...overrides,
  };
}

const baseArgs = {
  messages: [],
  tools: [],
  system: "base system",
  iteration: 0,
};

async function mkTmpWorkspace(): Promise<string> {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "workspace-awareness-"));
  return dir;
}

afterEach(() => {
  delete process.env.MAGI_WORKSPACE_AWARENESS;
  _clearWorkspaceAwarenessCache();
});

describe("workspaceAwarenessInjector", () => {
  beforeEach(() => {
    _clearWorkspaceAwarenessCache();
  });

  it("no-ops when workspace root does not exist", async () => {
    const hook = makeWorkspaceAwarenessHook({
      workspaceRoot: path.join(os.tmpdir(), "does-not-exist-" + Date.now()),
    });
    const result = await hook.handler(baseArgs, makeCtx());
    expect(result).toEqual({ action: "continue" });
  });

  it("does not inject workspace snapshots in incognito memory mode", async () => {
    const root = await mkTmpWorkspace();
    try {
      await fs.mkdir(path.join(root, "memory"));
      await fs.writeFile(path.join(root, "SCRATCHPAD.md"), "scratch");
      const hook = makeWorkspaceAwarenessHook({ workspaceRoot: root });
      const result = await hook.handler(
        baseArgs,
        makeCtx({ memoryMode: "incognito" }),
      );
      expect(result).toEqual({ action: "continue" });
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("no-ops when workspace is empty (nothing at top-level, no recent files)", async () => {
    const root = await mkTmpWorkspace();
    try {
      const hook = makeWorkspaceAwarenessHook({ workspaceRoot: root });
      const result = await hook.handler(baseArgs, makeCtx());
      expect(result).toEqual({ action: "continue" });
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("renders top-level dirs and .md files in the snapshot", async () => {
    const root = await mkTmpWorkspace();
    try {
      await fs.mkdir(path.join(root, "projects"));
      await fs.mkdir(path.join(root, "memory"));
      await fs.writeFile(path.join(root, "SCRATCHPAD.md"), "scratch");
      await fs.writeFile(path.join(root, "WORKING.md"), "working");
      await fs.mkdir(path.join(root, "node_modules")); // excluded

      const built = await buildWorkspaceSnapshot(root, Date.now());
      expect(built.fence).toContain("<workspace_snapshot");
      expect(built.fence).toContain("projects/");
      expect(built.fence).toContain("memory/");
      expect(built.fence).toContain("SCRATCHPAD.md");
      expect(built.fence).toContain("WORKING.md");
      expect(built.fence).not.toContain("node_modules");
      // dirs-first ordering.
      const pIdx = built.fence.indexOf("projects/");
      const mdIdx = built.fence.indexOf("SCRATCHPAD.md");
      expect(pIdx).toBeLessThan(mdIdx);
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("lists recently modified files sorted by mtime desc, truncated and excluding node_modules/.git", async () => {
    const root = await mkTmpWorkspace();
    try {
      const now = Date.now();
      await fs.mkdir(path.join(root, "a"));
      await fs.mkdir(path.join(root, "node_modules"));
      await fs.mkdir(path.join(root, ".git"));

      await fs.writeFile(path.join(root, "a", "old.txt"), "old");
      await fs.utimes(path.join(root, "a", "old.txt"), new Date(now - 10 * 24 * 3600 * 1000), new Date(now - 10 * 24 * 3600 * 1000));

      await fs.writeFile(path.join(root, "a", "new.txt"), "new");
      await fs.utimes(path.join(root, "a", "new.txt"), new Date(now - 1000), new Date(now - 1000));

      await fs.writeFile(path.join(root, "a", "middle.txt"), "m");
      await fs.utimes(path.join(root, "a", "middle.txt"), new Date(now - 2 * 24 * 3600 * 1000), new Date(now - 2 * 24 * 3600 * 1000));

      // Should be excluded by walker:
      await fs.writeFile(path.join(root, "node_modules", "pkg.json"), "x");
      await fs.writeFile(path.join(root, ".git", "HEAD"), "ref");

      const built = await buildWorkspaceSnapshot(root, now);
      expect(built.fence).toContain("a/new.txt");
      expect(built.fence).toContain("a/middle.txt");
      expect(built.fence).not.toContain("a/old.txt");
      expect(built.fence).not.toContain("node_modules");
      expect(built.fence).not.toContain(".git/HEAD");

      // new.txt (mtime now-1s) should appear before middle.txt (mtime now-2d).
      const newIdx = built.fence.indexOf("a/new.txt");
      const midIdx = built.fence.indexOf("a/middle.txt");
      expect(newIdx).toBeLessThan(midIdx);
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("uses cache on second invocation within TTL (no refreshedAt change)", async () => {
    const root = await mkTmpWorkspace();
    try {
      await fs.mkdir(path.join(root, "projects"));
      const hook = makeWorkspaceAwarenessHook({ workspaceRoot: root });

      const r1 = await hook.handler(baseArgs, makeCtx());
      if (r1?.action !== "replace") throw new Error("expected replace on first call");
      const s1 = r1.value.system;

      // Mutate workspace post-cache — should NOT be reflected.
      await fs.writeFile(path.join(root, "NEW.md"), "new");

      const r2 = await hook.handler(baseArgs, makeCtx());
      if (r2?.action !== "replace") throw new Error("expected replace on second call");
      const s2 = r2.value.system;

      expect(s1).toEqual(s2);
      expect(s2).not.toContain("NEW.md");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("respects MAGI_WORKSPACE_AWARENESS=off", async () => {
    process.env.MAGI_WORKSPACE_AWARENESS = "off";
    const root = await mkTmpWorkspace();
    try {
      await fs.mkdir(path.join(root, "projects"));
      const hook = makeWorkspaceAwarenessHook({ workspaceRoot: root });
      const result = await hook.handler(baseArgs, makeCtx());
      expect(result).toEqual({ action: "continue" });
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("skips on iteration > 0", async () => {
    const root = await mkTmpWorkspace();
    try {
      await fs.mkdir(path.join(root, "projects"));
      const hook = makeWorkspaceAwarenessHook({ workspaceRoot: root });
      const result = await hook.handler(
        { ...baseArgs, iteration: 2 },
        makeCtx(),
      );
      expect(result).toEqual({ action: "continue" });
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("declares name, point, priority 7, non-blocking", () => {
    const hook = makeWorkspaceAwarenessHook({ workspaceRoot: "/tmp" });
    expect(hook.name).toBe("builtin:workspace-awareness");
    expect(hook.point).toBe("beforeLLMCall");
    expect(hook.priority).toBe(7);
    expect(hook.blocking).toBe(false);
  });
});
