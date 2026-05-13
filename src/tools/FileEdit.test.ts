import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeFileEditTool } from "./FileEdit.js";

function makeCtx(workspaceRoot: string): ToolContext {
  return {
    botId: "bot-test",
    sessionKey: "agent:main:test:1",
    turnId: "turn-1",
    workspaceRoot,
    abortSignal: new AbortController().signal,
    askUser: async () => {
      throw new Error("askUser unavailable");
    },
    emitProgress: () => {},
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

describe("FileEdit", () => {
  let root: string;

  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "file-edit-"));
  });

  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("returns structured patch evidence with hashes and hunks", async () => {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(
      path.join(root, "src/example.ts"),
      "export function add(a: number, b: number) {\n  return a + b;\n}\n",
    );
    const tool = makeFileEditTool(root);

    const result = await tool.execute(
      {
        path: "src/example.ts",
        old_string: "  return a + b;",
        new_string: "  return Number(a) + Number(b);",
      },
      makeCtx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      path: "src/example.ts",
      replaced: 1,
      patch: {
        path: "src/example.ts",
        replaced: 1,
        hunks: [
          {
            oldText: "  return a + b;",
            newText: "  return Number(a) + Number(b);",
            oldStart: 2,
            newStart: 2,
          },
        ],
        changedSymbols: ["add"],
      },
    });
    expect(result.output?.patch.oldSha256).toMatch(/^[a-f0-9]{64}$/);
    expect(result.output?.patch.newSha256).toMatch(/^[a-f0-9]{64}$/);
    expect(result.output?.patch.oldSha256).not.toBe(result.output?.patch.newSha256);
    expect(result.metadata).toMatchObject({
      evidenceKind: "patch",
      changedFiles: ["src/example.ts"],
      patch: result.output?.patch,
    });
  });

  it("matches LF input against CRLF files and preserves CRLF in replacements", async () => {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(
      path.join(root, "src/crlf.ts"),
      "export function value() {\r\n  return 1;\r\n}\r\n",
    );
    const tool = makeFileEditTool(root);

    const result = await tool.execute(
      {
        path: "src/crlf.ts",
        old_string: "export function value() {\n  return 1;\n}",
        new_string: "export function value() {\n  const next = 2;\n  return next;\n}",
      },
      makeCtx(root),
    );

    expect(result.status).toBe("ok");
    expect(await fs.readFile(path.join(root, "src/crlf.ts"), "utf8")).toBe(
      "export function value() {\r\n  const next = 2;\r\n  return next;\r\n}\r\n",
    );
  });

  it("rejects edits when expected_file_sha256 does not match current content", async () => {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(path.join(root, "src/stale.ts"), "export const value = 1;\n");
    const tool = makeFileEditTool(root);

    const result = await tool.execute(
      {
        path: "src/stale.ts",
        old_string: "export const value = 1;",
        new_string: "export const value = 2;",
        expected_file_sha256: "0".repeat(64),
      },
      makeCtx(root),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("stale_file");
    expect(await fs.readFile(path.join(root, "src/stale.ts"), "utf8")).toBe(
      "export const value = 1;\n",
    );
  });
});
