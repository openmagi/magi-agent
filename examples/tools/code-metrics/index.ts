/**
 * Example: Code Metrics Tool
 *
 * Demonstrates a read-only tool that analyzes code files.
 * Uses the `read` permission class since it only reads files.
 */

import fs from "node:fs";
import path from "node:path";

import type { Tool, ToolContext, ToolResult } from "../../../src/Tool.js";

interface CodeMetricsInput {
  filePath: string;
}

interface CodeMetricsOutput {
  filePath: string;
  lines: number;
  blankLines: number;
  commentLines: number;
  codeLines: number;
  functionCount: number;
}

export function makeCodeMetricsTool(): Tool<
  CodeMetricsInput,
  CodeMetricsOutput
> {
  return {
    name: "CodeMetrics",
    description:
      "Analyze a source file and return code metrics: LOC, blank lines, comment lines, and function count.",
    permission: "read",
    isConcurrencySafe: true,
    inputSchema: {
      type: "object",
      properties: {
        filePath: {
          type: "string",
          description: "Path to the file to analyze (relative to workspace root)",
        },
      },
      required: ["filePath"],
      additionalProperties: false,
    },

    validate(input: CodeMetricsInput): string | null {
      if (!input.filePath || input.filePath.trim().length === 0) {
        return "filePath must be a non-empty string";
      }
      if (input.filePath.includes("..")) {
        return "filePath must not contain '..' (path traversal)";
      }
      return null;
    },

    async execute(
      input: CodeMetricsInput,
      ctx: ToolContext,
    ): Promise<ToolResult<CodeMetricsOutput>> {
      const startMs = Date.now();
      const absPath = path.resolve(ctx.workspaceRoot, input.filePath);

      // Verify the file is within the workspace
      if (!absPath.startsWith(ctx.workspaceRoot)) {
        return {
          status: "permission_denied",
          errorCode: "outside_workspace",
          errorMessage: "File is outside the workspace root",
          durationMs: Date.now() - startMs,
        };
      }

      try {
        const content = fs.readFileSync(absPath, "utf-8");
        const allLines = content.split("\n");
        const lines = allLines.length;

        let blankLines = 0;
        let commentLines = 0;
        let functionCount = 0;

        const ext = path.extname(input.filePath);
        const commentPattern =
          ext === ".py" ? /^\s*#/ : /^\s*(\/\/|\/\*|\*)/;
        const functionPattern =
          ext === ".py"
            ? /^\s*def\s+\w+/
            : /\b(function|async\s+function)\s+\w+|(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?\(/;

        for (const line of allLines) {
          if (line.trim().length === 0) {
            blankLines++;
          } else if (commentPattern.test(line)) {
            commentLines++;
          }
          if (functionPattern.test(line)) {
            functionCount++;
          }
        }

        return {
          status: "ok",
          output: {
            filePath: input.filePath,
            lines,
            blankLines,
            commentLines,
            codeLines: lines - blankLines - commentLines,
            functionCount,
          },
          durationMs: Date.now() - startMs,
        };
      } catch (err) {
        const code = (err as NodeJS.ErrnoException).code;
        if (code === "ENOENT") {
          return {
            status: "error",
            errorCode: "file_not_found",
            errorMessage: `File not found: ${input.filePath}`,
            durationMs: Date.now() - startMs,
          };
        }
        return {
          status: "error",
          errorCode: "read_error",
          errorMessage: (err as Error).message,
          durationMs: Date.now() - startMs,
        };
      }
    },
  };
}
