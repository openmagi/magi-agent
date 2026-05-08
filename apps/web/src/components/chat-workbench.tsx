import { useState } from "react";
import { phaseLabel, WorkInspector } from "./work-inspector";

type Section = "chat" | "overview" | "settings" | "usage" | "skills" | "knowledge" | "converter";
type DockView = "work" | "knowledge";
type Role = "user" | "assistant" | "system";
type JsonRecord = Record<string, unknown>;
type TurnPhase = "pending" | "planning" | "executing" | "verifying" | "committing" | "committed" | "aborted";
type ToolActivityStatus = "running" | "done" | "error" | "denied";
type SubagentActivityStatus = "running" | "waiting" | "done" | "error" | "cancelled";

interface Message {
  id: string;
  role: Role;
  text: string;
  streaming?: boolean;
  error?: boolean;
}

interface AppChannel {
  id: string;
  name: string;
  displayName: string | null;
  category: string | null;
  position: number;
}

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
        <path d="M12 19V5" />
        <path d="m5 12 7-7 7 7" />
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

function defaultLocalChannels(): AppChannel[] {
  return [
    {
      id: "local-general",
      name: "general",
      displayName: null,
      category: "General",
      position: 0,
    },
  ];
}

function groupLocalChannels(channels: AppChannel[]): Array<{ title: string; channels: AppChannel[] }> {
  const grouped = new Map<string, AppChannel[]>();
  for (const channel of channels) {
    const category = channel.category || "General";
    grouped.set(category, [...(grouped.get(category) ?? []), channel]);
  }
  return Array.from(grouped.entries()).map(([title, items]) => ({
    title,
    channels: [...items].sort((a, b) => a.position - b.position),
  }));
}

