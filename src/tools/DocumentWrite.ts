import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { resolveGeneratedOutputPath } from "../output/generatedOutputPath.js";
import type { OutputArtifactRegistry } from "../output/OutputArtifactRegistry.js";
import { Workspace } from "../storage/Workspace.js";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { errorResult } from "../util/toolResult.js";
import { convertDocxToPdf, type DocxToPdfConverter } from "./document/docxToPdfDriver.js";
import { markdownToStructuredBlocks, writeDocxFromBlocks, type StructuredBlock } from "./document/docxDriver.js";
import { renderMarkdownToHtml } from "./document/htmlDriver.js";
import { writeHwpxFromBlocks, type HwpxTemplate } from "./document/hwpxDriver.js";
import {
  writeDocumentAgentically,
  type AgenticDocumentAuthorDeps,
  type AgenticDocumentWriter,
} from "./document/agenticAuthor.js";
import {
  markdownToPlainText,
  structuredBlocksToMarkdown,
  structuredBlocksToPlainText,
  writeTextFile,
} from "./document/textDriver.js";

type DocumentOutputFormat = "html" | "docx" | "hwpx" | "md" | "txt" | "pdf";
type MarkdownSourceKind = "markdown" | "text" | "plain_text";
type DocumentWriteSourceInput =
  | string
  | {
      kind?: MarkdownSourceKind;
      type?: MarkdownSourceKind;
      content?: string;
      markdown?: string;
      text?: string;
      path?: string;
    }
  | {
      kind?: "structured";
      type?: "structured";
      blocks?: StructuredBlock[];
      blocksFile?: string;
    };

const SUPPORTED_FORMATS: readonly DocumentOutputFormat[] = [
  "html",
  "docx",
  "hwpx",
  "md",
  "txt",
  "pdf",
];

export interface DocumentWriteInput {
  mode: "create" | "edit";
  format: DocumentOutputFormat;
  title: string;
  filename: string;
  template?: HwpxTemplate;
  source: DocumentWriteSourceInput;
}

type NormalizedDocumentSource =
  | { kind: "markdown"; content: string }
  | { kind: "structured"; blocks: StructuredBlock[] };

export interface DocumentWriteOutput {
  artifactId: string;
  workspacePath: string;
  filename: string;
}

export interface DocumentWriteDeps {
  agenticWriter?: AgenticDocumentWriter;
  agentic?: AgenticDocumentAuthorDeps;
  docxToPdfConverter?: DocxToPdfConverter;
}

const STRUCTURED_BLOCK_SCHEMA = {
  type: "object",
  properties: {
    type: { type: "string", enum: ["heading", "paragraph"] },
    text: { type: "string" },
    level: { type: "number", enum: [1, 2, 3] },
  },
  required: ["type", "text"],
  additionalProperties: false,
} as const;

const SOURCE_SCHEMA = {
  anyOf: [
    {
      type: "string",
      description: "Markdown or plain text document content.",
    },
    {
      type: "object",
      properties: {
        kind: { type: "string", enum: ["markdown", "text", "plain_text"] },
        type: { type: "string", enum: ["markdown", "text", "plain_text"] },
        content: { type: "string" },
        markdown: { type: "string" },
        text: { type: "string" },
        path: {
          type: "string",
          description: "Workspace-relative markdown/text file path to read as source content.",
        },
      },
      anyOf: [
        { required: ["content"] },
        { required: ["markdown"] },
        { required: ["text"] },
        { required: ["path"] },
      ],
      additionalProperties: false,
    },
    {
      type: "object",
      properties: {
        kind: { type: "string", enum: ["structured"] },
        type: { type: "string", enum: ["structured"] },
        blocks: {
          type: "array",
          items: STRUCTURED_BLOCK_SCHEMA,
        },
        blocksFile: {
          type: "string",
          description: "Workspace-relative JSON file containing an array of structured blocks or an object with a blocks array.",
        },
      },
      anyOf: [
        { required: ["blocks"] },
        { required: ["blocksFile"] },
      ],
      additionalProperties: false,
    },
  ],
} as const;

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    mode: { type: "string", enum: ["create", "edit"] },
    format: { type: "string", enum: SUPPORTED_FORMATS },
    title: { type: "string" },
    filename: { type: "string" },
    template: { type: "string", enum: ["base", "gonmun", "report", "minutes"] },
    source: SOURCE_SCHEMA,
  },
  required: ["mode", "format", "title", "filename", "source"],
  additionalProperties: false,
} as const;

function basename(filePath: string): string {
  return filePath.split("/").pop() || filePath;
}

function isDocumentOutputFormat(format: unknown): format is DocumentOutputFormat {
  return typeof format === "string" && SUPPORTED_FORMATS.includes(format as DocumentOutputFormat);
}

