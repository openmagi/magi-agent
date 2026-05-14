import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

import { hookCreate, hookToggle, hookList } from "./hook.js";

function makeTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "magi-hook-cli-test-"));
}

describe("CLI hook commands", () => {
  let tmpDir: string;
  let origCwd: string;

  beforeEach(() => {
    tmpDir = makeTmpDir();
    origCwd = process.cwd();
    process.chdir(tmpDir);
  });

  afterEach(() => {
    process.chdir(origCwd);
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  describe("hookCreate", () => {
    it("creates hook file and fixture directory", async () => {
      await hookCreate(["my-hook", "--point", "beforeCommit"]);

      const hookFile = path.join(tmpDir, "hooks", "my-hook.ts");
      const fixtureFile = path.join(
        tmpDir,
        "hooks",
        "__fixtures__",
        "my-hook",
        "basic.yaml",
      );

      expect(fs.existsSync(hookFile)).toBe(true);
      expect(fs.existsSync(fixtureFile)).toBe(true);

      const content = fs.readFileSync(hookFile, "utf-8");
      expect(content).toContain('name: "my-hook"');
      expect(content).toContain('point: "beforeCommit"');
    });

    it("sets exitCode when no name provided", async () => {
      process.exitCode = 0;
      await hookCreate([]);
      expect(process.exitCode).toBe(1);
      process.exitCode = 0;
    });

    it("sets exitCode on invalid hook point", async () => {
      process.exitCode = 0;
      await hookCreate(["test", "--point", "invalidPoint"]);
      expect(process.exitCode).toBe(1);
      process.exitCode = 0;
    });

    it("sets exitCode if hook file already exists", async () => {
      // Create it first
      await hookCreate(["existing", "--point", "beforeCommit"]);

      // Try to create again
      process.exitCode = 0;
      await hookCreate(["existing", "--point", "beforeCommit"]);
      expect(process.exitCode).toBe(1);
      process.exitCode = 0;
    });
  });

  describe("hookToggle (enable/disable)", () => {
    it("creates magi.config.yaml with override when file absent", async () => {
      await hookToggle("my-hook", false);

      const configPath = path.join(tmpDir, "magi.config.yaml");
      expect(fs.existsSync(configPath)).toBe(true);

      const content = fs.readFileSync(configPath, "utf-8");
      expect(content).toContain("my-hook");
      expect(content).toContain("enabled: false");
    });

    it("updates existing config file", async () => {
      const configPath = path.join(tmpDir, "magi.config.yaml");
      fs.writeFileSync(
        configPath,
        "hooks:\n  overrides:\n    my-hook:\n      enabled: true\n",
      );

      await hookToggle("my-hook", false);

      const content = fs.readFileSync(configPath, "utf-8");
      expect(content).toContain("enabled: false");
    });
  });

  describe("hookList", () => {
    it("runs without crashing when no hooks exist", async () => {
      // Should not throw
      const spy = vi.spyOn(console, "log");
      await hookList();
      spy.mockRestore();
    });
  });
});
