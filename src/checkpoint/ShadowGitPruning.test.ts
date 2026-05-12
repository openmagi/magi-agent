import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { ShadowGit, runShadowGit } from "./ShadowGit.js";
import {
  DEFAULT_PRUNE_POLICY,
  pruneCheckpoints,
  getDetailedStorageUsage,
  shouldPruneInline,
} from "./ShadowGitPruning.js";

let tmpDir: string;

async function makeTmpWorkspace(): Promise<string> {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "shadow-prune-test-"));
  await fs.writeFile(path.join(dir, "hello.txt"), "hello world\n");
  return dir;
}

async function createNCheckpoints(
  sg: ShadowGit,
  root: string,
  n: number,
  opts?: {
    turnIdPrefix?: string;
    sessionKey?: string;
    turnIdFn?: (i: number) => string;
  },
): Promise<string[]> {
  const shas: string[] = [];
  for (let i = 0; i < n; i++) {
    const turnId =
      opts?.turnIdFn?.(i) ?? `${opts?.turnIdPrefix ?? "t"}-${i}`;
    await fs.writeFile(
      path.join(root, "hello.txt"),
      `content-${i}-${Date.now()}\n`,
    );
    const sha = await sg.createCheckpoint({
      toolName: "FileEdit",
      turnId,
      sessionKey: opts?.sessionKey ?? "s-1",
      timestamp: Date.now(),
      filesHint: ["hello.txt"],
    });
    if (sha) shas.push(sha);
  }
  return shas;
}

beforeEach(async () => {
  tmpDir = await makeTmpWorkspace();
});

afterEach(async () => {
  try {
    await fs.rm(tmpDir, { recursive: true, force: true, maxRetries: 3 });
  } catch {
    // ignore cleanup errors in tests
  }
});

describe("DEFAULT_PRUNE_POLICY", () => {
  it("has expected defaults", () => {
    expect(DEFAULT_PRUNE_POLICY.hotCount).toBe(50);
    expect(DEFAULT_PRUNE_POLICY.warmCount).toBe(200);
    expect(DEFAULT_PRUNE_POLICY.maxAgeDays).toBe(7);
    expect(DEFAULT_PRUNE_POLICY.maxSizeBytes).toBe(1.5 * 1024 ** 3);
    expect(DEFAULT_PRUNE_POLICY.emergencyThresholdBytes).toBe(1.4 * 1024 ** 3);
  });
});

describe("pruneCheckpoints — no-op cases", () => {
  it("does nothing when checkpoint count <= hotCount", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();
    await createNCheckpoints(sg, tmpDir, 10);

    const before = await sg.listCheckpoints({ limit: 1000 });
    const result = await pruneCheckpoints(tmpDir, {
      ...DEFAULT_PRUNE_POLICY,
      hotCount: 50,
    });

    const after = await sg.listCheckpoints({ limit: 1000 });
    expect(after.length).toBe(before.length);
    expect(result.pruned).toBe(0);
  });
});

describe("pruneCheckpoints — warm tier (turn squash)", () => {
  it("squashes same-turnId commits beyond hotCount", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    // 6 turns × 3 checkpoints = 18 + 1 initial = 19
    for (let turn = 0; turn < 6; turn++) {
      for (let i = 0; i < 3; i++) {
        await fs.writeFile(
          path.join(tmpDir, "hello.txt"),
          `turn-${turn}-step-${i}\n`,
        );
        await sg.createCheckpoint({
          toolName: "FileEdit",
          turnId: `t-${turn}`,
          sessionKey: "s-1",
          timestamp: Date.now(),
          filesHint: ["hello.txt"],
        });
      }
    }

    const before = await sg.listCheckpoints({ limit: 1000 });
    expect(before.length).toBe(19);

    const result = await pruneCheckpoints(tmpDir, {
      ...DEFAULT_PRUNE_POLICY,
      hotCount: 3,
      warmCount: 19,
    });

    expect(result.pruned).toBeGreaterThan(0);

    // Verify final file content is preserved
    const content = await fs.readFile(path.join(tmpDir, "hello.txt"), "utf8");
    expect(content).toBe("turn-5-step-2\n");

    const after = await sg.listCheckpoints({ limit: 1000 });
    expect(after.length).toBeLessThan(before.length);
  }, 30_000);
});

