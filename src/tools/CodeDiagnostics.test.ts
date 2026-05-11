import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import {
  makeCodeDiagnosticsTool,
  type CodeActionsOutput,
  type CodeReferencesOutput,
  type CodeRenameOutput,
  type CodeWorkspaceSymbolsOutput,
} from "./CodeDiagnostics.js";

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

describe("CodeDiagnostics", () => {
  let root: string;

  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "code-diagnostics-"));
  });

  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("runs TypeScript diagnostics and returns structured errors", async () => {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(
      path.join(root, "tsconfig.json"),
      JSON.stringify({
        compilerOptions: {
          strict: true,
          noEmit: true,
          target: "ES2022",
          module: "NodeNext",
          moduleResolution: "NodeNext",
        },
        include: ["src/**/*.ts"],
      }),
    );
    await fs.writeFile(
      path.join(root, "src/index.ts"),
      "const value: number = 'not a number';\n",
    );
    const tool = makeCodeDiagnosticsTool(root);

    const result = await tool.execute({}, makeCtx(root));

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      checker: "typescript",
      passed: false,
      diagnosticCount: 1,
      diagnostics: [
        {
          file: "src/index.ts",
          line: 1,
          column: 7,
          severity: "error",
          code: "TS2322",
        },
      ],
    });
    expect(result.metadata).toMatchObject({
      evidenceKind: "diagnostics",
      checker: "typescript",
      passed: false,
      diagnosticCount: 1,
    });
  });

  it("returns TypeScript references and rename preview locations", async () => {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(
      path.join(root, "tsconfig.json"),
      JSON.stringify({
        compilerOptions: {
          strict: true,
          noEmit: true,
          target: "ES2022",
          module: "NodeNext",
          moduleResolution: "NodeNext",
        },
        include: ["src/**/*.ts"],
      }),
    );
    await fs.writeFile(
      path.join(root, "src/math.ts"),
      [
        "export function add(a: number, b: number): number {",
        "  return a + b;",
        "}",
        "export const total = add(1, 2);",
      ].join("\n"),
    );
    await fs.writeFile(
      path.join(root, "src/use.ts"),
      [
        'import { add } from "./math";',
        "export const sum = add(3, 4);",
      ].join("\n"),
    );
    const tool = makeCodeDiagnosticsTool(root);

    const refs = await tool.execute(
      { action: "references", file: "src/math.ts", line: 1, column: 17 },
      makeCtx(root),
    );

    expect(refs.status).toBe("ok");
    const references = refs.output as CodeReferencesOutput;
    expect(references).toMatchObject({
      action: "references",
      file: "src/math.ts",
      referenceCount: 4,
    });
    expect(references.references.map((loc) => `${loc.file}:${loc.line}:${loc.column}`)).toEqual([
      "src/math.ts:1:17",
      "src/math.ts:4:22",
      "src/use.ts:1:10",
      "src/use.ts:2:20",
    ]);

    const rename = await tool.execute(
      {
        action: "rename",
        file: "src/math.ts",
        line: 1,
        column: 17,
        newName: "sumNumbers",
      },
      makeCtx(root),
    );

    expect(rename.status).toBe("ok");
    const renameOutput = rename.output as CodeRenameOutput;
    expect(renameOutput).toMatchObject({
      action: "rename",
      canRename: true,
      locationCount: 4,
    });
    expect(
      renameOutput.changes.map(
        (change) => `${change.file}:${change.line}:${change.column}:${change.newText}`,
      ),
    ).toEqual([
      "src/math.ts:1:17:sumNumbers",
      "src/math.ts:4:22:sumNumbers",
      "src/use.ts:1:10:sumNumbers",
      "src/use.ts:2:20:sumNumbers",
    ]);
  });

  it("returns workspace symbols and TypeScript code actions", async () => {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(
      path.join(root, "tsconfig.json"),
      JSON.stringify({
        compilerOptions: {
          strict: true,
          noEmit: true,
          noUnusedLocals: true,
          target: "ES2022",
          module: "NodeNext",
          moduleResolution: "NodeNext",
        },
        include: ["src/**/*.ts"],
      }),
    );
    await fs.writeFile(
      path.join(root, "src/math.ts"),
      [
        "export function add(a: number, b: number): number {",
        "  return a + b;",
        "}",
      ].join("\n"),
    );
    await fs.writeFile(
      path.join(root, "src/unused.ts"),
      ["const unused = 1;", "export const kept = 2;"].join("\n"),
    );
    const tool = makeCodeDiagnosticsTool(root);

    const symbols = await tool.execute(
      { action: "workspaceSymbols", query: "add" },
      makeCtx(root),
    );

    expect(symbols.status).toBe("ok");
    const symbolOutput = symbols.output as CodeWorkspaceSymbolsOutput;
    expect(symbolOutput.symbols).toContainEqual({
      name: "add",
      kind: "function",
      file: "src/math.ts",
      line: 1,
      column: 17,
    });

    const codeActions = await tool.execute(
      { action: "codeActions", file: "src/unused.ts", line: 1, column: 7 },
      makeCtx(root),
    );

    expect(codeActions.status).toBe("ok");
    const actionsOutput = codeActions.output as CodeActionsOutput;
    expect(actionsOutput.diagnostics).toEqual([
      expect.objectContaining({ code: "TS6133", line: 1, column: 7 }),
    ]);
    expect(actionsOutput.actions.some((action) => action.description.includes("Remove"))).toBe(
      true,
    );
    expect(
      actionsOutput.actions
        .flatMap((action) => action.changes)
        .some((change) => change.file === "src/unused.ts"),
    ).toBe(true);
  });
});
