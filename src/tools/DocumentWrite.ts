import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OutputArtifactRegistry } from "../output/OutputArtifactRegistry.js";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { errorResult } from "../util/toolResult.js";
import { markdownToStructuredBlocks, writeDocxFromBlocks, type StructuredBlock } from "./document/docxDriver.js";
import { renderMarkdownToHtml } from "./document/htmlDriver.js";
import { writeHwpxFromBlocks, type HwpxTemplate } from "./document/hwpxDriver.js";

export interface DocumentWriteInput {
  mode: "create" | "edit";
  format: "html" | "docx" | "hwpx";
  title: string;
  filename: string;
  template?: HwpxTemplate;
  source:
    | { kind: "markdown"; content: string }
    | { kind: "structured"; blocks: StructuredBlock[] };
}

type NormalizedDocumentSource =
  | { kind: "markdown"; content: string }
  | { kind: "structured"; blocks: StructuredBlock[] };

export interface DocumentWriteOutput {
  artifactId: string;
  workspacePath: string;
  filename: string;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    mode: { type: "string", enum: ["create", "edit"] },
    format: { type: "string", enum: ["html", "docx", "hwpx"] },
    title: { type: "string" },
    filename: { type: "string" },
    template: { type: "string", enum: ["base", "gonmun", "report", "minutes"] },
    source: { type: "object" },
  },
  required: ["mode", "format", "title", "filename", "source"],
} as const;

function basename(filePath: string): string {
  return filePath.split("/").pop() || filePath;
}

function normalizeSource(source: DocumentWriteInput["source"] | Record<string, unknown>): NormalizedDocumentSource {
  const raw = source as Record<string, unknown>;
  const kind = typeof raw.kind === "string"
    ? raw.kind
    : typeof raw.type === "string"
      ? raw.type
      : undefined;

  if (kind === "markdown" && typeof raw.content === "string") {
    return { kind, content: raw.content };
  }
  if (kind === "structured" && Array.isArray(raw.blocks)) {
    return { kind, blocks: raw.blocks as StructuredBlock[] };
  }
  throw new Error(`unsupported source: ${kind ?? "undefined"}`);
}

async function maybeCreateHwpxReferenceCopy(
  workspaceRoot: string,
  input: DocumentWriteInput,
): Promise<string | null> {
  if (input.mode !== "edit" || input.format !== "hwpx") {
    return null;
  }
  const sourcePath = path.join(workspaceRoot, input.filename);
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

export function makeDocumentWriteTool(
  workspaceRoot: string,
  outputRegistry: OutputArtifactRegistry,
): Tool<DocumentWriteInput, DocumentWriteOutput> {
  return {
    name: "DocumentWrite",
    description:
      "Create or edit user-facing HTML and DOCX documents inside the bot workspace and register the result as an output artifact.",
    inputSchema: INPUT_SCHEMA,
    permission: "write",
    validate(input) {
      if (!input || (input.mode !== "create" && input.mode !== "edit")) {
        return "`mode` must be create or edit";
      }
      if (input.format !== "html" && input.format !== "docx" && input.format !== "hwpx") {
        return "`format` must be html, docx, or hwpx";
      }
      if (typeof input.title !== "string" || input.title.trim().length === 0) {
        return "`title` is required";
      }
      if (typeof input.filename !== "string" || input.filename.trim().length === 0) {
        return "`filename` is required";
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
        const source = normalizeSource(input.source);
        const absPath = path.join(workspaceRoot, input.filename);
        await fs.mkdir(path.dirname(absPath), { recursive: true });
        referencePath = await maybeCreateHwpxReferenceCopy(workspaceRoot, input);

        if (input.format === "html" && source.kind === "markdown") {
          await fs.writeFile(absPath, renderMarkdownToHtml(source.content), "utf8");
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
        } else {
          throw new Error(`unsupported combination: ${input.format}/${source.kind}`);
        }

        const artifact = await outputRegistry.register({
          sessionKey: ctx.sessionKey,
          turnId: ctx.turnId,
          kind: "document",
          format: input.format,
          title: input.title,
          filename: basename(input.filename),
          mimeType:
            input.format === "html"
              ? "text/html"
              : input.format === "docx"
                ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                : "application/hwp+zip",
          workspacePath: input.filename,
          previewKind: input.format === "html" ? "inline-html" : "download-only",
          createdByTool: "DocumentWrite",
          sourceKind: source.kind,
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
      } finally {
        if (referencePath) {
          await fs.rm(path.dirname(referencePath), { recursive: true, force: true });
        }
      }
    },
  };
}
