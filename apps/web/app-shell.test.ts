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
    expect(source).toContain("cancelActiveTurnWithQueueHandoff");
    expect(source).toContain("buildEscCancelDecision");
    expect(source).toContain("promoteNextQueuedMessage");
    expect(source).toContain('type === "llm_progress"');
    expect(source).toContain('type === "tool_start"');
    expect(source).toContain('type === "task_board"');
    expect(source).toContain('type === "child_progress"');
    expect(source).toContain('type === "patch_preview"');
    expect(source).toContain('type === "source_inspected"');
    expect(source).toContain('type === "rule_check"');
    expect(source).toContain('type === "mission_created"');
    expect(source).toContain('type === "mission_event"');
    expect(source).toContain("goalMode");
    expect(source).toContain("/v1/app/knowledge");
    expect(source).toContain("/v1/app/workspace?path=");
    expect(source).toContain("/v1/app/workspace/file");
    expect(source).toContain("saveWorkspaceFile");
  });

  it("routes dashboard deep links with Next.js App Router", () => {
    const source = readAppFile(path.join("src", "App.tsx"));
    const sidebar = readAppFile(path.join("src", "components", "chat", "chat-sidebar.tsx"));
    const rootPage = readAppFile(path.join("app", "page.tsx"));
    const nextConfig = readAppFile("next.config.ts");

    expect(source).toContain("routeFromPathname");
    expect(source).toContain("LocalDashboardShell");
    expect(source).toContain('appRoute !== "chat"');
    expect(source).toContain('window.addEventListener("popstate", syncRoute)');
    expect(source).toContain('return `/dashboard/${BOT_ID}/${route}`');
    expect(sidebar).toContain('`/dashboard/${currentBotId}/overview`');
    expect(rootPage).toContain("App");
    expect(nextConfig).toContain('output: "export"');
  });

  it("keeps dashboard pages wired to local runtime controls instead of hosted SaaS controls", () => {
    const source = readAppFile(path.join("src", "App.tsx"));
    const settingsDash = readAppFile(path.join("src", "components", "dashboard", "settings-dashboard.tsx"));

    expect(source).toContain("OverviewDashboard");
    expect(source).toContain("SettingsDashboard");
    expect(source).toContain("UsageDashboard");
    expect(source).toContain("SkillsDashboard");
    expect(source).toContain("ConverterDashboard");
    expect(source).toContain("KnowledgeDashboard");
    expect(source).toContain("WorkspaceDashboard");
    expect(source).toContain("MemoryDashboard");
    expect(source).toContain('aria-label="Dashboard section"');
    expect(source).toContain("/v1/app/config");
    expect(source).toContain("/v1/app/config/reload");
    expect(source).toContain("/v1/app/runtime/restart");
    expect(source).toContain("/v1/app/knowledge/file");
    expect(source).toContain("onReadWorkspaceFile");
    expect(source).toContain("onSaveWorkspaceFile");
    expect(settingsDash).toContain("OpenAI-compatible / local");
  });

  it("uses the copied cloud chat components and visual system", () => {
    const sidebar = readAppFile(path.join("src", "components", "chat", "chat-sidebar.tsx"));
    const input = readAppFile(path.join("src", "components", "chat", "chat-input.tsx"));
    const modelPicker = readAppFile(path.join("src", "components", "chat", "chat-model-picker.tsx"));
    const sidePanel = readAppFile(path.join("src", "components", "chat", "kb-side-panel.tsx"));
    const workPanel = readAppFile(path.join("src", "components", "chat", "work-console-panel.tsx"));
    const runInspector = readAppFile(path.join("src", "components", "chat", "run-inspector-dock.tsx"));
    const css = readAppFile(path.join("src", "styles.css"));

    expect(sidebar).toContain("DndContext");
    expect(sidebar).toContain("SortableContext");
    expect(sidebar).toContain("DEFAULT_CHANNELS");
    expect(input).toContain("data-chat-queue-strip");
    expect(input).toContain("data-chat-goal-toggle");
    expect(modelPicker).toContain("data-chat-model-picker");
    expect(sidePanel).toContain("PANEL_VIEW_KEY");
    expect(sidePanel).toContain("WorkConsolePanel");
    expect(sidePanel).toContain("onWorkspaceFileSave");
    expect(sidePanel).toContain("shouldSuppressInlineRunDetails");
    expect(sidePanel).toContain("suppressInlineRunDetails={suppressInlineRunDetails}");
    expect(workPanel).toContain("deriveWorkConsoleRows");
    expect(workPanel).toContain("work-console-motion");
    expect(workPanel).toContain("work-console-row-motion");
    expect(runInspector).toContain("external_doc");
    expect(runInspector).toContain("subagent_result");
    expect(css).toContain('@import "tailwindcss"');
    expect(css).toContain("--background: #FAFAFA");
    expect(css).toContain("--primary: #7C3AED");
    expect(css).toContain(".chat-input-glow");
    expect(css).toContain(".work-console-running-dot");
    expect(css).toContain(".prose-chat");
  });

  it("does not seed hosted-only account channels or SaaS navigation into the self-hosted app", () => {
    const source = readAppFile(path.join("src", "App.tsx"));
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
    }
    for (const channel of forbiddenCloudChannels) {
      expect(source).not.toContain(channel);
    }
  });

  it("uses Next.js App Router instead of Vite SPA", () => {
    const nextConfig = readAppFile("next.config.ts");
    const rootLayout = readAppFile(path.join("app", "layout.tsx"));
    const rootPage = readAppFile(path.join("app", "page.tsx"));

    // Vite config should no longer exist
    expect(fs.existsSync(path.join(appDir, "vite.config.ts"))).toBe(false);
    expect(fs.existsSync(path.join(appDir, "index.html"))).toBe(false);
    // Next shims should no longer exist
    expect(fs.existsSync(path.join(appDir, "src", "shims"))).toBe(false);

    // Next.js config present
    expect(nextConfig).toContain('output: "export"');
    expect(nextConfig).toContain('distDir: "dist"');

    // Root layout + App page present
    expect(rootLayout).toContain("RootLayout");
    expect(rootLayout).toContain("styles.css");
    expect(rootPage).toContain("App");
  });

  it("does not expose hosted smart routers in the self-hosted model UI", () => {
    const source = readAppFile(path.join("src", "App.tsx"));
    const modelPicker = readAppFile(path.join("src", "components", "chat", "chat-model-picker.tsx"));
    const modelOptions = readAppFile(path.join("src", "lib", "models", "model-options.ts"));
    const hostedRouterLabels = [
      "Standard Router",
      "Premium Router",
      "Smart Routing",
      "Open Magi Router",
      "GPT Smart Routing",
    ];

    expect(source).toContain('const DEFAULT_MODEL = "auto"');
    expect(modelPicker).toContain("Configured LLM");
    expect(modelPicker).not.toContain("ROUTER_PICKER_OPTIONS");
    expect(modelPicker).not.toContain("applyRouterPickerMode");
    for (const label of hostedRouterLabels) {
      expect(modelPicker).not.toContain(label);
      expect(modelOptions).not.toContain(label);
    }
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

  it("surfaces a local memory editor with search, delete, compaction, and reindex controls", () => {
    const source = readAppFile(path.join("src", "App.tsx"));

    expect(source).toContain("type AppRoute =");
    expect(source).toContain('| "chat"');
    expect(source).toContain('"memory"');
    expect(source).toContain("MemoryDashboard");
    expect(source).toContain("memoryFiles");
    expect(source).toContain("refreshMemory");
    expect(source).toContain("deleteMemoryFiles");
    expect(source).toContain("/v1/app/memory/search");
    expect(source).toContain("/v1/app/memory/files");
    expect(source).toContain("/v1/app/memory/compact");
    expect(source).toContain("/v1/app/memory/reindex");
  });

  it("renders Skills as a searchable local capability directory", () => {
    const source = readAppFile(path.join("src", "App.tsx"));
    const skillsDash = readAppFile(path.join("src", "components", "dashboard", "skills-dashboard.tsx"));

    expect(source).toContain("type SkillDirectoryFilter");
    expect(source).toContain("normalizeSkillDirectoryItems");
    expect(skillsDash).toContain("filteredSkills");
    expect(skillsDash).toContain("Search skills...");
    expect(skillsDash).toContain("Prompt skills");
    expect(skillsDash).toContain("Script skills");
    expect(skillsDash).toContain("Runtime hooks");
    expect(skillsDash).toContain("Issue detail");
    expect(source).not.toContain("/api/bots/${botId}/custom-skills");
  });
});
