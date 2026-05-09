import fs from "node:fs";
import fsPromises from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  PDF_BODY_FONT_CANDIDATES,
  configureBodyFont,
  writePdfFromBlocks,
  type PdfFontCandidate,
} from "./pdfDriver.js";

const roots: string[] = [];

async function makeRoot(): Promise<string> {
  const root = await fsPromises.mkdtemp(path.join(os.tmpdir(), "pdf-driver-"));
  roots.push(root);
  return root;
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fsPromises.rm(root, { recursive: true, force: true })),
  );
});

describe("pdfDriver", () => {
  it("registers TTC fonts with an explicit collection face and tries the next candidate", async () => {
    const root = await makeRoot();
    const brokenFont = path.join(root, "broken.ttc");
    const workingFont = path.join(root, "working.ttc");
    await fsPromises.writeFile(brokenFont, "broken");
    await fsPromises.writeFile(workingFont, "working");

    const candidates: PdfFontCandidate[] = [
      { path: brokenFont, collectionFace: "NotoSansCJKkr-Regular" },
      { path: workingFont, collectionFace: "AppleSDGothicNeo-Regular" },
    ];
    const registrations: Array<{ name: string; src: string; family?: string }> = [];
    let fontCalls = 0;
    const doc = {
      registerFont(name: string, src: string, family?: string) {
        registrations.push({ name, src, family });
        return this;
      },
      font() {
        fontCalls += 1;
        if (fontCalls === 1) {
          throw new Error("unsupported collection without face");
        }
        return this;
      },
    };

    const configured = await configureBodyFont(doc, candidates);

    expect(configured).toEqual({
      cjkCapable: true,
      fontName: "MagiBody",
      fontPath: workingFont,
    });
    expect(registrations).toEqual([
      { name: "MagiBody", src: brokenFont, family: "NotoSansCJKkr-Regular" },
      { name: "MagiBody", src: workingFont, family: "AppleSDGothicNeo-Regular" },
    ]);
  });

  it("embeds a CJK font for Korean PDF output when a runtime font is available", async () => {
    if (!PDF_BODY_FONT_CANDIDATES.some((candidate) => fs.existsSync(candidate.path))) {
      return;
    }

    const root = await makeRoot();
    const pdfPath = path.join(root, "korean-report.pdf");

    await writePdfFromBlocks(pdfPath, "한글 보고서", [
      { type: "heading", level: 1, text: "한글 보고서" },
      { type: "paragraph", text: "예시 프로젝트 검토 리포트입니다." },
    ]);

    const bytes = await fsPromises.readFile(pdfPath);
    const pdfText = bytes.toString("latin1");
    expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
    expect(pdfText).toMatch(/NotoSansCJKkr-Regular|AppleGothic|AppleSDGothicNeo-Regular/);
    expect(pdfText).not.toContain("/BaseFont /Helvetica");
  });
});
