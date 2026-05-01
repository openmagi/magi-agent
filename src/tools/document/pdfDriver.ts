import fs from "node:fs";
import fsPromises from "node:fs/promises";
import path from "node:path";
import PDFDocument from "pdfkit";
import type { StructuredBlock } from "./docxDriver.js";

export interface PdfFontCandidate {
  path: string;
  collectionFace?: string;
}

export interface ConfiguredPdfFont {
  fontName: string;
  fontPath: string | null;
  cjkCapable: boolean;
}

export const PDF_BODY_FONT_CANDIDATES: readonly PdfFontCandidate[] = [
  {
    path: "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    collectionFace: "NotoSansCJKkr-Regular",
  },
  { path: "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf" },
  { path: "/System/Library/Fonts/Supplemental/AppleGothic.ttf" },
  {
    path: "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    collectionFace: "AppleSDGothicNeo-Regular",
  },
];

async function fileExists(candidatePath: string): Promise<boolean> {
  try {
    await fsPromises.access(candidatePath);
    return true;
  } catch {
    return false;
  }
}

async function firstExistingPath(candidates: readonly PdfFontCandidate[]): Promise<PdfFontCandidate | null> {
  for (const candidate of candidates) {
    if (await fileExists(candidate.path)) {
      return candidate;
    }
  }
  return null;
}

export async function configureBodyFont(
  doc: Pick<PDFKit.PDFDocument, "font" | "registerFont">,
  candidates: readonly PdfFontCandidate[] = PDF_BODY_FONT_CANDIDATES,
): Promise<ConfiguredPdfFont> {
  const existingFirst = await firstExistingPath(candidates);
  if (!existingFirst) {
    return { cjkCapable: false, fontName: "Helvetica", fontPath: null };
  }

  for (const candidate of candidates) {
    if (!(await fileExists(candidate.path))) {
      continue;
    }

    try {
      // PDFKit requires the PostScript face name for TTC collections. Without
      // it, Debian's Noto CJK collection opens as a collection object and text
      // rendering falls back to the standard WinAnsi fonts.
      doc.registerFont("ClawyBody", candidate.path, candidate.collectionFace);
      doc.font("ClawyBody");
      return { cjkCapable: true, fontName: "ClawyBody", fontPath: candidate.path };
    } catch {
      // Try the next known platform font path.
    }
  }

  return { cjkCapable: false, fontName: "Helvetica", fontPath: null };
}

function containsCjkText(text: string): boolean {
  return /[\u1100-\u11ff\u2e80-\ua4cf\uac00-\ud7af\uf900-\ufaff]/u.test(text);
}

function blocksContainCjk(blocks: StructuredBlock[]): boolean {
  return blocks.some((block) => {
    if (block.type === "table") {
      return block.rows.some((row) => row.some((cell) => containsCjkText(cell)));
    }
    if (block.type === "horizontal_rule") {
      return false;
    }
    return containsCjkText(block.text);
  });
}

function addParagraph(doc: PDFKit.PDFDocument, text: string, fontName: string): void {
  doc.font(fontName).fontSize(11).fillColor("black").text(text, {
    align: "left",
    lineGap: 4,
  });
  doc.moveDown(0.8);
}

function addHeading(
  doc: PDFKit.PDFDocument,
  block: Extract<StructuredBlock, { type: "heading" }>,
  fontName: string,
): void {
  const level = block.level ?? 1;
  const size = level === 1 ? 20 : level === 2 ? 16 : 13;
  doc.font(fontName).fontSize(size).fillColor("black").text(block.text, {
    align: level === 1 ? "center" : "left",
    lineGap: 3,
  });
  doc.moveDown(level === 1 ? 1 : 0.7);
}

export async function writePdfFromBlocks(
  absPath: string,
  title: string,
  blocks: StructuredBlock[],
): Promise<void> {
  await fsPromises.mkdir(path.dirname(absPath), { recursive: true });

  await new Promise<void>((resolve, reject) => {
    const doc = new PDFDocument({
      size: "A4",
      margin: 50,
      bufferPages: true,
      info: { Title: title },
    });
    const output = fs.createWriteStream(absPath);
    output.on("finish", resolve);
    output.on("error", reject);
    doc.on("error", reject);
    doc.pipe(output);

    const documentBlocks = blocks.length > 0
      ? blocks
      : [{ type: "heading" as const, level: 1 as const, text: title }];

    void configureBodyFont(doc)
      .then((configuredFont) => {
        if (!configuredFont.cjkCapable && blocksContainCjk(documentBlocks)) {
          throw new Error(
            "PDF CJK font unavailable; refusing to generate a Korean/Chinese/Japanese PDF with a fallback font.",
          );
        }

        const fontName = configuredFont.fontName;
        for (const block of documentBlocks) {
          if (block.type === "heading") {
            addHeading(doc, block, fontName);
          } else if (block.type === "table") {
            for (const row of block.rows) {
              addParagraph(doc, row.join("    "), fontName);
            }
          } else if (block.type === "bullet") {
            addParagraph(doc, `• ${block.text}`, fontName);
          } else if (block.type === "horizontal_rule") {
            doc.moveDown(0.5);
            doc.moveTo(50, doc.y).lineTo(doc.page.width - 50, doc.y).strokeColor("#D1D5DB").stroke();
            doc.moveDown(0.8);
          } else {
            addParagraph(doc, block.text, fontName);
          }
        }

        const pages = doc.bufferedPageRange();
        for (let i = 0; i < pages.count; i += 1) {
          doc.switchToPage(i);
          doc.font(fontName).fontSize(8).fillColor("gray").text(
            `${i + 1} / ${pages.count}`,
            50,
            doc.page.height - 40,
            { align: "center", width: doc.page.width - 100 },
          );
        }

        doc.end();
      })
      .catch((error: unknown) => {
        doc.destroy();
        reject(error);
      });
  });
}
