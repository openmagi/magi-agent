import { FormEvent, useMemo, useState } from "react";

type JsonRecord = Record<string, unknown>;

type WorkspaceSectionId = "system" | "contracts" | "harness" | "memory" | "compaction";

interface WorkspaceShortcut {
  label: string;
  path: string;
  kind: "file" | "directory";
  note: string;
}

interface WorkspaceSection {
  id: WorkspaceSectionId;
  title: string;
  detail: string;
  shortcuts: WorkspaceShortcut[];
}

interface WorkspaceEditorPageProps {
  workspacePath: string;
  setWorkspacePath: (value: string) => void;
  workspaceItems: JsonRecord[];
  selectedWorkspaceFile: string;
  setSelectedWorkspaceFile: (value: string) => void;
  workspaceFileContent: string;
  setWorkspaceFileContent: (value: string) => void;
  workspaceFileStatus: string;
  memoryQuery: string;
  setMemoryQuery: (value: string) => void;
  cronExpression: string;
  setCronExpression: (value: string) => void;
  cronPrompt: string;
  setCronPrompt: (value: string) => void;
  onLoadWorkspace: () => void;
  onOpenWorkspaceFile: (path: string) => void;
  onSaveWorkspaceFile: () => void;
  onSelectWorkspaceItem: (item: JsonRecord) => void;
  onSearchMemory: () => void;
  onCompactMemory: () => void;
  onSaveCron: () => void;
  onReloadSkills: () => void;
}

