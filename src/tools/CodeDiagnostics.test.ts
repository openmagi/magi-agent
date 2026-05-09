import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeCodeDiagnosticsTool } from "./CodeDiagnostics.js";

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
});
