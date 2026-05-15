/** Response/progress language currently supported by the runtime policy. */
export type ChatResponseLanguage = "en" | "ko" | "ja" | "zh" | "es";

/** Quoted-reply metadata attached to a message authored in reply to another. */
export interface ReplyTo {
  /** ID (local id or serverId) of the message being replied to. */
  messageId: string;
  /** Short plain-text preview of the quoted message content (max ~80 chars). */
  preview: string;
  /** Role of the message being replied to — determines preview label. */
  role: "user" | "assistant";
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  serverId?: string;
  /** Persisted chain-of-thought text (Anthropic extended thinking) */
  thinkingContent?: string;
  /** Duration of thinking phase in seconds */
  thinkingDuration?: number;
  /** Tool/skill activities captured during the streaming phase. */
  activities?: ToolActivity[];
  /** Persisted TaskBoard snapshot captured during the streaming phase. */
  taskBoard?: TaskBoardSnapshot;
  /** Persisted research evidence metadata, when the runtime attached a claim/source audit. */
  researchEvidence?: ResearchArtifactDelta;
  /** Token/cost usage reported at turn completion. */
  usage?: TokenUsage;
  /** If present, this message was authored as a reply to another. */
  replyTo?: ReplyTo;
  /**
   * Set on user messages that landed mid-turn via POST /v1/chat/:botId/inject
   * (#86). Rendered with a small indicator so the user can see that this
   * message was absorbed into the already-running turn rather than kicking
   * off a fresh one.
   */
  injected?: boolean;
  /**
   * Assistant text length that was visible when a mid-turn steering message
   * was sent. Local UI uses this to place the user bubble inside the answer
   * instead of above the entire finalized assistant message.
   */
  injectedAfterChars?: number;
}

/**
 * Minimal per-task shape echoed by the core-agent `task_board` AgentEvent.
 * See infra/docker/magi-core-agent/src/tools/TaskBoard.ts and design §7.1.
 */
export interface TaskBoardTask {
  id: string;
  title: string;
  description: string;
  status: "pending" | "in_progress" | "completed" | "cancelled";
  parallelGroup?: string;
  dependsOn?: string[];
}

/** Full-board snapshot. Server sends the complete board every emission. */
export interface TaskBoardSnapshot {
  tasks: TaskBoardTask[];
  /** Client-side receive timestamp, used for sort stability only. */
  receivedAt: number;
}

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
}

export interface ResearchClaimRecord {
  claimId: string;
  text: string;
  claimType: "fact" | "uncertainty" | "inference" | "recommendation" | "limitation";
  supportStatus: "supported" | "partial" | "unsupported" | "uncertain";
  sourceIds: string[];
  confidence?: number;
  reasoning?: {
    premiseSourceIds: string[];
    inference: string;
    assumptions: string[];
    status: "source_backed" | "partial" | "missing_source_support" | "uncertain";
  };
}

export interface ResearchArtifactDelta {
  claims?: ResearchClaimRecord[];
  claimSourceLinks?: Array<{
    claimId: string;
    sourceId: string;
    support: "supports" | "partially_supports" | "contradicts" | "context";
  }>;
  contradictions?: Array<{
    contradictionId: string;
    claimIds: string[];
    sourceIds: string[];
    resolution?: string;
    status: "handled" | "unresolved" | "not_applicable";
  }>;
}

export type PatchPreviewOperation = "create" | "update" | "delete";

export interface PatchPreviewFile {
  path: string;
  operation: PatchPreviewOperation;
  hunks: number;
  addedLines: number;
  removedLines: number;
  oldSha256?: string;
  newSha256?: string;
}

export interface PatchPreview {
  dryRun: boolean;
  changedFiles: string[];
  createdFiles: string[];
  deletedFiles: string[];
  files: PatchPreviewFile[];
}

export interface DocumentDraftPreview {
  id: string;
  filename?: string;
  format: "md" | "txt";
  status: "streaming" | "done";
  contentPreview: string;
  contentLength: number;
  truncated: boolean;
  updatedAt: number;
}

export interface Channel {
  id: string;
  name: string;
  display_name: string | null;
  position: number;
  category: string | null;
  memory_mode?: ChannelMemoryMode;
  created_at: string;
}

export type ChannelMemoryMode = "normal" | "read_only" | "incognito";

