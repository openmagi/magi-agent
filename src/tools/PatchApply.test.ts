import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makePatchApplyTool } from "./PatchApply.js";

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

describe("PatchApply", () => {
  let root: string;

  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "patch-apply-"));
  });

  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("applies a unified diff that updates one file and creates another", async () => {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(path.join(root, "src/app.ts"), "export const answer = 41;\n");
    const tool = makePatchApplyTool(root);

    const result = await tool.execute(
      {
        patch: [
          "--- a/src/app.ts",
          "+++ b/src/app.ts",
          "@@ -1 +1 @@",
          "-export const answer = 41;",
          "+export const answer = 42;",
          "--- /dev/null",
          "+++ b/src/created.ts",
          "@@ -0,0 +1,2 @@",
          "+export const created = true;",
          "+",
          "",
        ].join("\n"),
      },
      makeCtx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      dryRun: false,
      changedFiles: ["src/app.ts", "src/created.ts"],
      createdFiles: ["src/created.ts"],
      deletedFiles: [],
      files: [
        {
          path: "src/app.ts",
          operation: "update",
          hunks: 1,
        },
        {
          path: "src/created.ts",
          operation: "create",
          hunks: 1,
        },
      ],
    });
    expect(await fs.readFile(path.join(root, "src/app.ts"), "utf8")).toBe(
      "export const answer = 42;\n",
    );
    expect(await fs.readFile(path.join(root, "src/created.ts"), "utf8")).toBe(
      "export const created = true;\n",
    );
    expect(result.metadata).toMatchObject({
      evidenceKind: "patch",
      changedFiles: ["src/app.ts", "src/created.ts"],
    });
  });

  it("validates a patch in dry-run mode without writing files", async () => {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(path.join(root, "src/app.ts"), "export const answer = 41;\n");
    const tool = makePatchApplyTool(root);

    const result = await tool.execute(
      {
        dry_run: true,
        patch: [
          "--- a/src/app.ts",
          "+++ b/src/app.ts",
          "@@ -1 +1 @@",
          "-export const answer = 41;",
          "+export const answer = 42;",
          "",
        ].join("\n"),
      },
      makeCtx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      dryRun: true,
      changedFiles: ["src/app.ts"],
      files: [
        {
          path: "src/app.ts",
          operation: "update",
          hunks: 1,
        },
      ],
    });
    expect(await fs.readFile(path.join(root, "src/app.ts"), "utf8")).toBe(
      "export const answer = 41;\n",
    );
  });

  it("emits a structured patch preview event before writing files", async () => {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(path.join(root, "src/app.ts"), "export const answer = 41;\n");
    const events: unknown[] = [];
    const snapshots: string[] = [];
    const tool = makePatchApplyTool(root);
    const ctx = {
      ...makeCtx(root),
      toolUseId: "tu_patch",
      emitAgentEvent: (event: unknown) => {
        events.push(event);
        snapshots.push(fsSync.readFileSync(path.join(root, "src/app.ts"), "utf8"));
      },
    };

    const result = await tool.execute(
      {
        patch: [
          "--- a/src/app.ts",
          "+++ b/src/app.ts",
          "@@ -1 +1 @@",
          "-export const answer = 41;",
          "+export const answer = 42;",
          "",
        ].join("\n"),
      },
      ctx,
    );

    expect(result.status).toBe("ok");
    expect(events).toEqual([
      expect.objectContaining({
        type: "patch_preview",
        toolUseId: "tu_patch",
        dryRun: false,
        changedFiles: ["src/app.ts"],
        files: [
          expect.objectContaining({
            path: "src/app.ts",
            operation: "update",
            hunks: 1,
            addedLines: 1,
            removedLines: 1,
          }),
        ],
      }),
    ]);
    expect(snapshots).toEqual(["export const answer = 41;\n"]);
    expect(await fs.readFile(path.join(root, "src/app.ts"), "utf8")).toBe(
      "export const answer = 42;\n",
    );
  });

  it("does not write any file when one hunk fails preflight validation", async () => {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(path.join(root, "src/first.ts"), "export const first = 1;\n");
    await fs.writeFile(path.join(root, "src/second.ts"), "export const second = 2;\n");
    const tool = makePatchApplyTool(root);

    const result = await tool.execute(
      {
        patch: [
          "--- a/src/first.ts",
          "+++ b/src/first.ts",
          "@@ -1 +1 @@",
          "-export const first = 1;",
          "+export const first = 10;",
          "--- a/src/second.ts",
          "+++ b/src/second.ts",
          "@@ -1 +1 @@",
          "-export const second = 999;",
          "+export const second = 20;",
          "",
        ].join("\n"),
      },
      makeCtx(root),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("hunk_mismatch");
    expect(await fs.readFile(path.join(root, "src/first.ts"), "utf8")).toBe(
      "export const first = 1;\n",
    );
    expect(await fs.readFile(path.join(root, "src/second.ts"), "utf8")).toBe(
      "export const second = 2;\n",
    );
  });

  it("rejects absolute patch paths before writing", async () => {
    const tool = makePatchApplyTool(root);

    const result = await tool.execute(
      {
        patch: [
          "--- /dev/null",
          "+++ /tmp/escape.ts",
          "@@ -0,0 +1 @@",
          "+export const escape = true;",
          "",
        ].join("\n"),
      },
      makeCtx(root),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("path_escape");
  });

  it("deletes files when the unified diff targets /dev/null", async () => {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(path.join(root, "src/remove.ts"), "export const remove = true;\n");
    const tool = makePatchApplyTool(root);

    const result = await tool.execute(
      {
        patch: [
          "--- a/src/remove.ts",
          "+++ /dev/null",
          "@@ -1 +0,0 @@",
          "-export const remove = true;",
          "",
        ].join("\n"),
      },
      makeCtx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      changedFiles: ["src/remove.ts"],
      deletedFiles: ["src/remove.ts"],
      files: [
        {
          path: "src/remove.ts",
          operation: "delete",
          hunks: 1,
        },
      ],
    });
    await expect(fs.stat(path.join(root, "src/remove.ts"))).rejects.toMatchObject({
      code: "ENOENT",
    });
  });
});
