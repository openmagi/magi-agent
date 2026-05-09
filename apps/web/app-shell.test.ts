import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const appDir = path.join(process.cwd(), "apps", "web");

function readAppFile(name: string): string {
  return fs.readFileSync(path.join(appDir, name), "utf8");
}

describe("Magi App shell", () => {
  it("ports the cloud chat shell components instead of the old OSS workbench", () => {
    const source = readAppFile(path.join("src", "App.tsx"));

    expect(source).toContain("ChatSidebar");
    expect(source).toContain("ChatMessages");
    expect(source).toContain("ChatInput");
    expect(source).toContain("ChatModelPicker");
    expect(source).toContain("KbContextBar");
    expect(source).toContain("KbSidePanel");
    expect(source).toContain("RunInspectorDock");
    expect(source).not.toContain("ChatWorkbench");
    expect(fs.existsSync(path.join(appDir, "src", "components", "chat-workbench.tsx"))).toBe(false);
    expect(fs.existsSync(path.join(appDir, "src", "components", "work-inspector.tsx"))).toBe(false);
    expect(fs.existsSync(path.join(appDir, "src", "components", "workspace-editor.tsx"))).toBe(false);
  });

  it("keeps the local runtime adapter for streaming, steering, and workspace APIs", () => {
    const source = readAppFile(path.join("src", "App.tsx"));

    expect(source).toContain("createSseParser");
    expect(source).toContain("/v1/chat/completions");
    expect(source).toContain("/v1/chat/inject");
    expect(source).toContain("/v1/chat/interrupt");
    expect(source).toContain('type === "tool_start"');
    expect(source).toContain('type === "task_board"');
    expect(source).toContain('type === "child_progress"');
    expect(source).toContain("/v1/app/knowledge");
    expect(source).toContain("/v1/app/workspace?path=");
    expect(source).toContain("/v1/app/workspace/file");
    expect(source).toContain("saveWorkspaceFile");
  });

  it("uses the copied cloud chat components and visual system", () => {
    const sidebar = readAppFile(path.join("src", "components", "chat", "chat-sidebar.tsx"));
    const input = readAppFile(path.join("src", "components", "chat", "chat-input.tsx"));
    const modelPicker = readAppFile(path.join("src", "components", "chat", "chat-model-picker.tsx"));
    const sidePanel = readAppFile(path.join("src", "components", "chat", "kb-side-panel.tsx"));
    const workPanel = readAppFile(path.join("src", "components", "chat", "work-console-panel.tsx"));
    const css = readAppFile(path.join("src", "styles.css"));

    expect(sidebar).toContain("DndContext");
    expect(sidebar).toContain("SortableContext");
    expect(sidebar).toContain("DEFAULT_CHANNELS");
    expect(input).toContain("data-chat-queue-strip");
    expect(modelPicker).toContain("data-chat-model-picker");
    expect(sidePanel).toContain("PANEL_VIEW_KEY");
    expect(sidePanel).toContain("WorkConsolePanel");
    expect(sidePanel).toContain("onWorkspaceFileSave");
    expect(workPanel).toContain("deriveWorkConsoleRows");
    expect(css).toContain('@import "tailwindcss"');
    expect(css).toContain("--background: #FAFAFA");
    expect(css).toContain("--primary: #7C3AED");
    expect(css).toContain(".chat-input-glow");
    expect(css).toContain(".prose-chat");
  });

  it("does not seed hosted-only account channels or SaaS navigation into the self-hosted app", () => {
    const source = readAppFile(path.join("src", "App.tsx"));
    const js = readAppFile(path.join("dist", "app.js"));
    const hostedOnlyLabels = ["Billing", "Referral", "Organization", "Members", "Organization KB"];
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

    expect(source).toContain('const DEFAULT_CHANNEL = "general"');
    expect(source).toContain('name: DEFAULT_CHANNEL');
    for (const label of hostedOnlyLabels) {
      expect(source).not.toContain(label);
      expect(js).not.toContain(label);
    }
    for (const channel of forbiddenCloudChannels) {
      expect(source).not.toContain(channel);
    }
  });

  it("builds stable app assets served by the local runtime", () => {
    const html = readAppFile(path.join("dist", "index.html"));
    const js = readAppFile(path.join("dist", "app.js"));

    expect(html).toContain("/app/app.js");
    expect(html).toContain("/app/styles.css");
    expect(js).toContain("createSseParser");
    expect(js).toContain("/v1/chat/completions");
    expect(js).toContain("magi:rightInspectorView");
    expect(js).toContain("data-chat-model-picker");
    expect(js).not.toContain("ChatWorkbench");
    expect(js).not.toContain("workspace-editor");
  });

  it("surfaces editable local system, contract, harness, hook, memory, and compaction files through Workspace", () => {
    const source = readAppFile(path.join("src", "App.tsx"));
    const sidePanel = readAppFile(path.join("src", "components", "chat", "kb-side-panel.tsx"));

    expect(source).toContain("EDITABLE_WORKSPACE_ROOTS");
    expect(source).toContain("system-prompts");
    expect(source).toContain("contracts");
    expect(source).toContain("harness-rules");
    expect(source).toContain("hooks");
    expect(source).toContain("memory");
    expect(source).toContain("compactions");
    expect(sidePanel).toContain("textarea");
    expect(sidePanel).toContain("saveWorkspacePreview");
    expect(sidePanel).toContain('method: "PUT"');
    expect(sidePanel).toContain("/v1/app/knowledge/file");
  });
});