export interface ToolActivity {
  /** Stable id — tool_call.id or synthesized for skill mentions */
  id: string;
  /** Human-readable label (skill name, function name, etc.) */
  label: string;
  /** running while in flight, done/error/denied after tool_end */
  status: "running" | "done" | "error" | "denied";
  startedAt: number;
  /** JSON-stringified tool input, truncated ~400 chars (from tool_start event) */
  inputPreview?: string;
  /** Tool output (success) or error message, truncated ~400 chars (from tool_end event) */
  outputPreview?: string;
  /** Structured patch preview emitted by PatchApply after preflight and before writes. */
  patchPreview?: PatchPreview;
  /** Tool execution duration in ms (populated on tool_end) */
  durationMs?: number;
}

export interface BrowserFrame {
  action: string;
  url?: string;
  imageBase64: string;
  contentType: "image/png" | "image/jpeg";
  capturedAt: number;
}

export type InspectedSourceKind =
  | "web_search"
  | "web_fetch"
  | "browser"
  | "kb"
  | "file"
  | "external_repo"
  | "external_doc"
  | "subagent_result";

export interface InspectedSource {
  sourceId: string;
  kind: InspectedSourceKind;
  uri: string;
  inspectedAt: number;
  turnId?: string;
  toolName?: string;
  toolUseId?: string;
  title?: string;
  contentHash?: string;
  contentType?: string;
  trustTier?: "primary" | "official" | "secondary" | "unknown";
  snippets?: string[];
}

export interface CitationGateStatus {
  ruleId: "claim-citation-gate";
  verdict: "pending" | "ok" | "violation";
  detail?: string;
  checkedAt: number;
}

export interface RuntimeTrace {
  turnId: string;
  phase:
    | "verifier_blocked"
    | "retry_scheduled"
    | "retry_aborted"
    | "terminal_abort";
  severity: "info" | "warning" | "error";
  title: string;
  detail?: string;
  reasonCode?: string;
  ruleId?: string;
  attempt?: number;
  maxAttempts?: number;
  retryable?: boolean;
  requiredAction?: string;
  receivedAt: number;
}

export type SubagentActivityStatus = "running" | "waiting" | "done" | "error" | "cancelled";

export interface SubagentActivity {
  taskId: string;
  role: string;
  status: SubagentActivityStatus;
  detail?: string;
  startedAt: number;
  updatedAt: number;
}

export interface MissionActivity {
  id: string;
  title: string;
  kind: string;
  status: "queued" | "running" | "blocked" | "waiting" | "completed" | "failed" | "cancelled" | "paused";
  detail?: string;
  updatedAt: number;
}

export interface ChannelState {
  streaming: boolean;
  streamingText: string;
  thinkingText: string;
  error: string | null;
  /** True once a user-visible assistant text delta has streamed this turn. */
  hasTextContent?: boolean;
  /** Timestamp when thinking phase started (for elapsed timer) */
  thinkingStartedAt?: number | null;
  /** Latest structured runtime phase from core-agent. */
  turnPhase?: "pending" | "planning" | "executing" | "verifying" | "committing" | "compacting" | "committed" | "aborted" | null;
  /** Latest heartbeat elapsed time while the current iteration is still alive. */
  heartbeatElapsedMs?: number | null;
  /** Best-effort user-facing goal for the current live turn. */
  currentGoal?: string | null;
  /** Count of explicit mid-turn injections accepted by the runtime. */
  pendingInjectionCount?: number;
  /** Live tool activity feed during streaming */
  activeTools?: ToolActivity[];
  /** Latest safe browser preview frame from parent or subagent browser work. */
  browserFrame?: BrowserFrame | null;
  /** Latest live markdown/text draft preview from an in-flight document write. */
  documentDraft?: DocumentDraftPreview | null;
  /** Live spawned subagent roster during streaming. */
  subagents?: SubagentActivity[];
  /** Live TaskBoard snapshot during streaming (replaced on each emission). */
  taskBoard?: TaskBoardSnapshot | null;
  /** Durable public Mission state for long-running work. */
  missions?: MissionActivity[];
  /** Active persistent goal mission id for this channel, when present. */
  activeGoalMissionId?: string | null;
  /** One-shot goal request accepted by the client while runtime mission creation is pending. */
  pendingGoalMissionTitle?: string | null;
  /** Live inspected source ledger records from the current research turn. */
  inspectedSources?: InspectedSource[];
  /** Latest claim-citation gate status for the current research turn. */
  citationGate?: CitationGateStatus | null;
  /** Public runtime verifier/retry/abort trace for the current turn. */
  runtimeTraces?: RuntimeTrace[];
  /** True while chat-proxy is processing file attachments (KB ingest) before bot receives the message */
  fileProcessing?: boolean;
  /** True when SSE stream dropped mid-response and client is polling active-snapshot to recover */
  reconnecting?: boolean;
  /** Warning when message save to server failed */
  saveError?: string | null;
  /** Best-effort target language for this live turn's user-visible response/progress. */
  responseLanguage?: ChatResponseLanguage;
  /** Token usage from the latest completed turn. */
  turnUsage?: ResponseUsage;
  /** Research evidence from the latest completed turn. */
  researchEvidence?: ResearchEvidenceSnapshot | null;
  /** Monotonically increasing mission refresh counter. */
  missionRefreshSeq?: number;
  /** Last processed mission event mission id for dedup. */
  lastMissionEventMissionId?: string | null;
}

