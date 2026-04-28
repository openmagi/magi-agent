import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import type { StructuredBlock } from "./docxDriver.js";
import { writeHwpxFromBlocks } from "./hwpxDriver.js";

const execFileAsync = promisify(execFile);
const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));
const HWPX_VALIDATE_SCRIPT = path.resolve(MODULE_DIR, "../../../runtime/hwpx/scripts/validate.py");
const roots: string[] = [];

async function makeRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "hwpx-driver-"));
  roots.push(root);
  return root;
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("writeHwpxFromBlocks", () => {
  it("creates a structurally valid hwpx package with the bundled runtime", async () => {
    const root = await makeRoot();
    const absPath = path.join(root, "minutes.hwpx");
    const blocks: StructuredBlock[] = [
      { type: "heading", level: 1, text: "회의록" },
      { type: "paragraph", text: "참석자: 제품팀, 플랫폼팀" },
      { type: "paragraph", text: "안건: 문서 출력 기능 네이티브 승격" },
    ];

    await writeHwpxFromBlocks({
      absPath,
      title: "회의록",
      template: "minutes",
      blocks,
    });

    const { stdout } = await execFileAsync("python3", [
      HWPX_VALIDATE_SCRIPT,
      absPath,
    ]);

    expect(stdout).toContain("VALID:");
  });

  it("rewrites an existing hwpx file in place for edit flows", async () => {
    const root = await makeRoot();
    const absPath = path.join(root, "memo.hwpx");

    await writeHwpxFromBlocks({
      absPath,
      title: "초안",
      template: "report",
      blocks: [{ type: "paragraph", text: "초안 본문" }],
    });

    await writeHwpxFromBlocks({
      absPath,
      title: "수정본",
      template: "report",
      blocks: [{ type: "paragraph", text: "수정된 본문" }],
    });

    const bytes = await fs.readFile(absPath);
    expect(bytes.subarray(0, 2).toString()).toBe("PK");
  });
});
