import fs from "node:fs/promises";
import { inflateRawSync } from "node:zlib";

export interface DocxInspection {
  documentXml: string;
  text: string;
  tableCount: number;
}

function findEndOfCentralDirectory(buffer: Buffer): number {
  const minOffset = Math.max(0, buffer.length - 65_557);
  for (let offset = buffer.length - 22; offset >= minOffset; offset -= 1) {
    if (buffer.readUInt32LE(offset) === 0x06054b50) {
      return offset;
    }
  }
  throw new Error("DOCX central directory not found");
}

function readZipEntry(buffer: Buffer, entryName: string): Buffer {
  const eocdOffset = findEndOfCentralDirectory(buffer);
  const entryCount = buffer.readUInt16LE(eocdOffset + 10);
  const centralDirectoryOffset = buffer.readUInt32LE(eocdOffset + 16);
  let cursor = centralDirectoryOffset;

  for (let i = 0; i < entryCount; i += 1) {
    if (buffer.readUInt32LE(cursor) !== 0x02014b50) {
      throw new Error("invalid DOCX central directory");
    }
    const compressionMethod = buffer.readUInt16LE(cursor + 10);
    const compressedSize = buffer.readUInt32LE(cursor + 20);
    const filenameLength = buffer.readUInt16LE(cursor + 28);
    const extraLength = buffer.readUInt16LE(cursor + 30);
    const commentLength = buffer.readUInt16LE(cursor + 32);
    const localHeaderOffset = buffer.readUInt32LE(cursor + 42);
    const filename = buffer.toString("utf8", cursor + 46, cursor + 46 + filenameLength);
    cursor += 46 + filenameLength + extraLength + commentLength;

    if (filename !== entryName) {
      continue;
    }

    if (buffer.readUInt32LE(localHeaderOffset) !== 0x04034b50) {
      throw new Error(`invalid DOCX local header for ${entryName}`);
    }
    const localFilenameLength = buffer.readUInt16LE(localHeaderOffset + 26);
    const localExtraLength = buffer.readUInt16LE(localHeaderOffset + 28);
    const dataOffset = localHeaderOffset + 30 + localFilenameLength + localExtraLength;
    const compressed = buffer.subarray(dataOffset, dataOffset + compressedSize);
    if (compressionMethod === 0) return Buffer.from(compressed);
    if (compressionMethod === 8) return inflateRawSync(compressed);
    throw new Error(`unsupported DOCX compression method ${compressionMethod}`);
  }

  throw new Error(`DOCX entry missing: ${entryName}`);
}

function decodeXmlText(value: string): string {
  return value
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, "\"")
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&");
}

export async function inspectDocx(filePath: string): Promise<DocxInspection> {
  const buffer = await fs.readFile(filePath);
  const documentXml = readZipEntry(buffer, "word/document.xml").toString("utf8");
  const text = [...documentXml.matchAll(/<w:t[^>]*>(.*?)<\/w:t>/g)]
    .map((match) => decodeXmlText(match[1] ?? ""))
    .join(" ");
  const tableCount = (documentXml.match(/<w:tbl\b/g) ?? []).length;
  return { documentXml, text, tableCount };
}

function markdownSourceHasTable(sourceMarkdown: string): boolean {
  const lines = sourceMarkdown.split(/\r?\n/);
  for (let i = 0; i < lines.length - 1; i += 1) {
    const current = lines[i]?.trim() ?? "";
    const next = lines[i + 1]?.trim() ?? "";
    if (/^\|.+\|$/.test(current) && /^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$/.test(next)) {
      return true;
    }
  }
  return false;
}

export function validateDocxMarkdownRender(
  inspection: DocxInspection,
  sourceMarkdown: string,
): string | null {
  const problems: string[] = [];
  if (inspection.text.includes("**")) {
    problems.push("bold markers (**)");
  }
  if (inspection.text.includes("```")) {
    problems.push("code fences");
  }
  if (/(^|\s)---(\s|$)/.test(inspection.text)) {
    problems.push("horizontal rules (---)");
  }
  if (/(^|\s)>\s+\S/.test(inspection.text)) {
    problems.push("blockquote markers (>)");
  }
  if (markdownSourceHasTable(sourceMarkdown)) {
    if (inspection.tableCount === 0) {
      problems.push("markdown table was not rendered as a Word table");
    }
    if (inspection.text.includes("|")) {
      problems.push("table pipe characters (|)");
    }
  }

  if (problems.length === 0) return null;
  return `DOCX output contains raw markdown: ${problems.join(", ")}. Render markdown as Word headings, tables, text runs, and bullets instead of preserving source syntax.`;
}
