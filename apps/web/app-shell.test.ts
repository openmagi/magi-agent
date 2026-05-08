import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const appDir = path.join(process.cwd(), "apps", "web");

function readAppFile(name: string): string {
  return fs.readFileSync(path.join(appDir, name), "utf8");
}

describe("Magi App shell", () => {
  it("uses the cloud chat and dashboard information architecture in React source", () => {
    const source = readAppFile(path.join("src", "App.tsx"));

    expect(source).toContain('className="cloud-chat-shell"');
    expect(source).toContain('className="dashboard-shell"');
    expect(source).toContain('className="chat-sidebar"');
    expect(source).toContain('className="work-dock"');
    expect(source).toContain('data-chat-input-shell="true"');
    expect(source).toContain("Work in progress");
    expect(source).toContain("Knowledge Base");
    expect(source).toContain("Agent Safeguards");
  });

  it("carries the cloud visual system into the app stylesheet", () => {
    const css = readAppFile(path.join("src", "styles.css"));

    expect(css).toContain("--background: #FAFAFA");
    expect(css).toContain("--primary: #7C3AED");
    expect(css).toContain(".chat-sidebar");
    expect(css).toContain(".dashboard-sidebar");
    expect(css).toContain(".message-bubble.user");
    expect(css).toContain(".current-run-card");
    expect(css).toContain("@media (max-width: 860px)");
  });

  it("builds stable app assets served by the local runtime", () => {
    const html = readAppFile(path.join("dist", "index.html"));
    const js = readAppFile(path.join("dist", "app.js"));

    expect(html).toContain("/app/app.js");
    expect(html).toContain("/app/styles.css");
    expect(js).toContain("createSseParser");
    expect(js).toContain("cloud-chat-shell");
    expect(js).toContain("runtime-config-form");
  });
});
