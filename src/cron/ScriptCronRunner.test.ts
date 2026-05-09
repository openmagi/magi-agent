import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { runScriptCron } from "./ScriptCronRunner.js";

describe("runScriptCron", () => {
  it("runs workspace-relative scripts and captures stdout", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "script-cron-"));
    try {
      await fs.writeFile(path.join(root, "check.sh"), "echo hello\n", { mode: 0o755 });
      const result = await runScriptCron({
        workspaceRoot: root,
        scriptPath: "check.sh",
        timeoutMs: 5_000,
      });
      expect(result.code).toBe(0);
      expect(result.stdout.trim()).toBe("hello");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("rejects path traversal", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "script-cron-"));
    try {
      await expect(
        runScriptCron({ workspaceRoot: root, scriptPath: "../x.sh", timeoutMs: 5_000 }),
      ).rejects.toThrow(/outside workspace/);
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });
});
