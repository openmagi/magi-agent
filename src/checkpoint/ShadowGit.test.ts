import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { ShadowGit, runShadowGit } from "./ShadowGit.js";

let tmpDir: string;

async function makeTmpWorkspace(): Promise<string> {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "shadow-git-test-"));
  await fs.writeFile(path.join(dir, "hello.txt"), "hello world\n");
  return dir;
}

beforeEach(async () => {
  tmpDir = await makeTmpWorkspace();
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe("ShadowGit.ensureInitialized", () => {
  it("creates .shadow-git directory with initial commit", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    const head = await fs.access(path.join(tmpDir, ".shadow-git", "HEAD"));
    expect(head).toBeUndefined(); // access resolves undefined on success

    // Verify initial commit exists
    const log = await runShadowGit(tmpDir, ["log", "--oneline"]);
    expect(log.stdout).toContain("initial workspace state");
  });

  it("is idempotent on repeated init", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();
    await sg.ensureInitialized(); // should not throw

    const log = await runShadowGit(tmpDir, ["log", "--oneline"]);
    const lines = log.stdout.split("\n").filter((l) => l.trim());
    expect(lines.length).toBe(1); // only initial commit
  });

  it("sets gc.auto=0", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    const cfg = await runShadowGit(tmpDir, ["config", "gc.auto"]);
    expect(cfg.stdout.trim()).toBe("0");
  });

  it("writes default exclude patterns", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    const exclude = await fs.readFile(
      path.join(tmpDir, ".shadow-git", "info", "exclude"),
      "utf8",
    );
    expect(exclude).toContain("node_modules/");
    expect(exclude).toContain(".git/");
    expect(exclude).toContain(".shadow-git/");
  });

  it("works without real .git", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    // no .git exists in tmpDir
    await expect(sg.ensureInitialized()).resolves.toBeUndefined();
  });

  it("adds .shadow-git to real .git/info/exclude if .git exists", async () => {
    // Create a real git repo first
    const { execSync } = await import("node:child_process");
    execSync("git init", { cwd: tmpDir, stdio: "ignore" });

    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    const exclude = await fs.readFile(
      path.join(tmpDir, ".git", "info", "exclude"),
      "utf8",
    );
    expect(exclude).toContain(".shadow-git");
  });
});

describe("ShadowGit.createCheckpoint", () => {
  it("commits staged changes and returns sha", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    // Modify a file
    await fs.writeFile(path.join(tmpDir, "hello.txt"), "updated\n");

    const sha = await sg.createCheckpoint({
      toolName: "FileEdit",
      turnId: "t-123",
      sessionKey: "s-456",
      timestamp: Date.now(),
      filesHint: ["hello.txt"],
    });

    expect(sha).toBeTruthy();
    expect(typeof sha).toBe("string");
    expect(sha!.length).toBe(40);
  });

  it("returns null when workspace is clean", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    const sha = await sg.createCheckpoint({
      toolName: "FileEdit",
      turnId: "t-123",
      sessionKey: "s-456",
      timestamp: Date.now(),
    });

    expect(sha).toBeNull();
  });

  it("encodes turn and session in commit message", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    await fs.writeFile(path.join(tmpDir, "new.txt"), "content\n");

    await sg.createCheckpoint({
      toolName: "FileWrite",
      turnId: "t-abc",
      sessionKey: "s-xyz",
      timestamp: Date.now(),
      filesHint: ["new.txt"],
    });

    const log = await runShadowGit(tmpDir, ["log", "-1", "--format=%B"]);
    expect(log.stdout).toContain("checkpoint: FileWrite");
    expect(log.stdout).toContain("turn: t-abc");
    expect(log.stdout).toContain("session: s-xyz");
    expect(log.stdout).toContain("new.txt");
  });

  it("excludes files over largeFileThreshold", async () => {
    const sg = new ShadowGit({
      workspaceRoot: tmpDir,
      largeFileThreshold: 100, // 100 bytes for testing
    });
    await sg.ensureInitialized();

    // Create a "large" file > 100 bytes
    await fs.writeFile(path.join(tmpDir, "big.bin"), "x".repeat(200));

    await sg.createCheckpoint({
      toolName: "FileWrite",
      turnId: "t-1",
      sessionKey: "s-1",
      timestamp: Date.now(),
      filesHint: ["big.bin"],
    });

    // .shadowgitignore should contain the large file
    const ignore = await fs.readFile(
      path.join(tmpDir, ".shadowgitignore"),
      "utf8",
    );
    expect(ignore).toContain("big.bin");
  });
});

