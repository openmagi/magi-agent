import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { makeBatchReadTool, type BatchReadOutput } from "./BatchRead.js";
import type { ToolContext } from "../Tool.js";

function stubCtx(workspaceRoot: string): ToolContext {
  return {
    botId: "test",
    sessionKey: "s1",
    turnId: "t1",
    workspaceRoot,
    abortSignal: new AbortController().signal,
    askUser: async () => ({}),
    emitProgress: () => {},
    staging: {
      stageFileWrite() {},
      stageTranscriptAppend() {},
      stageAuditEvent() {},
    },
  };
}

describe("BatchRead", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "batchread-"));
    fs.writeFileSync(path.join(tmpDir, "a.txt"), "hello\nworld\n");
    fs.writeFileSync(path.join(tmpDir, "b.txt"), "foo\nbar\nbaz\n");
    fs.mkdirSync(path.join(tmpDir, "sub"));
    fs.writeFileSync(path.join(tmpDir, "sub", "c.txt"), "nested\n");
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("reads multiple files in a single call", async () => {
    const tool = makeBatchReadTool(tmpDir);
    const result = await tool.execute({ paths: ["a.txt", "b.txt"] }, stubCtx(tmpDir));
    expect(result.status).toBe("ok");
    const output = result.output as BatchReadOutput;
    expect(output.results).toHaveLength(2);
    expect(output.results[0]!.status).toBe("ok");
    expect(output.results[0]!.content).toContain("hello");
    expect(output.results[1]!.status).toBe("ok");
    expect(output.results[1]!.content).toContain("foo");
  });

  it("returns sha256 hash for each file", async () => {
    const tool = makeBatchReadTool(tmpDir);
    const result = await tool.execute({ paths: ["a.txt"] }, stubCtx(tmpDir));
    const output = result.output as BatchReadOutput;
    expect(output.results[0]!.contentSha256).toMatch(/^[a-f0-9]{64}$/);
  });

  it("handles missing files gracefully", async () => {
    const tool = makeBatchReadTool(tmpDir);
    const result = await tool.execute({ paths: ["a.txt", "missing.txt"] }, stubCtx(tmpDir));
    const output = result.output as BatchReadOutput;
    expect(output.results[0]!.status).toBe("ok");
    expect(output.results[1]!.status).toBe("error");
    expect(output.results[1]!.errorCode).toBe("not_found");
  });

  it("reads nested files", async () => {
    const tool = makeBatchReadTool(tmpDir);
    const result = await tool.execute({ paths: ["sub/c.txt"] }, stubCtx(tmpDir));
    const output = result.output as BatchReadOutput;
    expect(output.results[0]!.status).toBe("ok");
    expect(output.results[0]!.content).toContain("nested");
  });

  it("rejects directories", async () => {
    const tool = makeBatchReadTool(tmpDir);
    const result = await tool.execute({ paths: ["sub"] }, stubCtx(tmpDir));
    const output = result.output as BatchReadOutput;
    expect(output.results[0]!.status).toBe("error");
    expect(output.results[0]!.errorCode).toBe("not_a_file");
  });

  it("validates max paths", () => {
    const tool = makeBatchReadTool(tmpDir);
    const err = tool.validate!({ paths: Array(21).fill("a.txt") });
    expect(err).toContain("too many");
  });

  it("validates empty paths", () => {
    const tool = makeBatchReadTool(tmpDir);
    const err = tool.validate!({ paths: [] });
    expect(err).toBeTruthy();
  });

  it("applies offset and limit", async () => {
    const tool = makeBatchReadTool(tmpDir);
    const result = await tool.execute(
      { paths: ["b.txt"], offset: 2, limit: 1 },
      stubCtx(tmpDir),
    );
    const output = result.output as BatchReadOutput;
    expect(output.results[0]!.content).toBe("bar");
  });

  it("rejects path escape attempts", async () => {
    const tool = makeBatchReadTool(tmpDir);
    const result = await tool.execute(
      { paths: ["../../../etc/passwd"] },
      stubCtx(tmpDir),
    );
    const output = result.output as BatchReadOutput;
    expect(output.results[0]!.status).toBe("error");
    expect(output.results[0]!.errorCode).toBe("path_escape");
  });
});
