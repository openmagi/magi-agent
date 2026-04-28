import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { execFile as execFileCb } from "node:child_process";
import { promisify } from "node:util";
import type { StructuredBlock } from "./docxDriver.js";

const execFile = promisify(execFileCb);
const HWPX_RUNTIME_ROOT = path.resolve(__dirname, "../../../runtime/hwpx");

export type HwpxTemplate = "base" | "gonmun" | "report" | "minutes";

export interface WriteHwpxInput {
  absPath: string;
  title: string;
  blocks: StructuredBlock[];
  template?: HwpxTemplate;
  creator?: string;
  referencePath?: string;
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

function paragraphXml(id: number, text: string): string {
  return [
    `  <hp:p id="${id}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">`,
    `    <hp:run charPrIDRef="0">`,
    `      <hp:t>${escapeXml(text)}</hp:t>`,
    "    </hp:run>",
    "    <hp:linesegarray>",
    '      <hp:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" baseline="850" spacing="600" horzpos="0" horzsize="42520" flags="393216"/>',
    "    </hp:linesegarray>",
    "  </hp:p>",
  ].join("\n");
}

async function renderSectionXml(blocks: StructuredBlock[]): Promise<string> {
  const baseSectionPath = path.join(HWPX_RUNTIME_ROOT, "templates", "base", "Contents", "section0.xml");
  const baseSection = await fs.readFile(baseSectionPath, "utf8");
  const closeTag = "</hs:sec>";
  const closeIndex = baseSection.lastIndexOf(closeTag);
  if (closeIndex < 0) {
    throw new Error("base HWPX section template is malformed");
  }

  const paragraphs = blocks
    .flatMap((block) => normalizeBlockText(block.text))
    .map((text, index) => paragraphXml(4_000_000_000 + index + 1, text))
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

  try {
    await fs.mkdir(path.dirname(input.absPath), { recursive: true });
    await fs.writeFile(sectionPath, await renderSectionXml(input.blocks), "utf8");

    const buildArgs = ["--output", input.absPath, "--section", sectionPath, "--title", input.title];
    if (input.template && input.template !== "base") {
      buildArgs.unshift(input.template);
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
