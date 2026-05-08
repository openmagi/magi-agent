import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react";

const storage = {
  agentUrl: "magi.agent.app.agentUrl",
  token: "magi.agent.app.token",
  sessionKey: "magi.agent.app.sessionKey",
  modelOverride: "magi.agent.app.modelOverride",
};

type Section = "chat" | "overview" | "settings" | "usage" | "skills" | "knowledge" | "converter";
type DockView = "work" | "knowledge";
type Role = "user" | "assistant" | "system";
type JsonRecord = Record<string, unknown>;

interface Message {
  id: string;
  role: Role;
  text: string;
  streaming?: boolean;
  error?: boolean;
}

interface EventRecord {
  id: string;
  type: string;
  payload: JsonRecord;
  ts: number;
}

interface RuntimeSnapshot {
  sessions?: { count?: number; items?: JsonRecord[] };
  tasks?: { count?: number; items?: JsonRecord[] };
  crons?: { count?: number; items?: JsonRecord[] };
  artifacts?: { count?: number; items?: JsonRecord[] };
  tools?: { count?: number; items?: JsonRecord[] };
  skills?: { loadedCount?: number; items?: JsonRecord[]; runtimeHookCount?: number };
}

interface AppConfig {
  llm?: {
    provider?: string;
    model?: string;
    baseUrl?: string;
    apiKeyEnvVar?: string;
    capabilities?: {
      contextWindow?: number;
      maxOutputTokens?: number;
    };
  };
  server?: {
    gatewayTokenEnvVar?: string;
  };
  workspace?: string;
}

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: string }>;
}

function defaultSessionKey(): string {
  return "agent:local:app:web:default";
}

function nowId(prefix: string): string {
  return `${prefix}:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

function getStored(key: string, fallback: string): string {
  return window.localStorage.getItem(key) || fallback;
}

function normalizeAgentUrl(value: string): string {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed.replace(/\/+$/, "") : window.location.origin;
}

function asArray(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter((item): item is JsonRecord => !!item && typeof item === "object") : [];
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function formatTime(ts: unknown): string {
  const value = typeof ts === "number" ? ts : Date.now();
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function preview(value: unknown, max = 140): string {
  const text = typeof value === "string" ? value : value == null ? "" : JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max - 3)}...` : text;
}

export function createSseParser(onEvent: (eventName: string, rawData: string) => void) {
  let buffer = "";
  return (chunk: string) => {
    buffer += chunk;
    const frames = buffer.split(/\n\n/);
    buffer = frames.pop() || "";
    for (const frame of frames) {
      const lines = frame.split(/\n/);
      let eventName = "message";
      const data: string[] = [];
      for (const line of lines) {
        if (line.startsWith(":")) continue;
        if (line.startsWith("event:")) eventName = line.slice("event:".length).trim();
        if (line.startsWith("data:")) data.push(line.slice("data:".length).trimStart());
      }
      if (data.length > 0) onEvent(eventName, data.join("\n"));
    }
  };
}

function Icon({ name }: { name: "doc" | "refresh" | "send" | "attach" | "settings" | "chevron" }) {
  if (name === "refresh") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4 4v5h5M20 20v-5h-5" />
        <path d="M5.6 15.5A7 7 0 0 0 18.8 18M18.4 8.5A7 7 0 0 0 5.2 6" />
      </svg>
    );
  }
  if (name === "send") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m5 12 14-7-5 14-3-6-6-1Z" />
      </svg>
    );
  }
  if (name === "attach") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m21 11-9 9a6 6 0 0 1-8.5-8.5l9-9a4 4 0 0 1 5.7 5.7l-9 9a2 2 0 0 1-2.8-2.8l8.4-8.4" />
      </svg>
    );
  }
  if (name === "settings") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z" />
        <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2 3.4-.2-.1a1.8 1.8 0 0 0-2.1-.3 1.8 1.8 0 0 0-1 1.6v.2h-4v-.2a1.8 1.8 0 0 0-1-1.6 1.8 1.8 0 0 0-2.1.3l-.2.1-2-3.4.1-.1A1.7 1.7 0 0 0 5.6 15a1.8 1.8 0 0 0-1.4-1.2H4v-4h.2a1.8 1.8 0 0 0 1.4-1.2 1.7 1.7 0 0 0-.3-1.9l-.1-.1 2-3.4.2.1a1.8 1.8 0 0 0 2.1.3 1.8 1.8 0 0 0 1-1.6V2h4v.2a1.8 1.8 0 0 0 1 1.6 1.8 1.8 0 0 0 2.1-.3l.2-.1 2 3.4-.1.1a1.7 1.7 0 0 0-.3 1.9 1.8 1.8 0 0 0 1.4 1.2h.2v4h-.2A1.8 1.8 0 0 0 19.4 15Z" />
      </svg>
    );
  }
  if (name === "chevron") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m9 18 6-6-6-6" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
      <path d="M14 2v6h6" />
    </svg>
  );
}

function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "green" | "purple" | "red" | "amber" }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