function mimeTypeFor(format: DocumentOutputFormat): string {
  switch (format) {
    case "html":
      return "text/html";
    case "docx":
      return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    case "hwpx":
      return "application/hwp+zip";
    case "md":
      return "text/markdown";
    case "txt":
      return "text/plain";
    case "pdf":
      return "application/pdf";
  }
}

function previewKindFor(format: DocumentOutputFormat): "inline-html" | "inline-markdown" | "download-only" {
  if (format === "html") return "inline-html";
  if (format === "md") return "inline-markdown";
  return "download-only";
}

function isMarkdownSourceKind(kind: string | undefined): kind is MarkdownSourceKind | undefined {
  return kind === undefined || kind === "markdown" || kind === "text" || kind === "plain_text";
}

function firstStringField(raw: Record<string, unknown>, fields: readonly string[]): string | null {
  for (const field of fields) {
    const value = raw[field];
    if (typeof value === "string") return value;
  }
  return null;
}

function kindOf(raw: Record<string, unknown>): string | undefined {
  return typeof raw.kind === "string"
    ? raw.kind
    : typeof raw.type === "string"
      ? raw.type
      : undefined;
}

function normalizeInlineSource(source: DocumentWriteInput["source"]): NormalizedDocumentSource {
  if (typeof source === "string") {
    return { kind: "markdown", content: source };
  }
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    throw new Error("source must be a markdown string or an object with content or blocks");
  }

  const raw = source as Record<string, unknown>;
  const kind = kindOf(raw);

  const content = firstStringField(raw, ["content", "markdown", "text"]);
  if (content !== null && isMarkdownSourceKind(kind)) {
    return { kind: "markdown", content };
  }
  if ((kind === undefined || kind === "structured") && Array.isArray(raw.blocks)) {
    return { kind: "structured", blocks: raw.blocks as StructuredBlock[] };
  }
  if (kind === "structured") {
    throw new Error("source.blocks must be an array or source.blocksFile must be a string for structured source");
  }
  if (isMarkdownSourceKind(kind)) {
    throw new Error("source.content or source.path must be a string for markdown source");
  }
  throw new Error(`unsupported source: ${kind ?? "undefined"}`);
}

function validateSourceShape(source: DocumentWriteInput["source"]): string | null {
  if (typeof source === "string") return null;
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    return "source must be a markdown string or an object with content, path, blocks, or blocksFile";
  }
  const raw = source as Record<string, unknown>;
  const kind = kindOf(raw);
  if (typeof raw.path === "string" && isMarkdownSourceKind(kind)) return null;
  if (typeof raw.blocksFile === "string" && (kind === undefined || kind === "structured")) return null;
  try {
    normalizeInlineSource(source);
    return null;
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
}

async function readMarkdownSourcePath(
  workspaceRoot: string,
  relPath: string,
): Promise<NormalizedDocumentSource> {
  const workspace = new Workspace(workspaceRoot);
  const content = await workspace.readFile(relPath);
  return { kind: "markdown", content };
}

function parseStructuredBlocksFile(rawJson: string, relPath: string): StructuredBlock[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawJson);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`source.blocksFile is not valid JSON (${relPath}): ${message}`);
  }
  const blocks = Array.isArray(parsed)
    ? parsed
    : parsed && typeof parsed === "object" && Array.isArray((parsed as { blocks?: unknown }).blocks)
      ? (parsed as { blocks: unknown[] }).blocks
      : null;
  if (!blocks) {
    throw new Error("source.blocksFile must contain a JSON array or an object with a blocks array");
  }
  return blocks as StructuredBlock[];
}

async function readStructuredBlocksFile(
  workspaceRoot: string,
  relPath: string,
): Promise<NormalizedDocumentSource> {
  const workspace = new Workspace(workspaceRoot);
  const content = await workspace.readFile(relPath);
  return { kind: "structured", blocks: parseStructuredBlocksFile(content, relPath) };
}

async function normalizeSource(
  source: DocumentWriteInput["source"],
  workspaceRoot: string,
): Promise<NormalizedDocumentSource> {
  if (source && typeof source === "object" && !Array.isArray(source)) {
    const raw = source as Record<string, unknown>;
    const kind = kindOf(raw);
    if (typeof raw.path === "string" && isMarkdownSourceKind(kind)) {
      return readMarkdownSourcePath(workspaceRoot, raw.path);
    }
    if (typeof raw.blocksFile === "string" && (kind === undefined || kind === "structured")) {
      return readStructuredBlocksFile(workspaceRoot, raw.blocksFile);
    }
  }
  return normalizeInlineSource(source);
}

