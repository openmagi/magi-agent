/**
 * coreToolsIsolation.test — T1-03b / PRE-01 completion.
 *
 * Verifies that the 6 core tool factories (FileRead, FileWrite, FileEdit,
 * Bash, Glob, Grep) consult `ctx.spawnWorkspace` at runtime rather than
 * the factory-captured workspaceRoot. A spawned child whose context has
 * `spawnWorkspace` set must NOT be able to reach the parent's PVC root
 * through these tools.
 *
 * The five scenarios below map directly to the deliverable in the task
 * brief:
 *   1. Parent plants `parent-secret.txt`; child FileRead targeting that
 *      path fails because it resolves under `.spawn/{taskId}/` where no
 *      such file exists.
 *   2. Child FileWrite("hello.txt") lands inside the spawn subdir, not
 *      at the parent root.
 *   3. Child Bash runs with cwd=spawn subdir; `pwd` reflects the subdir.
 *   4. Child Glob("** /*") sees only files under the spawn subdir.
 *   5. A parent tool context with no spawnWorkspace falls back to the
 *      factory's default workspaceRoot — existing behaviour preserved.
 */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, it, expect } from "vitest";
import type { ToolContext } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { makeFileReadTool } from "./FileRead.js";
import { makeFileWriteTool } from "./FileWrite.js";
import { makeFileEditTool } from "./FileEdit.js";
import { makeBashTool, type BashOutput } from "./Bash.js";
import { makeGlobTool, type GlobOutput } from "./Glob.js";
import { makeGrepTool, type GrepOutput } from "./Grep.js";

function makeCtx(overrides: Partial<ToolContext> & Pick<ToolContext, "workspaceRoot">): ToolContext {
  return {
    botId: "bot_iso",
    sessionKey: "agent:main:iso:1",
    turnId: "turn_iso",
    abortSignal: new AbortController().signal,
    emitProgress: () => {},
    emitAgentEvent: () => {},
    askUser: async () => {
      throw new Error("no askUser in isolation test");
    },
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
    ...overrides,
  };
}

