import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

import { HookLogger } from "./HookLogger.js";
import type { HookLogEntry } from "./HookLogger.js";

function makeTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "magi-hooklogger-test-"));
}

describe("HookLogger", () => {
  let tmpDir: string;
  let logger: HookLogger;

  beforeEach(() => {
    tmpDir = makeTmpDir();
    logger = new HookLogger(tmpDir);
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("writes a log entry to a JSONL file", () => {
    const entry: HookLogEntry = {
      timestamp: new Date().toISOString(),
      hookName: "test-hook",
      point: "beforeCommit",
      action: "continue",
      durationMs: 42,
    };

    logger.log(entry);

    const filePath = logger.logFilePath("test-hook");
    expect(fs.existsSync(filePath)).toBe(true);

    const content = fs.readFileSync(filePath, "utf-8");
    const parsed = JSON.parse(content.trim());
    expect(parsed.hookName).toBe("test-hook");
    expect(parsed.durationMs).toBe(42);
  });

  it("appends multiple entries", () => {
    for (let i = 0; i < 3; i++) {
      logger.log({
        timestamp: new Date().toISOString(),
        hookName: "multi-hook",
        point: "beforeCommit",
        action: "continue",
        durationMs: i,
      });
    }

    const filePath = logger.logFilePath("multi-hook");
    const lines = fs
      .readFileSync(filePath, "utf-8")
      .trim()
      .split("\n");
    expect(lines.length).toBe(3);
  });

  it("getLogs returns entries", () => {
    for (let i = 0; i < 5; i++) {
      logger.log({
        timestamp: new Date(Date.now() + i * 1000).toISOString(),
        hookName: "query-hook",
        point: "beforeCommit",
        action: i < 3 ? "continue" : "block",
        durationMs: i,
      });
    }

    const all = logger.getLogs("query-hook");
    expect(all.length).toBe(5);
  });

  it("getLogs filters by since", () => {
    const now = Date.now();
    for (let i = 0; i < 5; i++) {
      logger.log({
        timestamp: new Date(now + i * 60_000).toISOString(),
        hookName: "since-hook",
        point: "beforeCommit",
        action: "continue",
        durationMs: i,
      });
    }

    const since = new Date(now + 2 * 60_000);
    const filtered = logger.getLogs("since-hook", { since });
    expect(filtered.length).toBe(3);
  });

  it("getLogs applies limit (last N)", () => {
    for (let i = 0; i < 10; i++) {
      logger.log({
        timestamp: new Date().toISOString(),
        hookName: "limit-hook",
        point: "beforeCommit",
        action: "continue",
        durationMs: i,
      });
    }

    const limited = logger.getLogs("limit-hook", { limit: 3 });
    expect(limited.length).toBe(3);
    // Should be the last 3
    expect(limited[0]?.durationMs).toBe(7);
  });

  it("returns empty for nonexistent hook", () => {
    const entries = logger.getLogs("nonexistent");
    expect(entries).toEqual([]);
  });

  it("rotates file at 10MB", () => {
    const filePath = logger.logFilePath("rotate-hook");
    const dir = path.dirname(filePath);
    fs.mkdirSync(dir, { recursive: true });

    // Create a file larger than 10MB
    const bigContent = "x".repeat(11 * 1024 * 1024);
    fs.writeFileSync(filePath, bigContent);

    // Writing a new entry should trigger rotation
    logger.log({
      timestamp: new Date().toISOString(),
      hookName: "rotate-hook",
      point: "beforeCommit",
      action: "continue",
      durationMs: 1,
    });

    // The .1 file should exist with the old content
    expect(fs.existsSync(filePath + ".1")).toBe(true);

    // The main file should have only the new entry
    const newContent = fs.readFileSync(filePath, "utf-8").trim();
    const parsed = JSON.parse(newContent);
    expect(parsed.hookName).toBe("rotate-hook");
  });

  it("logs error field when present", () => {
    logger.log({
      timestamp: new Date().toISOString(),
      hookName: "error-hook",
      point: "beforeCommit",
      action: "error",
      durationMs: 100,
      error: "Something went wrong",
    });

    const entries = logger.getLogs("error-hook");
    expect(entries.length).toBe(1);
    expect(entries[0]?.error).toBe("Something went wrong");
  });

  it("sanitizes hook name for filesystem safety", () => {
    const filePath = logger.logFilePath("my/bad:hook<name>");
    const basename = path.basename(filePath);
    // The basename should not contain path separators or angle brackets
    expect(basename).not.toMatch(/[/<>]/);
    // Colons are allowed (used in hook naming convention like "builtin:foo")
    expect(basename).toBe("my_bad:hook_name_.jsonl");
  });
});