async function maybeCreateHwpxReferenceCopy(
  workspaceRoot: string,
  input: DocumentWriteInput,
  workspacePath: string,
): Promise<string | null> {
  if (input.mode !== "edit" || input.format !== "hwpx") {
    return null;
  }
  const sourcePath = path.join(workspaceRoot, workspacePath);
  try {
    await fs.access(sourcePath);
  } catch {
    return null;
  }

  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "document-write-hwpx-ref-"));
  const referencePath = path.join(tempRoot, path.basename(input.filename));
  await fs.copyFile(sourcePath, referencePath);
  return referencePath;
}

function sourceToMarkdown(source: NormalizedDocumentSource): string {
  return source.kind === "markdown"
    ? source.content
    : structuredBlocksToMarkdown(source.blocks);
}

function sourceToStructuredBlocks(source: NormalizedDocumentSource): StructuredBlock[] {
  return source.kind === "structured"
    ? source.blocks
    : markdownToStructuredBlocks(source.content);
}

async function writeDocumentFast(
  input: DocumentWriteInput,
  source: NormalizedDocumentSource,
  absPath: string,
  referencePath: string | null,
): Promise<void> {
  if (input.format === "html" && source.kind === "markdown") {
    await fs.writeFile(absPath, renderMarkdownToHtml(source.content), "utf8");
  } else if (input.format === "html" && source.kind === "structured") {
    await fs.writeFile(absPath, renderMarkdownToHtml(structuredBlocksToMarkdown(source.blocks)), "utf8");
  } else if (input.format === "docx" && source.kind === "structured") {
    await writeDocxFromBlocks(absPath, source.blocks);
  } else if (input.format === "docx" && source.kind === "markdown") {
    await writeDocxFromBlocks(absPath, markdownToStructuredBlocks(source.content));
  } else if (input.format === "hwpx" && source.kind === "structured") {
    await writeHwpxFromBlocks({
      absPath,
      title: input.title,
      template: input.template,
      blocks: source.blocks,
      referencePath: referencePath ?? undefined,
    });
  } else if (input.format === "hwpx" && source.kind === "markdown") {
    await writeHwpxFromBlocks({
      absPath,
      title: input.title,
      template: input.template,
      blocks: markdownToStructuredBlocks(source.content),
      referencePath: referencePath ?? undefined,
    });
  } else if (input.format === "md" && source.kind === "structured") {
    await writeTextFile(absPath, structuredBlocksToMarkdown(source.blocks));
  } else if (input.format === "md" && source.kind === "markdown") {
    await writeTextFile(absPath, source.content);
  } else if (input.format === "txt" && source.kind === "structured") {
    await writeTextFile(absPath, structuredBlocksToPlainText(source.blocks));
  } else if (input.format === "txt" && source.kind === "markdown") {
    await writeTextFile(absPath, markdownToPlainText(source.content));
  } else {
    throw new Error(`unsupported combination: ${input.format}/${source.kind}`);
  }
}

function messageForError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function writePdfViaDocx(
  input: DocumentWriteInput,
  source: NormalizedDocumentSource,
  absPath: string,
  workspacePath: string,
  workspaceRoot: string,
  ctx: ToolContext,
  agenticWriter: AgenticDocumentWriter | undefined,
  docxToPdfConverter: DocxToPdfConverter,
): Promise<Record<string, unknown>> {
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "document-write-pdf-docx-"));
  const docxPath = path.join(tempRoot, "intermediate.docx");
  try {
    const intermediateFilename = workspacePath.replace(/\.pdf$/i, ".docx");
    let writeMetadata: Record<string, unknown>;
    if (agenticWriter) {
      try {
        const agenticResult = await agenticWriter({
          format: "docx",
          mode: input.mode,
          title: input.title,
          filename: intermediateFilename === workspacePath ? `${workspacePath}.docx` : intermediateFilename,
          absPath: docxPath,
          workspaceRoot,
          sourceMarkdown: sourceToMarkdown(source),
          ctx,
        });
        writeMetadata = {
          documentWriteMode: "agentic_docx_pdf",
          agenticIntermediateFormat: "docx",
          agenticTurns: agenticResult.turns,
          agenticToolCallCount: agenticResult.toolCallCount,
          ...(agenticResult.model ? { agenticModel: agenticResult.model } : {}),
        };
      } catch (error) {
        await writeDocxFromBlocks(docxPath, sourceToStructuredBlocks(source));
        writeMetadata = {
          documentWriteMode: "fast_docx_pdf",
          agenticIntermediateFormat: "docx",
          agenticError: messageForError(error),
        };
      }
    } else {
      await writeDocxFromBlocks(docxPath, sourceToStructuredBlocks(source));
      writeMetadata = {
        documentWriteMode: "fast_docx_pdf",
        agenticIntermediateFormat: "docx",
      };
    }

    try {
      await docxToPdfConverter({
        docxPath,
        pdfPath: absPath,
        abortSignal: ctx.abortSignal,
      });
    } catch (error) {
      await fs.rm(absPath, { force: true });
      throw new Error(`DOCX to PDF conversion failed: ${messageForError(error)}`);
    }
    return {
      ...writeMetadata,
      pdfConversionMode: "docx_to_pdf",
    };
  } finally {
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
}