function normalizeChannelName(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-_ ]/g, "")
    .replace(/\s+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function ChatSidebar({
  channels,
  activeChannel,
  setActiveChannel,
  setActive,
  onRefresh,
  runtimeStatus,
  editing,
  onToggleEdit,
  onCancelEdit,
  onCreateChannel,
}: {
  channels: AppChannel[];
  activeChannel: string;
  setActiveChannel: (channel: string) => void;
  setActive: (section: Section) => void;
  onRefresh: () => void;
  runtimeStatus: string;
  editing: boolean;
  onToggleEdit: () => void;
  onCancelEdit: () => void;
  onCreateChannel: (name: string) => void;
}) {
  const [showNewChannel, setShowNewChannel] = useState(false);
  const [newChannelName, setNewChannelName] = useState("");
  const groupedChannels = groupLocalChannels(channels.length > 0 ? channels : defaultLocalChannels());
  const createChannel = () => {
    const name = normalizeChannelName(newChannelName);
    if (!name) return;
    onCreateChannel(name);
    setNewChannelName("");
    setShowNewChannel(false);
  };

  return (
    <aside className="chat-sidebar" aria-label="Chat channels">
      <div className="bot-status">
        <div>
          <strong>Magi_Local</strong>
          <span><i /> {runtimeStatus}</span>
        </div>
      </div>
      <div className="chat-edit-row">
        {editing && <button type="button" onClick={onCancelEdit}>Cancel</button>}
        <button type="button" onClick={onToggleEdit}>{editing ? "Done" : "Edit"}</button>
      </div>
      <nav className="channel-scroll">
        {groupedChannels.map(({ title, channels: channelItems }) => (
          <div key={title} className="channel-group">
            <div className="channel-group-label">{title}</div>
            {channelItems.map((channel) => (
              <button
                key={channel.id}
                className={`channel-row ${activeChannel === channel.name ? "active" : ""}`}
                type="button"
                onClick={() => setActiveChannel(channel.name)}
              >
                <span>#</span>
                <strong>{channel.displayName || channel.name}</strong>
              </button>
            ))}
          </div>
        ))}
        <div className="channel-add-row">
          <button type="button" onClick={() => setShowNewChannel(true)}>
            <span>+</span>
            Add Channel
          </button>
        </div>
      </nav>
      <div className="chat-sidebar-bottom">
        <button type="button" onClick={onRefresh}><Icon name="refresh" /> Refresh</button>
        <button type="button" onClick={() => setActive("overview")}><Icon name="settings" /> Dashboard</button>
      </div>
      {showNewChannel && (
        <div className="local-modal-backdrop" onClick={() => setShowNewChannel(false)}>
          <div className="local-modal" onClick={(event) => event.stopPropagation()}>
            <h3>New Channel</h3>
            <input
              value={newChannelName}
              onChange={(event) => setNewChannelName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") createChannel();
                if (event.key === "Escape") setShowNewChannel(false);
              }}
              placeholder="Channel name"
              autoFocus
            />
            <div className="modal-actions">
              <button type="button" onClick={() => setShowNewChannel(false)}>Cancel</button>
              <button type="button" onClick={createChannel}>Create</button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}

function EmptyChatIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 8h10" />
      <path d="M7 12h7" />
      <path d="M5 19.5V6a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3H8.5Z" />
    </svg>
  );
}

export function ChatWorkbench({
  channels,
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
  streamingMode,
  setStreamingMode,
  activeDock,
  setActiveDock,
  runtime,
  events,
  channelState,
  queuedMessages,
  controlRequests,
  knowledgeProps,
  onReloadSkills,
  editingChannels,
  onToggleEditChannels,
  onCancelEditChannels,
  onCreateChannel,
}: {
  channels: AppChannel[];
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
  streamingMode: "queue" | "steer";
  setStreamingMode: (value: "queue" | "steer") => void;
  activeDock: DockView;
  setActiveDock: (view: DockView) => void;
  runtime: RuntimeSnapshot | null;
  events: EventRecord[];
  channelState: ChannelState;
  queuedMessages: QueuedMessage[];
  controlRequests: ControlRequestRecord[];
  knowledgeProps: KnowledgeProps;
  onReloadSkills: () => void;
  editingChannels: boolean;
  onToggleEditChannels: () => void;
  onCancelEditChannels: () => void;
  onCreateChannel: (name: string) => void;
}) {
  const activeToolCount = (channelState.activeTools ?? []).filter((tool) => tool.status === "running").length;
  const activeSubagentCount = (channelState.subagents ?? []).filter((subagent) => subagent.status === "running" || subagent.status === "waiting").length;
  const pendingRequests = controlRequests.filter((request) => request.state === "pending");
  const visibleRunState =
    channelState.streaming ||
    activeToolCount > 0 ||
    activeSubagentCount > 0 ||
    queuedMessages.length > 0 ||
    pendingRequests.length > 0 ||
    Boolean(channelState.taskBoard?.tasks.some((task) => task.status === "pending" || task.status === "in_progress"));
  const currentWork =
    channelState.taskBoard?.tasks.find((task) => task.status === "in_progress")?.title ||
    channelState.activeTools?.find((tool) => tool.status === "running")?.label ||
    channelState.subagents?.find((subagent) => subagent.status === "running" || subagent.status === "waiting")?.detail ||
    (isStreaming ? "Working on your request" : "Waiting for input");

  return (
    <div className="cloud-chat-shell" data-cloud-chat-shell="true">
      <ChatSidebar
        channels={channels}
        activeChannel={activeChannel}
        setActiveChannel={setActiveChannel}
        setActive={setActive}
        onRefresh={onRefresh}
        runtimeStatus={runtimeStatus}
        editing={editingChannels}
        onToggleEdit={onToggleEditChannels}
        onCancelEdit={onCancelEditChannels}
        onCreateChannel={onCreateChannel}
      />
      <main className="chat-main">
        <header className="chat-header">
          <h1>{activeChannel}</h1>
          <button id="clear-button" type="button" onClick={onReset}>Reset</button>
        </header>
        <div id="messages" className="message-timeline" aria-live="polite">
          {messages.length === 0 ? (
            <section className="empty-chat">
              <div className="empty-chat-icon"><EmptyChatIcon /></div>
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
        {visibleRunState && (
          <section className="current-run-card" aria-label="Current run">
            <button type="button" className="run-card-close">×</button>
            <div className="run-grid">
              <span>CURRENT RUN</span>
              <strong>{channelState.reconnecting ? "Reconnecting" : phaseLabel(channelState.turnPhase ?? null)}</strong>
              <span>CURRENT WORK</span>
              <strong>{currentWork}</strong>
              {activeToolCount > 0 && (
                <>
                  <span>ACTIONS</span>
                  <strong>{activeToolCount} active</strong>
                </>
              )}
              {activeSubagentCount > 0 && (
                <>
                  <span>HELPERS</span>
                  <strong>{activeSubagentCount} active</strong>
                </>
              )}
            </div>
          </section>
        )}
        <form
          id="message-form"
          className="chat-composer"
          data-chat-input-shell="true"
          onSubmit={(event) => {
            event.preventDefault();
            onSend();
          }}
        >
          {isStreaming && (
            <div className="composer-mode-row" aria-label="Streaming send mode">
              <button type="button" className={streamingMode === "queue" ? "mode-active" : ""} onClick={() => setStreamingMode("queue")}>Queue after run</button>
              <button type="button" className={streamingMode === "steer" ? "mode-active" : ""} onClick={() => setStreamingMode("steer")}>Steer current run</button>
            </div>
          )}
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
      <WorkInspector
        activeDock={activeDock}
        setActiveDock={setActiveDock}
        runtime={runtime}
        events={events}
        channelState={channelState}
        queuedMessages={queuedMessages}
        controlRequests={controlRequests}
        onReloadSkills={onReloadSkills}
        {...knowledgeProps}
      />
    </div>
  );
}
