/**
 * Tests for the read-before-edit guard in FileEdit.
 * Category 1 (Guardrails Not Classifiers): deterministic constraint
 * replacing LLM-based verification.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { makeFileEditTool } from "./FileEdit.js";

let tmpDir: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "fileedit-guard-"));
  await fs.writeFile(path.join(tmpDir, "hello.txt"), "hello world\n");
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe("FileEdit read-before-edit guard", () => {
  it("blocks edit when file has NOT been read and filesRead is provided", async () => {
    const tool = makeFileEditTool(tmpDir);
    const filesRead = new Set<string>();

    const result = await tool.execute(
      { path: "hello.txt", old_string: "hello", new_string: "goodbye" },
      {
        botId: "test",
        sessionKey: "s1",
        turnId: "t1",
        workspaceRoot: tmpDir,
        filesRead,
        emitProgress: () => {},
        emitAgentEvent: () => {},
        emitControlEvent: async () => {},
        askUser: async () => ({ selectedId: "" }),
        staging: {
          stageFileWrite: () => {},
          stageTranscriptAppend: () => {},
          stageAuditEvent: () => {},
        },
      },
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("read_required");
    expect(result.errorMessage).toContain("read");
    expect(result.errorMessage).toContain("hello.txt");
  });

  it("allows edit when file HAS been read", async () => {
    const tool = makeFileEditTool(tmpDir);
    const filesRead = new Set<string>(["hello.txt"]);

    const result = await tool.execute(
      { path: "hello.txt", old_string: "hello", new_string: "goodbye" },
      {
        botId: "test",
        sessionKey: "s1",
        turnId: "t1",
        workspaceRoot: tmpDir,
        filesRead,
        emitProgress: () => {},
        emitAgentEvent: () => {},
        emitControlEvent: async () => {},
        askUser: async () => ({ selectedId: "" }),
        staging: {
          stageFileWrite: () => {},
          stageTranscriptAppend: () => {},
          stageAuditEvent: () => {},
        },
      },
    );

    expect(result.status).toBe("ok");
  });

  it("allows edit when filesRead is undefined (guard disabled)", async () => {
    const tool = makeFileEditTool(tmpDir);

    const result = await tool.execute(
      { path: "hello.txt", old_string: "hello", new_string: "goodbye" },
      {
        botId: "test",
        sessionKey: "s1",
        turnId: "t1",
        workspaceRoot: tmpDir,
        emitProgress: () => {},
        emitAgentEvent: () => {},
        emitControlEvent: async () => {},
        askUser: async () => ({ selectedId: "" }),
        staging: {
          stageFileWrite: () => {},
          stageTranscriptAppend: () => {},
          stageAuditEvent: () => {},
        },
      },
    );

    expect(result.status).toBe("ok");
  });

  it("allows edit when parent directory was read (Grep on dir)", async () => {
    const tool = makeFileEditTool(tmpDir);
    const filesRead = new Set<string>(["src/"]);

    await fs.mkdir(path.join(tmpDir, "src"), { recursive: true });
    await fs.writeFile(path.join(tmpDir, "src", "app.ts"), 'const x = "old";');

    const result = await tool.execute(
      { path: "src/app.ts", old_string: '"old"', new_string: '"new"' },
      {
        botId: "test",
        sessionKey: "s1",
        turnId: "t1",
        workspaceRoot: tmpDir,
        filesRead,
        emitProgress: () => {},
        emitAgentEvent: () => {},
        emitControlEvent: async () => {},
        askUser: async () => ({ selectedId: "" }),
        staging: {
          stageFileWrite: () => {},
          stageTranscriptAppend: () => {},
          stageAuditEvent: () => {},
        },
      },
    );

    expect(result.status).toBe("ok");
  });

  it("normalizes ./prefix when checking read status", async () => {
    const tool = makeFileEditTool(tmpDir);
    const filesRead = new Set<string>(["hello.txt"]);

    const result = await tool.execute(
      { path: "./hello.txt", old_string: "hello", new_string: "goodbye" },
      {
        botId: "test",
        sessionKey: "s1",
        turnId: "t1",
        workspaceRoot: tmpDir,
        filesRead,
        emitProgress: () => {},
        emitAgentEvent: () => {},
        emitControlEvent: async () => {},
        askUser: async () => ({ selectedId: "" }),
        staging: {
          stageFileWrite: () => {},
          stageTranscriptAppend: () => {},
          stageAuditEvent: () => {},
        },
      },
    );

    expect(result.status).toBe("ok");
  });
});