export type ControlRequestKind =
  | "tool_permission"
  | "plan_approval"
  | "user_question";

export type ControlRequestState =
  | "pending"
  | "approved"
  | "denied"
  | "answered"
  | "cancelled"
  | "timed_out";

export type ControlRequestDecision = "approved" | "denied" | "answered";

export interface ControlRequestRecord {
  requestId: string;
  kind: ControlRequestKind;
  state: ControlRequestState;
  sessionKey: string;
  turnId?: string;
  channelName?: string;
  source: "turn" | "mcp" | "child-agent" | "plan" | "system";
  prompt: string;
  proposedInput?: unknown;
  createdAt: number;
  expiresAt: number;
  resolvedAt?: number;
  decision?: ControlRequestDecision;
  feedback?: string;
  updatedInput?: unknown;
  answer?: string;
}

export type ControlEvent =
  | { type: "control_request_created"; request: ControlRequestRecord }
  | {
      type: "control_request_resolved";
      requestId: string;
      decision: ControlRequestDecision;
      feedback?: string;
      updatedInput?: unknown;
      answer?: string;
    }
  | { type: "control_request_cancelled"; requestId: string; reason: string }
  | { type: "control_request_timed_out"; requestId: string }
  | ({ type: "runtime_trace" } & Omit<RuntimeTrace, "receivedAt"> & { receivedAt?: number });

export interface ControlRequestResponse {
  decision: ControlRequestDecision;
  feedback?: string;
  updatedInput?: unknown;
  answer?: string;
}

export interface ResponseUsage {
  inputTokens?: number;
  outputTokens?: number;
  cacheReadTokens?: number;
  cacheCreationTokens?: number;
  totalCost?: number;
  costUsd?: number;
  model?: string;
}

export interface ResearchEvidenceSnapshot {
  inspectedSources: InspectedSource[];
  citationGate?: CitationGateStatus | null;
  capturedAt: number;
}

export interface ServerMessage {
  id: string;
  role: "assistant" | "system";
  content: string;
  created_at: string;
  usage?: ResponseUsage;
  researchEvidence?: ResearchEvidenceSnapshot;
  research_evidence?: ResearchEvidenceSnapshot | null;
}

export interface ReorderEntry {
  name: string;
  category?: string;
  position: number;
}

/**
 * A message that the user typed while a stream was in flight. Lives
 * client-side only; fires through the regular `sendMessage` path once
 * the current turn's `onDone` handler drains the queue.
 */
export interface QueuedMessage {
  /** Local id (no server round-trip). */
  id: string;
  content: string;
  /**
   * Drain priority. Missing means "next" for legacy queued messages.
   * `now` is reserved for ESC handoff promotion.
   */
  priority?: "now" | "next" | "later";
  /** Preserved so quoted replies survive the queue. */
  replyTo?: ReplyTo;
  /**
   * Legacy raw-file queue payload. New KB-first web uploads resolve to
   * `kbDocs` before queueing, but older callers may still use this field
   * until they migrate.
   */
  pendingFiles?: File[];
  /** KB docs resolved before queueing (picker refs or chat uploads). */
  kbDocs?: KbDocReference[];
  /** Runtime model override captured when the message was queued. */
  modelOverride?: string;
  /** Preserve persistent-goal intent when the message drains later. */
  goalMode?: boolean;
  queuedAt: number;
}

/** A Knowledge Base document selected as conversation context. */
export interface KbDocReference {
  /** knowledge_documents.id */
  id: string;
  filename: string;
  collectionId: string;
  collectionName: string;
  mimeType?: string;
  source?: "picker" | "chat_upload";
}

export interface Attachment {
  id: string;
  bot_id: string;
  channel_name: string;
  direction: "user_to_bot" | "bot_to_user";
  filename: string;
  mimetype: string;
  size_bytes: number;
  storage_path: string;
  metadata: Record<string, unknown>;
  created_at: string;
}