describe("ShadowGit.listCheckpoints", () => {
  it("returns checkpoints most-recent-first", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    await fs.writeFile(path.join(tmpDir, "a.txt"), "a\n");
    await sg.createCheckpoint({
      toolName: "FileWrite",
      turnId: "t-1",
      sessionKey: "s-1",
      timestamp: 1000,
      filesHint: ["a.txt"],
    });

    await fs.writeFile(path.join(tmpDir, "b.txt"), "b\n");
    await sg.createCheckpoint({
      toolName: "FileWrite",
      turnId: "t-2",
      sessionKey: "s-1",
      timestamp: 2000,
      filesHint: ["b.txt"],
    });

    const list = await sg.listCheckpoints();

    // Most recent first (b.txt checkpoint), then a.txt, then initial
    expect(list.length).toBe(3);
    expect(list[0]!.toolName).toBe("FileWrite");
    expect(list[0]!.turnId).toBe("t-2");
    expect(list[1]!.turnId).toBe("t-1");
    expect(list[2]!.message).toContain("initial workspace state");
  });

  it("respects limit parameter", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    await fs.writeFile(path.join(tmpDir, "a.txt"), "a\n");
    await sg.createCheckpoint({
      toolName: "FileWrite",
      turnId: "t-1",
      sessionKey: "s-1",
      timestamp: 1000,
    });

    await fs.writeFile(path.join(tmpDir, "b.txt"), "b\n");
    await sg.createCheckpoint({
      toolName: "FileWrite",
      turnId: "t-2",
      sessionKey: "s-1",
      timestamp: 2000,
    });

    const list = await sg.listCheckpoints({ limit: 1 });
    expect(list.length).toBe(1);
    expect(list[0]!.turnId).toBe("t-2");
  });
});

describe("ShadowGit.diffCheckpoints", () => {
  it("shows unified diff between two shas", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    const list0 = await sg.listCheckpoints();
    const initialSha = list0[0]!.fullSha;

    await fs.writeFile(path.join(tmpDir, "hello.txt"), "changed content\n");
    const sha1 = await sg.createCheckpoint({
      toolName: "FileEdit",
      turnId: "t-1",
      sessionKey: "s-1",
      timestamp: Date.now(),
    });

    const diff = await sg.diffCheckpoints(initialSha, sha1!);
    expect(diff).toContain("hello.txt");
    expect(diff).toContain("-hello world");
    expect(diff).toContain("+changed content");
  });
});

describe("ShadowGit.restoreCheckpoint", () => {
  it("restores workspace to checkpoint state", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    const list0 = await sg.listCheckpoints();
    const initialSha = list0[0]!.fullSha;

    // Modify files
    await fs.writeFile(path.join(tmpDir, "hello.txt"), "modified\n");
    await fs.writeFile(path.join(tmpDir, "extra.txt"), "extra\n");
    await sg.createCheckpoint({
      toolName: "FileEdit",
      turnId: "t-1",
      sessionKey: "s-1",
      timestamp: Date.now(),
    });

    // Restore to initial state
    const result = await sg.restoreCheckpoint(initialSha);
    expect(result.newSha).toBeTruthy();

    // Verify file content restored
    const content = await fs.readFile(path.join(tmpDir, "hello.txt"), "utf8");
    expect(content).toBe("hello world\n");
  });

  it("creates a safety checkpoint before restore", async () => {
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    const list0 = await sg.listCheckpoints();
    const initialSha = list0[0]!.fullSha;

    await fs.writeFile(path.join(tmpDir, "hello.txt"), "modified\n");
    await sg.createCheckpoint({
      toolName: "FileEdit",
      turnId: "t-1",
      sessionKey: "s-1",
      timestamp: Date.now(),
    });

    await sg.restoreCheckpoint(initialSha);

    // Should have: initial, edit checkpoint, safety checkpoint, restore checkpoint
    const list = await sg.listCheckpoints({ limit: 10 });
    const messages = list.map((e) => e.message);
    expect(messages.some((m) => m.includes("restore-safety"))).toBe(true);
  });
});

describe("GIT_DIR separation", () => {
  it("operates independently from real .git", async () => {
    const { execSync } = await import("node:child_process");

    // Init real git
    execSync("git init", { cwd: tmpDir, stdio: "ignore" });
    execSync('git add -A && git commit -m "real initial"', {
      cwd: tmpDir,
      stdio: "ignore",
      env: {
        ...process.env,
        GIT_AUTHOR_NAME: "test",
        GIT_AUTHOR_EMAIL: "test@test.com",
        GIT_COMMITTER_NAME: "test",
        GIT_COMMITTER_EMAIL: "test@test.com",
      },
    });

    // Init shadow git
    const sg = new ShadowGit({ workspaceRoot: tmpDir });
    await sg.ensureInitialized();

    // Create checkpoint in shadow
    await fs.writeFile(path.join(tmpDir, "new.txt"), "new\n");
    await sg.createCheckpoint({
      toolName: "FileWrite",
      turnId: "t-1",
      sessionKey: "s-1",
      timestamp: Date.now(),
    });

    // Real git should NOT have the shadow checkpoint commit
    const realLog = execSync("git log --oneline", {
      cwd: tmpDir,
      encoding: "utf8",
    });
    expect(realLog).not.toContain("checkpoint:");
    expect(realLog).toContain("real initial");

    // Shadow git should NOT have the real commit message
    const shadowLog = await runShadowGit(tmpDir, ["log", "--oneline"]);
    expect(shadowLog.stdout).not.toContain("real initial");
    expect(shadowLog.stdout).toContain("checkpoint:");
  });
});
