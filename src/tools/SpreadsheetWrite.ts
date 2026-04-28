import fs from "node:fs/promises";
import path from "node:path";
import ExcelJS from "exceljs";
import type { OutputArtifactRegistry } from "../output/OutputArtifactRegistry.js";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { errorResult } from "../util/toolResult.js";

type SpreadsheetCellValue = string | number | boolean | { formula: string };

const CELL_VALUE_SCHEMA = {
  anyOf: [
    { type: "string" },
    { type: "number" },
    { type: "boolean" },
    {
      type: "object",
      properties: {
        formula: { type: "string" },
      },
      required: ["formula"],
      additionalProperties: false,
    },
  ],
} as const;

interface SpreadsheetSheetInput {
  name: string;
  rows: SpreadsheetCellValue[][];
}

interface SpreadsheetCellMutation {
  type: "setCell";
  sheet: string;
  cell: string;
  value: SpreadsheetCellValue;
}

export interface SpreadsheetWriteInput {
  mode: "create" | "edit";
  title: string;
  filename: string;
  sheets?: SpreadsheetSheetInput[];
  mutations?: SpreadsheetCellMutation[];
}

export interface SpreadsheetWriteOutput {
  artifactId: string;
  workspacePath: string;
  filename: string;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    mode: { type: "string", enum: ["create", "edit"] },
    title: { type: "string" },
    filename: { type: "string" },
    sheets: {
      type: "array",
      items: {
        type: "object",
        properties: {
          name: { type: "string" },
          rows: {
            type: "array",
            items: {
              type: "array",
              items: CELL_VALUE_SCHEMA,
            },
          },
        },
        required: ["name", "rows"],
        additionalProperties: false,
      },
    },
    mutations: {
      type: "array",
      items: {
        type: "object",
        properties: {
          type: { type: "string", enum: ["setCell"] },
          sheet: { type: "string" },
          cell: { type: "string" },
          value: CELL_VALUE_SCHEMA,
        },
        required: ["type", "sheet", "cell", "value"],
        additionalProperties: false,
      },
    },
  },
  required: ["mode", "title", "filename"],
  additionalProperties: false,
} as const;

function basename(filePath: string): string {
  return filePath.split("/").pop() || filePath;
}

function toCellValue(value: SpreadsheetCellValue): ExcelJS.CellValue {
  return value as ExcelJS.CellValue;
}

async function applyMutations(
  workbook: ExcelJS.Workbook,
  mutations: SpreadsheetCellMutation[] = [],
): Promise<void> {
  for (const mutation of mutations) {
    const sheet = workbook.getWorksheet(mutation.sheet) ?? workbook.addWorksheet(mutation.sheet);
    sheet.getCell(mutation.cell).value = toCellValue(mutation.value);
  }
}

export function makeSpreadsheetWriteTool(
  workspaceRoot: string,
  outputRegistry: OutputArtifactRegistry,
): Tool<SpreadsheetWriteInput, SpreadsheetWriteOutput> {
  return {
    name: "SpreadsheetWrite",
    description:
      "Create or edit spreadsheet files inside the bot workspace and register the result as a user-visible output artifact.",
    inputSchema: INPUT_SCHEMA,
    permission: "write",
    validate(input) {
      if (!input || (input.mode !== "create" && input.mode !== "edit")) {
        return "`mode` must be create or edit";
      }
      if (typeof input.title !== "string" || input.title.trim().length === 0) {
        return "`title` is required";
      }
      if (typeof input.filename !== "string" || input.filename.trim().length === 0) {
        return "`filename` is required";
      }
      if (input.mode === "create" && (!Array.isArray(input.sheets) || input.sheets.length === 0)) {
        return "`sheets` is required in create mode";
      }
      if (input.mode === "edit" && (!Array.isArray(input.mutations) || input.mutations.length === 0)) {
        return "`mutations` is required in edit mode";
      }
      return null;
    },
    async execute(
      input: SpreadsheetWriteInput,
      ctx: ToolContext,
    ): Promise<ToolResult<SpreadsheetWriteOutput>> {
      const start = Date.now();
      try {
        const workbook = new ExcelJS.Workbook();
        const absPath = path.join(workspaceRoot, input.filename);
        await fs.mkdir(path.dirname(absPath), { recursive: true });

        if (input.mode === "edit") {
          await workbook.xlsx.readFile(absPath);
          await applyMutations(workbook, input.mutations);
        } else {
          for (const sheetInput of input.sheets ?? []) {
            const sheet = workbook.addWorksheet(sheetInput.name);
            for (const row of sheetInput.rows) {
              sheet.addRow(row.map((value) => toCellValue(value)));
            }
          }
        }

        await workbook.xlsx.writeFile(absPath);

        const artifact = await outputRegistry.register({
          sessionKey: ctx.sessionKey,
          turnId: ctx.turnId,
          kind: "spreadsheet",
          format: "xlsx",
          title: input.title,
          filename: basename(input.filename),
          mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          workspacePath: input.filename,
          previewKind: "none",
          createdByTool: "SpreadsheetWrite",
          sourceKind: input.mode,
        });

        return {
          status: "ok",
          output: {
            artifactId: artifact.artifactId,
            workspacePath: input.filename,
            filename: basename(input.filename),
          },
          durationMs: Date.now() - start,
        };
      } catch (error) {
        return errorResult(error, start);
      }
    },
  };
}
