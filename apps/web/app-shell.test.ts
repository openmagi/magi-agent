import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const appDir = path.join(process.cwd(), "apps", "web");

function readAppFile(name: string): string {
  return fs.readFileSync(path.join(appDir, name), "utf8");
}

describe("Magi App shell", () => {
  it("uses the cloud-style chat workbench information architecture", () => {
    const html = readAppFile("index.html");

    expect(html).toContain('class="magi-app-shell"');
    expect(html).toContain('class="chat-sidebar"');
    expect(html).toContain('class="chat-workspace"');
    expect(html).toContain('class="work-dock"');
    expect(html).toContain('data-chat-input-shell="true"');
    expect(html).toContain('data-panel-target="work"');
    expect(html).toContain('data-panel-target="knowledge"');
    expect(html).toContain('data-panel-target="runtime"');
    expect(html).not.toContain('class="inspector-grid"');
  });

  it("carries the cloud visual system into the dependency-free app", () => {
    const css = readAppFile("styles.css");

    expect(css).toContain("--background: #FAFAFA");
    expect(css).toContain("--primary: #7C3AED");
    expect(css).toContain(".chat-sidebar");
    expect(css).toContain(".message.user");
    expect(css).toContain(".run-inspector");
    expect(css).toContain("@media (max-width: 960px)");
  });
});
