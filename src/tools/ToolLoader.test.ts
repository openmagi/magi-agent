/**
 * ToolLoader.test — discovery, loading, validation, name collision,
 * missing dir graceful handling.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

import { loadUserTools } from "./ToolLoader.js";

let tmpDir: string;

function createTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "toolloader-test-"));
}

function writeTool(
  baseDir: string,
  name: string,
  content: string,
  ext = "js",
): void {
  const toolDir = path.join(baseDir, name);
  fs.mkdirSync(toolDir, { recursive: true });
  fs.writeFileSync(path.join(toolDir, `index.${ext}`), content, "utf-8");
}

beforeEach(() => {
  tmpDir = createTmpDir();
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

describe("ToolLoader", () => {
  it("discovers tools from project-local directory", async () => {
    const toolsDir = path.join(tmpDir, "tools");
    writeTool(
      toolsDir,
      "my-tool",
      `
      export function makeMyToolTool() {
        return {
          name: "MyTool",
          description: "A test tool",
          permission: "read",
          inputSchema: { type: "object", properties: {}, required: [] },
          execute: async () => ({ status: "ok", durationMs: 0 }),
        };
      }
    `,
    );

    const result = await loadUserTools({
      directory: "./tools",
      globalDirectory: path.join(tmpDir, "global-tools"),
      workspaceRoot: tmpDir,
    });

    expect(result.tools).toHaveLength(1);
    expect(result.tools[0].name).toBe("MyTool");
    expect(result.tools[0].permission).toBe("read");
    expect(result.warnings).toHaveLength(0);
  });

  it("handles missing directories gracefully", async () => {
    const result = await loadUserTools({
      directory: "./nonexistent-tools",
      globalDirectory: path.join(tmpDir, "nonexistent-global"),
      workspaceRoot: tmpDir,
    });

    expect(result.tools).toHaveLength(0);
    expect(result.warnings).toHaveLength(0);
  });

  it("skips directories without index files", async () => {
    const toolsDir = path.join(tmpDir, "tools");
    const emptyDir = path.join(toolsDir, "empty-tool");
    fs.mkdirSync(emptyDir, { recursive: true });
    fs.writeFileSync(
      path.join(emptyDir, "README.md"),
      "no index",
      "utf-8",
    );

    const result = await loadUserTools({
      directory: "./tools",
      globalDirectory: path.join(tmpDir, "global-tools"),
      workspaceRoot: tmpDir,
    });

    expect(result.tools).toHaveLength(0);
  });

  it("validates tool objects — rejects missing required fields", async () => {
    const toolsDir = path.join(tmpDir, "tools");
    writeTool(
      toolsDir,
      "invalid-tool",
      `
      export function makeInvalidToolTool() {
        return {
          name: "InvalidTool",
          // missing description, inputSchema, permission, execute
        };
      }
    `,
    );

    const result = await loadUserTools({
      directory: "./tools",
      globalDirectory: path.join(tmpDir, "global-tools"),
      workspaceRoot: tmpDir,
    });

    expect(result.tools).toHaveLength(0);
    expect(result.warnings.length).toBeGreaterThan(0);
  });

  it("validates tool objects — rejects invalid permission", async () => {
    const toolsDir = path.join(tmpDir, "tools");
    writeTool(
      toolsDir,
      "bad-perm",
      `
      export function makeBadPermTool() {
        return {
          name: "BadPerm",
          description: "bad",
          permission: "admin",
          inputSchema: { type: "object" },
          execute: async () => ({ status: "ok", durationMs: 0 }),
        };
      }
    `,
    );

    const result = await loadUserTools({
      directory: "./tools",
      globalDirectory: path.join(tmpDir, "global-tools"),
      workspaceRoot: tmpDir,
    });

    expect(result.tools).toHaveLength(0);
  });

  it("project-local wins on name collision", async () => {
    const localDir = path.join(tmpDir, "tools");
    const globalDir = path.join(tmpDir, "global-tools");

    writeTool(
      localDir,
      "shared-tool",
      `
      export function makeSharedToolTool() {
        return {
          name: "SharedTool",
          description: "local version",
          permission: "read",
          inputSchema: { type: "object", properties: {}, required: [] },
          execute: async () => ({ status: "ok", durationMs: 0 }),
        };
      }
    `,
    );

    writeTool(
      globalDir,
      "shared-tool",
      `
      export function makeSharedToolTool() {
        return {
          name: "SharedTool",
          description: "global version",
          permission: "write",
          inputSchema: { type: "object", properties: {}, required: [] },
          execute: async () => ({ status: "ok", durationMs: 0 }),
        };
      }
    `,
    );

    const result = await loadUserTools({
      directory: "./tools",
      globalDirectory: globalDir,
      workspaceRoot: tmpDir,
    });

    expect(result.tools).toHaveLength(1);
    expect(result.tools[0].description).toBe("local version");
    expect(
      result.warnings.some((w) => w.includes("name collision")),
    ).toBe(true);
  });

  it("loads tool from default export object", async () => {
    const toolsDir = path.join(tmpDir, "tools");
    writeTool(
      toolsDir,
      "default-export",
      `
      const tool = {
        name: "DefaultExport",
        description: "from default",
        permission: "meta",
        inputSchema: { type: "object", properties: {} },
        execute: async () => ({ status: "ok", durationMs: 0 }),
      };
      exports.default = tool;
    `,
    );

    const result = await loadUserTools({
      directory: "./tools",
      globalDirectory: path.join(tmpDir, "global-tools"),
      workspaceRoot: tmpDir,
    });

    expect(result.tools).toHaveLength(1);
    expect(result.tools[0]?.name).toBe("DefaultExport");
  });

  it("skips hidden directories and __fixtures__", async () => {
    const toolsDir = path.join(tmpDir, "tools");
    fs.mkdirSync(path.join(toolsDir, ".hidden"), { recursive: true });
    fs.mkdirSync(path.join(toolsDir, "__fixtures__"), { recursive: true });

    const result = await loadUserTools({
      directory: "./tools",
      globalDirectory: path.join(tmpDir, "global-tools"),
      workspaceRoot: tmpDir,
    });

    expect(result.tools).toHaveLength(0);
    expect(result.warnings).toHaveLength(0);
  });
});
