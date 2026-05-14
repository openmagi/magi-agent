import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { TurnSnapshotService } from "./TurnSnapshotService.js";
import { ShadowGit } from "./ShadowGit.js";

let tmpDir: string;
let shadowGit: ShadowGit;
let service: TurnSnapshotService;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "turn-snap-test-"));
  await fs.writeFile(path.join(tmpDir, "hello.txt"), "initial content\n");
  shadowGit = new ShadowGit({ workspaceRoot: tmpDir });
  await shadowGit.ensureInitialized();
  service = new TurnSnapshotService(shadowGit);
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe("TurnSnapshotService", () => {
  describe("snapshotTurnStart", () => {
    it("returns null when workspace is clean", async () => {
      const sha = await service.snapshotTurnStart("turn-1", "sess-1");
      expect(sha).toBeNull();
    });

    it("captures dirty state at turn start", async () => {
      await fs.writeFile(path.join(tmpDir, "hello.txt"), "modified\n");
      const sha = await service.snapshotTurnStart("turn-1", "sess-1");
      expect(sha).toBeTruthy();
      expect(sha!.length).toBeGreaterThan(6);
    });
  });

  describe("snapshotTurnEnd", () => {
    it("returns a TurnSnapshot with patch when files changed", async () => {
      // Dirty workspace so start creates a real checkpoint
      await fs.writeFile(path.join(tmpDir, "setup.txt"), "setup\n");
      const startSha = await service.snapshotTurnStart("turn-1", "sess-1");
      expect(startSha).toBeTruthy();

      await fs.writeFile(path.join(tmpDir, "new-file.ts"), "export const x = 1;\n");

      const snap = await service.snapshotTurnEnd("turn-1", "sess-1", startSha);
      expect(snap).not.toBeNull();
      expect(snap!.turnId).toBe("turn-1");
      expect(snap!.sessionKey).toBe("sess-1");
      expect(snap!.endSha).toBeTruthy();
      expect(snap!.patch).toBeTruthy();
      expect(snap!.patch!).toContain("new-file.ts");
      expect(snap!.filesChanged.length).toBeGreaterThan(0);
    });

    it("returns null when no files changed during turn", async () => {
      const startSha = await service.snapshotTurnStart("turn-1", "sess-1");
      const snap = await service.snapshotTurnEnd("turn-1", "sess-1", startSha);
      expect(snap).toBeNull();
    });
  });

  describe("rollbackTurn", () => {
    it("restores workspace to turn start state", async () => {
      // Make a small change first so turn start has a real commit
      await fs.writeFile(path.join(tmpDir, "setup.txt"), "setup\n");
      const startSha = await service.snapshotTurnStart("turn-1", "sess-1");
      expect(startSha).toBeTruthy();

      await fs.writeFile(path.join(tmpDir, "hello.txt"), "changed by LLM\n");
      await fs.writeFile(path.join(tmpDir, "new.ts"), "new file\n");

      await service.snapshotTurnEnd("turn-1", "sess-1", startSha);

      const result = await service.rollbackTurn("turn-1");
      expect(result).not.toBeNull();

      const content = await fs.readFile(path.join(tmpDir, "hello.txt"), "utf8");
      expect(content).toBe("initial content\n");
    });
  });

  describe("listTurnSnapshots", () => {
    it("returns snapshots filtered by session", async () => {
      await fs.writeFile(path.join(tmpDir, "a.txt"), "a\n");
      await service.snapshotTurnStart("t1", "sess-1");
      await fs.writeFile(path.join(tmpDir, "b.txt"), "b\n");
      await service.snapshotTurnEnd("t1", "sess-1", null);

      await service.snapshotTurnStart("t2", "sess-1");
      await fs.writeFile(path.join(tmpDir, "c.txt"), "c\n");
      await service.snapshotTurnEnd("t2", "sess-1", null);

      const snaps = await service.listTurnSnapshots({ sessionKey: "sess-1" });
      expect(snaps.length).toBe(2);
    });
  });

  describe("patchTruncation", () => {
    it("marks patch as truncated when exceeding limit", async () => {
      // Create a dirty state for start to commit
      await fs.writeFile(path.join(tmpDir, "pre.txt"), "pre\n");
      const startSha = await service.snapshotTurnStart("turn-1", "sess-1");

      // Write many unique lines to generate a large diff (>512KB)
      const lines: string[] = [];
      for (let i = 0; i < 20_000; i++) {
        lines.push(`line_${i}_${"data".repeat(10)}`);
      }
      await fs.writeFile(path.join(tmpDir, "large.txt"), lines.join("\n"));

      const snap = await service.snapshotTurnEnd("turn-1", "sess-1", startSha);
      expect(snap).not.toBeNull();
      expect(snap!.patchTruncated).toBe(true);
      expect(snap!.patch).toBeNull();
    });
  });

  describe("pruneOlderThan", () => {
    it("is callable without errors", async () => {
      await expect(service.pruneOlderThan(7)).resolves.toBe(0);
    });
  });
});
