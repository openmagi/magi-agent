import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { execFile as execFileCb } from "node:child_process";
import { promisify } from "node:util";
import type { StructuredBlock } from "./docxDriver.js";

const execFile = promisify(execFileCb);
export const HWPX_RUNTIME_ROOT = path.resolve(__dirname, "../../../runtime/hwpx");

export type HwpxTemplate = "base" | "gonmun" | "report" | "minutes";

export interface WriteHwpxInput {
  absPath: string;
  title: string;
  blocks: StructuredBlock[];
  template?: HwpxTemplate;
  creator?: string;
  referencePath?: string;
}

interface ParagraphStyle {
  paraPr: number;
  charPr: number;
  vertSize: number;
  textHeight: number;
  baseline: number;
  spacing: number;
}

interface HwpxRenderStyles {
  title: ParagraphStyle;
  heading2: ParagraphStyle;
  heading3: ParagraphStyle;
  body: ParagraphStyle;
  bullet: ParagraphStyle;
  tableHeader: ParagraphStyle;
  tableCell: ParagraphStyle;
  tableHeaderFill: number;
  tableCellFill: number;
}

type RenderItem =
  | { kind: "paragraph"; text: string; style: ParagraphStyle }
  | { kind: "table"; rows: string[][] };

function richTemplateStyles(): HwpxRenderStyles {
  return {
    title: { paraPr: 20, charPr: 7, vertSize: 2000, textHeight: 2000, baseline: 1700, spacing: 1200 },
    heading2: { paraPr: 0, charPr: 8, vertSize: 1400, textHeight: 1400, baseline: 1190, spacing: 840 },
    heading3: { paraPr: 0, charPr: 6, vertSize: 1200, textHeight: 1200, baseline: 1020, spacing: 720 },
    body: { paraPr: 0, charPr: 0, vertSize: 1000, textHeight: 1000, baseline: 850, spacing: 600 },
    bullet: { paraPr: 22, charPr: 0, vertSize: 1000, textHeight: 1000, baseline: 850, spacing: 600 },
    tableHeader: { paraPr: 21, charPr: 9, vertSize: 1000, textHeight: 1000, baseline: 850, spacing: 300 },
    tableCell: { paraPr: 22, charPr: 0, vertSize: 1000, textHeight: 1000, baseline: 850, spacing: 300 },
    tableHeaderFill: 4,
    tableCellFill: 3,
  };
}

function baseTemplateStyles(): HwpxRenderStyles {
  return {
    title: { paraPr: 0, charPr: 5, vertSize: 1600, textHeight: 1600, baseline: 1360, spacing: 960 },
    heading2: { paraPr: 0, charPr: 6, vertSize: 1100, textHeight: 1100, baseline: 935, spacing: 660 },
    heading3: { paraPr: 0, charPr: 1, vertSize: 1000, textHeight: 1000, baseline: 850, spacing: 600 },
    body: { paraPr: 0, charPr: 0, vertSize: 1000, textHeight: 1000, baseline: 850, spacing: 600 },
    bullet: { paraPr: 0, charPr: 2, vertSize: 1000, textHeight: 1000, baseline: 850, spacing: 600 },
    tableHeader: { paraPr: 0, charPr: 2, vertSize: 1000, textHeight: 1000, baseline: 850, spacing: 300 },
    tableCell: { paraPr: 0, charPr: 0, vertSize: 1000, textHeight: 1000, baseline: 850, spacing: 300 },
    tableHeaderFill: 2,
    tableCellFill: 2,
  };
}

function stylesFor(template: HwpxTemplate): HwpxRenderStyles {
  return template === "base" ? baseTemplateStyles() : richTemplateStyles();
}

