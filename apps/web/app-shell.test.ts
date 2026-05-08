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
    const workbench = readAppFile(path.join("src", "components", "chat-workbench.tsx"));
    const inspector = readAppFile(path.join("src", "components", "work-inspector.tsx"));

    expect(source).toContain("ChatWorkbench");
    expect(workbench).toContain('className="cloud-chat-shell"');
    expect(source).toContain('className="dashboard-shell"');
    expect(workbench).toContain('className="chat-sidebar"');
    expect(inspector).toContain('className="work-dock"');
    expect(workbench).toContain('data-chat-input-shell="true"');
    expect(inspector).toContain("deriveWorkConsoleRows");
    expect(source).toContain('type === "tool_start"');
    expect(source).toContain('type === "task_board"');
    expect(source).toContain('type === "child_progress"');
    expect(source).toContain("defaultLocalChannels");
    expect(source).toContain("Work in progress");
    expect(source).toContain("Knowledge Base");
    expect(source).toContain("Agent Safeguards");
    expect(source).not.toContain("math-computer");
    expect(source).not.toContain("Assigning helper");
    expect(source).not.toContain("TaskOutput");
  });

  it("removes hosted-only SaaS navigation from the self-hosted workbench", () => {
    const source = readAppFile(path.join("src", "App.tsx"));
    const js = readAppFile(path.join("dist", "app.js"));
    const hostedOnlyLabels = ["Billing", "Referral", "Organization", "Members", "Organization KB"];

    for (const label of hostedOnlyLabels) {
      expect(source).not.toContain(label);
      expect(js).not.toContain(label);
    }
  });

  it("summarizes work events instead of dumping raw JSON in the inspector", () => {
    const inspector = readAppFile(path.join("src", "components", "work-inspector.tsx"));

    expect(inspector).toContain("summarizeEventPayload");
    expect(inspector).toContain("event-summary");
    expect(inspector).not.toContain("JSON.stringify(event.payload");
  });

  it("does not seed cloud account channels into the self-hosted app", () => {
    const source = readAppFile(path.join("src", "App.tsx"));
    const js = readAppFile(path.join("dist", "app.js"));
    const forbiddenCloudChannels = [
      "chatter",
      "quick-notes",
      "keepers",
      "runtime-proof",
      "local-kb",
      "scheduled-work",
      "daily-update",
      "learning",
    ];

    expect(source).toContain('useState("general")');
    expect(source).toContain('name: "general"');
    for (const channel of forbiddenCloudChannels) {
      expect(source).not.toContain(channel);
      expect(js).not.toContain(channel);
    }
  });

  it("carries the cloud visual system into the app stylesheet", () => {
    const css = readAppFile(path.join("src", "styles.css"));

    expect(css).toContain("--background: #FAFAFA");
    expect(css).toContain("--primary: #7C3AED");
    expect(css).toContain(".chat-sidebar");
    expect(css).toContain(".dashboard-sidebar");
    expect(css).toContain(".message-bubble.user");
    expect(css).toContain(".current-run-card");
    expect(css).toContain("grid-template-columns: 256px minmax(0, 1fr) 320px");
    expect(css).toContain("max-width: 768px");
    expect(css).toContain("border-radius: 16px");
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
    expect(js).toContain("deriveWorkConsoleRows");
    expect(js).not.toContain("math-computer");
    expect(js).not.toContain("Assigning helper");
  });
});
