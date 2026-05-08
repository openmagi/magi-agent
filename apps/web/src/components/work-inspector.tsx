import { ReactNode } from "react";

type DockView = "work" | "knowledge";
type JsonRecord = Record<string, unknown>;
type TurnPhase = "pending" | "planning" | "executing" | "verifying" | "committing" | "committed" | "aborted";
type ToolActivityStatus = "running" | "done" | "error" | "denied";
type SubagentActivityStatus = "running" | "waiting" | "done" | "error" | "cancelled";

interface ToolActivity {
  id: string;
  label: string;
  status: ToolActivityStatus;
  startedAt: number;
  inputPreview?: string;
  outputPreview?: string;
  durationMs?: number;
}

interface SubagentActivity {
  taskId: string;
  role: string;
  status: SubagentActivityStatus;
  detail?: string;
  startedAt: number;
  updatedAt: number;
}

interface TaskBoardTask {
  id: string;
  title: string;
  description: string;
  status: "pending" | "in_progress" | "completed" | "cancelled";
  parallelGroup?: string;
  dependsOn?: string[];
}

interface TaskBoardSnapshot {
  tasks: TaskBoardTask[];
  receivedAt: number;
}

interface ChannelState {
  streaming: boolean;
  streamingText: string;
  thinkingText: string;
  error: string | null;
  hasTextContent?: boolean;
  thinkingStartedAt?: number | null;
  turnPhase?: TurnPhase | null;
  heartbeatElapsedMs?: number | null;
  pendingInjectionCount?: number;
  activeTools?: ToolActivity[];
  subagents?: SubagentActivity[];
  taskBoard?: TaskBoardSnapshot | null;
  fileProcessing?: boolean;
  reconnecting?: boolean;
  saveError?: string | null;
}

interface QueuedMessage {
  id: string;
  content: string;
  priority?: "now" | "next" | "later";
  queuedAt: number;
}