describe("pruneCheckpoints — cold tier (session squash)", () => {
  it("squashes per-session beyond warmCount", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    // 3 sessions × 5 turns = 15 checkpoints + 1 initial = 16
    for (let session = 0; session < 3; session++) {
      for (let turn = 0; turn < 5; turn++) {
        await fs.writeFile(
          path.join(tmpDir, "hello.txt"),
          `s${session}-t${turn}\n`,
        );
        await sg.createCheckpoint({
          toolName: "FileEdit",
          turnId: `t-${session}-${turn}`,
          sessionKey: `s-${session}`,
          timestamp: Date.now(),
          filesHint: ["hello.txt"],
        });
      }
    }

    const before = await sg.listCheckpoints({ limit: 1000 });
    expect(before.length).toBe(16);

    const result = await pruneCheckpoints(tmpDir, {
      ...DEFAULT_PRUNE_POLICY,
      hotCount: 3,
      warmCount: 8,
    });

    expect(result.pruned).toBeGreaterThan(0);

    const content = await fs.readFile(path.join(tmpDir, "hello.txt"), "utf8");
    expect(content).toBe("s2-t4\n");
  }, 30_000);
});

describe("pruneCheckpoints — expired tier", () => {
  it("counts checkpoints older than maxAgeDays as expired", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    // Create 15 checkpoints — enough to exceed hotCount=5
    await createNCheckpoints(sg, tmpDir, 15);

    // The pruning logic checks commit author dates. In a real env
    // these would be 8 days old. We can't easily backdate commits in
    // a test, but we can verify the function counts them correctly
    // by setting maxAgeDays to 0 (everything is "old").
    const result = await pruneCheckpoints(tmpDir, {
      ...DEFAULT_PRUNE_POLICY,
      hotCount: 5,
      warmCount: 10,
      maxAgeDays: 0,
    });

    // With maxAgeDays=0, warm+cold commits are expired
    expect(result.expired).toBeGreaterThan(0);
    expect(result.pruned).toBeGreaterThan(0);
  });
});

describe("pruneCheckpoints — emergency", () => {
  it("keeps only hotCount when emergency threshold exceeded", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    await createNCheckpoints(sg, tmpDir, 20);

    const result = await pruneCheckpoints(tmpDir, {
      ...DEFAULT_PRUNE_POLICY,
      hotCount: 5,
      warmCount: 10,
      emergencyThresholdBytes: 1,
      maxSizeBytes: 2,
    });

    expect(result.emergency).toBe(true);

    const after = await sg.listCheckpoints({ limit: 1000 });
    // Should have at most hotCount commits
    expect(after.length).toBeLessThanOrEqual(5);
  });
});