const WORKSPACE_SECTIONS: WorkspaceSection[] = [
  {
    id: "system",
    title: "System Prompts",
    detail: "Identity, tools, agent rules, and the files that shape every turn.",
    shortcuts: [
      { label: "Soul", path: "SOUL.md", kind: "file", note: "Primary agent posture" },
      { label: "Tools", path: "TOOLS.md", kind: "file", note: "Workspace tool policy" },
      { label: "Agents", path: "AGENTS.md", kind: "file", note: "Agent operating guide" },
      { label: "Bootstrap", path: "BOOTSTRAP.md", kind: "file", note: "Startup instructions" },
      { label: "Identity", path: "IDENTITY.md", kind: "file", note: "Bot identity profile" },
      { label: "User Rules", path: "USER-RULES.md", kind: "file", note: "User-level preferences" },
      { label: "Harness Rules", path: "USER-HARNESS-RULES.md", kind: "file", note: "Natural-language runtime gates" },
    ],
  },
  {
    id: "contracts",
    title: "First-class Contracts",
    detail: "Acceptance criteria, delivery expectations, and explicit completion rules.",
    shortcuts: [
      { label: "Execution Contract", path: "contracts/execution-contract.md", kind: "file", note: "What counts as done" },
      { label: "Delivery Contract", path: "contracts/delivery-contract.md", kind: "file", note: "How outputs reach the user" },
      { label: "Verification Contract", path: "contracts/verification-contract.md", kind: "file", note: "Evidence required before done" },
      { label: "Contracts Folder", path: "contracts", kind: "directory", note: "Browse all contract files" },
    ],
  },
  {
    id: "harness",
    title: "Harness & Hooks",
    detail: "Runtime guardrails and custom hook notes that should be visible to operators.",
    shortcuts: [
      { label: "File Delivery", path: "harness-rules/file-delivery.md", kind: "file", note: "Require file delivery before claims" },
      { label: "Final Answer", path: "harness-rules/final-answer-verifier.md", kind: "file", note: "Shape final response checks" },
      { label: "Tool Input Match", path: "harness-rules/tool-input-match.md", kind: "file", note: "Constrain tool arguments" },
      { label: "Before Turn Hook", path: ".magi/hooks/before-turn.md", kind: "file", note: "Local before-turn policy" },
      { label: "Before Tool Hook", path: ".magi/hooks/before-tool.md", kind: "file", note: "Local tool-use policy" },
      { label: "Before Commit Hook", path: ".magi/hooks/before-commit.md", kind: "file", note: "Local completion policy" },
      { label: "Harness Folder", path: "harness-rules", kind: "directory", note: "Browse harness rules" },
      { label: "Hooks Folder", path: ".magi/hooks", kind: "directory", note: "Browse local hook notes" },
    ],
  },
  {
    id: "memory",
    title: "Memory Tree",
    detail: "Hipocampus memory roots and chronological memory buckets.",
    shortcuts: [
      { label: "Root Memory", path: "memory/ROOT.md", kind: "file", note: "Long-lived memory index" },
      { label: "Legacy Memory", path: "MEMORY.md", kind: "file", note: "Fallback memory file" },
      { label: "Daily", path: "memory/daily", kind: "directory", note: "Daily memory notes" },
      { label: "Weekly", path: "memory/weekly", kind: "directory", note: "Weekly rollups" },
      { label: "Monthly", path: "memory/monthly", kind: "directory", note: "Monthly rollups" },
    ],
  },
  {
    id: "compaction",
    title: "Compaction Tree",
    detail: "Files that make memory compaction inspectable and repairable.",
    shortcuts: [
      { label: "Compaction State", path: "memory/.compaction-state.json", kind: "file", note: "Compaction checkpoint" },
      { label: "Compaction Notes", path: "memory/compaction.md", kind: "file", note: "Operator notes" },
      { label: "Daily Rollups", path: "memory/daily", kind: "directory", note: "Raw source buckets" },
      { label: "Weekly Rollups", path: "memory/weekly", kind: "directory", note: "Intermediate summaries" },
      { label: "Monthly Rollups", path: "memory/monthly", kind: "directory", note: "Durable summaries" },
    ],
  },
];

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function formatBytes(value: unknown): string {
  const bytes = asNumber(value, 0);
  if (bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function itemTitle(item: JsonRecord): string {
  return asString(item.name) || asString(item.path) || "workspace item";
}

function itemMeta(item: JsonRecord): string {
  const size = formatBytes(item.sizeBytes);
  const type = asString(item.type, "file");
  return size ? `${type} · ${size}` : type;
}

export function WorkspaceEditorPage({
  workspacePath,
  setWorkspacePath,
  workspaceItems,
  selectedWorkspaceFile,
  setSelectedWorkspaceFile,
  workspaceFileContent,
  setWorkspaceFileContent,
  workspaceFileStatus,
  memoryQuery,
  setMemoryQuery,
  cronExpression,
  setCronExpression,
  cronPrompt,
  setCronPrompt,
  onLoadWorkspace,
  onOpenWorkspaceFile,
  onSaveWorkspaceFile,
  onSelectWorkspaceItem,
  onSearchMemory,
  onCompactMemory,
  onSaveCron,
  onReloadSkills,
}: WorkspaceEditorPageProps) {
  const [activeSection, setActiveSection] = useState<WorkspaceSectionId>("system");
  const section = useMemo(
    () => WORKSPACE_SECTIONS.find((item) => item.id === activeSection) ?? WORKSPACE_SECTIONS[0],
    [activeSection],
  );

  const openShortcut = (shortcut: WorkspaceShortcut) => {
    if (shortcut.kind === "directory") {
      setWorkspacePath(shortcut.path);
      onSelectWorkspaceItem({ path: shortcut.path, type: "directory" });
      return;
    }
    onOpenWorkspaceFile(shortcut.path);
  };

  return (
    <main className="dashboard-content workspace-editor-page" data-workspace-editor-page="true">
      <div className="page-title">
        <h1>Workspace</h1>
        <p>Edit the local files that define this agent: prompts, contracts, harness rules, hooks, memory, and schedules.</p>
      </div>

      <section className="workspace-command-center cloud-card">
        <div className="workspace-command-copy">
          <span>LOCAL WORKSPACE</span>
          <h2>Operator console for the bot filesystem.</h2>
          <p>Changes are written into the bot workspace and stay available to the local runtime.</p>
        </div>
        <div className="workspace-command-actions">
          <button type="button" onClick={onReloadSkills}>Reload Skills</button>
          <button type="button" className="secondary-button" onClick={onCompactMemory}>Compact Memory</button>
        </div>
      </section>

      <div className="workspace-editor-grid">
        <section className="cloud-card workspace-browser-card">
          <div className="workspace-tabs" role="tablist" aria-label="Workspace editor sections">
            {WORKSPACE_SECTIONS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={activeSection === item.id ? "active" : ""}
                onClick={() => setActiveSection(item.id)}
              >
                {item.title}
              </button>
            ))}
          </div>

          <div className="workspace-section-heading">
            <div>
              <h2>{section.title}</h2>
              <p>{section.detail}</p>
            </div>
            <span>{section.shortcuts.length} paths</span>
          </div>

          <div className="workspace-shortcuts" id="workspace-system-files">
            {section.shortcuts.map((shortcut) => (
              <button key={shortcut.path} type="button" onClick={() => openShortcut(shortcut)}>
                <span className="workspace-file-kind">{shortcut.kind === "directory" ? "DIR" : "MD"}</span>
                <strong>{shortcut.label}</strong>
                <small>{shortcut.path}</small>
                <em>{shortcut.note}</em>
              </button>
            ))}
          </div>

          <form
            id="workspace-form"
            className="workspace-path-row"
            onSubmit={(event: FormEvent) => {
              event.preventDefault();
              onLoadWorkspace();
            }}
          >
            <label>
              <span>Browse path</span>
              <input id="workspace-path" value={workspacePath} onChange={(event) => setWorkspacePath(event.target.value)} />
            </label>
            <button type="submit">List files</button>
          </form>

          <div id="workspace-list" className="workspace-file-list">
            {workspaceItems.length === 0 ? (
              <div className="workspace-empty">No workspace files</div>
            ) : (
              workspaceItems.map((item, index) => (
                <button
                  key={`${asString(item.path, itemTitle(item))}-${index}`}
                  type="button"
                  onClick={() => onSelectWorkspaceItem(item)}
                >
                  <span>{asString(item.type) === "directory" ? "Folder" : "File"}</span>
                  <strong>{itemTitle(item)}</strong>
                  <small>{itemMeta(item)}</small>
                </button>
              ))
            )}
          </div>
        </section>

        <section className="cloud-card workspace-file-editor" id="workspace-file-editor">
          <div className="workspace-file-editor-header">
            <div>
              <span>EDIT FILE</span>
              <h2>{selectedWorkspaceFile || "Select a file"}</h2>
            </div>
            {workspaceFileStatus && <em>{workspaceFileStatus}</em>}
          </div>
          <form
            onSubmit={(event: FormEvent) => {
              event.preventDefault();
              onSaveWorkspaceFile();
            }}
          >
            <label className="workspace-editor-path">
              <span>Path</span>
              <input value={selectedWorkspaceFile} onChange={(event) => setSelectedWorkspaceFile(event.target.value)} />
            </label>
            <textarea
              aria-label="Workspace file content"
              value={workspaceFileContent}
              onChange={(event) => setWorkspaceFileContent(event.target.value)}
              placeholder="# Edit this workspace file"
            />
            <div className="workspace-editor-actions">
              <button type="button" className="secondary-button" onClick={() => onOpenWorkspaceFile(selectedWorkspaceFile)}>Reload file</button>
              <button type="submit">Save workspace file</button>
            </div>
          </form>
        </section>
      </div>

      <div className="workspace-utility-grid">
        <section className="cloud-card workspace-utility-card">
          <span>MEMORY SEARCH</span>
          <h2>Find what the agent remembers.</h2>
          <form
            id="memory-search-form"
            onSubmit={(event: FormEvent) => {
              event.preventDefault();
              onSearchMemory();
            }}
          >
            <input
              id="memory-search-query"
              value={memoryQuery}
              onChange={(event) => setMemoryQuery(event.target.value)}
              placeholder="Search memory"
            />
            <button type="submit">Search memory</button>
          </form>
        </section>

        <section className="cloud-card workspace-utility-card">
          <span>SCHEDULES</span>
          <h2>Create durable scheduled work.</h2>
          <form
            id="cron-editor-form"
            onSubmit={(event: FormEvent) => {
              event.preventDefault();
              onSaveCron();
            }}
          >
            <input id="cron-expression" value={cronExpression} onChange={(event) => setCronExpression(event.target.value)} />
            <textarea id="cron-prompt" value={cronPrompt} onChange={(event) => setCronPrompt(event.target.value)} placeholder="Scheduled prompt" />
            <button type="submit">Save Cron</button>
          </form>
        </section>
      </div>
    </main>
  );
}
