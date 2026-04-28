import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import ExcelJS from "exceljs";
import { afterEach, describe, expect, it } from "vitest";
import { OutputArtifactRegistry } from "../output/OutputArtifactRegistry.js";
import { makeSpreadsheetWriteTool } from "./SpreadsheetWrite.js";
import type { ToolContext } from "../Tool.js";

const roots: string[] = [];

function makeCtx(root: string): ToolContext {
  return {
    botId: "bot-1",
    sessionKey: "s-1",
    turnId: "t-1",
    workspaceRoot: root,
    askUser: async () => ({ selectedId: "ok" }),
    emitProgress: () => {},
    abortSignal: AbortSignal.timeout(5_000),
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

async function makeRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "spreadsheet-write-"));
  roots.push(root);
  return root;
}

function assertArraySchemasDeclareItems(schema: unknown, path = "$"): void {
  if (!schema || typeof schema !== "object") return;
  const node = schema as Record<string, unknown>;

  if (node["type"] === "array") {
    expect(node, `${path} is an array schema without items`).toHaveProperty("items");
  }

  for (const [key, value] of Object.entries(node)) {
    assertArraySchemasDeclareItems(value, `${path}.${key}`);
  }
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("SpreadsheetWrite", () => {
  it("declares items for every array schema exposed to upstream providers", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeSpreadsheetWriteTool(root, registry);

    assertArraySchemasDeclareItems(tool.inputSchema);
  });

  it("creates an xlsx file and registers it", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeSpreadsheetWriteTool(root, registry);

    const result = await tool.execute(
      {
        mode: "create",
        title: "Quarterly Revenue",
        filename: "exports/quarterly-revenue.xlsx",
        sheets: [
          {
            name: "Revenue",
            rows: [
              ["Month", "Amount"],
              ["Jan", 1200],
              ["Feb", 1800],
              ["Total", { formula: "SUM(B2:B3)" }],
            ],
          },
        ],
      },
      makeCtx(root),
    );

    expect(result.status).toBe("ok");

    const workbook = new ExcelJS.Workbook();
    await workbook.xlsx.readFile(path.join(root, "exports", "quarterly-revenue.xlsx"));
    const sheet = workbook.getWorksheet("Revenue");

    expect(sheet?.getCell("A2").value).toBe("Jan");
    expect(sheet?.getCell("B4").value).toMatchObject({ formula: "SUM(B2:B3)" });

    const record = await registry.get(result.output!.artifactId);
    expect(record).toMatchObject({
      kind: "spreadsheet",
      format: "xlsx",
      filename: "quarterly-revenue.xlsx",
      workspacePath: "exports/quarterly-revenue.xlsx",
    });
  });

  it("edits an existing workbook in place", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const workbook = new ExcelJS.Workbook();
    const sheet = workbook.addWorksheet("Sheet1");
    sheet.getCell("A1").value = "Draft";
    await fs.mkdir(path.join(root, "exports"), { recursive: true });
    await workbook.xlsx.writeFile(path.join(root, "exports", "draft.xlsx"));

    const tool = makeSpreadsheetWriteTool(root, registry);
    const result = await tool.execute(
      {
        mode: "edit",
        title: "Draft Workbook",
        filename: "exports/draft.xlsx",
        mutations: [
          { type: "setCell", sheet: "Sheet1", cell: "B2", value: 42 },
          { type: "setCell", sheet: "Sheet1", cell: "A1", value: "Final" },
        ],
      },
      makeCtx(root),
    );

    expect(result.status).toBe("ok");

    const reloaded = new ExcelJS.Workbook();
    await reloaded.xlsx.readFile(path.join(root, "exports", "draft.xlsx"));
    const reloadedSheet = reloaded.getWorksheet("Sheet1");

    expect(reloadedSheet?.getCell("A1").value).toBe("Final");
    expect(reloadedSheet?.getCell("B2").value).toBe(42);
  });
});
