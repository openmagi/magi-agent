import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeCodeSymbolSearchTool } from "./CodeSymbolSearch.js";

function toolContext(workspaceRoot: string): ToolContext {
  return {
    botId: "bot",
    sessionKey: "session",
    turnId: "turn-1",
    workspaceRoot,
    askUser: async () => ({}),
    emitProgress: () => {},
    abortSignal: new AbortController().signal,
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

describe("CodeSymbolSearch", () => {
  let workspaceRoot: string;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "symbol-search-"));
    await fs.mkdir(path.join(workspaceRoot, "src"), { recursive: true });
    await fs.writeFile(
      path.join(workspaceRoot, "src/service.ts"),
      [
        "export interface UserService {",
        "  findUser(id: string): string;",
        "}",
        "export function findUser(id: string): string {",
        "  return id;",
        "}",
      ].join("\n"),
      "utf8",
    );
  });

  afterEach(async () => {
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("finds likely symbol definitions with file and line evidence", async () => {
    const tool = makeCodeSymbolSearchTool(workspaceRoot);

    const out = await tool.execute(
      { symbol: "findUser", cwd: "src" },
      toolContext(workspaceRoot),
    );

    expect(out.status).toBe("ok");
    expect(out.output?.results).toEqual([
      {
        file: "src/service.ts",
        line: 4,
        preview: "export function findUser(id: string): string {",
      },
    ]);
  });
});