function escapeXml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function normalizeBlockText(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function lineSegXml(style: ParagraphStyle, horzSize = 42520): string {
  return `<hp:lineseg textpos="0" vertpos="0" vertsize="${style.vertSize}" textheight="${style.textHeight}" baseline="${style.baseline}" spacing="${style.spacing}" horzpos="0" horzsize="${horzSize}" flags="393216"/>`;
}

function paragraphXml(id: number, text: string, style: ParagraphStyle): string {
  return [
    `  <hp:p id="${id}" paraPrIDRef="${style.paraPr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">`,
    `    <hp:run charPrIDRef="${style.charPr}">`,
    `      <hp:t>${escapeXml(text)}</hp:t>`,
    "    </hp:run>",
    "    <hp:linesegarray>",
    `      ${lineSegXml(style)}`,
    "    </hp:linesegarray>",
    "  </hp:p>",
  ].join("\n");
}

function splitTableCells(line: string): string[] | null {
  if (!line.includes("|")) return null;
  const cells = line
    .split("|")
    .map((cell) => cell.trim())
    .filter((cell) => cell.length > 0);
  if (cells.length < 2) return null;
  if (cells.every((cell) => /^:?-{3,}:?$/.test(cell))) return [];
  return cells;
}

function flushTableRows(items: RenderItem[], rows: string[][], styles: HwpxRenderStyles): void {
  if (rows.length === 0) return;
  if (rows.length < 2) {
    for (const row of rows) {
      items.push({ kind: "paragraph", text: row.join(" | "), style: styles.body });
    }
  } else {
    items.push({ kind: "table", rows: [...rows] });
  }
  rows.length = 0;
}

function normalizeBulletLine(line: string): string | null {
  const bullet = /^(?:[•·▪*-]\s+|\d+[.)]\s+)(.+)$/.exec(line);
  return bullet ? `• ${bullet[1]?.trim() ?? ""}` : null;
}

function normalizedBlocks(title: string, blocks: StructuredBlock[]): StructuredBlock[] {
  const source = blocks.length > 0
    ? blocks
    : [{ type: "heading" as const, level: 1 as const, text: title }];
  const first = source[0];
  if (
    first?.type === "heading" &&
    first.text.trim().replace(/\s+/g, " ") === title.trim().replace(/\s+/g, " ")
  ) {
    return source;
  }
  return [{ type: "heading", level: 1, text: title }, ...source];
}

function renderItems(title: string, blocks: StructuredBlock[], styles: HwpxRenderStyles): RenderItem[] {
  const items: RenderItem[] = [];
  const tableRows: string[][] = [];

  for (const block of normalizedBlocks(title, blocks)) {
    if (block.type === "table") {
      flushTableRows(items, tableRows, styles);
      flushTableRows(items, block.rows.map((row) => [...row]), styles);
      continue;
    }
    if (block.type === "horizontal_rule") {
      flushTableRows(items, tableRows, styles);
      continue;
    }
    flushTableRows(items, tableRows, styles);
    const lines = normalizeBlockText(block.text);
    if (block.type === "heading") {
      const [firstLine, ...rest] = lines;
      if (firstLine) {
        const style = block.level === 1
          ? styles.title
          : block.level === 2
            ? styles.heading2
            : styles.heading3;
        items.push({ kind: "paragraph", text: firstLine, style });
      }
      for (const line of rest) {
        items.push({ kind: "paragraph", text: line, style: styles.body });
      }
      continue;
    }

    for (const line of lines) {
      const cells = splitTableCells(line);
      if (cells) {
        if (cells.length > 0) tableRows.push(cells);
        continue;
      }

      flushTableRows(items, tableRows, styles);
      const bullet = normalizeBulletLine(line);
      const forcedBullet = block.type === "bullet";
      items.push({
        kind: "paragraph",
        text: forcedBullet ? `• ${line}` : bullet ?? line,
        style: forcedBullet || bullet ? styles.bullet : styles.body,
      });
    }
  }

  flushTableRows(items, tableRows, styles);
  return items;
}

function tableCellXml(
  id: number,
  rowIndex: number,
  colIndex: number,
  width: number,
  height: number,
  text: string,
  style: ParagraphStyle,
  borderFill: number,
): string {
  const textWidth = Math.max(0, width - 566);
  return [
    `          <hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="1" borderFillIDRef="${borderFill}">`,
    `            <hp:cellAddr colAddr="${colIndex}" rowAddr="${rowIndex}"/>`,
    '            <hp:cellSpan colSpan="1" rowSpan="1"/>',
    `            <hp:cellSz width="${width}" height="${height}"/>`,
    '            <hp:cellMargin left="283" right="283" top="141" bottom="141"/>',
    `            <hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="${textWidth}" fieldName="">`,
    `              <hp:p id="${id}" paraPrIDRef="${style.paraPr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">`,
    `                <hp:run charPrIDRef="${style.charPr}">`,
    `                  <hp:t>${escapeXml(text)}</hp:t>`,
    "                </hp:run>",
    "                <hp:linesegarray>",
    `                  ${lineSegXml(style, textWidth)}`,
    "                </hp:linesegarray>",
    "              </hp:p>",
    "            </hp:subList>",
    "          </hp:tc>",
  ].join("\n");
}

