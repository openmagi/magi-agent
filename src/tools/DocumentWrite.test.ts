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
    const html = await fs.readFile(path.join(root, "exports", "board-update.html"), "utf8");
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

    const bytes = await fs.readFile(path.join(root, "exports", "investor-memo.docx"));
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
    const bytes = await fs.readFile(path.join(root, "exports", "korean-report.docx"));
    expect(bytes.subarray(0, 2).toString()).toBe("PK");

    const artifact = await registry.get(result.output!.artifactId);
    expect(artifact).toMatchObject({
      format: "docx",
      sourceKind: "markdown",
      filename: "korean-report.docx",
    });
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
    const bytes = await fs.readFile(path.join(root, "exports", "compatibility-memo.docx"));
    expect(bytes.subarray(0, 2).toString()).toBe("PK");
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

    const bytes = await fs.readFile(path.join(root, "exports", "weekly-minutes.hwpx"));
    expect(bytes.subarray(0, 2).toString()).toBe("PK");

    const artifact = await registry.get(result.output!.artifactId);
    expect(artifact).toMatchObject({
      format: "hwpx",
      filename: "weekly-minutes.hwpx",
      previewKind: "download-only",
    });
  });
});
