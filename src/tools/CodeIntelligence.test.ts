import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeCodeIntelligenceTool } from "./CodeIntelligence.js";

function makeCtx(workspaceRoot: string): ToolContext {
  return {
    botId: "bot_test",
    sessionKey: "session_test",
    turnId: "turn_test",
    workspaceRoot,
    abortSignal: new AbortController().signal,
    emitProgress: () => {},
    askUser: async () => {
      throw new Error("askUser not available in tests");
    },
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

describe("CodeIntelligence", () => {
  let root: string;

  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "code-intelligence-"));
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(
      path.join(root, "tsconfig.json"),
      JSON.stringify({
        compilerOptions: {
          target: "ES2022",
          module: "NodeNext",
          moduleResolution: "NodeNext",
          strict: true,
        },
        include: ["src/**/*.ts"],
      }),
      "utf8",
    );
    await fs.writeFile(
      path.join(root, "src/math.ts"),
      [
        "export function add(left: number, right: number): number {",
        "  return left + right;",
        "}",
        "",
        "export const label = 'math';",
        "",
      ].join("\n"),
      "utf8",
    );
    await fs.writeFile(
      path.join(root, "src/use.ts"),
      [
        'import { add } from "./math";',
        "",
        "export const total = add(1, 2);",
        "",
      ].join("\n"),
      "utf8",
    );
  });

  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("resolves TypeScript definitions and references from a source position", async () => {
    const tool = makeCodeIntelligenceTool(root);
    const ctx = makeCtx(root);

    const definition = await tool.execute(
      {
        action: "definition",
        file: "src/use.ts",
        line: 3,
        column: 22,
      },
      ctx,
    );

    expect(definition.status).toBe("ok");
    expect(definition.output?.results).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          file: "src/math.ts",
          preview: expect.stringContaining("export function add"),
        }),
      ]),
    );

    const references = await tool.execute(
      {
        action: "references",
        file: "src/use.ts",
        line: 3,
        column: 22,
      },
      ctx,
    );

    expect(references.status).toBe("ok");
    expect(references.output?.results).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ file: "src/math.ts" }),
        expect.objectContaining({ file: "src/use.ts" }),
      ]),
    );
  });

  it("returns document and workspace symbols for TypeScript projects", async () => {
    const tool = makeCodeIntelligenceTool(root);
    const ctx = makeCtx(root);

    const documentSymbols = await tool.execute(
      {
        action: "document_symbols",
        file: "src/math.ts",
      },
      ctx,
    );

    expect(documentSymbols.status).toBe("ok");
    expect(documentSymbols.output?.results).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "add",
          kind: expect.stringMatching(/function/i),
          file: "src/math.ts",
        }),
        expect.objectContaining({
          name: "label",
          file: "src/math.ts",
        }),
      ]),
    );

    const workspaceSymbols = await tool.execute(
      {
        action: "workspace_symbols",
        query: "add",
      },
      ctx,
    );

    expect(workspaceSymbols.status).toBe("ok");
    expect(workspaceSymbols.output?.results).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "add",
          file: "src/math.ts",
        }),
      ]),
    );
  });

  it("returns hover text and compiler diagnostics", async () => {
    await fs.writeFile(
      path.join(root, "src/broken.ts"),
      "export const count: number = 'bad';\n",
      "utf8",
    );
    const tool = makeCodeIntelligenceTool(root);
    const ctx = makeCtx(root);

    const hover = await tool.execute(
      {
        action: "hover",
        file: "src/use.ts",
        line: 3,
        column: 22,
      },
      ctx,
    );

    expect(hover.status).toBe("ok");
    expect(hover.output?.results).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          text: expect.stringContaining("add(left: number, right: number): number"),
          file: "src/use.ts",
        }),
      ]),
    );

    const diagnostics = await tool.execute(
      {
        action: "diagnostics",
      },
      ctx,
    );

    expect(diagnostics.status).toBe("ok");
    expect(diagnostics.output?.results).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          file: "src/broken.ts",
          severity: "error",
          code: "TS2322",
        }),
      ]),
    );
  });
});
