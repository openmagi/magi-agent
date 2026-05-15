import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

function read(path: string): string {
  return readFileSync(path, "utf8");
}

describe("document generation CJK runtime contract", () => {
  it("installs Korean-capable fonts in the per-bot core-agent image", () => {
    const dockerfile = read("infra/docker/clawy-core-agent/Dockerfile");

    expect(dockerfile).toContain("fontconfig");
    expect(dockerfile).toContain("fonts-noto-cjk");
  });

  it("tells document-writing skills not to refuse Korean DOCX generation because of local fonts", () => {
    const documentWriter = read("src/lib/templates/skills/document-writer/SKILL.md");
    const auditReport = read("src/lib/templates/skills/audit-report-draft/SKILL.md");

    expect(documentWriter).toContain("DOCX Korean text does not require an installed system font");
    expect(documentWriter).toContain("Noto Sans CJK KR");
    expect(auditReport).toContain("Noto Sans CJK KR");
  });

  it("documents native DocumentWrite output formats without conditional PDF language", () => {
    const documentWriter = read("src/lib/templates/skills/document-writer/SKILL.md");
    const tools = read("src/lib/templates/static/TOOLS.md");

    expect(documentWriter).toContain("`DocumentWrite` for `md`, `txt`, `html`, `pdf`, `docx`, and `hwpx`");
    expect(documentWriter).toContain("HWPX output is independently validated with the bundled HWPX validator");
    expect(documentWriter).toContain("source-content coverage guard");
    expect(documentWriter).not.toContain("or `pdf` if supported in the current runtime");
    expect(tools).toContain("`DocumentWrite` — `md` / `txt` / `html` / `pdf` / `docx` / `hwpx`");
    expect(tools).toContain("HWPX는 starter XML과 템플릿 header를 기반으로 작성");
  });
});
