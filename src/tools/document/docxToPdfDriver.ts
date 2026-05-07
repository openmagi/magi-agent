import { execFile as execFileCb } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";

const execFile = promisify(execFileCb);

export interface DocxToPdfInput {
  docxPath: string;
  pdfPath: string;
  abortSignal?: AbortSignal;
}

export type DocxToPdfConverter = (input: DocxToPdfInput) => Promise<void>;

async function assertHeader(filePath: string, expected: string, label: string): Promise<void> {
  const handle = await fs.open(filePath, "r");
  try {
    const header = Buffer.alloc(expected.length);
    await handle.read(header, 0, header.length, 0);
    if (header.toString("utf8") !== expected) {
      throw new Error(`${label} has invalid header`);
    }
  } finally {
    await handle.close();
  }
}

async function convertWithCommand(
  command: string,
  docxPath: string,
  outDir: string,
  profileDir: string,
  abortSignal?: AbortSignal,
): Promise<void> {
  await execFile(
    command,
    [
      "--headless",
      "--nologo",
      "--nofirststartwizard",
      `-env:UserInstallation=${pathToFileURL(profileDir).href}`,
      "--convert-to",
      "pdf",
      "--outdir",
      outDir,
      docxPath,
    ],
    {
      timeout: 120_000,
      maxBuffer: 512 * 1024,
      signal: abortSignal,
      env: {
        ...process.env,
        HOME: profileDir,
      },
    },
  );
}

export async function convertDocxToPdf(input: DocxToPdfInput): Promise<void> {
  await assertHeader(input.docxPath, "PK", "DOCX input");
  await fs.mkdir(path.dirname(input.pdfPath), { recursive: true });

  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "magi-docx-pdf-"));
  const outDir = path.join(tempRoot, "out");
  const profileDir = path.join(tempRoot, "lo-profile");
  const expectedPdf = path.join(
    outDir,
    `${path.basename(input.docxPath).replace(/\.docx$/i, "")}.pdf`,
  );

  try {
    await fs.mkdir(outDir, { recursive: true });
    await fs.mkdir(profileDir, { recursive: true });
    let lastError: unknown;
    for (const command of ["libreoffice", "soffice"]) {
      try {
        await convertWithCommand(command, input.docxPath, outDir, profileDir, input.abortSignal);
        lastError = null;
        break;
      } catch (error) {
        lastError = error;
      }
    }
    if (lastError) {
      const message = lastError instanceof Error ? lastError.message : String(lastError);
      throw new Error(`DOCX to PDF conversion failed: ${message}`);
    }

    await assertHeader(expectedPdf, "%PDF-", "PDF output");
    await fs.copyFile(expectedPdf, input.pdfPath);
  } finally {
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
}
