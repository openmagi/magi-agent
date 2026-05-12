/**
 * Tool abstraction — borrowed shape from Claude Code's Tool.ts, trimmed.
 * Design reference: §5.4.
 *
 * A Tool is the ONLY surface through which a Turn produces side-effects.
 * All reads, writes, shell calls, and LLM-facing actions flow through
 * tools so that the Turn's atomic-commit guarantee (invariant A) holds.
 */

import type { Workspace } from "./storage/Workspace.js";
import type { ExecutionContractStore } from "./execution/ExecutionContract.js";
import type { SourceLedgerStore } from "./research/SourceLedger.js";
import type { ChannelMemoryMode, UserMessage } from "./util/types.js";

export type PermissionClass = "read" | "write" | "execute" | "net" | "meta";

export type ToolStatus =
  | "ok"
  | "error"
  | "empty"
  | "permission_denied"
  | "aborted";

export interface ToolProgress {
  label: string;
  percent?: number;
  etaMs?: number;
}

export interface AskUserQuestionInput {
  question: string;
  choices: { id: string; label: string; description?: string }[];
  allowFreeText?: boolean;
}

export interface AskUserQuestionOutput {
  selectedId?: string;
  freeText?: string;
}

export interface ToolContext {
  botId: string;
  sessionKey: string;
  turnId: string;
  /** Workspace root; tools MUST scope all paths under this. */
  workspaceRoot: string;
  /** Channel-level long-term memory behavior. Defaults to normal. */
  memoryMode?: ChannelMemoryMode;
  /** Called by the Tool when it needs the human in the loop. */
  askUser(q: AskUserQuestionInput): Promise<AskUserQuestionOutput>;
  emitProgress(p: ToolProgress): void;
  /**
   * Emit a richer AgentEvent on the SSE `event: agent` channel. Used
   * by tools that produce structured live UI state (TaskBoard,
   * artifact emitters, rule_check). Typed as `unknown` here to avoid
   * a circular dep on SseWriter — call sites import AgentEvent.
   */
  emitAgentEvent?(event: unknown): void;
  /**
   * Emit a durable control event when a tool mutates state that should
   * survive SSE disconnects/replays. Tools keep this optional so unit
   * tests and child runtimes can run without a ledger.
   */
  emitControlEvent?(event: unknown): Promise<void> | void;
  abortSignal: AbortSignal;
  /** Per-turn staging surface — tools write here, not to disk directly. */
  staging: StagingSurface;
  /** Active first-class execution contract for tools that produce evidence. */
  executionContract?: ExecutionContractStore;
  /** Per-session ledger of inspected sources for research/evidence tracking. */
  sourceLedger?: SourceLedgerStore;
  /** Optional cross-service diagnostic trace id for tool logs/events. */
  traceId?: string;
  /**
   * The current top-level user message for this turn. Tools that create
   * child work, such as SpawnAgent, use this to preserve selected KB,
   * attachment, and channel-provided context instead of relying on the
   * parent model to copy it into a sub-task prompt.
   */
  currentUserMessage?: UserMessage;
  /**
   * Current LLM tool_use id when the tool is running inside a turn.
   * Tool-emitted AgentEvents can use this to attach structured previews
   * to the matching tool activity on the client.
   */
  toolUseId?: string;
  /**
   * Spawn depth — 0 for a top-level turn, 1 for a direct child spawned
   * via SpawnAgent, 2 for a grandchild. `MAX_SPAWN_DEPTH` enforced by
   * SpawnAgent (§7.12.d). Undefined is treated as 0.
   */
  spawnDepth?: number;
  /**
   * Present when this context belongs to a spawned child. Child tools
   * should prefer `spawnWorkspace` over constructing paths from
   * `workspaceRoot` directly — it is rooted at the ephemeral
   * `workspace/.spawn/{childTurnId}/` subdirectory and enforces
   * path-scope via `Workspace.resolve()`. Present only for spawned
   * children; top-level turns leave this undefined.
   *
   * Audit 02 / PRE-01: the parent's tool closures still point at the
   * parent `workspaceRoot`; promoting tools to consult this field is
   * Tier-2 follow-up work. For now, the ephemeral subdir is the
   * process-level isolation boundary: `allowed_tools` + spawnDir
   * together scope a child's reach.
   */
  spawnWorkspace?: Workspace;
}

/**
 * The Turn's StagedWriteJournal exposes this interface to tools. Any
 * file write, transcript append, or audit entry goes through it so
 * Turn.commit() can fsync everything atomically (see §6 invariant A).
 */
export interface StagingSurface {
  stageFileWrite(relPath: string, content: string | Uint8Array): void;
  stageTranscriptAppend(entry: object): void;
  stageAuditEvent(event: string, data?: Record<string, unknown>): void;
}

export interface ToolResult<T = unknown> {
  status: ToolStatus;
  output?: T;
  errorCode?: string;
  errorMessage?: string;
  durationMs: number;
  metadata?: Record<string, unknown>;
}

export interface Tool<I = unknown, O = unknown> {
  name: string;
  description: string;
  /** JSONSchema; validated by the runtime before execute(). */
  inputSchema: object;
  permission: PermissionClass;
  outputSchema?: object;
  /** Requires user consent even if permission class allows. */
  dangerous?: boolean;
  /**
   * True when multiple calls to this tool can safely run alongside
   * other concurrency-safe tools. Defaults to true for read/meta tools
   * and false for write/execute/net tools.
   */
  isConcurrencySafe?: boolean;
  /** True when the tool can mutate files, external state, or the workspace. */
  mutatesWorkspace?: boolean;
  /**
   * Modes in which this tool is available. Defaults to ["plan", "act"]
   * for read/net/meta tools; write/execute tools default to ["act"] when
   * unset (inferred by ToolRegistry from permission class).
   */
  availableInModes?: ("plan" | "act")[];
  /** "core" tools are always loaded; "skill" tools go through intent
   * filtering (§9.8 P2/P3). Defaults to "core" when unset. */
  kind?: "core" | "skill";
  /** Intent tags used by the classifier to decide whether to expose
   * this tool in a given turn's tools[] (§9.8 P2). */
  tags?: string[];
  /** Optional pre-validation; return null if ok, string error if not. */
  validate?(input: I): string | null;
  execute(input: I, ctx: ToolContext): Promise<ToolResult<O>>;
}

export interface ToolRegistry {
  register(tool: Tool): void;
  resolve(name: string): Tool | null;
  list(): Tool[];
  /** Loads SKILL.md files under `dir` as tools. Returns count loaded. */
  loadSkills(
    dir: string,
    workspaceRoot?: string,
    opts?: {
      trustedSkillRoots?: readonly string[];
      trustedSkillDirs?: readonly string[];
    },
  ): Promise<number>;
}

export type ToolInput<T extends Tool> = T extends Tool<infer I, unknown>
  ? I
  : never;
