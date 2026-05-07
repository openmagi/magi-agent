/**
 * Hook system type surface.
 * Design reference: §7.12.
 *
 * Hooks are the primary extension point. Every lifecycle phase of a
 * Session / Turn emits a typed hook event; handlers can observe, mutate,
 * or block the phase. Unlike Claude Code's hook system, handlers here
 * receive a scoped `LLMClient` so LLM-based hook logic (classification,
 * moderation, enrichment) has a first-class home.
 */

import type {
  LLMClient,
  LLMMessage,
  LLMToolDef,
  ProviderHealthContext,
} from "../transport/LLMClient.js";
import type { AgentEvent } from "../transport/SseWriter.js";
import type { TranscriptEntry } from "../storage/Transcript.js";
import type {
  AskUserQuestionInput,
  AskUserQuestionOutput,
  ToolResult,
} from "../Tool.js";
import type { ExecutionContractStore } from "../execution/ExecutionContract.js";

/**
 * Every hook point exposed by core-agent. Add new ones here as
 * lifecycle phases are added — handlers against unknown points are
 * rejected at load time.
 */
export type HookPoint =
  // Turn lifecycle
  | "beforeTurnStart"
  | "afterTurnEnd"
  // LLM round-trip
  | "beforeLLMCall"
  | "afterLLMCall"
  // Tool execution
  | "beforeToolUse"
  | "afterToolUse"
  // Commit
  | "beforeCommit"
  | "afterCommit"
  | "onAbort"
  // Errors
  | "onError"
  // Memory / hipocampus
  | "onTaskCheckpoint"
  | "beforeCompaction"
  | "afterCompaction"
  // Future (stubbed so built-ins can target them early):
  | "onRuleViolation"
  | "onArtifactCreated";

/**
 * Read-only context passed to every hook handler. Wrapping types (not
 * the Session / Turn classes) keep handlers decoupled from internals
 * and make them unit-testable with plain objects.
 */
export interface HookContext {
  readonly botId: string;
  readonly userId: string;
  readonly sessionKey: string;
  readonly turnId: string;
  readonly contextId?: string;
  /**
   * Scoped LLM client — same gateway token, with x-magi-hook header
   * tagging all requests so api-proxy can attribute cost to the
   * hook. Per-turn token budget enforced (see HookRegistry).
   */
  readonly llm: LLMClient;
  /** Transcript entries up to (and including) the current phase. */
  readonly transcript: ReadonlyArray<TranscriptEntry>;
  /** Emit a §7.9 AgentEvent into the client stream. */
  readonly emit: (event: AgentEvent) => void;
  /** Structured log (goes to audit channel — phase 2h). */
  readonly log: (level: "info" | "warn" | "error", msg: string, data?: object) => void;
  /** The bot's configured main model (e.g. "claude-opus-4-7"). Hooks
   * that need an LLM judge should use this instead of hardcoding a
   * model — Haiku misjudges Korean context and Opus-quality output. */
  readonly agentModel: string;
  /** Cancellation propagated from the turn. */
  readonly abortSignal: AbortSignal;
  /** Remaining time the hook has to return, in ms. */
  readonly deadlineMs: number;
  /**
   * Optional workflow-native debugging state manager. Populated by
   * Agent so debug-related hooks and gates can coordinate on the same
   * per-session/per-turn state without reparsing the transcript.
   */
  readonly debugWorkflow?: {
    getTurnState(
      sessionKey: string,
      turnId: string,
    ): {
      classified: boolean;
      investigated: boolean;
      hypothesized: boolean;
      patched: boolean;
      verified: boolean;
      warnings: string[];
    } | null;
  };
  /** Deterministic provider-health metadata from the most recent API
   * proxy response. Used by harness-level verification hooks; this is
   * not model self-assessment. */
  readonly providerHealth?: ProviderHealthContext | null;
  /**
   * First-class execution state for long-running work. Unlike the
   * transcript, this keeps the current goal, constraints, plan,
   * blockers, acceptance criteria, artifacts, and verification
   * evidence as structured state that hooks can inspect deterministically.
   */
  readonly executionContract?: ExecutionContractStore;
  /**
   * Optional human-in-the-loop delegate. Populated by Turn.ts for
   * phases that can reasonably interact with the user (currently
   * `beforeToolUse` only) so a hook returning
   * `{ action: "permission_decision", decision: "ask" }` has a way to
   * pose the question. Handlers MUST NOT assume this is present for
   * arbitrary hook points.
   */
  askUser?: (q: AskUserQuestionInput) => Promise<AskUserQuestionOutput>;
}

/**
 * Typed arguments for each hook point. Callers reference
 * `HookArgs["beforeLLMCall"]`. Post-hooks get the result of the phase
 * in addition to the input.
 */
export interface HookArgs {
  beforeTurnStart: { userMessage: string };
  afterTurnEnd: {
    userMessage: string;
    assistantText: string;
    status: "committed" | "aborted";
    reason?: string;
  };

