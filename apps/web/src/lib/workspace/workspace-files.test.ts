import { describe, expect, it } from "vitest";
import {
  buildWorkspaceFileTree,
  buildWorkspaceFileContentUrl,
  getWorkspaceFilePreviewKind,
  normalizeWorkspaceFileList,
  type WorkspaceFileTreeNode,
} from "./workspace-files";

function summarizeWorkspaceFileTree(nodes: WorkspaceFileTreeNode[]): unknown[] {
  return nodes.map((node) => {
    if (node.type === "file") return { file: node.path };
    return {
      dir: node.path,
      count: node.fileCount,
      children: summarizeWorkspaceFileTree(node.children),
    };
  });
}

describe("workspace generated files helpers", () => {
  it("builds preview and download URLs with a workspace path query", () => {
    expect(
      buildWorkspaceFileContentUrl({
        botId: "bot-1",
        path: "outputs/reports/q1.pdf",
        mode: "inline",
      }),
    ).toBe("/api/bots/bot-1/workspace-files?path=outputs%2Freports%2Fq1.pdf&mode=inline");

    expect(
      buildWorkspaceFileContentUrl({
        botId: "bot-1",
        path: "outputs/reports/q1.pdf",
        mode: "download",
      }),
    ).toBe("/api/bots/bot-1/workspace-files?path=outputs%2Freports%2Fq1.pdf&mode=download");
  });

  it("classifies generated files by preview capability", () => {
    expect(getWorkspaceFilePreviewKind("outputs/report.md")).toBe("markdown");
    expect(getWorkspaceFilePreviewKind("outputs/report.pdf")).toBe("pdf");
    expect(getWorkspaceFilePreviewKind("outputs/chart.png")).toBe("image");
    expect(getWorkspaceFilePreviewKind("outputs/script_part_a.py")).toBe("text");
    expect(getWorkspaceFilePreviewKind("outputs/script_part_a.rpy")).toBe("text");
    expect(getWorkspaceFilePreviewKind("outputs/agent.cpp")).toBe("text");
    expect(getWorkspaceFilePreviewKind("outputs/schema.xml")).toBe("text");
    expect(getWorkspaceFilePreviewKind("outputs/model.xlsx")).toBe("download");
  });

  it("normalizes API file rows for the side panel", () => {
    expect(
      normalizeWorkspaceFileList([
        { path: "outputs/report.md", size: 42 },
        { path: "outputs/nested/model.xlsx", size: 1024, modifiedAt: "2026-04-30T00:00:00.000Z" },
      ]),
    ).toEqual([
      {
        path: "outputs/report.md",
        filename: "report.md",
        size: 42,
        modifiedAt: null,
        previewKind: "markdown",
      },
      {
        path: "outputs/nested/model.xlsx",
        filename: "model.xlsx",
        size: 1024,
        modifiedAt: "2026-04-30T00:00:00.000Z",
        previewKind: "download",
      },
    ]);
  });

  it("builds generated workspace files into a folder tree", () => {
    const files = normalizeWorkspaceFileList([
      { path: "outputs/reports/one-plus-one-benchmark.pdf", size: 2048 },
      { path: "outputs/reports/one-plus-one-consensus.md", size: 1024 },
      { path: "outputs/audit-test.md", size: 20 },
      { path: "scratch.txt", size: 12 },
    ]);

    expect(summarizeWorkspaceFileTree(buildWorkspaceFileTree(files))).toEqual([
      {
        dir: "outputs",
        count: 3,
        children: [
          {
            dir: "outputs/reports",
            count: 2,
            children: [
              { file: "outputs/reports/one-plus-one-benchmark.pdf" },
              { file: "outputs/reports/one-plus-one-consensus.md" },
            ],
          },
          { file: "outputs/audit-test.md" },
        ],
      },
      { file: "scratch.txt" },
    ]);
  });
});