interface ControlRequestRecord {
  requestId: string;
  kind: "tool_permission" | "plan_approval" | "user_question";
  state: "pending" | "approved" | "denied" | "answered" | "cancelled" | "timed_out";
  sessionKey: string;
  turnId?: string;
  channelName?: string;
  source: "turn" | "mcp" | "child-agent" | "plan" | "system";
  prompt: string;
  proposedInput?: unknown;
  createdAt: number;
  expiresAt: number;
  resolvedAt?: number;
  decision?: "approved" | "denied" | "answered";
  feedback?: string;
  updatedInput?: unknown;
  answer?: string;
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

interface KnowledgeProps {
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
}

type WorkConsoleRowGroup = "status" | "tool" | "subagent" | "task" | "queue" | "control";
type WorkConsoleRowStatus = "running" | "done" | "waiting" | "error" | "info";

interface WorkConsoleRow {
  id: string;
  group: WorkConsoleRowGroup;
  label: string;
  detail?: string;
  snippet?: string;
  status: WorkConsoleRowStatus;
  meta?: string;
}

const GROUP_LABELS: Record<WorkConsoleRowGroup, string> = {
  status: "Now",
  tool: "Current steps",
  subagent: "Helpers",
  task: "Plan",
  queue: "Queued messages",
  control: "Needs input",
};

const SUBAGENT_NAMES = [
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

const LOW_SIGNAL_TOOL_LABELS = new Set(["glob", "grep", "taskget", "subagentrunning", "subagenttooldecision"]);

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asArray(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter((item): item is JsonRecord => !!item && typeof item === "object") : [];
}

function preview(value: unknown, max = 140): string {
  const text = typeof value === "string" ? value : value == null ? "" : String(value);
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length > max ? `${collapsed.slice(0, max - 3)}...` : collapsed;
}

function formatElapsed(ms?: number | null): string | undefined {
  if (!ms || ms < 1000) return undefined;
  return `${Math.max(1, Math.round(ms / 1000))}s`;
}

export function phaseLabel(phase: ChannelState["turnPhase"]): string {
  switch (phase) {
    case "pending":
      return "Preparing";
    case "planning":
      return "Planning";
    case "executing":
      return "Running";
    case "verifying":
      return "Checking work";
    case "committing":
      return "Writing answer";
    case "committed":
      return "Finishing";
    case "aborted":
      return "Interrupted";
    default:
      return "Working";
  }
}

function normalizeRole(role: string): string {
  const value = role.trim().toLowerCase();
  if (value === "explore" || value === "explorer" || value === "research") return "research";
  if (value === "review" || value === "reviewer") return "review";
  if (value === "work" || value === "worker") return "worker";
  return value || "helper";
}

function normalizeToolLabel(label: string): string {
  return label.replace(/[^a-z0-9]/gi, "").toLowerCase();
}

function shouldDisplayToolActivity(activity: ToolActivity): boolean {
  return !LOW_SIGNAL_TOOL_LABELS.has(normalizeToolLabel(activity.label));
}

function subagentName(index: number): string {
  return SUBAGENT_NAMES[index % SUBAGENT_NAMES.length] ?? `Agent ${index + 1}`;
}

function statusFromTool(activity: ToolActivity): WorkConsoleRowStatus {
  if (activity.status === "running") return "running";
  if (activity.status === "done") return "done";
  if (activity.status === "error" || activity.status === "denied") return "error";
  return "info";
}

function statusFromSubagent(activity: SubagentActivity): WorkConsoleRowStatus {
  if (activity.status === "running") return "running";
  if (activity.status === "waiting") return "waiting";
  if (activity.status === "done") return "done";
  if (activity.status === "error" || activity.status === "cancelled") return "error";
  return "info";
}

function statusFromTask(task: TaskBoardTask): WorkConsoleRowStatus {
  if (task.status === "in_progress") return "running";
  if (task.status === "completed") return "done";
  if (task.status === "cancelled") return "error";
  return "waiting";
}

function taskMeta(task: TaskBoardTask): string {
  if (task.status === "in_progress") return "running";
  if (task.status === "completed") return "done";
  if (task.status === "cancelled") return "cancelled";
  return "pending";
}

function parseMaybeJson(text?: string): JsonRecord | null {
  if (!text) return null;
  const trimmed = text.trim();
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
  try {
    const parsed = JSON.parse(trimmed);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as JsonRecord : null;
  } catch {
    return null;
  }
}

function firstText(record: JsonRecord, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function summarizeToolPayload(text?: string): string | undefined {
  if (!text) return undefined;
  const parsed = parseMaybeJson(text);
  if (!parsed) return preview(text, 160);

  const path = firstText(parsed, ["path", "file", "filePath", "targetPath"]);
  const command = firstText(parsed, ["command", "cmd"]);
  const action = firstText(parsed, ["action"]);
  const stdout = firstText(parsed, ["stdout", "output", "result", "finalText", "content"]);
  const prompt = firstText(parsed, ["prompt", "query", "text"]);
  const error = firstText(parsed, ["error", "errorMessage", "message", "reason"]);
  const operation = firstText(parsed, ["operation"]);

  if (path && stdout) return `${path} - ${preview(stdout, 120)}`;
  if (path) return path;
  if (command) return `Command: ${preview(command, 140)}`;
  if (action === "create_session") return "Opened a browser workspace.";
  if (action === "scrape") return "Read the current browser page.";
  if (action) return `Browser action: ${action.replace(/_/g, " ")}`;
  if (operation) return `Calculated ${operation}${stdout ? `: ${preview(stdout, 80)}` : ""}`;
  if (prompt) return preview(prompt, 160);
  if (stdout) return preview(stdout, 160);
  if (error) return preview(error, 160);
  return "Processed tool result.";
}

function humanToolLabel(label: string, inputPreview?: string, outputPreview?: string): string {
  const normalized = normalizeToolLabel(label);
  if (normalized === "fileread") return "Reviewing document";
  if (normalized === "filewrite") return "Writing file";
  if (normalized === "fileedit") return "Editing file";
  if (normalized === "bash") return "Running command";
  if (normalized === "browser") return "Using browser";
  if (normalized === "spawnagent") return "Starting helper";
  if (normalized === "calculation") return "Calculating";
  if (normalized === "codeworkspace") return "Working in workspace";
  if (normalized === "documentsend" || normalized === "filesend" || normalized === "filedeliver") return "Delivering file";
  if (normalized === "websearch") return "Searching the web";
  if (normalized === "knowledgesearch") return "Searching knowledge";
  if (summarizeToolPayload(outputPreview)?.startsWith("Command:")) return "Running command";
  if (summarizeToolPayload(inputPreview)?.includes("/")) return "Reviewing document";
  return label.replace(/([a-z])([A-Z])/g, "$1 $2") || "Working";
}

function toolPreview(activity: ToolActivity): Pick<WorkConsoleRow, "label" | "detail" | "snippet"> {
  const detail = summarizeToolPayload(activity.inputPreview);
  const snippet = summarizeToolPayload(activity.outputPreview);
  return {
    label: humanToolLabel(activity.label, activity.inputPreview, activity.outputPreview),
    detail,
    snippet: snippet && snippet !== detail ? snippet : undefined,
  };
}

export function deriveWorkConsoleRows({
  channelState,
  queuedMessages = [],
  controlRequests = [],
}: {
  channelState: ChannelState;
  queuedMessages?: QueuedMessage[];
  controlRequests?: ControlRequestRecord[];
}): WorkConsoleRow[] {
  const rows: WorkConsoleRow[] = [];
  const phase = channelState.reconnecting
    ? "Reconnecting"
    : channelState.error
      ? "Blocked"
      : channelState.turnPhase
        ? phaseLabel(channelState.turnPhase)
        : channelState.streaming
          ? "Working"
          : null;
  const elapsed = formatElapsed(channelState.heartbeatElapsedMs);

  if (phase) {
    rows.push({
      id: "phase",
      group: "status",
      label: phase,
      detail: elapsed ? `${elapsed} elapsed` : undefined,
      status: channelState.error || channelState.turnPhase === "aborted" ? "error" : "running",
    });
  }

  for (const [index, subagent] of (channelState.subagents ?? []).entries()) {
    rows.push({
      id: `subagent:${subagent.taskId}`,
      group: "subagent",
      label: subagentName(index),
      detail: preview(subagent.detail, 90),
      status: statusFromSubagent(subagent),
      meta: normalizeRole(subagent.role),
    });
  }

  for (const activity of channelState.activeTools ?? []) {
    if (!shouldDisplayToolActivity(activity)) continue;
    const duration = activity.durationMs ? formatElapsed(activity.durationMs) : undefined;
    rows.push({
      id: `tool:${activity.id}`,
      group: "tool",
      ...toolPreview(activity),
      status: statusFromTool(activity),
      ...(duration ? { meta: duration } : {}),
    });
  }

  for (const task of channelState.taskBoard?.tasks ?? []) {
    rows.push({
      id: `task:${task.id}`,
      group: "task",
      label: task.title,
      detail: task.description,
      status: statusFromTask(task),
      meta: taskMeta(task),
    });
  }

  for (const [index, message] of queuedMessages.entries()) {
    rows.push({
      id: `queue:${message.id}`,
      group: "queue",
      label: index === 0 ? "Queued follow-up" : `Queued follow-up ${index + 1}`,
      detail: message.content,
      status: message.priority === "now" ? "running" : "waiting",
      meta: message.priority === "now" ? "steering next" : "will send later",
    });
  }

  for (const request of controlRequests.filter((item) => item.state === "pending")) {
    rows.push({
      id: `control:${request.requestId}`,
      group: "control",
      label: request.kind === "user_question" ? "Needs answer" : "Needs approval",
      detail: request.prompt,
      status: "waiting",
      meta: request.kind.replace("_", " "),
    });
  }

  if (rows.length === 0) {
    return [
      {
        id: "idle",
        group: "status",
        label: "Idle",
        detail: "Live agent work will appear here.",
        status: "info",
      },
    ];
  }
  return rows;
}

function groupWorkRows(rows: WorkConsoleRow[]): Array<[WorkConsoleRowGroup, WorkConsoleRow[]]> {
  const order: WorkConsoleRowGroup[] = ["status", "subagent", "tool", "task", "queue", "control"];
  return order
    .map((group) => [group, rows.filter((row) => row.group === group)] as [WorkConsoleRowGroup, WorkConsoleRow[]])
    .filter(([, groupRows]) => groupRows.length > 0);
}

function summarizeValue(value: unknown): string {
  if (typeof value === "string") return preview(value, 150);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return `${value.length} items`;
  if (value && typeof value === "object") {
    const record = value as JsonRecord;
    return firstText(record, ["title", "name", "path", "status", "message", "summary"]) || "details updated";
  }
  return "";
}

export function summarizeEventPayload(type: string, payload: JsonRecord): string {
  if (type === "runtime_snapshot") {
    return `${asNumber(payload.sessions)} sessions, ${asNumber(payload.tasks)} tasks, ${asNumber(payload.artifacts)} artifacts`;
  }
  if (type === "knowledge_search") {
    return `Searched "${asString(payload.query)}" and found ${asNumber(payload.count)} results.`;
  }
  if (type === "knowledge_loaded") {
    return `${asNumber(payload.documents)} documents across ${asNumber(payload.collections)} collections.`;
  }
  if (type === "message_queued") {
    return "Follow-up will run after the current answer finishes.";
  }
  if (type === "message_injected") {
    return "Instruction sent into the active run.";
  }
  if (type.endsWith("_error") || type === "send_error") {
    return firstText(payload, ["message", "error", "reason"]) || "The runtime reported an error.";
  }

  const direct = firstText(payload, ["summary", "message", "status", "phase", "name", "path", "query"]);
  if (direct) return preview(direct, 150);

  const entries = Object.entries(payload)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .slice(0, 3)
    .map(([key, value]) => `${key.replace(/([a-z])([A-Z])/g, "$1 $2")}: ${summarizeValue(value)}`)
    .filter(Boolean);

  return entries.length > 0 ? entries.join(" · ") : "Updated runtime state.";
}

function displayEventType(type: string): string {
  return type
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "green" | "purple" | "red" | "amber" }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
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
            {asString(item.status) || asString(item.kind) || asString(item.collection) || asString(item.permission) || summarizeValue(item.meta)}
          </span>
          <span className="snapshot-detail">
            {asString(item.detail) || asString(item.promptPreview) || asString(item.resultPreview) || asString(item.path) || preview(item.contentPreview || item.inputPreview || item.outputPreview, 120)}
          </span>
        </button>
      ))}
    </div>
  );
}