  beforeLLMCall: {
    messages: LLMMessage[];
    tools: LLMToolDef[];
    system: string;
    iteration: number;
  };
  afterLLMCall: {
    messages: LLMMessage[];
    tools: LLMToolDef[];
    system: string;
    iteration: number;
    stopReason: string | null;
    assistantBlocks: unknown[];
  };

  beforeToolUse: {
    toolName: string;
    toolUseId: string;
    input: unknown;
  };
  afterToolUse: {
    toolName: string;
    toolUseId: string;
    input: unknown;
    result: ToolResult;
  };

  beforeCommit: {
    assistantText: string;
    toolCallCount: number;
    toolReadHappened: boolean;
    /** Original user message text for this turn (§7.13 answer-verifier). */
    userMessage: string;
    /**
     * How many times this turn has been retried due to a beforeCommit
     * block. 0 on the first commit attempt. Consulted by retry-aware
     * gates (§7.13) to bound retry loops.
     */
    retryCount: number;
    /**
     * Workspace-relative paths written by the current turn, derived
     * from FileWrite/FileEdit tool calls. Hooks should use this to
     * distinguish current-turn writes from pre-existing PVC drift.
     */
    filesChanged?: string[];
  };
  afterCommit: { assistantText: string };
  onAbort: { reason: string };

  onError: { code: string; message: string; phase: HookPoint | "execute" };

  /**
   * Fired once per committed turn, after afterCommit/afterTurnEnd
   * observers, as an explicit "task checkpoint" signal hipocampus
   * (and other memory systems) can hook to decide whether/what to
   * persist. Always non-blocking observer — never affects the turn.
   */
  onTaskCheckpoint: {
    userMessage: string;
    assistantText: string;
    toolCallCount: number;
    toolNames: string[];
    filesChanged: string[];
    startedAt: number;
    endedAt: number;
  };

  onRuleViolation: { ruleId: string; detail?: string };
  beforeCompaction: { transcript: ReadonlyArray<TranscriptEntry> };
  afterCompaction: { summary: string };
  onArtifactCreated: { artifactId: string; kind: string; name: string };
}

/**
 * Return values from a hook handler. Most hooks use `continue`
 * implicitly (returning void). `replace` mutates the phase's input
 * for downstream hooks + the phase itself. `block` aborts the phase
 * with a user-surfaced reason. `skip` bypasses the phase only
 * (pre-hooks) without aborting the turn.
 *
 * T2-07 — `permission_decision` formalises the approve/deny/ask
 * pattern that `selfClaimVerifier` (and future dangerous_patterns)
 * imply. Only meaningful on `beforeToolUse` today. `decision: "ask"`
 * requires {@link HookContext.askUser} to be populated; otherwise the
 * registry treats it as deny.
 */
export type HookResult<T> =
  | { action: "continue" }
  | { action: "replace"; value: T }
  | { action: "block"; reason: string }
  | { action: "skip" }
  | {
      action: "permission_decision";
      decision: "approve" | "deny" | "ask";
      reason?: string;
    };

export type HookHandler<Point extends HookPoint> = (
  args: HookArgs[Point],
  ctx: HookContext,
) => Promise<HookResult<HookArgs[Point]> | void>;

/**
 * A single registered hook. `priority` ascending = runs first; ties
 * broken by registration order. `blocking: true` pre-hooks halt the
 * phase on failure/timeout; `blocking: false` are fire-and-forget.
 */
export interface RegisteredHook<Point extends HookPoint = HookPoint> {
  name: string;
  point: Point;
  handler: HookHandler<Point>;
  priority?: number;
  blocking?: boolean;
  timeoutMs?: number;
  /** Optional per-turn LLM token cap for this hook (soft — logged). */
  maxTokensPerTurn?: number;
  /**
   * Optional declarative gate — Claude Code permission-rule grammar
   * (e.g. `"Bash(git *)"`, `"Read(*.ts)"`, `"*"`, `"beforeCommit"`).
   * When supplied, the HookRegistry parses the rule once (cached) and
   * skips the hook entirely when it does not match the current
   * dispatch context. Malformed rules cause the hook to be skipped
   * with a single warn-level log entry. See {@link ./ruleMatcher.ts}.
   *
   * Rationale: lets built-ins and user-authored hooks replace
   * `if (args.toolName !== "X") return;` boilerplate with a single
   * field, making intent readable and enabling registry-level
   * short-circuit (no timer armed, no error accounting) for
   * non-applicable events.
   *
   * Undefined → run for every event at the declared `point` (legacy
   * behaviour preserved).
   */
  if?: string;
  /**
   * When true, timeout or error in a blocking pre-hook is treated as
   * `{ action: "continue" }` instead of `{ action: "block" }`. Use
   * for advisory hooks (answer-verifier, fact-grounding) where a
   * broken/slow judge must never block a turn.
   *
   * Default: false (fail-closed — legacy behaviour preserved).
   */
  failOpen?: boolean;
}