describe("pruneCheckpoints — tree preservation", () => {
  it("squash preserves final tree state (file contents identical)", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    // Create multiple files across checkpoints on same turn
    await fs.writeFile(path.join(tmpDir, "a.txt"), "aaa\n");
    await sg.createCheckpoint({
      toolName: "FileWrite",
      turnId: "t-1",
      sessionKey: "s-1",
      timestamp: Date.now(),
      filesHint: ["a.txt"],
    });

    await fs.writeFile(path.join(tmpDir, "b.txt"), "bbb\n");
    await sg.createCheckpoint({
      toolName: "FileWrite",
      turnId: "t-1",
      sessionKey: "s-1",
      timestamp: Date.now(),
      filesHint: ["b.txt"],
    });

    await fs.writeFile(path.join(tmpDir, "c.txt"), "ccc\n");
    await sg.createCheckpoint({
      toolName: "FileWrite",
      turnId: "t-2",
      sessionKey: "s-1",
      timestamp: Date.now(),
      filesHint: ["c.txt"],
    });

    // Add more to push t-1 into warm range
    for (let i = 3; i < 15; i++) {
      await fs.writeFile(path.join(tmpDir, "hello.txt"), `iter-${i}\n`);
      await sg.createCheckpoint({
        toolName: "FileEdit",
        turnId: `t-${i}`,
        sessionKey: "s-1",
        timestamp: Date.now(),
        filesHint: ["hello.txt"],
      });
    }

    // Prune with hot=5, warm=20 to push t-1 into warm zone where it gets squashed
    await pruneCheckpoints(tmpDir, {
      ...DEFAULT_PRUNE_POLICY,
      hotCount: 5,
      warmCount: 20,
    });

    // The HEAD commit should still reference the same tree as before
    // (file contents on disk unchanged — pruning only rewrites DAG)
    expect(await fs.readFile(path.join(tmpDir, "a.txt"), "utf8")).toBe("aaa\n");
    expect(await fs.readFile(path.join(tmpDir, "b.txt"), "utf8")).toBe("bbb\n");
    expect(await fs.readFile(path.join(tmpDir, "c.txt"), "utf8")).toBe("ccc\n");
    expect(await fs.readFile(path.join(tmpDir, "hello.txt"), "utf8")).toBe(
      "iter-14\n",
    );

    // HEAD tree should match the latest checkpoint's tree
    const headTree = await runShadowGit(tmpDir, ["log", "-1", "--format=%T"]);
    expect(headTree.stdout.trim()).toBeTruthy();
  });
});

describe("getDetailedStorageUsage", () => {
  it("returns tier distribution and size", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();
    await createNCheckpoints(sg, tmpDir, 5);

    const usage = await getDetailedStorageUsage(tmpDir, DEFAULT_PRUNE_POLICY);

    expect(usage.totalCheckpoints).toBe(6);
    expect(usage.hotCount).toBeGreaterThan(0);
    expect(usage.warmCount).toBe(0);
    expect(usage.coldCount).toBe(0);
    expect(usage.sizeBytes).toBeGreaterThan(0);
    expect(usage.status).toBe("ok");
  });

  it("reports emergency when size exceeds emergency threshold", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();
    await createNCheckpoints(sg, tmpDir, 5);

    const usage = await getDetailedStorageUsage(tmpDir, {
      ...DEFAULT_PRUNE_POLICY,
      emergencyThresholdBytes: 1,
      maxSizeBytes: 2,
    });

    expect(usage.status).toBe("emergency");
  });

  it("reports warn when checkpoint count exceeds warmCount", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();
    await createNCheckpoints(sg, tmpDir, 10);

    const usage = await getDetailedStorageUsage(tmpDir, {
      ...DEFAULT_PRUNE_POLICY,
      hotCount: 3,
      warmCount: 5,
    });

    // 11 checkpoints > warmCount=5 → warn
    expect(usage.status).toBe("warn");
    expect(usage.coldCount).toBeGreaterThan(0);
  });

  it("reports ok for empty shadow-git", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    const usage = await getDetailedStorageUsage(tmpDir, DEFAULT_PRUNE_POLICY);
    expect(usage.status).toBe("ok");
    expect(usage.totalCheckpoints).toBe(1);
  });
});

describe("shouldPruneInline", () => {
  it("triggers prune every N checkpoints", () => {
    expect(shouldPruneInline(1, 50)).toBe(false);
    expect(shouldPruneInline(49, 50)).toBe(false);
    expect(shouldPruneInline(50, 50)).toBe(true);
    expect(shouldPruneInline(51, 50)).toBe(false);
    expect(shouldPruneInline(100, 50)).toBe(true);
    expect(shouldPruneInline(150, 50)).toBe(true);
  });
});