export function makeDocumentWriteTool(
  workspaceRoot: string,
  outputRegistry: OutputArtifactRegistry,
  deps: DocumentWriteDeps = {},
): Tool<DocumentWriteInput, DocumentWriteOutput> {
  return {
    name: "DocumentWrite",
    description:
      "Create or edit user-facing md, txt, html, pdf, docx, and hwpx documents inside the bot workspace and register the result as an output artifact. DOCX/HWPX use an agentic authoring loop when available. PDF is always produced by first authoring a DOCX intermediate, then converting DOCX to PDF; conversion failure returns an error instead of direct-rendering a degraded PDF. Source may be inline markdown/blocks or a workspace-relative path/blocksFile.",
    inputSchema: INPUT_SCHEMA,
    permission: "write",
    validate(input) {
      if (!input || (input.mode !== "create" && input.mode !== "edit")) {
        return "`mode` must be create or edit";
      }
      if (!isDocumentOutputFormat(input.format)) {
        return "`format` must be md, txt, html, pdf, docx, or hwpx";
      }
      if (typeof input.title !== "string" || input.title.trim().length === 0) {
        return "`title` is required";
      }
      if (typeof input.filename !== "string" || input.filename.trim().length === 0) {
        return "`filename` is required";
      }
      try {
        const sourceError = validateSourceShape(input.source);
        if (sourceError) return `invalid source: ${sourceError}`;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        return `invalid source: ${message}`;
      }
      return null;
    },
    async execute(
      input: DocumentWriteInput,
      ctx: ToolContext,
    ): Promise<ToolResult<DocumentWriteOutput>> {
      const start = Date.now();
      let referencePath: string | null = null;
      try {
        const source = await normalizeSource(input.source, workspaceRoot);
        const outputPath = resolveGeneratedOutputPath(input.filename);
        const absPath = path.join(workspaceRoot, outputPath.workspacePath);
        await fs.mkdir(path.dirname(absPath), { recursive: true });
        referencePath = await maybeCreateHwpxReferenceCopy(workspaceRoot, input, outputPath.workspacePath);
        let writeMetadata: Record<string, unknown> = { documentWriteMode: "fast" };
        let agenticWriter = deps.agenticWriter;
        if (!agenticWriter && deps.agentic) {
          const agenticDeps = deps.agentic;
          agenticWriter = (args) => writeDocumentAgentically(args, agenticDeps);
        }
        const docxToPdfConverter = deps.docxToPdfConverter ?? convertDocxToPdf;

        if (input.format === "pdf") {
          writeMetadata = await writePdfViaDocx(
            input,
            source,
            absPath,
            outputPath.workspacePath,
            workspaceRoot,
            ctx,
            agenticWriter,
            docxToPdfConverter,
          );
        } else if ((input.format === "docx" || input.format === "hwpx") && agenticWriter) {
          try {
            const agenticResult = await agenticWriter({
              format: input.format,
              mode: input.mode,
              title: input.title,
              filename: outputPath.workspacePath,
              absPath,
              workspaceRoot,
              sourceMarkdown: sourceToMarkdown(source),
              template: input.template,
              referencePath: input.format === "hwpx" ? referencePath ?? undefined : undefined,
              ctx,
            });
            writeMetadata = {
              documentWriteMode: "agentic",
              agenticTurns: agenticResult.turns,
              agenticToolCallCount: agenticResult.toolCallCount,
              ...(agenticResult.model ? { agenticModel: agenticResult.model } : {}),
            };
          } catch (error) {
            writeMetadata = {
              documentWriteMode: "fast_fallback",
              agenticError: messageForError(error),
            };
            await writeDocumentFast(input, source, absPath, referencePath);
          }
        } else {
          await writeDocumentFast(input, source, absPath, referencePath);
        }

        const artifact = await outputRegistry.register({
          sessionKey: ctx.sessionKey,
          turnId: ctx.turnId,
          kind: "document",
          format: input.format,
          title: input.title,
          filename: outputPath.filename,
          mimeType: mimeTypeFor(input.format),
          workspacePath: outputPath.workspacePath,
          previewKind: previewKindFor(input.format),
          createdByTool: "DocumentWrite",
          sourceKind: source.kind,
        });

        return {
          status: "ok",
          output: {
            artifactId: artifact.artifactId,
            workspacePath: outputPath.workspacePath,
            filename: outputPath.filename,
          },
          durationMs: Date.now() - start,
          metadata: writeMetadata,
        };
      } catch (error) {
        return errorResult(error, start);
      } finally {
        if (referencePath) {
          await fs.rm(path.dirname(referencePath), { recursive: true, force: true });
        }
      }
    },
  };
}