function Dot({ status }: { status: WorkConsoleRowStatus }) {
  const className =
    status === "running" ? "purple-dot" :
      status === "error" ? "red-dot" :
        status === "waiting" ? "amber-dot" :
          "green-dot";
  return <span className={className} />;
}

export function WorkInspector({
  activeDock,
  setActiveDock,
  runtime,
  events,
  channelState,
  queuedMessages,
  controlRequests,
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
  channelState: ChannelState;
  queuedMessages: QueuedMessage[];
  controlRequests: ControlRequestRecord[];
  onReloadSkills: () => void;
} & KnowledgeProps) {
  const sessionRows = runtime?.sessions?.items ?? [];
  const artifactRows = runtime?.artifacts?.items ?? [];
  const cronRows = runtime?.crons?.items ?? [];
  const workGroups = groupWorkRows(deriveWorkConsoleRows({ channelState, queuedMessages, controlRequests }));

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
          {workGroups.map(([group, rows]) => (
            <section key={group} className={`work-card work-card-${group} ${group === "status" ? "live" : ""} ${group === "subagent" ? "helpers-card" : ""}`}>
              <div className="work-card-title">
                <span>{GROUP_LABELS[group]}</span>
                {group === "status" && channelState.streaming && <Pill tone="purple">LIVE</Pill>}
                {group === "tool" && <Pill>{rows.length}</Pill>}
                {group === "subagent" && <Pill tone="green">{rows.length} AGENTS</Pill>}
                {group === "queue" && <Pill tone="amber">{rows.length} WAITING</Pill>}
              </div>
              <div
                id={group === "tool" ? "tasks-list" : undefined}
                className={group === "subagent" ? "helper-grid" : group === "tool" ? "current-step-list" : "work-row-list"}
              >
                {rows.map((row) => (
                  group === "subagent" ? (
                    <div key={row.id} className="helper-chip" data-work-console-agent-chip="true" data-work-console-row-status={row.status}>
                      <span className={`helper-dot ${row.status === "done" ? "green" : row.status === "waiting" ? "amber" : row.status === "error" ? "red" : "purple"}`} />
                      <strong>{row.label} {row.meta && <em>{row.meta}</em>}</strong>
                      {row.detail && <div className="helper-bar">{row.detail}</div>}
                    </div>
                  ) : (
                    <div key={row.id} className={`current-step-row work-row-${row.status}`} data-work-console-action-row={group === "tool" ? "true" : undefined} data-work-console-row-status={row.status}>
                      <Dot status={row.status} />
                      <div>
                        <strong>{row.label}</strong>
                        {row.meta && <small>{row.meta}</small>}
                        {row.detail && <p>{row.detail}</p>}
                        {row.snippet && <p className="step-summary">{row.snippet}</p>}
                      </div>
                    </div>
                  )
                ))}
              </div>
            </section>
          ))}
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
                  <strong>{displayEventType(event.type)}</strong>
                  <p className="event-summary">{summarizeEventPayload(event.type, event.payload)}</p>
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