function SnapshotList({
  id,
  items,
  empty,
  onSelect,
}: {
  id?: string;
  items: JsonRecord[];
  empty: string;
  onSelect?: (item: JsonRecord) => void;
}) {
  if (items.length === 0) {
    return <div id={id} className="snapshot-list"><div className="snapshot-empty">{empty}</div></div>;
  }
  return (
    <div id={id} className="snapshot-list">
      {items.map((item, index) => (
        <button
          key={`${asString(item.id) || asString(item.taskId) || asString(item.path) || index}`}
          type="button"
          className="snapshot-row"
          onClick={() => onSelect?.(item)}
        >
          <span className="snapshot-title">
            {asString(item.title) || asString(item.name) || asString(item.sessionKey) || asString(item.taskId) || asString(item.cronId) || asString(item.path) || `item ${index + 1}`}
          </span>
          <span className="snapshot-meta">
            {asString(item.status) || asString(item.kind) || asString(item.collection) || asString(item.permission) || preview(item.meta, 60)}
          </span>
          <span className="snapshot-detail">
            {asString(item.detail) || asString(item.promptPreview) || asString(item.resultPreview) || asString(item.path) || preview(item.contentPreview || item.inputPreview || item.outputPreview, 120)}
          </span>
        </button>
      ))}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function SectionCard({ title, action, children, id }: { title: string; action?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section id={id} className="panel-card">
      <div className="panel-card-header">
        <h3>{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function OpenMagiLogo() {
  return (
    <div className="openmagi-logo" aria-label="Open Magi">
      <span className="logo-mark">M</span>
      <span>Open <strong>Magi</strong></span>
    </div>
  );
}

function DashboardSidebar({
  active,
  setActive,
  runtimeStatus,
}: {
  active: Section;
  setActive: (section: Section) => void;
  runtimeStatus: string;
}) {
  const nav = [
    { group: "Chat", items: [["chat", "Chat"], ["overview", "Overview"], ["settings", "Settings"], ["usage", "Usage"], ["skills", "Skills"], ["converter", "Converter"]] },
    { group: "Account", items: [["knowledge", "Knowledge"], ["billing", "Billing"], ["support", "Support"], ["referral", "Referral"]] },
    { group: "Magi", items: [["organization", "Organization"], ["members", "Members"], ["org-kb", "Organization KB"]] },
  ] as const;
  return (
    <aside className="dashboard-sidebar" aria-label="Dashboard navigation">
      <OpenMagiLogo />
      <button className="bot-switcher" type="button">
        <span>Magi_Local</span>
        <Icon name="chevron" />
      </button>
      <nav className="dashboard-nav">
        {nav.map((group) => (
          <div key={group.group} className="nav-section">
            <div className="nav-label">{group.group}</div>
            {group.items.map(([key, label]) => {
              const section = (["chat", "overview", "settings", "usage", "skills", "knowledge", "converter"].includes(key) ? key : "overview") as Section;
              return (
                <button
                  key={key}
                  className={`nav-item ${active === section && key === section ? "active" : ""}`}
                  type="button"
                  onClick={() => setActive(section)}
                >
                  {label}
                </button>
              );
            })}
          </div>
        ))}
      </nav>
      <div className="sidebar-footer">
        <span className="status-dot" />
        <span>{runtimeStatus}</span>
      </div>
    </aside>
  );
}

function ChatSidebar({
  activeChannel,
  setActiveChannel,
  setActive,
  onRefresh,
  runtimeStatus,
}: {
  activeChannel: string;
  setActiveChannel: (channel: string) => void;
  setActive: (section: Section) => void;
  onRefresh: () => void;
  runtimeStatus: string;
}) {
  const channels: Array<[string, string[]]> = [
    ["General", ["general", "chatter", "quick-notes", "keepers"]],
    ["Work", ["runtime-proof", "local-kb", "scheduled-work", "tmp"]],
    ["Info", ["news", "daily-update"]],
    ["Life", ["schedule"]],
    ["Finance", ["finance"]],
    ["Study", ["learning"]],
  ];
  return (
    <aside className="chat-sidebar" aria-label="Chat channels">
      <div className="bot-status">
        <div>
          <strong>Magi_Local</strong>
          <span><i /> {runtimeStatus}</span>
        </div>
      </div>
      <div className="chat-edit-row">
        <button type="button">Edit</button>
      </div>
      <nav className="channel-scroll">
        {channels.map(([group, items]) => (
          <div key={group} className="channel-group">
            <div className="channel-group-label">{group}</div>
            {items.map((channel) => (
              <button
                key={channel}
                className={`channel-row ${activeChannel === channel ? "active" : ""}`}
                type="button"
                onClick={() => setActiveChannel(channel)}
              >
                <span>#</span>
                <strong>{channel}</strong>
                {channel === "tmp" && <i className="unread-dot" />}
              </button>
            ))}
          </div>
        ))}
      </nav>
      <div className="chat-sidebar-bottom">
        <button type="button" onClick={onRefresh}><Icon name="refresh" /> Refresh</button>
        <button type="button" onClick={() => setActive("overview")}><Icon name="settings" /> Dashboard</button>
      </div>
    </aside>
  );
}

function RuntimeMetrics({ runtime, eventCount }: { runtime: RuntimeSnapshot | null; eventCount: number }) {
  const rows = [
    ["Sessions", runtime?.sessions?.count ?? 0],
    ["Tasks", runtime?.tasks?.count ?? 0],
    ["Crons", runtime?.crons?.count ?? 0],
    ["Artifacts", runtime?.artifacts?.count ?? 0],
    ["Tools", runtime?.tools?.count ?? 0],
    ["Skills", runtime?.skills?.loadedCount ?? 0],
    ["Events", eventCount],
  ];
  return (
    <div className="metric-grid">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </div>
  );
}

function WorkDock({
  activeDock,
  setActiveDock,
  runtime,
  events,
  knowledgeQuery,
  setKnowledgeQuery,
  knowledgePath,
  setKnowledgePath,
  knowledgeContent,
  setKnowledgeContent,
  knowledgeItems,
  onSearchKnowledge,
  onLoadKnowledge,
  onSaveKnowledge,
  onReloadSkills,
}: {
  activeDock: DockView;
  setActiveDock: (view: DockView) => void;
  runtime: RuntimeSnapshot | null;
  events: EventRecord[];
  knowledgeQuery: string;
  setKnowledgeQuery: (value: string) => void;
  knowledgePath: string;
  setKnowledgePath: (value: string) => void;
  knowledgeContent: string;
  setKnowledgeContent: (value: string) => void;
  knowledgeItems: JsonRecord[];
  onSearchKnowledge: () => void;
  onLoadKnowledge: () => void;
  onSaveKnowledge: () => void;
  onReloadSkills: () => void;
}) {
  const toolRows = runtime?.tasks?.items ?? [];
  const sessionRows = runtime?.sessions?.items ?? [];
  const artifactRows = runtime?.artifacts?.items ?? [];
  const cronRows = runtime?.crons?.items ?? [];
  const helperNames = [
    "Halley",
    "Meitner",
    "Kant",
    "Noether",
    "Turing",
    "Curie",
    "Hopper",
    "Lovelace",
    "Feynman",
    "Franklin",
    "Shannon",
    "Lamarr",
  ];
  const helperRows = helperNames.map((name, index) => ({
    name,
    role: index < 6 ? "math-computer" : index < 10 ? "calculator-opus" : "calculator-gemini",
    iteration: index === 2 ? 2 : index === 6 ? 3 : 1,
    tone: index % 3 === 0 ? "green" : "purple",
  }));
  const activeTools = toolRows.length > 0
    ? toolRows
    : [
        { title: "Assigning helper", status: "running", detail: "Compute, verify, or inspect a bounded part of the task." },
        { title: "TaskList", status: "running", detail: "Status: running" },
        { title: "TaskOutput", status: "running", detail: "Waiting for results" },
      ];
  return (
    <aside className="work-dock" aria-label="Work">
      <header className="work-dock-header">
        <div>
          <p>WORK</p>
          <h2>{activeDock === "work" ? "Work" : "Knowledge"}</h2>
        </div>
        <button className="icon-button" type="button" aria-label="Collapse">»</button>
      </header>
      <div className="dock-tabs" role="tablist" aria-label="Right inspector">
        <button className={activeDock === "work" ? "active" : ""} type="button" onClick={() => setActiveDock("work")}>Work</button>
        <button className={activeDock === "knowledge" ? "active" : ""} type="button" onClick={() => setActiveDock("knowledge")}>Knowledge</button>
      </div>
      {activeDock === "work" ? (
        <div className="dock-panel">
          <div className="dock-intro">
            <h3>Work in progress</h3>
            <p>Plain-language progress from the current run.</p>
          </div>
          <section className="work-card live">
            <div className="work-card-title">
              <span>NOW</span>
              <Pill tone="purple">LIVE</Pill>
            </div>
            <div className="work-step running">
              <span />
              <div>
                <strong>Running</strong>
                <p>Runtime is ready for a local session.</p>
              </div>
            </div>
          </section>
          <section className="work-card helpers-card">
            <div className="work-card-title">
              <span>HELPERS</span>
              <Pill tone="green">{helperRows.length} AGENTS</Pill>
            </div>
            <div className="helper-grid">
              {helperRows.map((helper) => (
                <div key={helper.name} className="helper-chip">
                  <span className={`helper-dot ${helper.tone}`} />
                  <strong>{helper.name} <em>{helper.role}</em></strong>
                  <div className="helper-bar">iteration {helper.iteration}</div>
                </div>
              ))}
            </div>
          </section>
          <section className="work-card">
            <div className="work-card-title">
              <span>CURRENT STEPS</span>
              <Pill>{activeTools.length}</Pill>
            </div>
            <div id="tasks-list" className="current-step-list">
              {activeTools.map((tool, index) => (
                <div key={`${asString(tool.taskId) || asString(tool.title) || index}`} className="current-step-row">
                  <span className="green-dot" />
                  <div>
                    <strong>{asString(tool.title) || asString(tool.name) || asString(tool.taskId) || "Working in workspace"}</strong>
                    <p>{asString(tool.detail) || asString(tool.promptPreview) || asString(tool.status, "Status: running")}</p>
                  </div>
                </div>
              ))}
            </div>
          </section>
          <section className="work-card">
            <div className="work-card-title"><span>SESSIONS</span></div>
            <SnapshotList id="sessions-list" items={sessionRows} empty="No sessions" />
          </section>
          <section className="work-card">
            <div className="work-card-title"><span>SCHEDULES</span></div>
            <SnapshotList id="crons-list" items={cronRows} empty="No schedules" />
          </section>
          <section className="work-card">
            <div className="work-card-title">
              <span>ARTIFACTS</span>
              <div className="row-actions">
                <button id="open-artifact-button" type="button">Open</button>
                <button id="download-artifact-button" type="button">Download</button>
              </div>
            </div>
            <SnapshotList id="artifacts-list" items={artifactRows} empty="No artifacts" />
            <pre id="artifact-content" className="code-view" />
          </section>
          <section className="work-card">
            <div className="work-card-title"><span>EVENTS</span></div>
            <div id="events" className="event-list">
              {events.slice(0, 12).map((event) => (
                <div key={event.id} className="event-row">
                  <strong>{event.type}</strong>
                  <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                </div>
              ))}
            </div>
          </section>
        </div>
      ) : (
        <div className="dock-panel">
          <div className="dock-intro">
            <h3>Knowledge Base</h3>
            <p>Local workspace KB. No hosted Knowledge Base required.</p>
          </div>
          <form id="knowledge-search-form" className="dock-form" onSubmit={(event) => { event.preventDefault(); onSearchKnowledge(); }}>
            <Field label="Search">
              <input id="knowledge-query" value={knowledgeQuery} onChange={(event) => setKnowledgeQuery(event.target.value)} placeholder="delivery evidence" />
            </Field>
            <div className="row-actions full">
              <button type="submit">Search</button>
              <button id="load-knowledge-button" type="button" onClick={onLoadKnowledge}>List KB</button>
            </div>
          </form>
          <SnapshotList id="knowledge-results" items={knowledgeItems} empty="No KB documents" />
          <form id="knowledge-file-form" className="dock-form" onSubmit={(event) => { event.preventDefault(); onSaveKnowledge(); }}>
            <Field label="Path">
              <input id="knowledge-file-path" value={knowledgePath} onChange={(event) => setKnowledgePath(event.target.value)} placeholder="reports/brief.md" />
            </Field>
            <Field label="Markdown">
              <textarea id="knowledge-file-content" rows={7} value={knowledgeContent} onChange={(event) => setKnowledgeContent(event.target.value)} />
            </Field>
            <button type="submit">Save KB document</button>
          </form>
          <button id="reload-skills-button" className="secondary-button" type="button" onClick={onReloadSkills}>Reload Skills</button>
        </div>
      )}
    </aside>
  );
}

function ChatView({
  activeChannel,
  setActiveChannel,
  setActive,
  runtimeStatus,
  onRefresh,
  messages,
  input,
  setInput,
  isStreaming,
  onSend,
  onReset,
  modelOverride,
  setModelOverride,
  activeDock,
  setActiveDock,
  runtime,
  events,
  knowledgeProps,
  onReloadSkills,
}: {
  activeChannel: string;
  setActiveChannel: (channel: string) => void;
  setActive: (section: Section) => void;
  runtimeStatus: string;
  onRefresh: () => void;
  messages: Message[];
  input: string;
  setInput: (value: string) => void;
  isStreaming: boolean;
  onSend: () => void;
  onReset: () => void;
  modelOverride: string;
  setModelOverride: (value: string) => void;
  activeDock: DockView;
  setActiveDock: (view: DockView) => void;
  runtime: RuntimeSnapshot | null;
  events: EventRecord[];
  knowledgeProps: Pick<Parameters<typeof WorkDock>[0], "knowledgeQuery" | "setKnowledgeQuery" | "knowledgePath" | "setKnowledgePath" | "knowledgeContent" | "setKnowledgeContent" | "knowledgeItems" | "onSearchKnowledge" | "onLoadKnowledge" | "onSaveKnowledge">;
  onReloadSkills: () => void;
}) {
  return (
    <div className="cloud-chat-shell" data-cloud-chat-shell="true">
      <ChatSidebar
        activeChannel={activeChannel}
        setActiveChannel={setActiveChannel}
        setActive={setActive}
        onRefresh={onRefresh}
        runtimeStatus={runtimeStatus}
      />
      <main className="chat-main">
        <header className="chat-header">
          <h1>{activeChannel}</h1>
          <button id="clear-button" type="button" onClick={onReset}>Reset</button>
        </header>
        <div id="messages" className="message-timeline" aria-live="polite">
          {messages.length === 0 ? (
            <section className="empty-chat">
              <div className="empty-chat-icon">⌁</div>
              <p>Start a conversation</p>
            </section>
          ) : (
            messages.map((message) => (
              <div key={message.id} className={`message-bubble ${message.role} ${message.error ? "error" : ""} ${message.streaming ? "streaming" : ""}`}>
                {message.text}
              </div>
            ))
          )}
        </div>
        <section className="current-run-card" aria-label="Current run">
          <button type="button" className="run-card-close">×</button>
          <div className="run-grid">
            <span>CURRENT RUN</span>
            <strong>{isStreaming ? "Running" : "Ready"}</strong>
            <span>CURRENT WORK</span>
            <strong>{isStreaming ? "Working on your request" : "Waiting for input"}</strong>
          </div>
        </section>
        <form
          id="message-form"
          className="chat-composer"
          data-chat-input-shell="true"
          onSubmit={(event) => {
            event.preventDefault();
            onSend();
          }}
        >
          <div className="composer-mode-row">
            <button type="button" className="mode-active">Queue after run</button>
            <button type="button">Steer current run</button>
          </div>
          <textarea
            id="message-input"
            value={input}
            rows={1}
            placeholder="Message..."
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                onSend();
              }
            }}
          />
          <div className="composer-bottom">
            <button type="button" className="attach-button" aria-label="Attach file"><Icon name="attach" /></button>
            <div className="model-picker">
              <span>Custom</span>
              <input id="model-override" value={modelOverride} onChange={(event) => setModelOverride(event.target.value)} placeholder="auto" />
            </div>
            <button id="send-button" className={isStreaming ? "stop-button" : "send-button"} type="submit">
              {isStreaming ? "■" : <Icon name="send" />}
            </button>
          </div>
        </form>
      </main>
      <WorkDock
        activeDock={activeDock}
        setActiveDock={setActiveDock}
        runtime={runtime}
        events={events}
        onReloadSkills={onReloadSkills}
        {...knowledgeProps}
      />
    </div>
  );
}

function Overview({ runtime, eventCount }: { runtime: RuntimeSnapshot | null; eventCount: number }) {
  return (
    <main className="dashboard-content">
      <div className="page-title">
        <h1>Dashboard</h1>
        <p>Manage your local agent and monitor runtime performance.</p>
      </div>
      <section className="cloud-card bot-card">
        <div className="bot-card-header">
          <div className="bot-name"><span className="green-dot" /> <strong>Magi_Local</strong></div>
          <Pill tone="green">Active</Pill>
        </div>
        <div className="bot-facts">
          <div><span>Runtime</span><strong>Self-hosted</strong></div>
          <div><span>Model</span><strong>OpenAI-compatible</strong></div>
          <div><span>Workspace</span><strong>./workspace</strong></div>
          <div><span>Created</span><strong>Local</strong></div>
        </div>
        <div className="settings-tabs">
          <button type="button">Settings</button>
          <button type="button">Usage</button>
          <button type="button">Runtime proof</button>
        </div>
        <RuntimeMetrics runtime={runtime} eventCount={eventCount} />
      </section>
      <section className="cloud-card">
        <div className="card-heading">
          <div>
            <h2>Integrations</h2>
            <p>Connect local providers and workspace services to your runtime.</p>
          </div>
        </div>
        <div className="integration-row">
          <span className="integration-icon">O</span>
          <div><strong>OpenAI-compatible</strong><p>Ollama, LM Studio, vLLM, llama.cpp, LiteLLM</p></div>
          <Pill tone="green">Local</Pill>
        </div>
        <div className="integration-row">
          <span className="integration-icon">K</span>
          <div><strong>Workspace Knowledge</strong><p>Markdown, text, CSV, JSON, YAML, HTML</p></div>
          <Pill tone="green">Connected</Pill>
        </div>
        <div className="integration-row">
          <span className="integration-icon">C</span>
          <div><strong>Cron workflows</strong><p>Scheduled runs with delivery safety</p></div>
          <Pill tone="purple">Runtime</Pill>
        </div>
      </section>
    </main>
  );
}

function Settings({
  agentUrl,
  setAgentUrl,
  token,
  setToken,
  sessionKey,
  setSessionKey,
  planMode,
  setPlanMode,
  runtimeStatus,
  onSaveConnection,
  onCheckRuntime,
  installAvailable,
  onInstall,
  config,
  setConfig,
  onSaveConfig,
  onReloadConfig,
  configStatus,
  harnessName,
  setHarnessName,
  harnessContent,
  setHarnessContent,
  onSaveHarnessRule,
}: {
  agentUrl: string;
  setAgentUrl: (value: string) => void;
  token: string;
  setToken: (value: string) => void;
  sessionKey: string;
  setSessionKey: (value: string) => void;
  planMode: boolean;
  setPlanMode: (value: boolean) => void;
  runtimeStatus: string;
  onSaveConnection: () => void;
  onCheckRuntime: () => void;
  installAvailable: boolean;
  onInstall: () => void;
  config: AppConfig;
  setConfig: (value: AppConfig) => void;
  onSaveConfig: () => void;
  onReloadConfig: () => void;
  configStatus: string;
  harnessName: string;
  setHarnessName: (value: string) => void;
  harnessContent: string;
  setHarnessContent: (value: string) => void;
  onSaveHarnessRule: () => void;
}) {
  const llm = config.llm ?? {};
  const server = config.server ?? {};
  const capabilities = llm.capabilities ?? {};
  const updateLlm = (patch: NonNullable<AppConfig["llm"]>) => setConfig({ ...config, llm: { ...llm, ...patch } });
  return (
    <main className="dashboard-content">
      <div className="page-title">
        <h1>Settings</h1>
        <p>Local runtime connection, model routing, and agent safeguards.</p>
      </div>
      <section className="cloud-card settings-card">
        <form id="connection-form" onSubmit={(event) => { event.preventDefault(); onSaveConnection(); }}>
          <div className="settings-row-title">
            <h2>Local Runtime</h2>
            <Pill tone={runtimeStatus === "active" ? "green" : "purple"}>{runtimeStatus}</Pill>
          </div>
          <Field label="Agent URL">
            <input id="agent-url" value={agentUrl} onChange={(event) => setAgentUrl(event.target.value)} />
          </Field>
          <Field label="Server token">
            <input id="server-token" type="password" value={token} onChange={(event) => setToken(event.target.value)} />
          </Field>
          <Field label="Session key">
            <input id="session-key" value={sessionKey} onChange={(event) => setSessionKey(event.target.value)} />
          </Field>
          <label className="check-row">
            <input id="plan-mode" type="checkbox" checked={planMode} onChange={(event) => setPlanMode(event.target.checked)} />
            Plan mode
          </label>
          <div className="row-actions">
            <button type="submit">Save Settings</button>
            <button id="health-button" type="button" onClick={onCheckRuntime}>Check Runtime</button>
            {installAvailable && <button id="install-button" type="button" onClick={onInstall}>Install App</button>}
          </div>
        </form>
      </section>
      <section className="cloud-card settings-card">
        <form id="runtime-config-form" onSubmit={(event) => { event.preventDefault(); onSaveConfig(); }}>
          <Field label="Model">
            <select id="config-provider" value={llm.provider ?? "openai-compatible"} onChange={(event) => updateLlm({ provider: event.target.value })}>
              <option value="openai-compatible">Custom</option>
              <option value="anthropic">Anthropic</option>
              <option value="openai">OpenAI</option>
              <option value="google">Google</option>
            </select>
          </Field>
          <Field label="Custom model">
            <input id="config-model" value={llm.model ?? ""} onChange={(event) => updateLlm({ model: event.target.value })} placeholder="llama3.1" />
          </Field>
          <Field label="Base URL">
            <input id="config-base-url" value={llm.baseUrl ?? ""} onChange={(event) => updateLlm({ baseUrl: event.target.value })} placeholder="http://host.docker.internal:11434/v1" />
          </Field>
          <div className="two-col">
            <Field label="API key env var">
              <input id="config-api-key-env" value={llm.apiKeyEnvVar ?? ""} onChange={(event) => updateLlm({ apiKeyEnvVar: event.target.value })} />
            </Field>
            <Field label="Server token env var">
              <input
                id="config-server-token-env"
                value={server.gatewayTokenEnvVar ?? "MAGI_AGENT_SERVER_TOKEN"}
                onChange={(event) => setConfig({ ...config, server: { ...server, gatewayTokenEnvVar: event.target.value } })}
              />
            </Field>
          </div>
          <Field label="Workspace">
            <input id="config-workspace" value={config.workspace ?? "./workspace"} onChange={(event) => setConfig({ ...config, workspace: event.target.value })} />
          </Field>
          <div className="two-col">
            <Field label="Context window">
              <input
                id="config-context-window"
                type="number"
                value={capabilities.contextWindow ?? ""}
                onChange={(event) => updateLlm({ capabilities: { ...capabilities, contextWindow: Number(event.target.value) || undefined } })}
              />
            </Field>
            <Field label="Max output tokens">
              <input
                id="config-max-output"
                type="number"
                value={capabilities.maxOutputTokens ?? ""}
                onChange={(event) => updateLlm({ capabilities: { ...capabilities, maxOutputTokens: Number(event.target.value) || undefined } })}
              />
            </Field>
          </div>
          <div className="row-actions">
            <button type="submit">Save Config</button>
            <button id="config-reload-button" type="button" onClick={onReloadConfig}>Reload</button>
          </div>
          <p id="config-restart-status" className="muted-line">{configStatus}</p>
        </form>
      </section>
      <section className="cloud-card settings-card">
        <h2>Agent Safeguards</h2>
        <p className="muted-line">Build safeguards that tell the agent what it must verify, deliver, or ask before finishing work.</p>
        <form id="harness-rule-form" onSubmit={(event) => { event.preventDefault(); onSaveHarnessRule(); }}>
          <Field label="Rule file">
            <input id="harness-rule-name" value={harnessName} onChange={(event) => setHarnessName(event.target.value)} placeholder="file-delivery.md" />
          </Field>
          <Field label="Markdown rule">
            <textarea id="harness-rule-content" rows={7} value={harnessContent} onChange={(event) => setHarnessContent(event.target.value)} />
          </Field>
          <button type="submit">Save Rule</button>
        </form>
      </section>
    </main>
  );
}

function KnowledgePage({
  knowledgeQuery,
  setKnowledgeQuery,
  knowledgeItems,
  onSearchKnowledge,
  onLoadKnowledge,
  knowledgePath,
  setKnowledgePath,
  knowledgeContent,
  setKnowledgeContent,
  onSaveKnowledge,
}: Pick<Parameters<typeof WorkDock>[0], "knowledgeQuery" | "setKnowledgeQuery" | "knowledgePath" | "setKnowledgePath" | "knowledgeContent" | "setKnowledgeContent" | "knowledgeItems" | "onSearchKnowledge" | "onLoadKnowledge" | "onSaveKnowledge">) {
  return (
    <main className="dashboard-content">
      <div className="page-title">
        <h1>Knowledge Base</h1>
        <p>Local KB backed by files under <code>workspace/knowledge</code>.</p>
      </div>
      <section className="cloud-card">
        <form id="knowledge-search-page-form" onSubmit={(event) => { event.preventDefault(); onSearchKnowledge(); }}>
          <Field label="Search documents">
            <input value={knowledgeQuery} onChange={(event) => setKnowledgeQuery(event.target.value)} placeholder="reusable runtime context" />
          </Field>
          <div className="row-actions">
            <button type="submit">Search</button>
            <button type="button" onClick={onLoadKnowledge}>List documents</button>
          </div>
        </form>
        <SnapshotList items={knowledgeItems} empty="No local KB documents" />
      </section>
      <section className="cloud-card">
        <h2>Write a KB document</h2>
        <form onSubmit={(event) => { event.preventDefault(); onSaveKnowledge(); }}>
          <Field label="Path">
            <input value={knowledgePath} onChange={(event) => setKnowledgePath(event.target.value)} placeholder="reports/brief.md" />
          </Field>
          <Field label="Markdown">
            <textarea rows={10} value={knowledgeContent} onChange={(event) => setKnowledgeContent(event.target.value)} />
          </Field>
          <button type="submit">Save KB document</button>
        </form>
      </section>
    </main>
  );
}

function UtilityPage({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return (
    <main className="dashboard-content">
      <div className="page-title">
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <section className="cloud-card">{children}</section>
    </main>
  );
}

export function App() {
  const [active, setActive] = useState<Section>("chat");
  const [activeChannel, setActiveChannel] = useState("tmp");
  const [activeDock, setActiveDock] = useState<DockView>("work");
  const [agentUrl, setAgentUrl] = useState(() => getStored(storage.agentUrl, window.location.origin));
  const [token, setToken] = useState(() => getStored(storage.token, ""));
  const [sessionKey, setSessionKey] = useState(() => getStored(storage.sessionKey, defaultSessionKey()));
  const [modelOverride, setModelOverride] = useState(() => getStored(storage.modelOverride, "auto"));
  const [planMode, setPlanMode] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState("active");
  const [runtime, setRuntime] = useState<RuntimeSnapshot | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [deferredInstallPrompt, setDeferredInstallPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [config, setConfig] = useState<AppConfig>({});
  const [configStatus, setConfigStatus] = useState("");
  const [knowledgeQuery, setKnowledgeQuery] = useState("");
  const [knowledgePath, setKnowledgePath] = useState("notes/local.md");
  const [knowledgeContent, setKnowledgeContent] = useState("# Local note\n\n");
  const [knowledgeItems, setKnowledgeItems] = useState<JsonRecord[]>([]);
  const [memoryQuery, setMemoryQuery] = useState("");
  const [workspacePath, setWorkspacePath] = useState(".");
  const [workspaceItems, setWorkspaceItems] = useState<JsonRecord[]>([]);
  const [transcriptItems, setTranscriptItems] = useState<JsonRecord[]>([]);
  const [evidenceItems, setEvidenceItems] = useState<JsonRecord[]>([]);
  const [cronExpression, setCronExpression] = useState("@daily");
  const [cronPrompt, setCronPrompt] = useState("");
  const [harnessName, setHarnessName] = useState("file-delivery.md");
  const [harnessContent, setHarnessContent] = useState("---\ntrigger: beforeCommit\naction:\n  type: require_tool\n  toolName: FileDeliver\n---\nDeliver generated files before claiming completion.\n");

  const addEvent = useCallback((type: string, payload: JsonRecord = {}) => {
    setEvents((current) => [{ id: nowId("event"), type, payload, ts: Date.now() }, ...current].slice(0, 80));
  }, []);

  const normalizedBase = useMemo(() => normalizeAgentUrl(agentUrl), [agentUrl]);

  const authHeaders = useCallback((json = false): HeadersInit => {
    return {
      ...(json ? { "Content-Type": "application/json" } : {}),
      ...(token.trim() ? { Authorization: `Bearer ${token.trim()}` } : {}),
      "X-Core-Agent-Session-Key": sessionKey.trim() || defaultSessionKey(),
      ...(planMode ? { "X-Core-Agent-Plan-Mode": "on" } : {}),
    };
  }, [planMode, sessionKey, token]);

  const getJson = useCallback(async (path: string): Promise<JsonRecord> => {
    const response = await fetch(`${normalizedBase}${path}`, { headers: authHeaders() });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(asString((payload as JsonRecord).error, response.statusText));
    return payload as JsonRecord;
  }, [authHeaders, normalizedBase]);

  const sendJson = useCallback(async (path: string, method: string, body: JsonRecord): Promise<JsonRecord> => {
    const response = await fetch(`${normalizedBase}${path}`, {
      method,
      headers: authHeaders(true),
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(asString((payload as JsonRecord).error, response.statusText));
    return payload as JsonRecord;
  }, [authHeaders, normalizedBase]);

  const saveConnection = useCallback(() => {
    const nextUrl = normalizeAgentUrl(agentUrl);
    setAgentUrl(nextUrl);
    window.localStorage.setItem(storage.agentUrl, nextUrl);
    window.localStorage.setItem(storage.token, token.trim());
    window.localStorage.setItem(storage.sessionKey, sessionKey.trim() || defaultSessionKey());
    window.localStorage.setItem(storage.modelOverride, modelOverride.trim() || "auto");
    addEvent("connection_saved", { agentUrl: nextUrl, sessionKey, modelOverride });
  }, [addEvent, agentUrl, modelOverride, sessionKey, token]);

  const loadRuntimeSnapshot = useCallback(async () => {
    const payload = await getJson("/v1/app/runtime?limit=16");
    setRuntime(payload as RuntimeSnapshot);
    addEvent("runtime_snapshot", {
      sessions: (payload.sessions as JsonRecord | undefined)?.count ?? 0,
      tasks: (payload.tasks as JsonRecord | undefined)?.count ?? 0,
      crons: (payload.crons as JsonRecord | undefined)?.count ?? 0,
      artifacts: (payload.artifacts as JsonRecord | undefined)?.count ?? 0,
    });
  }, [addEvent, getJson]);

  const loadAppConfig = useCallback(async () => {
    const payload = await getJson("/v1/app/config");
    setConfig((payload.config as AppConfig | undefined) ?? {});
    addEvent("config_loaded", { exists: payload.exists === true });
  }, [addEvent, getJson]);

  const loadTranscript = useCallback(async () => {
    const payload = await getJson(`/v1/app/transcript?sessionKey=${encodeURIComponent(sessionKey || defaultSessionKey())}&limit=80`);
    setTranscriptItems(asArray(payload.entries));
    addEvent("transcript_loaded", { count: asArray(payload.entries).length });
  }, [addEvent, getJson, sessionKey]);

  const loadEvidence = useCallback(async () => {
    const payload = await getJson(`/v1/app/evidence?sessionKey=${encodeURIComponent(sessionKey || defaultSessionKey())}&limit=20`);
    setEvidenceItems(asArray(payload.turns));
    addEvent("evidence_loaded", { count: asArray(payload.turns).length });
  }, [addEvent, getJson, sessionKey]);

  const loadKnowledge = useCallback(async () => {
    const payload = await getJson("/v1/app/knowledge");
    setKnowledgeItems([...asArray(payload.collections), ...asArray(payload.documents)]);
    addEvent("knowledge_loaded", {
      collections: asArray(payload.collections).length,
      documents: asArray(payload.documents).length,
    });
  }, [addEvent, getJson]);

  const searchKnowledge = useCallback(async () => {
    if (!knowledgeQuery.trim()) {
      await loadKnowledge();
      return;
    }
    const payload = await getJson(`/v1/app/knowledge/search?q=${encodeURIComponent(knowledgeQuery.trim())}&limit=12`);
    setKnowledgeItems(asArray(payload.results));
    addEvent("knowledge_search", { query: knowledgeQuery, count: asArray(payload.results).length });
  }, [addEvent, getJson, knowledgeQuery, loadKnowledge]);

  const saveKnowledge = useCallback(async () => {
    if (!knowledgePath.trim()) throw new Error("KB path is required");
    const payload = await sendJson("/v1/app/knowledge/file", "PUT", {
      path: knowledgePath.trim(),
      content: knowledgeContent,
    });
    addEvent("knowledge_file_saved", { path: asString(payload.path, knowledgePath) });
    await loadKnowledge();
  }, [addEvent, knowledgeContent, knowledgePath, loadKnowledge, sendJson]);

  const loadWorkspace = useCallback(async () => {
    const payload = await getJson(`/v1/app/workspace?path=${encodeURIComponent(workspacePath || ".")}`);
    setWorkspacePath(asString(payload.path, "."));
    setWorkspaceItems(asArray(payload.entries));
    addEvent("workspace_loaded", { count: asArray(payload.entries).length });
  }, [addEvent, getJson, workspacePath]);

  const searchMemory = useCallback(async () => {
    if (!memoryQuery.trim()) return;
    const payload = await getJson(`/v1/app/memory/search?q=${encodeURIComponent(memoryQuery.trim())}&limit=8`);
    addEvent("memory_search", { query: memoryQuery, count: asArray(payload.results).length });
  }, [addEvent, getJson, memoryQuery]);

  const compactMemory = useCallback(async () => {
    await sendJson("/v1/app/memory/compact", "POST", { force: true });
    addEvent("memory_compacted", {});
  }, [addEvent, sendJson]);

  const saveCron = useCallback(async () => {
    await sendJson("/v1/app/crons", "POST", {
      expression: cronExpression.trim(),
      prompt: cronPrompt,
      sessionKey,
      durable: true,
      enabled: true,
    });
    addEvent("cron_saved", { expression: cronExpression });
    await loadRuntimeSnapshot();
  }, [addEvent, cronExpression, cronPrompt, loadRuntimeSnapshot, sendJson, sessionKey]);

  const reloadSkills = useCallback(async () => {
    const payload = await sendJson("/v1/app/skills/reload", "POST", {});
    addEvent("skills_reloaded", {
      loaded: Array.isArray(payload.loaded) ? payload.loaded.length : 0,
      issues: Array.isArray(payload.issues) ? payload.issues.length : 0,
    });
    await loadRuntimeSnapshot();
  }, [addEvent, loadRuntimeSnapshot, sendJson]);

  const saveAppConfig = useCallback(async () => {
    const llm = config.llm ?? {};
    const server = config.server ?? {};
    const payload = await sendJson("/v1/app/config", "PUT", {
      llm: {
        provider: llm.provider ?? "openai-compatible",
        model: llm.model ?? "llama3.1",
        baseUrl: llm.baseUrl ?? "",
        apiKeyEnvVar: llm.apiKeyEnvVar ?? "",
        capabilities: llm.capabilities,
      },
      server: {
        gatewayTokenEnvVar: server.gatewayTokenEnvVar ?? "MAGI_AGENT_SERVER_TOKEN",
      },
      workspace: config.workspace ?? "./workspace",
    });
    setConfigStatus(payload.restartRequired === true ? "Runtime restart required" : "Config saved");
    addEvent("config_saved", { model: llm.model ?? "llama3.1" });
  }, [addEvent, config, sendJson]);

  const reloadRuntimeConfig = useCallback(async () => {
    const payload = await sendJson("/v1/app/config/reload", "POST", {});
    setConfig((payload.config as AppConfig | undefined) ?? config);
    setConfigStatus(payload.restartRequired === true ? "Runtime restart required" : "Runtime config is current");
    addEvent("config_reload_status", { restartRequired: payload.restartRequired === true });
  }, [addEvent, config, sendJson]);

  const saveHarnessRule = useCallback(async () => {
    if (!harnessName.trim()) throw new Error("Rule file name is required");
    await sendJson(`/v1/app/harness-rules/${encodeURIComponent(harnessName.trim())}`, "PUT", {
      content: harnessContent,
    });
    addEvent("harness_rule_saved", { name: harnessName.trim() });
  }, [addEvent, harnessContent, harnessName, sendJson]);

  const checkRuntime = useCallback(async () => {
    setRuntimeStatus("checking");
    try {
      const response = await fetch(`${normalizedBase}/health`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(asString((payload as JsonRecord).error, response.statusText));
      setRuntimeStatus("active");
      addEvent("health", payload as JsonRecord);
      await Promise.allSettled([loadRuntimeSnapshot(), loadAppConfig(), loadKnowledge(), loadTranscript(), loadEvidence(), loadWorkspace()]);
    } catch (error) {
      setRuntimeStatus("unavailable");
      addEvent("health_error", { message: String(error instanceof Error ? error.message : error) });
    }
  }, [addEvent, loadAppConfig, loadEvidence, loadKnowledge, loadRuntimeSnapshot, loadTranscript, loadWorkspace, normalizedBase]);

  const appendAssistantText = useCallback((text: string) => {
    setMessages((current) => {
      const last = current[current.length - 1];
      if (last?.role === "assistant" && last.streaming) {
        return [...current.slice(0, -1), { ...last, text: last.text + text }];
      }
      return [...current, { id: nowId("assistant"), role: "assistant", text, streaming: true }];
    });
  }, []);

  const finishAssistantMessage = useCallback(() => {
    setMessages((current) => current.map((message) => message.streaming ? { ...message, streaming: false } : message));
  }, []);

  const handleSseEvent = useCallback((eventName: string, rawData: string) => {
    if (rawData === "[DONE]") {
      finishAssistantMessage();
      addEvent("done", {});
      return;
    }
    let payload: JsonRecord;
    try {
      payload = JSON.parse(rawData) as JsonRecord;
    } catch {
      addEvent("sse_parse_error", { eventName, rawData });
      return;
    }
    if (eventName === "agent") {
      const type = asString(payload.type, "agent");
      addEvent(type, payload);
      if (type === "text_delta" && typeof payload.delta === "string") appendAssistantText(payload.delta);
      if (type === "turn_end") finishAssistantMessage();
      return;
    }
    const choices = Array.isArray(payload.choices) ? payload.choices : [];
    const delta = choices[0] && typeof choices[0] === "object"
      ? (((choices[0] as JsonRecord).delta as JsonRecord | undefined)?.content)
      : undefined;
    if (typeof delta === "string") appendAssistantText(delta);
    if (choices[0] && typeof choices[0] === "object" && (choices[0] as JsonRecord).finish_reason) finishAssistantMessage();
  }, [addEvent, appendAssistantText, finishAssistantMessage]);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || isStreaming) return;
    saveConnection();
    setInput("");
    setMessages((current) => [...current, { id: nowId("user"), role: "user", text }]);
    setIsStreaming(true);
    try {
      const response = await fetch(`${normalizedBase}/v1/chat/completions`, {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({
          stream: true,
          ...(modelOverride.trim() && modelOverride.trim() !== "auto" ? { model: modelOverride.trim() } : {}),
          messages: [{ role: "user", content: text }],
        }),
      });
      if (!response.ok || !response.body) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(asString((payload as JsonRecord).error, response.statusText));
      }
      const decoder = new TextDecoder();
      const parser = createSseParser(handleSseEvent);
      const reader = response.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        parser(decoder.decode(value, { stream: true }));
      }
      parser(decoder.decode());
      finishAssistantMessage();
      await Promise.allSettled([loadRuntimeSnapshot(), loadTranscript(), loadEvidence()]);
    } catch (error) {
      finishAssistantMessage();
      setMessages((current) => [...current, { id: nowId("error"), role: "assistant", text: String(error instanceof Error ? error.message : error), error: true }]);
      addEvent("send_error", { message: String(error instanceof Error ? error.message : error) });
    } finally {
      setIsStreaming(false);
    }
  }, [addEvent, authHeaders, finishAssistantMessage, handleSseEvent, input, isStreaming, loadEvidence, loadRuntimeSnapshot, loadTranscript, modelOverride, normalizedBase, saveConnection]);

  useEffect(() => {
    addEvent("app_ready", { agentUrl, sessionKey });
    const installHandler = (event: Event) => {
      event.preventDefault();
      setDeferredInstallPrompt(event as BeforeInstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", installHandler);
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker
        .register("/app/sw.js", { scope: "/app/" })
        .then(() => addEvent("service_worker_ready", {}))
        .catch((error: Error) => addEvent("service_worker_error", { message: error.message }));
    }
    void checkRuntime();
    return () => window.removeEventListener("beforeinstallprompt", installHandler);
    // Intentionally one boot pass. The latest connection settings are saved by explicit actions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const installApp = useCallback(async () => {
    if (!deferredInstallPrompt) return;
    const prompt = deferredInstallPrompt;
    setDeferredInstallPrompt(null);
    await prompt.prompt();
    const choice = await prompt.userChoice;
    addEvent("install_prompt", { outcome: choice.outcome });
  }, [addEvent, deferredInstallPrompt]);

  const knowledgeProps = {
    knowledgeQuery,
    setKnowledgeQuery,
    knowledgePath,
    setKnowledgePath,
    knowledgeContent,
    setKnowledgeContent,
    knowledgeItems,
    onSearchKnowledge: () => void searchKnowledge().catch((error) => addEvent("knowledge_error", { message: String(error instanceof Error ? error.message : error) })),
    onLoadKnowledge: () => void loadKnowledge().catch((error) => addEvent("knowledge_error", { message: String(error instanceof Error ? error.message : error) })),
    onSaveKnowledge: () => void saveKnowledge().catch((error) => addEvent("knowledge_error", { message: String(error instanceof Error ? error.message : error) })),
  };

  if (active === "chat") {
    return (
      <ChatView
        activeChannel={activeChannel}
        setActiveChannel={setActiveChannel}
        setActive={setActive}
        runtimeStatus={runtimeStatus}
        onRefresh={() => void checkRuntime()}
        messages={messages}
        input={input}
        setInput={setInput}
        isStreaming={isStreaming}
        onSend={() => void sendMessage()}
        onReset={() => setMessages([])}
        modelOverride={modelOverride}
        setModelOverride={setModelOverride}
        activeDock={activeDock}
        setActiveDock={setActiveDock}
        runtime={runtime}
        events={events}
        knowledgeProps={knowledgeProps}
        onReloadSkills={() => void reloadSkills().catch((error) => addEvent("skills_reload_error", { message: String(error instanceof Error ? error.message : error) }))}
      />
    );
  }

  return (
    <div className="dashboard-shell" data-dashboard-shell="true">
      <DashboardSidebar active={active} setActive={setActive} runtimeStatus={runtimeStatus} />
      {active === "overview" && <Overview runtime={runtime} eventCount={events.length} />}
      {active === "settings" && (
        <Settings
          agentUrl={agentUrl}
          setAgentUrl={setAgentUrl}
          token={token}
          setToken={setToken}
          sessionKey={sessionKey}
          setSessionKey={setSessionKey}
          planMode={planMode}
          setPlanMode={setPlanMode}
          runtimeStatus={runtimeStatus}
          onSaveConnection={saveConnection}
          onCheckRuntime={() => void checkRuntime()}
          installAvailable={!!deferredInstallPrompt}
          onInstall={() => void installApp()}
          config={config}
          setConfig={setConfig}
          onSaveConfig={() => void saveAppConfig().catch((error) => addEvent("config_error", { message: String(error instanceof Error ? error.message : error) }))}
          onReloadConfig={() => void reloadRuntimeConfig().catch((error) => addEvent("config_error", { message: String(error instanceof Error ? error.message : error) }))}
          configStatus={configStatus}
          harnessName={harnessName}
          setHarnessName={setHarnessName}
          harnessContent={harnessContent}
          setHarnessContent={setHarnessContent}
          onSaveHarnessRule={() => void saveHarnessRule().catch((error) => addEvent("harness_rule_error", { message: String(error instanceof Error ? error.message : error) }))}
        />
      )}
      {active === "knowledge" && <KnowledgePage {...knowledgeProps} />}
      {active === "usage" && (
        <UtilityPage title="Usage" description="Local runtime usage counters and recent proof events.">
          <RuntimeMetrics runtime={runtime} eventCount={events.length} />
          <SnapshotList id="transcript-list" items={transcriptItems} empty="No transcript entries" />
          <SnapshotList id="evidence-list" items={evidenceItems} empty="No runtime proof evidence" />
        </UtilityPage>
      )}
      {active === "skills" && (
        <UtilityPage title="Skills" description="Reload and inspect workspace SKILL.md capabilities.">
          <button id="reload-skills-button" type="button" onClick={() => void reloadSkills()}>Reload Skills</button>
          <SnapshotList id="skills-list" items={runtime?.skills?.items ?? []} empty="No loaded skills" />
          <SnapshotList id="tools-list" items={runtime?.tools?.items ?? []} empty="No registered tools" />
        </UtilityPage>
      )}
      {active === "converter" && (
        <UtilityPage title="Converter" description="Workspace files, memory, schedules, and local runtime utilities.">
          <form id="workspace-form" onSubmit={(event: FormEvent) => { event.preventDefault(); void loadWorkspace(); }}>
            <Field label="Workspace path">
              <input id="workspace-path" value={workspacePath} onChange={(event) => setWorkspacePath(event.target.value)} />
            </Field>
            <button type="submit">List files</button>
          </form>
          <SnapshotList id="workspace-list" items={workspaceItems} empty="No workspace files" />
          <form id="memory-search-form" onSubmit={(event: FormEvent) => { event.preventDefault(); void searchMemory(); }}>
            <Field label="Search memory">
              <input id="memory-search-query" value={memoryQuery} onChange={(event) => setMemoryQuery(event.target.value)} />
            </Field>
            <button type="submit">Search memory</button>
            <button id="memory-compact-button" type="button" onClick={() => void compactMemory()}>Compact memory</button>
          </form>
          <form id="cron-editor-form" onSubmit={(event: FormEvent) => { event.preventDefault(); void saveCron(); }}>
            <Field label="Expression">
              <input id="cron-expression" value={cronExpression} onChange={(event) => setCronExpression(event.target.value)} />
            </Field>
            <Field label="Prompt">
              <textarea id="cron-prompt" value={cronPrompt} onChange={(event) => setCronPrompt(event.target.value)} />
            </Field>
            <button type="submit">Save Cron</button>
          </form>
        </UtilityPage>
      )}
    </div>
  );
}
