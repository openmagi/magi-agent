/**
 * ToolLogger.test — write, rotation, query, non-blocking.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

import { ToolLogger } from "./ToolLogger.js";
import type { ToolLogEntry } from "./ToolLogger.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "toollogger-test-"));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

describe("ToolLogger", () => {
  it("writes a log entry to JSONL file", () => {
    const logger = new ToolLogger(tmpDir);
    const entry: ToolLogEntry = {
      timestamp: new Date().toISOString(),
      toolName: "TestTool",
      status: "ok",
      durationMs: 42,
    };

    logger.log(entry);

    const filePath = logger.logFilePath("TestTool");
    expect(fs.existsSync(filePath)).toBe(true);

    const content = fs.readFileSync(filePath, "utf-8");
    const parsed = JSON.parse(content.trim()) as ToolLogEntry;
    expect(parsed.toolName).toBe("TestTool");
    expect(parsed.status).toBe("ok");
    expect(parsed.durationMs).toBe(42);
  });

  it("creates directory if it does not exist", () => {
    const nestedDir = path.join(tmpDir, "nested", "logs");
    const logger = new ToolLogger(nestedDir);

    logger.log({
      timestamp: new Date().toISOString(),
      toolName: "TestTool",
      status: "ok",
      durationMs: 1,
    });

    expect(fs.existsSync(nestedDir)).toBe(true);
  });

  it("appends multiple entries", () => {
    const logger = new ToolLogger(tmpDir);

    for (let i = 0; i < 3; i++) {
      logger.log({
        timestamp: new Date().toISOString(),
        toolName: "MultiTool",
        status: "ok",
        durationMs: i,
      });
    }

    const entries = logger.getLogs("MultiTool");
    expect(entries).toHaveLength(3);
  });

  it("getLogs filters by since", () => {
    const logger = new ToolLogger(tmpDir);
    const oldDate = new Date("2020-01-01T00:00:00Z");
    const newDate = new Date("2025-01-01T00:00:00Z");

    logger.log({
      timestamp: oldDate.toISOString(),
      toolName: "TimeTool",
      status: "ok",
      durationMs: 1,
    });
    logger.log({
      timestamp: newDate.toISOString(),
      toolName: "TimeTool",
      status: "ok",
      durationMs: 2,
    });

    const filtered = logger.getLogs("TimeTool", {
      since: new Date("2024-01-01"),
    });
    expect(filtered).toHaveLength(1);
    expect(filtered[0].durationMs).toBe(2);
  });

  it("getLogs filters by status", () => {
    const logger = new ToolLogger(tmpDir);

    logger.log({
      timestamp: new Date().toISOString(),
      toolName: "StatusTool",
      status: "ok",
      durationMs: 1,
    });
    logger.log({
      timestamp: new Date().toISOString(),
      toolName: "StatusTool",
      status: "error",
      durationMs: 2,
      error: "something broke",
    });

    const errors = logger.getLogs("StatusTool", { status: "error" });
    expect(errors).toHaveLength(1);
    expect(errors[0].error).toBe("something broke");
  });

  it("getLogs respects limit", () => {
    const logger = new ToolLogger(tmpDir);

    for (let i = 0; i < 10; i++) {
      logger.log({
        timestamp: new Date().toISOString(),
        toolName: "LimitTool",
        status: "ok",
        durationMs: i,
      });
    }

    const limited = logger.getLogs("LimitTool", { limit: 3 });
    expect(limited).toHaveLength(3);
    // Should be last 3 entries
    expect(limited[0].durationMs).toBe(7);
    expect(limited[2].durationMs).toBe(9);
  });

  it("returns empty array for unknown tool", () => {
    const logger = new ToolLogger(tmpDir);
    expect(logger.getLogs("NonexistentTool")).toHaveLength(0);
  });

  it("sanitizes tool name for filesystem safety", () => {
    const logger = new ToolLogger(tmpDir);
    const filePath = logger.logFilePath("../evil/path");
    expect(filePath).not.toContain("..");
  });

  it("createEntry builds entry with input preview", () => {
    const entry = ToolLogger.createEntry("Test", "ok", 100, {
      input: { query: "hello world" },
    });

    expect(entry.toolName).toBe("Test");
    expect(entry.status).toBe("ok");
    expect(entry.durationMs).toBe(100);
    expect(entry.inputPreview).toContain("hello world");
  });

  it("createEntry truncates long input preview", () => {
    const longInput = { query: "x".repeat(500) };
    const entry = ToolLogger.createEntry("Test", "ok", 1, {
      input: longInput,
    });

    expect(entry.inputPreview!.length).toBeLessThanOrEqual(200);
  });

  it("never throws on log failure", () => {
    // Use a path that can't be written to on most systems
    const logger = new ToolLogger("/dev/null/impossible/path");

    // Should not throw
    logger.log({
      timestamp: new Date().toISOString(),
      toolName: "Test",
      status: "ok",
      durationMs: 0,
    });
  });
});
