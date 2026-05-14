import fs from "node:fs/promises";
import path from "node:path";
import { parseCanonicalMarkdown } from "./canonicalMarkdownParser.js";
import { renderCanonicalMarkdownHtml } from "./canonicalMarkdownHtml.js";
import {
  writeEditableDocxFromCanonicalMarkdown,
  writeFixedLayoutDocxFromPngPages,
} from "./canonicalMarkdownDocx.js";
import type {
  BrowserRenderRequest,
  CanonicalDocxMode,
  CanonicalMarkdownLocale,
  CanonicalMarkdownPageOptions,
  CanonicalMarkdownPreset,
  CanonicalMarkdownQa,
} from "./canonicalMarkdownTypes.js";

export type CanonicalMarkdownOutputFormat = "html" | "pdf" | "docx";

export interface CanonicalMarkdownRenderPdfOutput {
  pdfBytes: Buffer;
  screenshots: Array<{ page: number; pngBytes: Buffer; width: number; height: number }>;
  pageCount: number;
  rendererVersion: string;
}

export interface CanonicalMarkdownExportInput {
  workspaceRoot: string;
  title: string;
  filenameBase: string;
  sourceMarkdown: string;
  outputs: CanonicalMarkdownOutputFormat[];
  docxMode: CanonicalDocxMode;
  preset: CanonicalMarkdownPreset;
  locale: CanonicalMarkdownLocale;
  page: CanonicalMarkdownPageOptions;
  renderPdf: (request: BrowserRenderRequest) => Promise<CanonicalMarkdownRenderPdfOutput>;
}

export interface CanonicalMarkdownExportFile {
  format: CanonicalMarkdownOutputFormat;
  workspacePath: string;
  filename: string;
  mimeType: string;
}

export interface CanonicalMarkdownExportResult {
  files: CanonicalMarkdownExportFile[];
  qa: CanonicalMarkdownQa;
}

function safePathSegment(segment: string): string {
  const safe = segment.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  if (!safe || safe === "." || safe === "..") return "document";
  return safe;
}

function outputPath(root: string, filename: string): { workspacePath: string; absPath: string } {
  const safeParts = filename
    .replace(/\\/g, "/")
    .split("/")
    .filter((part) => part.length > 0 && part !== ".")
    .map(safePathSegment);
  const workspacePath = `outputs/${safeParts.length > 0 ? safeParts.join("/") : "document"}`;
  return { workspacePath, absPath: path.join(root, workspacePath) };
}

function mimeType(format: CanonicalMarkdownOutputFormat): string {
  if (format === "html") return "text/html";
  if (format === "pdf") return "application/pdf";
  return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
}

async function writeFileEnsuringDir(absPath: string, data: string | Buffer): Promise<void> {
  await fs.mkdir(path.dirname(absPath), { recursive: true });
  await fs.writeFile(absPath, data);
}

export async function exportCanonicalMarkdownDocument(
  input: CanonicalMarkdownExportInput,
): Promise<CanonicalMarkdownExportResult> {
  const document = parseCanonicalMarkdown(input.sourceMarkdown);
  const renderOptions = {
    title: input.title,
    preset: input.preset,
    locale: input.locale,
    page: input.page,
  };
  const htmlOutput = renderCanonicalMarkdownHtml(document, renderOptions);
  const files: CanonicalMarkdownExportFile[] = [];
  const warnings: string[] = [];
  let rendererVersion = htmlOutput.rendererVersion;
  let pageCount = 0;

  if (input.outputs.includes("html") || input.outputs.includes("pdf")) {
    const htmlPath = outputPath(input.workspaceRoot, `${input.filenameBase}.html`);
    await writeFileEnsuringDir(htmlPath.absPath, htmlOutput.html);
    if (input.outputs.includes("html")) {
      files.push({
        format: "html",
        workspacePath: htmlPath.workspacePath,
        filename: path.basename(htmlPath.workspacePath),
        mimeType: mimeType("html"),
      });
    }
  }

  let pdfRender: CanonicalMarkdownRenderPdfOutput | null = null;
  if (input.outputs.includes("pdf") || input.docxMode === "fixed_layout") {
    pdfRender = await input.renderPdf({
      html: htmlOutput.html,
      css: htmlOutput.css,
      title: input.title,
      locale: input.locale,
      page: input.page,
    });
    if (pdfRender.pdfBytes.subarray(0, 5).toString() !== "%PDF-") {
      throw new Error("canonical PDF renderer returned invalid PDF bytes");
    }
    rendererVersion = `${htmlOutput.rendererVersion}; ${pdfRender.rendererVersion}`;
    pageCount = pdfRender.pageCount;
    for (const screenshot of pdfRender.screenshots) {
      const screenshotPath = outputPath(
        input.workspaceRoot,
        `${input.filenameBase}.preview-${String(screenshot.page).padStart(3, "0")}.png`,
      );
      await writeFileEnsuringDir(screenshotPath.absPath, screenshot.pngBytes);
    }
    if (input.outputs.includes("pdf")) {
      const pdfPath = outputPath(input.workspaceRoot, `${input.filenameBase}.pdf`);
      await writeFileEnsuringDir(pdfPath.absPath, pdfRender.pdfBytes);
      files.push({
        format: "pdf",
        workspacePath: pdfPath.workspacePath,
        filename: path.basename(pdfPath.workspacePath),
        mimeType: mimeType("pdf"),
      });
    }
  }

  if (input.outputs.includes("docx")) {
    const docxPath = outputPath(input.workspaceRoot, `${input.filenameBase}.docx`);
    if (input.docxMode === "fixed_layout") {
      if (!pdfRender) throw new Error("fixed_layout DOCX requires PDF render screenshots");
      await writeFixedLayoutDocxFromPngPages(docxPath.absPath, pdfRender.screenshots, input.page);
    } else {
      await writeEditableDocxFromCanonicalMarkdown(docxPath.absPath, document, renderOptions);
    }
    files.push({
      format: "docx",
      workspacePath: docxPath.workspacePath,
      filename: path.basename(docxPath.workspacePath),
      mimeType: mimeType("docx"),
    });
  }

  const qa: CanonicalMarkdownQa = {
    status: warnings.length > 0 ? "passed_with_warnings" : "passed",
    sourceHash: document.sourceHash,
    rendererVersion,
    warnings,
    ...(pageCount ? { pageCount } : {}),
  };
  const qaPath = outputPath(input.workspaceRoot, `${input.filenameBase}.export-qa.json`);
  await writeFileEnsuringDir(qaPath.absPath, `${JSON.stringify(qa, null, 2)}\n`);
  return { files, qa };
}
