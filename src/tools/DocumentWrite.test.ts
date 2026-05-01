import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { OutputArtifactRegistry } from "../output/OutputArtifactRegistry.js";
import { makeDocumentWriteTool } from "./DocumentWrite.js";
import type { ToolContext } from "../Tool.js";

const roots: string[] = [];

function ctx(root: string): ToolContext {
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
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "document-write-"));
  roots.push(root);
  return root;
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("DocumentWrite", () => {
  it("stores generated documents under outputs when callers provide a bare filename", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const result = await tool.execute(
      {
        mode: "create",
        format: "md",
        title: "Generated Memo",
        filename: "generated-memo.md",
        source: "# Generated Memo\n\nVisible in workspace outputs.",
      } as never,
      ctx(root),
    );

    expect(result.status).toBe("ok");
    await expect(fs.readFile(path.join(root, "outputs", "generated-memo.md"), "utf8"))
      .resolves.toBe("# Generated Memo\n\nVisible in workspace outputs.\n");
    await expect(fs.access(path.join(root, "generated-memo.md"))).rejects.toThrow();

    const artifact = await registry.get(result.output!.artifactId);
    expect(result.output).toMatchObject({
      workspacePath: "outputs/generated-memo.md",
      filename: "generated-memo.md",
    });
    expect(artifact).toMatchObject({
      workspacePath: "outputs/generated-memo.md",
      filename: "generated-memo.md",
    });
  });

  it("declares explicit source schema variants for upstream tool callers", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const schema = JSON.stringify(tool.inputSchema);
    expect(schema).toContain("\"source\"");
    expect(schema).toContain("\"anyOf\"");
    expect(schema).toContain("\"string\"");
    expect(schema).toContain("\"content\"");
    expect(schema).toContain("\"blocks\"");
  });

  it("creates html from markdown and exposes inline preview", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const result = await tool.execute(
      {
        mode: "create",
        format: "html",
        title: "Board Update",
        filename: "exports/board-update.html",
        source: {
          kind: "markdown",
          content: "# Board Update\n\n- Revenue up\n- Burn down",
        },
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    const html = await fs.readFile(path.join(root, "outputs", "exports", "board-update.html"), "utf8");
    expect(html).toContain("<h1>Board Update</h1>");
    expect(html).toContain("<li>Revenue up</li>");

    const artifact = await registry.get(result.output!.artifactId);
    expect(artifact.previewKind).toBe("inline-html");
  });

  it("creates docx, then edits the same file in place", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const created = await tool.execute(
      {
        mode: "create",
        format: "docx",
        title: "Investor Memo",
        filename: "exports/investor-memo.docx",
        source: {
          kind: "structured",
          blocks: [
            { type: "heading", level: 1, text: "Investor Memo" },
            { type: "paragraph", text: "This document was generated inside the bot pod." },
          ],
        },
      },
      ctx(root),
    );

    expect(created.status).toBe("ok");

    const edited = await tool.execute(
      {
        mode: "edit",
        format: "docx",
        title: "Investor Memo",
        filename: "exports/investor-memo.docx",
        source: {
          kind: "structured",
          blocks: [
            { type: "heading", level: 1, text: "Investor Memo" },
            { type: "paragraph", text: "Updated inside the bot pod." },
          ],
        },
      },
      ctx(root),
    );

    expect(edited.status).toBe("ok");

    const bytes = await fs.readFile(path.join(root, "outputs", "exports", "investor-memo.docx"));
    expect(bytes.subarray(0, 2).toString()).toBe("PK");

    const artifact = await registry.get(edited.output!.artifactId);
    expect(artifact).toMatchObject({
      format: "docx",
      filename: "investor-memo.docx",
      previewKind: "download-only",
    });
  });

  it("creates docx from markdown source", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const result = await tool.execute(
      {
        mode: "create",
        format: "docx",
        title: "한글 보고서",
        filename: "exports/korean-report.docx",
        source: {
          kind: "markdown",
          content: "# 한글 보고서\n\n## 요약\n\n본문입니다.",
        },
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    const bytes = await fs.readFile(path.join(root, "outputs", "exports", "korean-report.docx"));
    expect(bytes.subarray(0, 2).toString()).toBe("PK");

    const artifact = await registry.get(result.output!.artifactId);
    expect(artifact).toMatchObject({
      format: "docx",
      sourceKind: "markdown",
      filename: "korean-report.docx",
    });
  });

  it("uses the agentic writer for docx when available", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry, {
      agenticWriter: async ({ absPath, format, sourceMarkdown }) => {
        expect(format).toBe("docx");
        expect(sourceMarkdown).toContain("# Agentic Memo");
        await fs.writeFile(absPath, Buffer.from("agentic-docx"));
        return { mode: "agentic", turns: 2, toolCallCount: 3 };
      },
    });

    const result = await tool.execute(
      {
        mode: "create",
        format: "docx",
        title: "Agentic Memo",
        filename: "exports/agentic-memo.docx",
        source: "# Agentic Memo\n\nCreate a polished board memo.",
      } as never,
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.metadata).toMatchObject({
      documentWriteMode: "agentic",
      agenticTurns: 2,
      agenticToolCallCount: 3,
    });
    const bytes = await fs.readFile(path.join(root, "outputs", "exports", "agentic-memo.docx"));
    expect(bytes.toString("utf8")).toBe("agentic-docx");
  });

  it("falls back to the fast docx renderer when agentic writing fails", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry, {
      agenticWriter: async () => {
        throw new Error("agentic loop failed");
      },
    });

    const result = await tool.execute(
      {
        mode: "create",
        format: "docx",
        title: "Fallback Memo",
        filename: "exports/fallback-memo.docx",
        source: "# Fallback Memo\n\nStill produce a usable file.",
      } as never,
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.metadata).toMatchObject({
      documentWriteMode: "fast_fallback",
      agenticError: "agentic loop failed",
    });
    const bytes = await fs.readFile(path.join(root, "outputs", "exports", "fallback-memo.docx"));
    expect(bytes.subarray(0, 2).toString()).toBe("PK");
  });

  it("creates pdf by authoring docx agentically and converting it deterministically", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry, {
      agenticWriter: async ({ absPath, format, sourceMarkdown }) => {
        expect(format).toBe("docx");
        expect(absPath.endsWith(".docx")).toBe(true);
        expect(sourceMarkdown).toContain("| Metric | Value |");
        await fs.writeFile(absPath, Buffer.from("PK agentic docx with tables"));
        return { mode: "agentic", turns: 4, toolCallCount: 7, model: "test-model" };
      },
      docxToPdfConverter: async ({ docxPath, pdfPath }) => {
        const docx = await fs.readFile(docxPath);
        expect(docx.subarray(0, 2).toString()).toBe("PK");
        await fs.writeFile(pdfPath, Buffer.from("%PDF-agentic-table-layout"));
      },
    } as never);

    const result = await tool.execute(
      {
        mode: "create",
        format: "pdf",
        title: "Metrics Report",
        filename: "exports/metrics-report.pdf",
        source: [
          "# Metrics Report",
          "",
          "| Metric | Value |",
          "| --- | --- |",
          "| ARR | $1.2M |",
        ].join("\n"),
      } as never,
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.metadata).toMatchObject({
      documentWriteMode: "agentic_docx_pdf",
      agenticIntermediateFormat: "docx",
      agenticTurns: 4,
      agenticToolCallCount: 7,
      pdfConversionMode: "docx_to_pdf",
    });
    const bytes = await fs.readFile(path.join(root, "outputs", "exports", "metrics-report.pdf"));
    expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
  });

  it("does not fall back to the fast pdf renderer when docx-to-pdf conversion fails", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry, {
      agenticWriter: async ({ absPath }) => {
        await fs.writeFile(absPath, Buffer.from("PK valid intermediate docx"));
        return { mode: "agentic", turns: 1, toolCallCount: 1 };
      },
      docxToPdfConverter: async () => {
        throw new Error("libreoffice failed");
      },
    } as never);

    const result = await tool.execute(
      {
        mode: "create",
        format: "pdf",
        title: "Fallback PDF",
        filename: "exports/fallback.pdf",
        source: "# Fallback PDF\n\nStill generate a usable PDF.",
      } as never,
      ctx(root),
    );

    expect(result.status).toBe("error");
    expect(result.errorMessage).toContain("DOCX to PDF conversion failed");
    await expect(fs.access(path.join(root, "outputs", "exports", "fallback.pdf"))).rejects.toThrow();
  });

  it("accepts source.type as a compatibility alias for source.kind", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const result = await tool.execute(
      {
        mode: "create",
        format: "docx",
        title: "Compatibility Memo",
        filename: "exports/compatibility-memo.docx",
        source: {
          type: "markdown",
          content: "# Compatibility Memo\n\nGenerated from the legacy alias.",
        } as never,
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    const bytes = await fs.readFile(path.join(root, "outputs", "exports", "compatibility-memo.docx"));
    expect(bytes.subarray(0, 2).toString()).toBe("PK");
  });

  it("accepts markdown source.path as a workspace-relative source file", async () => {
    const root = await makeRoot();
    await fs.mkdir(path.join(root, "reports"), { recursive: true });
    await fs.writeFile(
      path.join(root, "reports", "full-report.md"),
      "# Full Report\n\nGenerated from an existing markdown file.",
      "utf8",
    );
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const result = await tool.execute(
      {
        mode: "create",
        format: "docx",
        title: "Full Report",
        filename: "exports/full-report.docx",
        source: {
          type: "markdown",
          path: "reports/full-report.md",
        },
      } as never,
      ctx(root),
    );

    expect(result.status).toBe("ok");
    const bytes = await fs.readFile(path.join(root, "outputs", "exports", "full-report.docx"));
    expect(bytes.subarray(0, 2).toString()).toBe("PK");

    const artifact = await registry.get(result.output!.artifactId);
    expect(artifact).toMatchObject({
      format: "docx",
      sourceKind: "markdown",
      filename: "full-report.docx",
    });
  });

  it("accepts structured blocksFile as a workspace-relative JSON source file", async () => {
    const root = await makeRoot();
    await fs.mkdir(path.join(root, "scripts"), { recursive: true });
    await fs.writeFile(
      path.join(root, "scripts", "blocks.json"),
      JSON.stringify([
        { type: "heading", level: 1, text: "Structured Report" },
        { type: "paragraph", text: "Generated from blocksFile." },
      ]),
      "utf8",
    );
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const result = await tool.execute(
      {
        mode: "create",
        format: "md",
        title: "Structured Report",
        filename: "exports/structured-report.md",
        source: {
          kind: "structured",
          blocksFile: "scripts/blocks.json",
        },
      } as never,
      ctx(root),
    );

    expect(result.status).toBe("ok");
    const markdown = await fs.readFile(path.join(root, "outputs", "exports", "structured-report.md"), "utf8");
    expect(markdown).toBe("# Structured Report\n\nGenerated from blocksFile.\n");
  });

  it("accepts raw string source as markdown content", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const result = await tool.execute(
      {
        mode: "create",
        format: "md",
        title: "String Source Memo",
        filename: "exports/string-source.md",
        source: "# String Source Memo\n\nGenerated from a direct source string.",
      } as never,
      ctx(root),
    );

    expect(result.status).toBe("ok");
    const markdown = await fs.readFile(path.join(root, "outputs", "exports", "string-source.md"), "utf8");
    expect(markdown).toBe("# String Source Memo\n\nGenerated from a direct source string.\n");
  });

  it("infers markdown source from a content-only object", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry, {
      docxToPdfConverter: async ({ docxPath, pdfPath }) => {
        const docx = await fs.readFile(docxPath);
        expect(docx.subarray(0, 2).toString()).toBe("PK");
        await fs.writeFile(pdfPath, Buffer.from("%PDF-from-docx"));
      },
    });

    const result = await tool.execute(
      {
        mode: "create",
        format: "pdf",
        title: "Content Source Memo",
        filename: "exports/content-source.pdf",
        source: {
          content: "# Content Source Memo\n\nGenerated without an explicit source kind.",
        },
      } as never,
      ctx(root),
    );

    expect(result.status).toBe("ok");
    const bytes = await fs.readFile(path.join(root, "outputs", "exports", "content-source.pdf"));
    expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
  });

  it("creates hwpx from structured blocks and registers a downloadable artifact", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const result = await tool.execute(
      {
        mode: "create",
        format: "hwpx",
        title: "주간 회의록",
        filename: "exports/weekly-minutes.hwpx",
        template: "minutes",
        source: {
          kind: "structured",
          blocks: [
            { type: "heading", level: 1, text: "주간 회의록" },
            { type: "paragraph", text: "안건 1. 출력 아티팩트 전달" },
          ],
        },
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");

    const bytes = await fs.readFile(path.join(root, "outputs", "exports", "weekly-minutes.hwpx"));
    expect(bytes.subarray(0, 2).toString()).toBe("PK");

    const artifact = await registry.get(result.output!.artifactId);
    expect(artifact).toMatchObject({
      format: "hwpx",
      filename: "weekly-minutes.hwpx",
      previewKind: "download-only",
    });
  });

  it("creates hwpx from markdown source instead of rejecting the ordinary document-writer path", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const result = await tool.execute(
      {
        mode: "create",
        format: "hwpx",
        title: "한글 보고서",
        filename: "exports/korean-report.hwpx",
        template: "report",
        source: "# 한글 보고서\n\n## 요약\n\n본문입니다.",
      } as never,
      ctx(root),
    );

    expect(result.status).toBe("ok");
    const bytes = await fs.readFile(path.join(root, "outputs", "exports", "korean-report.hwpx"));
    expect(bytes.subarray(0, 2).toString()).toBe("PK");

    const artifact = await registry.get(result.output!.artifactId);
    expect(artifact).toMatchObject({
      format: "hwpx",
      sourceKind: "markdown",
      filename: "korean-report.hwpx",
    });
  });

  it("uses the agentic writer for hwpx creation when available", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    let calls = 0;
    const tool = makeDocumentWriteTool(root, registry, {
      agenticWriter: async (input) => {
        calls += 1;
        expect(input.format).toBe("hwpx");
        expect(input.mode).toBe("create");
        expect(input.template).toBe("report");
        expect(input.sourceMarkdown).toContain("지표 | 값");
        expect(input.referencePath).toBeUndefined();
        await fs.writeFile(input.absPath, Buffer.from("PK agentic hwpx"));
        return { mode: "agentic", turns: 4, toolCallCount: 6, model: "test-model" };
      },
    });

    const result = await tool.execute(
      {
        mode: "create",
        format: "hwpx",
        title: "투자 보고서",
        filename: "exports/agentic-report.hwpx",
        template: "report",
        source: "# 투자 보고서\n\n지표 | 값\n매출 | 2억",
      } as never,
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.metadata).toMatchObject({
      documentWriteMode: "agentic",
      agenticTurns: 4,
      agenticToolCallCount: 6,
      agenticModel: "test-model",
    });
    expect(calls).toBe(1);
    const bytes = await fs.readFile(path.join(root, result.output!.workspacePath));
    expect(bytes.toString("utf8")).toBe("PK agentic hwpx");
  });

  it("passes an existing hwpx file as reference context for agentic edit flows", async () => {
    const root = await makeRoot();
    await fs.mkdir(path.join(root, "outputs", "exports"), { recursive: true });
    await fs.writeFile(path.join(root, "outputs", "exports", "minutes.hwpx"), Buffer.from("PK reference hwpx"));
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry, {
      agenticWriter: async (input) => {
        expect(input.format).toBe("hwpx");
        const referencePath = (input as { referencePath?: string }).referencePath;
        expect(referencePath).toBeTruthy();
        await expect(fs.readFile(referencePath!, "utf8")).resolves.toBe("PK reference hwpx");
        await fs.writeFile(input.absPath, Buffer.from("PK edited hwpx"));
        return { mode: "agentic", turns: 3, toolCallCount: 5 };
      },
    });

    const result = await tool.execute(
      {
        mode: "edit",
        format: "hwpx",
        title: "회의록",
        filename: "exports/minutes.hwpx",
        source: "# 회의록\n\n기존 양식을 유지하고 본문만 교체합니다.",
      } as never,
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.metadata).toMatchObject({
      documentWriteMode: "agentic",
      agenticTurns: 3,
      agenticToolCallCount: 5,
    });
  });

  it("creates markdown from structured blocks and exposes inline markdown preview", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const result = await tool.execute(
      {
        mode: "create",
        format: "md",
        title: "Investment Notes",
        filename: "exports/investment-notes.md",
        source: {
          kind: "structured",
          blocks: [
            { type: "heading", level: 1, text: "Investment Notes" },
            { type: "paragraph", text: "Revenue quality is improving." },
          ],
        },
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    const markdown = await fs.readFile(path.join(root, "outputs", "exports", "investment-notes.md"), "utf8");
    expect(markdown).toBe("# Investment Notes\n\nRevenue quality is improving.\n");

    const artifact = await registry.get(result.output!.artifactId);
    expect(artifact).toMatchObject({
      format: "md",
      mimeType: "text/markdown",
      filename: "investment-notes.md",
      previewKind: "inline-markdown",
    });
  });

  it("creates plain text from markdown source", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeDocumentWriteTool(root, registry);

    const result = await tool.execute(
      {
        mode: "create",
        format: "txt",
        title: "Summary",
        filename: "exports/summary.txt",
        source: {
          kind: "markdown",
          content: "# Summary\n\n- First point\n- Second point",
        },
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    const text = await fs.readFile(path.join(root, "outputs", "exports", "summary.txt"), "utf8");
    expect(text).toBe("Summary\n\nFirst point\nSecond point\n");

    const artifact = await registry.get(result.output!.artifactId);
    expect(artifact).toMatchObject({
      format: "txt",
      mimeType: "text/plain",
      filename: "summary.txt",
      previewKind: "download-only",
    });
  });

  it("creates pdf from markdown source by first creating an intermediate docx", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    let converterSawDocx = false;
    const tool = makeDocumentWriteTool(root, registry, {
      docxToPdfConverter: async ({ docxPath, pdfPath }) => {
        const docx = await fs.readFile(docxPath);
        expect(docx.subarray(0, 2).toString()).toBe("PK");
        converterSawDocx = true;
        await fs.writeFile(pdfPath, Buffer.from("%PDF-from-docx"));
      },
    });

    const result = await tool.execute(
      {
        mode: "create",
        format: "pdf",
        title: "Investment Report",
        filename: "exports/investment-report.pdf",
        source: {
          kind: "markdown",
          content: "# Investment Report\n\n## Verdict\n\nStrong pass.",
        },
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(converterSawDocx).toBe(true);
    expect(result.metadata).toMatchObject({
      documentWriteMode: "fast_docx_pdf",
      agenticIntermediateFormat: "docx",
      pdfConversionMode: "docx_to_pdf",
    });
    const bytes = await fs.readFile(path.join(root, "outputs", "exports", "investment-report.pdf"));
    expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");

    const artifact = await registry.get(result.output!.artifactId);
    expect(artifact).toMatchObject({
      format: "pdf",
      mimeType: "application/pdf",
      filename: "investment-report.pdf",
      previewKind: "download-only",
    });
  });
});