function tableXml(id: number, rows: string[][], styles: HwpxRenderStyles, nextId: () => number): string {
  const rowCount = rows.length;
  const colCount = Math.max(...rows.map((row) => row.length));
  const tableWidth = 42520;
  const rowHeight = 2400;
  const baseWidth = Math.floor(tableWidth / colCount);
  const tableRows = rows.map((row, rowIndex) => {
    const cells = Array.from({ length: colCount }, (_, colIndex) => row[colIndex] ?? "");
    return [
      "        <hp:tr>",
      ...cells.map((cell, colIndex) => {
        const width = colIndex === colCount - 1
          ? tableWidth - baseWidth * (colCount - 1)
          : baseWidth;
        const isHeader = rowIndex === 0;
        return tableCellXml(
          nextId(),
          rowIndex,
          colIndex,
          width,
          rowHeight,
          cell,
          isHeader ? styles.tableHeader : styles.tableCell,
          isHeader ? styles.tableHeaderFill : styles.tableCellFill,
        );
      }),
      "        </hp:tr>",
    ].join("\n");
  });

  return [
    `  <hp:p id="${id}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">`,
    '    <hp:run charPrIDRef="0">',
    `      <hp:tbl id="${nextId()}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" rowCnt="${rowCount}" colCnt="${colCount}" cellSpacing="0" borderFillIDRef="${styles.tableCellFill}" noAdjust="0">`,
    `        <hp:sz width="${tableWidth}" widthRelTo="ABSOLUTE" height="${rowCount * rowHeight}" heightRelTo="ABSOLUTE" protect="0"/>`,
    '        <hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>',
    '        <hp:outMargin left="0" right="0" top="0" bottom="0"/>',
    '        <hp:inMargin left="0" right="0" top="0" bottom="0"/>',
    ...tableRows,
    "      </hp:tbl>",
    "    </hp:run>",
    "  </hp:p>",
  ].join("\n");
}

async function renderSectionXml(title: string, blocks: StructuredBlock[], template: HwpxTemplate): Promise<string> {
  const baseSectionPath = path.join(HWPX_RUNTIME_ROOT, "templates", "base", "Contents", "section0.xml");
  const baseSection = await fs.readFile(baseSectionPath, "utf8");
  const closeTag = "</hs:sec>";
  const closeIndex = baseSection.lastIndexOf(closeTag);
  if (closeIndex < 0) {
    throw new Error("base HWPX section template is malformed");
  }

  let counter = 4_000_000_000;
  const nextId = () => {
    counter += 1;
    return counter;
  };
  const styles = stylesFor(template);
  const paragraphs = renderItems(title, blocks, styles)
    .map((item) => item.kind === "table"
      ? tableXml(nextId(), item.rows, styles, nextId)
      : paragraphXml(nextId(), item.text, item.style))
    .join("\n");

  return `${baseSection.slice(0, closeIndex)}\n${paragraphs}\n${closeTag}\n`;
}

async function runPython(scriptPath: string, args: string[]): Promise<void> {
  const { stderr } = await execFile("python3", [scriptPath, ...args], {
    cwd: HWPX_RUNTIME_ROOT,
  });
  if (stderr && stderr.trim().length > 0) {
    // validation/build scripts print warnings to stderr; keep them visible only
    // when they actually fail via execFile throwing.
  }
}

export async function writeHwpxFromBlocks(input: WriteHwpxInput): Promise<void> {
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "clawy-hwpx-"));
  const sectionPath = path.join(tempRoot, "section0.xml");
  const buildScript = path.join(HWPX_RUNTIME_ROOT, "scripts", "build_hwpx.py");
  const validateScript = path.join(HWPX_RUNTIME_ROOT, "scripts", "validate.py");
  const pageGuardScript = path.join(HWPX_RUNTIME_ROOT, "scripts", "page_guard.py");
  const effectiveTemplate = input.template ?? "report";

  try {
    await fs.mkdir(path.dirname(input.absPath), { recursive: true });
    await fs.writeFile(sectionPath, await renderSectionXml(input.title, input.blocks, effectiveTemplate), "utf8");

    const buildArgs = ["--output", input.absPath, "--section", sectionPath, "--title", input.title];
    if (effectiveTemplate !== "base") {
      buildArgs.unshift(effectiveTemplate);
      buildArgs.unshift("--template");
    }
    if (input.creator) {
      buildArgs.push("--creator", input.creator);
    }

    await runPython(buildScript, buildArgs);
    await runPython(validateScript, [input.absPath]);

    if (input.referencePath) {
      await runPython(pageGuardScript, ["--reference", input.referencePath, "--output", input.absPath]);
    }
  } finally {
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
}