describe("core tools — ctx.spawnWorkspace isolation (T1-03b / PRE-01)", () => {
  let parentRoot: string;
  let spawnDir: string;

  beforeEach(async () => {
    parentRoot = await fs.mkdtemp(path.join(os.tmpdir(), "core-iso-parent-"));
    spawnDir = path.join(parentRoot, ".spawn", "task_abc");
    await fs.mkdir(spawnDir, { recursive: true });
  });

  afterEach(async () => {
    await fs.rm(parentRoot, { recursive: true, force: true });
  });

  it("(1) child FileRead cannot reach parent-root files via the parent's relative path", async () => {
    await fs.writeFile(path.join(parentRoot, "parent-secret.txt"), "topsecret");

    const readTool = makeFileReadTool(parentRoot);
    const ws = new Workspace(spawnDir);
    const childCtx = makeCtx({ workspaceRoot: parentRoot, spawnWorkspace: ws });

    const result = await readTool.execute({ path: "parent-secret.txt" }, childCtx);
    expect(result.status).toBe("error");
    // ENOENT from fs.stat when resolving under spawnDir.
    expect(result.errorCode).toBeDefined();

    // Parent-side read still works (no spawnWorkspace).
    const parentCtx = makeCtx({ workspaceRoot: parentRoot });
    const parentResult = await readTool.execute({ path: "parent-secret.txt" }, parentCtx);
    expect(parentResult.status).toBe("ok");
    expect(parentResult.output?.content).toBe("topsecret");
  });

  it("(2) child FileWrite lands inside spawnDir, not at parent root", async () => {
    const writeTool = makeFileWriteTool(parentRoot);
    const ws = new Workspace(spawnDir);
    const childCtx = makeCtx({ workspaceRoot: parentRoot, spawnWorkspace: ws });

    const result = await writeTool.execute(
      { path: "hello.txt", content: "child-wrote-this" },
      childCtx,
    );
    expect(result.status).toBe("ok");

    // File inside spawnDir.
    const inSpawn = await fs.readFile(path.join(spawnDir, "hello.txt"), "utf8");
    expect(inSpawn).toBe("child-wrote-this");
    // Parent root does NOT gain a hello.txt at its top level.
    await expect(fs.access(path.join(parentRoot, "hello.txt"))).rejects.toBeDefined();
  });

  it("(2b) FileEdit respects ctx.spawnWorkspace", async () => {
    // Seed file inside spawnDir.
    await fs.writeFile(path.join(spawnDir, "note.txt"), "alpha beta");
    // Seed a same-named file at parent root — must NOT be touched.
    await fs.writeFile(path.join(parentRoot, "note.txt"), "parent-orig");

    const editTool = makeFileEditTool(parentRoot);
    const ws = new Workspace(spawnDir);
    const childCtx = makeCtx({ workspaceRoot: parentRoot, spawnWorkspace: ws });

    const result = await editTool.execute(
      { path: "note.txt", old_string: "alpha", new_string: "ALPHA" },
      childCtx,
    );
    expect(result.status).toBe("ok");

    const spawnAfter = await fs.readFile(path.join(spawnDir, "note.txt"), "utf8");
    expect(spawnAfter).toBe("ALPHA beta");
    const parentAfter = await fs.readFile(path.join(parentRoot, "note.txt"), "utf8");
    expect(parentAfter).toBe("parent-orig");
  });

  it("(3) child Bash runs with cwd=spawnDir — `pwd` returns the subdir", async () => {
    const bash = makeBashTool(parentRoot);
    const ws = new Workspace(spawnDir);
    const childCtx = makeCtx({ workspaceRoot: parentRoot, spawnWorkspace: ws });

    const result = await bash.execute({ command: "pwd" }, childCtx);
    expect(result.status).toBe("ok");
    const out = result.output as BashOutput;
    // macOS tmp dirs can be symlinks (/var → /private/var). fs.realpath
    // both sides before comparing so the assertion is robust.
    const observed = await fs.realpath(out.stdout.trim());
    const expected = await fs.realpath(spawnDir);
    expect(observed).toBe(expected);
  });

  it("(3b) child Bash cannot escape spawnDir via ../", async () => {
    const bash = makeBashTool(parentRoot);
    const ws = new Workspace(spawnDir);
    const childCtx = makeCtx({ workspaceRoot: parentRoot, spawnWorkspace: ws });

    // ws.resolve on "../" escapes → Bash returns error before spawn.
    const result = await bash.execute({ command: "pwd", cwd: "../../.." }, childCtx);
    expect(result.status).toBe("error");
    expect(result.errorMessage).toMatch(/escapes workspace/);
  });

  it("(3c) child Bash prepends /home/ocuser/.clawy/bin to PATH for wrapper scripts", async () => {
    const originalPath = process.env.PATH;
    process.env.PATH = "/usr/local/bin:/usr/bin:/bin";
    try {
      const bash = makeBashTool(parentRoot);
      const ws = new Workspace(spawnDir);
      const childCtx = makeCtx({ workspaceRoot: parentRoot, spawnWorkspace: ws });

      const result = await bash.execute({ command: "printf '%s' \"$PATH\"" }, childCtx);
      expect(result.status).toBe("ok");
      const out = result.output as BashOutput;
      expect(out.stdout.startsWith("/home/ocuser/.clawy/bin:")).toBe(true);
    } finally {
      process.env.PATH = originalPath;
    }
  });

  it("(4) child Glob never returns files outside spawnDir", async () => {
    // Plant files at parent root AND inside spawnDir.
    await fs.writeFile(path.join(parentRoot, "parent-only.txt"), "p");
    await fs.writeFile(path.join(spawnDir, "child-a.txt"), "a");
    await fs.writeFile(path.join(spawnDir, "child-b.txt"), "b");

    const glob = makeGlobTool(parentRoot);
    const ws = new Workspace(spawnDir);
    const childCtx = makeCtx({ workspaceRoot: parentRoot, spawnWorkspace: ws });

    // The Glob tool shells out to `find -printf`; some hosts (macOS BSD
    // find) lack -printf → status=ok with empty matches. What we care
    // about for isolation is: parent-only.txt NEVER appears, and any
    // returned match must NOT escape spawnDir.
    const result = await glob.execute({ pattern: "*.txt" }, childCtx);
    expect(result.status).toBe("ok");
    const out = result.output as GlobOutput;
    const names = out.matches.map((m) => path.basename(m));
    expect(names).not.toContain("parent-only.txt");
    // All returned matches resolve inside spawnDir.
    for (const rel of out.matches) {
      const abs = path.resolve(ws.root, rel);
      expect(abs.startsWith(path.resolve(ws.root))).toBe(true);
    }
  });

  it("(4b) child Grep sees only matches inside spawnDir", async () => {
    await fs.writeFile(path.join(parentRoot, "parent.txt"), "NEEDLE at parent");
    await fs.writeFile(path.join(spawnDir, "child.txt"), "NEEDLE at child");

    const grep = makeGrepTool(parentRoot);
    const ws = new Workspace(spawnDir);
    const childCtx = makeCtx({ workspaceRoot: parentRoot, spawnWorkspace: ws });

    const result = await grep.execute({ pattern: "NEEDLE" }, childCtx);
    expect(result.status).toBe("ok");
    const out = result.output as GrepOutput;
    // Files reported are relative to ws.root (spawnDir).
    const files = (out.matches ?? []).map((m) => m.file);
    // Only child.txt should appear — parent.txt lives outside spawnDir.
    expect(files.some((f) => f.endsWith("child.txt"))).toBe(true);
    expect(files.some((f) => f.endsWith("parent.txt"))).toBe(false);
  });

  it("(5) parent tool (no spawnWorkspace) still uses the factory-captured workspaceRoot", async () => {
    await fs.writeFile(path.join(parentRoot, "top.txt"), "top-level");

    const readTool = makeFileReadTool(parentRoot);
    const writeTool = makeFileWriteTool(parentRoot);
    const parentCtx = makeCtx({ workspaceRoot: parentRoot });

    const read = await readTool.execute({ path: "top.txt" }, parentCtx);
    expect(read.status).toBe("ok");
    expect(read.output?.content).toBe("top-level");

    const write = await writeTool.execute(
      { path: "written-by-parent.txt", content: "owned" },
      parentCtx,
    );
    expect(write.status).toBe("ok");
    const check = await fs.readFile(path.join(parentRoot, "written-by-parent.txt"), "utf8");
    expect(check).toBe("owned");
  });
});
