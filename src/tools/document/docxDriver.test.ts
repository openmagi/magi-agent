import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { markdownToStructuredBlocks, writeDocxFromBlocks } from "./docxDriver.js";
import { inspectDocx } from "./docxQuality.js";

const roots: string[] = [];

async function makeRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "docx-driver-"));
  roots.push(root);
  return root;
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("docxDriver", () => {
  it("renders markdown tables and emphasis as Word structures instead of literal markdown", async () => {
    const root = await makeRoot();
    const output = path.join(root, "memo.docx");
    const markdown = [
      "# 투자위원회 메모",
      "",
      "| 항목 | 내용 |",
      "| --- | --- |",
      "| **추천** | **투기적 매수** |",
      "| 목표가 | USD 165 |",
      "",
      "---",
      "",
      "## 요약",
      "",
      "- **매출** 성장 지속",
      "> **위원회 결론:** 제한적 매수",
    ].join("\n");

    await writeDocxFromBlocks(output, markdownToStructuredBlocks(markdown));

    const { text, documentXml } = await inspectDocx(output);
    expect(text).toContain("투자위원회 메모");
    expect(text).toContain("투기적 매수");
    expect(text).not.toContain("**");
    expect(text).not.toContain("|");
    expect(text).not.toContain("---");
    expect(documentXml).toContain("<w:tbl>");
  });
});
