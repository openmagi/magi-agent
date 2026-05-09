/**
 * ChildAgentLoop — spawned-child mini-agent loop.
 *
 * Extracted from tools/SpawnAgent.ts (R4 step 2, 2026-04-19). Owns the
 * per-child turn loop that SpawnAgent previously kept inline. Reuses
 * `turn/LLMStreamReader.readOne` (R3) for stream consumption, fronted by
 * a `StubSseWriter` so children don't emit on the parent's SSE channel
 * for every delta — only structured events (tool_start / tool_end /
 * spawn_result) reach the client, same as before.
 *
 * The child does NOT share ToolDispatcher with Turn — Turn's dispatcher
 * is tightly coupled to Session transcript append semantics. Children
 * are ephemeral (§7.12.d), but the child-tool runner still honours the
 * same policy plane where it can:
 *   • allowedTools intersection (filtered at child-tool selection time)
 *   • beforeToolUse / afterToolUse hooks when the Agent exposes them
 *   • runtime permission decisions and parent askUser consent delegate
 *   • MAX_SPAWN_DEPTH (parent enforces at SpawnAgent.execute entry)
 *   • trusted children use the parent workspace by default
 *   • opt-in isolated children use PRE-01 workspace isolation
 *   • timeout + parent abort propagation via a merged AbortController
 *
 * Invariants preserved 1:1 from the inline implementation:
 *   • CHILD_MAX_ITERATIONS=25 loop cap (`errorMessage: child exceeded N iterations`)
 *   • deadline → `{ status:"error", errorMessage:"child timeout" }`
 *   • parent abort → `{ status:"aborted" }`
 *   • `askUser` throws inside the child
 *   • child staging hooks are all no-ops (durable lifecycle events carry
 *     child audit/provenance instead of parent transcript writes)
 */

import path from "node:path";
import type { Agent } from "../Agent.js";
import type {
  AskUserQuestionInput,
  AskUserQuestionOutput,
  Tool,
  ToolContext,
  ToolResult,
} from "../Tool.js";
import type { HookContext } from "../hooks/types.js";
import type {
  LLMContentBlock,
  LLMMessage,
  LLMToolDef,
} from "../transport/LLMClient.js";
import { StubSseWriter } from "../transport/SseWriter.js";
import { decideRuntimePermission } from "../permissions/PermissionArbiter.js";
import { isReadOnlyTool } from "../permissions/ToolPermissionAdapters.js";
import type { ChildAgentLifecycle } from "./ChildAgentHarness.js";
import type { Workspace } from "../storage/Workspace.js";
import type { TranscriptEntry } from "../storage/Transcript.js";
import { readOne } from "../turn/LLMStreamReader.js";
import type { PermissionMode } from "../turn/ToolDispatcher.js";
import { summariseToolOutput } from "../util/toolResult.js";
import { classifyFinalAnswerMeta } from "../hooks/builtin/turnMetaClassifier.js";
import { buildChildSystemPrompt } from "./ChildSystemPrompt.js";
import type { ChannelMemoryMode, UserMessage } from "../util/types.js";
import type { ExecutionContractStore } from "../execution/ExecutionContract.js";
import {
  classifyEvidence,
  transcriptEvidenceForTurn,
} from "../verification/VerificationEvidence.js";

/**
 * Cap on child loop iterations — protects against pathological loops.
 * 2026-04-20: bumped 25 → 40 after admin-bot POS deep-dive exhausted
 * 24 iterations on integration.sh API collection and stopped narrating
 * "Now let me write the report" without ever writing (trace-confirmed).
 * Env override: CORE_AGENT_CHILD_MAX_ITERATIONS.
 */
export const CHILD_MAX_ITERATIONS = (() => {
  const raw = process.env.CORE_AGENT_CHILD_MAX_ITERATIONS;
  const parsed = raw !== undefined ? Number.parseInt(raw, 10) : NaN;
  // 2026-04-20 0.17.1: bumped default 40 → 200 for Claude Code parity.
  // 0.17.0 bumped 25 → 40 but POS deep-dive hit 40/40 still. Claude
  // Code has no effective cap — match that. Env clamps 5..1000.
  return Number.isFinite(parsed) && parsed >= 5 && parsed <= 1000 ? parsed : 200;
})();

const CHILD_DEFERRAL_CLASSIFIER_TIMEOUT_MS = 3_000;

function childDeferralClassifierTimeoutMs(deadline: number): number {
  const remaining = deadline - Date.now();
  if (remaining <= 0) return 0;
  return Math.max(250, Math.min(CHILD_DEFERRAL_CLASSIFIER_TIMEOUT_MS, remaining));
}

/**
 * LLM-based child deferral classification. No regex fallback.
 * Mirrors the parent DeferralBlocker classifier but avoids requiring a
 * full HookContext inside spawned children.
 */
export async function classifyChildDeferralNarrative(input: {
  llm: Agent["llm"];
  model: string;
  prompt: string;
  finalText: string;
  signal: AbortSignal;
  timeoutMs: number;
}): Promise<boolean> {
  if (!input.finalText.trim()) return false;
  if (input.timeoutMs <= 0) return false;
  const controller = new AbortController();
  const onAbort = (): void => controller.abort(input.signal.reason);
  if (input.signal.aborted) {
    onAbort();
  } else {
    input.signal.addEventListener("abort", onAbort, { once: true });
  }
  const timer = setTimeout(
    () => controller.abort(new Error("child_deferral_classifier_timeout")),
    input.timeoutMs,
  );
  try {
    const meta = await classifyFinalAnswerMeta({
      llm: input.llm,
      model: input.model,
      userMessage: input.prompt,
      assistantText: input.finalText,
      timeoutMs: input.timeoutMs,
      signal: controller.signal,
    });
    return meta.deferralPromise;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
    input.signal.removeEventListener("abort", onAbort);
  }
}

export function classifyChildBashBoundary(input: unknown): { safe: boolean; reason?: string } {
  const command = commandOf(input);
  if (!command.trim()) return { safe: true };
  if (/(^|[\s"'`])\.\.(?:\/|$)/.test(command)) {
    return { safe: false, reason: "child Bash cannot reference parent directories" };
  }
  if (/(^|[\s"'`])\/(?:etc|proc|sys|var|tmp|home|root|Users|Volumes|private)\b/.test(command)) {
    return { safe: false, reason: "child Bash cannot reference absolute system paths" };
  }
  if (/(^|[\s"'`])\/[^\s"'`]*/.test(command)) {
    return { safe: false, reason: "child Bash cannot use absolute paths" };
  }
  if (/(^|[\s"'`])(?:\.env|id_rsa|id_ed25519|credentials|secrets?)(?:[\s"'`]|$)/i.test(command)) {
    return { safe: false, reason: "child Bash cannot access secret-looking paths" };
  }
  if (/\brm\s+-[^\n]*r[^\n]*f|\brm\s+-[^\n]*f[^\n]*r/i.test(command)) {
    return { safe: false, reason: "destructive rm -rf" };
  }
  if (/[;&|]|`|\$\(|>|<</.test(command)) {
    return { safe: false, reason: "child Bash supports only simple single commands" };
  }
  return { safe: true };
}

export interface SpawnChildResult {
  status: "ok" | "error" | "aborted";
  finalText: string;
  toolCallCount: number;
  errorMessage?: string;
  evidence?: SpawnChildEvidence;
}

export type SpawnWorkspacePolicy = "trusted" | "isolated" | "git_worktree";

export interface SpawnChildEvidence {
  toolNames: string[];
  changedFiles: string[];
  verificationCommands: string[];
}

export class SpawnChildPartialError extends Error {
  readonly partialResult: SpawnChildResult;

  constructor(message: string, partialResult: SpawnChildResult) {
    super(message);
    this.name = "SpawnChildPartialError";
    this.partialResult = partialResult;
  }
}

export interface SpawnChildOptions {
  parentSessionKey: string;
  parentTurnId: string;
  parentSpawnDepth: number;
  persona: string;
  prompt: string;
  allowedTools?: string[];
  allowedSkills?: string[];
  timeoutMs: number;
  abortSignal: AbortSignal;
  botId: string;
  /** Parent workspace root. Trusted children use this as their tool workspace. */
  workspaceRoot: string;
  /**
   * Ephemeral subdirectory for the child — `workspace/.spawn/{taskId}/`.
   * Trusted children use this as scratch/audit metadata; isolated children
   * use it as their tool workspace.
   */
  spawnDir: string;
  /** Workspace instance rooted at `spawnDir`. */
  spawnWorkspace: Workspace;
  /** SpawnAgent resolves omitted policy before entering the child loop. */
  workspacePolicy?: SpawnWorkspacePolicy;
  taskId: string;
  /** Override the LLM model for this child (e.g. "gpt-5.4", "gemini-3.1-pro-preview"). */
  modelOverride?: string;
  /** Parent channel memory mode. */
  memoryMode?: ChannelMemoryMode;
  /** Called when child emits tool events — wired to the parent's SSE. */
  onAgentEvent?(event: unknown): void;
  /** Parent turn's askUser delegate, when the child can request consent. */
  askUser?(input: AskUserQuestionInput): Promise<AskUserQuestionOutput>;
  /** Parent session permission mode at spawn time. */
  permissionMode?: PermissionMode;
  /** Parent execution contract, propagated so child hooks/tools enforce the same runtime harness. */
  executionContract?: ExecutionContractStore;
  /** Disable the extra LLM deferral classifier for answer-only/tournament children. */
  deferralCheck?: boolean;
  /** Workspace-relative files/directories the child may read when provided. */
  allowedFiles?: string[];
  /** Workspace-relative files/directories the child may write when provided. */
  writeSet?: string[];
  /** Durable child lifecycle recorder wired to the parent's control ledger. */
  lifecycle?: ChildAgentLifecycle;
}

/**
 * Build the filtered tool list for the child, per §7.12.d "Native vs skill".
 * Extracted alongside runChildAgentLoop so it can be unit-tested in
 * isolation (and reused by SpawnAgent's factory / tests).
 *
 * - `allowed_tools` intersects by name with the parent registry.
 * - `allowed_skills` additionally admits any skill-kind tool whose tags
 *   overlap the allowlist.
 * - When neither is provided, inherit the parent's full registry.
 */
export function selectChildTools(
  parentTools: Tool[],
  allowedTools?: string[],
  allowedSkills?: string[],
): Tool[] {
  if (!allowedTools && !allowedSkills) return parentTools.slice();

  const toolNameSet = allowedTools ? new Set(allowedTools) : null;
  const skillTagSet = allowedSkills
    ? new Set(allowedSkills.map((t) => t.toLowerCase()))
    : null;

  const out: Tool[] = [];
  const seen = new Set<string>();
  for (const t of parentTools) {
    if (toolNameSet?.has(t.name)) {
      if (!seen.has(t.name)) {
        out.push(t);
        seen.add(t.name);
      }
      continue;
    }
    if (skillTagSet && t.kind === "skill") {
      const tags = (t.tags ?? []).map((x) => x.toLowerCase());
      if (tags.some((x) => skillTagSet.has(x))) {
        if (!seen.has(t.name)) {
          out.push(t);
          seen.add(t.name);
        }
      }
    }
  }
  return out;
}

function childSessionKey(
  parentSessionKey: string,
  persona: string,
  n: number,
): string {
  const safePersona =
    persona.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 32) || "child";
  return `${parentSessionKey}::spawn::${safePersona}::${n}`;
}

function collectText(blocks: LLMContentBlock[]): string {
  return blocks
    .filter((b): b is Extract<LLMContentBlock, { type: "text" }> => b.type === "text")
    .map((b) => b.text)
    .join("");
}

function childWorkspacePolicy(opts: Pick<SpawnChildOptions, "workspacePolicy">): SpawnWorkspacePolicy {
  return opts.workspacePolicy ?? "trusted";
}

function childWorkspaceRoot(opts: SpawnChildOptions): string {
  const policy = childWorkspacePolicy(opts);
  return policy === "isolated" || policy === "git_worktree"
    ? opts.spawnWorkspace.root
    : opts.workspaceRoot;
}

function childSpawnWorkspace(opts: SpawnChildOptions): Workspace | undefined {
  const policy = childWorkspacePolicy(opts);
  return policy === "isolated" || policy === "git_worktree"
    ? opts.spawnWorkspace
    : undefined;
}

function childTurnId(opts: SpawnChildOptions): string {
  return `${opts.parentTurnId}::spawn::${opts.taskId}`;
}

function shouldRunChildDeferralCheck(opts: SpawnChildOptions): boolean {
  return opts.deferralCheck !== false;
}

function normalizeChildRelPath(value: string): string | null {
  const normalized = path.posix.normalize(value.replace(/\\/g, "/").replace(/^\/+/, ""));
  if (!normalized || normalized === "." || normalized.startsWith("../") || normalized === "..") {
    return null;
  }
  return normalized;
}

function inputPathForTool(toolName: string, input: unknown): string | null {
  if (!input || typeof input !== "object") return null;
  const obj = input as Record<string, unknown>;
  const value =
    toolName === "DocumentWrite" || toolName === "SpreadsheetWrite"
      ? obj.filename
      : obj.path;
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function pathInScope(target: string, allowed: readonly string[] | undefined): boolean {
  if (!allowed) return true;
  const normalizedTarget = normalizeChildRelPath(target);
  if (!normalizedTarget) return false;
  return allowed.some((entry) => {
    const normalizedEntry = normalizeChildRelPath(entry);
    if (!normalizedEntry) return false;
    return (
      normalizedTarget === normalizedEntry ||
      normalizedTarget.startsWith(`${normalizedEntry.replace(/\/+$/, "")}/`)
    );
  });
}

function childScopeViolation(
  toolName: string,
  input: unknown,
  opts: SpawnChildOptions,
): string | null {
  const target = inputPathForTool(toolName, input);
  if (!target) return null;
  if (toolName === "FileRead" && !pathInScope(target, opts.allowedFiles)) {
    return `child read path outside allowed_files: ${target}`;
  }
  if (
    /^(FileWrite|FileEdit|DocumentWrite|SpreadsheetWrite)$/.test(toolName) &&
    !pathInScope(target, opts.writeSet)
  ) {
    return `child write path outside write_set: ${target}`;
  }
  return null;
}

function changedFileFromTool(toolName: string, input: unknown): string | null {
  if (!/^(FileWrite|FileEdit|DocumentWrite|SpreadsheetWrite)$/.test(toolName)) {
    return null;
  }
  const raw = inputPathForTool(toolName, input);
  return raw ? normalizeChildRelPath(raw) : null;
}

function isSuccessfulResultEntry(entry: Extract<TranscriptEntry, { kind: "tool_result" }>): boolean {
  return entry.isError !== true && (entry.status === "ok" || entry.status === "success");
}

function buildChildEvidence(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): SpawnChildEvidence {
  const evidence = transcriptEvidenceForTurn(transcript, turnId);
  const classified = classifyEvidence(evidence);
  const results = new Map<string, Extract<TranscriptEntry, { kind: "tool_result" }>>();
  for (const entry of transcript) {
    if (entry.kind === "tool_result" && entry.turnId === turnId) {
      results.set(entry.toolUseId, entry);
    }
  }
  const changedFiles = new Set<string>();
  for (const entry of transcript) {
    if (entry.kind !== "tool_call" || entry.turnId !== turnId) continue;
    const result = results.get(entry.toolUseId);
    if (!result || !isSuccessfulResultEntry(result)) continue;
    const changed = changedFileFromTool(entry.name, entry.input);
    if (changed) changedFiles.add(changed);
  }
  return {
    toolNames: classified.tools,
    changedFiles: [...changedFiles].sort(),
    verificationCommands: classified.verificationCommands,
  };
}

type ChildHookRegistry = Pick<Agent["hooks"], "list" | "runPre" | "runPost">;

function getChildHookRegistry(agent: Agent): ChildHookRegistry | null {
  const maybe = (agent as { hooks?: Partial<ChildHookRegistry> }).hooks;
  if (
    maybe &&
    typeof maybe.runPre === "function" &&
    typeof maybe.runPost === "function" &&
    typeof maybe.list === "function"
  ) {
    return maybe as ChildHookRegistry;
  }
  return null;
}

function makeChildHookContext(
  agent: Agent,
  opts: SpawnChildOptions,
  abortSignal: AbortSignal,
  agentModel = agent.config.model,
  transcript: ReadonlyArray<TranscriptEntry> = [],
): HookContext {
  return {
    botId: opts.botId,
    userId: agent.config.userId,
    sessionKey: opts.parentSessionKey,
    turnId: childTurnId(opts),
    contextId: childSessionKey(opts.parentSessionKey, opts.persona, 0),
    llm: agent.llm,
    transcript,
    emit: (event) => opts.onAgentEvent?.(event),
    log: () => {
      /* child hook logs stay local; rule_check events still emit */
    },
    agentModel,
    abortSignal,
    deadlineMs: 5_000,
    memoryMode: opts.memoryMode,
    executionContract: opts.executionContract,
    ...(opts.askUser ? { askUser: opts.askUser } : {}),
  };
}

function hasBuiltinAutoApprovalHook(hooks: ChildHookRegistry | null): boolean {
  if (!hooks) return false;
  try {
    return hooks
      .list("beforeToolUse")
      .some((hook) => hook.name === "builtin:auto-approval");
  } catch {
    return false;
  }
}

async function askDangerousChildToolConsent(
  opts: SpawnChildOptions,
  toolName: string,
): Promise<string | null> {
  if (!opts.askUser) {
    return `[PERMISSION:NO_DELEGATE] dangerous child tool ${toolName}`;
  }
  try {
    const answer = await opts.askUser({
      question: `Spawned child requested dangerous tool ${toolName}. Allow it to proceed?`,
      choices: [
        { id: "approve", label: "Approve" },
        { id: "deny", label: "Deny" },
      ],
    });
    if (answer.selectedId === "approve") return null;
    return `[PERMISSION:USER_DENIED] dangerous child tool ${toolName}`;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return `[PERMISSION:ASK_FAILED] dangerous child tool ${toolName}: ${msg}`;
  }
}

/**
 * Run a single mini-agent loop for a spawned child. Does NOT persist to
 * the session transcript — the child is ephemeral. Parent is responsible
 * for deciding whether to record the child's output in its own trail
 * (today, via the returned `finalText`).
 */
export async function runChildAgentLoop(
  agent: Agent,
  opts: SpawnChildOptions,
): Promise<SpawnChildResult> {
  const allTools = agent.tools.list();
  const childTools = selectChildTools(allTools, opts.allowedTools, opts.allowedSkills);
  const toolsByName = new Map<string, Tool>(childTools.map((t) => [t.name, t]));
  let toolDefs: LLMToolDef[] = childTools.map((t) => ({
    name: t.name,
    description: t.description,
    input_schema: t.inputSchema,
  }));

  let system = await buildChildSystemPrompt({
    persona: opts.persona,
    prompt: opts.prompt,
    parentTurnId: opts.parentTurnId,
    parentSpawnDepth: opts.parentSpawnDepth,
    parentWorkspaceRoot: opts.workspaceRoot,
    spawnDir: opts.spawnDir,
    workspacePolicy: childWorkspacePolicy(opts),
    memoryMode: opts.memoryMode,
  });

  let messages: LLMMessage[] = [{ role: "user", content: opts.prompt }];
  const resolvedModel =
    typeof (agent as { resolveRuntimeModel?: () => Promise<string> }).resolveRuntimeModel === "function"
      ? await agent.resolveRuntimeModel()
      : agent.config.model;
  const effectiveModel = opts.modelOverride ?? resolvedModel;

  const assistantBlocks: LLMContentBlock[] = [];
  let toolCallCount = 0;
  let deferralRetryUsed = false;
  const deadline = Date.now() + opts.timeoutMs;
  const childTranscript: TranscriptEntry[] = [];
  const effectiveTurnId = childTurnId(opts);

  // Merge timeout + parent abort into one signal for nested tools.
  const controller = new AbortController();
  let abortReason: "parent" | "timeout" | null = null;
  const onParentAbort = (): void => {
    abortReason ??= "parent";
    controller.abort();
  };
  let parentAbortListenerRegistered = false;
  if (opts.abortSignal.aborted) {
    onParentAbort();
  } else {
    opts.abortSignal.addEventListener("abort", onParentAbort, { once: true });
    parentAbortListenerRegistered = true;
  }
  const timer = setTimeout(() => {
    abortReason ??= "timeout";
    controller.abort();
  }, Math.max(0, opts.timeoutMs));

  // Stub SSE writer so LLMStreamReader's sse.agent / sse.legacyDelta
  // calls are silent — children only surface structured events
  // (spawn_started / spawn_result / tool_start / tool_end emitted by
  // the tools themselves via ctx.emitAgentEvent).
  const sse = new StubSseWriter();
  const hooks = getChildHookRegistry(agent);

  try {
    for (let iter = 0; iter < CHILD_MAX_ITERATIONS; iter++) {
      if (controller.signal.aborted) {
        if (abortReason === "timeout") {
          await opts.lifecycle?.failed("child timeout");
          return {
            status: "error",
            finalText: collectText(assistantBlocks),
            toolCallCount,
            errorMessage: "child timeout",
            evidence: buildChildEvidence(childTranscript, effectiveTurnId),
          };
        }
        await opts.lifecycle?.cancelled("parent aborted");
        return {
          status: "aborted",
          finalText: collectText(assistantBlocks),
          toolCallCount,
          evidence: buildChildEvidence(childTranscript, effectiveTurnId),
        };
      }
      if (Date.now() > deadline) {
        await opts.lifecycle?.failed("child timeout");
        return {
          status: "error",
          finalText: collectText(assistantBlocks),
          toolCallCount,
          errorMessage: "child timeout",
          evidence: buildChildEvidence(childTranscript, effectiveTurnId),
        };
      }
      await opts.lifecycle?.progress(`iteration ${iter + 1}`);

      if (hooks) {
        const preLLM = await hooks.runPre(
          "beforeLLMCall",
          { messages, tools: toolDefs, system, iteration: iter },
          makeChildHookContext(agent, opts, controller.signal, effectiveModel, childTranscript),
        );
        if (preLLM.action === "block") {
          const reason = `beforeLLMCall blocked: ${preLLM.reason}`;
          await opts.lifecycle?.failed(reason);
          return {
            status: "error",
            finalText: collectText(assistantBlocks),
            toolCallCount,
            errorMessage: reason,
            evidence: buildChildEvidence(childTranscript, effectiveTurnId),
          };
        }
        if (preLLM.action === "skip") {
          const reason = "beforeLLMCall skipped child LLM call";
          await opts.lifecycle?.failed(reason);
          return {
            status: "error",
            finalText: collectText(assistantBlocks),
            toolCallCount,
            errorMessage: reason,
            evidence: buildChildEvidence(childTranscript, effectiveTurnId),
          };
        }
        ({ messages, tools: toolDefs, system } = preLLM.args);
      }

      const { blocks, stopReason } = await readOne(
        {
          llm: agent.llm,
          model: effectiveModel,
          sse,
          abortSignal: controller.signal,
          onError: () => {
            /* child-side error surfaces through the thrown exception */
          },
        },
        system,
        messages,
        toolDefs,
      );
      assistantBlocks.push(...blocks);
      if (hooks) {
        void hooks.runPost(
          "afterLLMCall",
          { messages, tools: toolDefs, system, iteration: iter, stopReason, assistantBlocks: blocks },
          makeChildHookContext(agent, opts, controller.signal, effectiveModel, childTranscript),
        );
      }

      if (stopReason !== "tool_use") {
        // LLM-based DeferralBlocker for children: if the child is
        // stopping after promising future action, give it one chance to
        // actually execute. Fail-open on classifier errors/timeouts.
        const finalText = collectText(assistantBlocks);
        if (
          shouldRunChildDeferralCheck(opts) &&
          !deferralRetryUsed &&
          (await classifyChildDeferralNarrative({
            llm: agent.llm,
            model: effectiveModel,
            prompt: opts.prompt,
            finalText,
            signal: controller.signal,
            timeoutMs: childDeferralClassifierTimeoutMs(deadline),
          }))
        ) {
          deferralRetryUsed = true;
          messages.push({ role: "assistant", content: blocks });
          messages.push({
            role: "user",
            content:
              "You stopped after narrating what you would do next but you did not actually execute it. Do the work NOW using tool calls. Do not narrate; do not defer. Write the file / run the command / produce the artifact. Then stop.",
          });
          opts.onAgentEvent?.({
            type: "spawn_deferral_retry",
            taskId: opts.taskId,
            iteration: iter,
          });
          continue;
        }
        const final = {
          status: "ok",
          finalText,
          toolCallCount,
          evidence: buildChildEvidence(childTranscript, effectiveTurnId),
        } satisfies SpawnChildResult;
        await opts.lifecycle?.completed({
          status: final.status,
          finalText: final.finalText,
          toolCallCount: final.toolCallCount,
        });
        return final;
      }

      const toolUses = blocks.filter(
        (b): b is Extract<LLMContentBlock, { type: "tool_use" }> =>
          b.type === "tool_use",
      );
      if (toolUses.length === 0) {
        const finalText = collectText(assistantBlocks);
        if (
          shouldRunChildDeferralCheck(opts) &&
          !deferralRetryUsed &&
          (await classifyChildDeferralNarrative({
            llm: agent.llm,
            model: effectiveModel,
            prompt: opts.prompt,
            finalText,
            signal: controller.signal,
            timeoutMs: childDeferralClassifierTimeoutMs(deadline),
          }))
        ) {
          deferralRetryUsed = true;
          messages.push({ role: "assistant", content: blocks });
          messages.push({
            role: "user",
            content:
              "You stopped after narrating what you would do next but you did not actually execute it. Do the work NOW using tool calls. Do not narrate; do not defer. Write the file / run the command / produce the artifact. Then stop.",
          });
          opts.onAgentEvent?.({
            type: "spawn_deferral_retry",
            taskId: opts.taskId,
            iteration: iter,
          });
          continue;
        }
        const final = {
          status: "ok",
          finalText,
          toolCallCount,
          evidence: buildChildEvidence(childTranscript, effectiveTurnId),
        } satisfies SpawnChildResult;
        await opts.lifecycle?.completed({
          status: final.status,
          finalText: final.finalText,
          toolCallCount: final.toolCallCount,
        });
        return final;
      }

      toolCallCount += toolUses.length;
      const results = await runChildTools({
        toolUses,
        toolsByName,
        agent,
        opts,
        abortSignal: controller.signal,
        agentModel: effectiveModel,
        childTranscript,
      });
      messages.push({ role: "assistant", content: blocks });
      messages.push({
        role: "user",
        content: results.map((r) => ({
          type: "tool_result" as const,
          tool_use_id: r.toolUseId,
          content: r.content,
          ...(r.isError ? { is_error: true as const } : {}),
        })),
      });
    }

    await opts.lifecycle?.failed(`child exceeded ${CHILD_MAX_ITERATIONS} iterations`);
    return {
      status: "error",
      finalText: collectText(assistantBlocks),
      toolCallCount,
      errorMessage: `child exceeded ${CHILD_MAX_ITERATIONS} iterations`,
      evidence: buildChildEvidence(childTranscript, effectiveTurnId),
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const partialResult: SpawnChildResult = {
      status: controller.signal.aborted && abortReason === "parent" ? "aborted" : "error",
      finalText: collectText(assistantBlocks),
      toolCallCount,
      errorMessage: msg,
      evidence: buildChildEvidence(childTranscript, effectiveTurnId),
    };
    await opts.lifecycle?.failed(msg);
    throw new SpawnChildPartialError(msg, partialResult);
  } finally {
    clearTimeout(timer);
    if (parentAbortListenerRegistered) {
      opts.abortSignal.removeEventListener("abort", onParentAbort);
    }
  }
}

interface ChildToolRunInput {
  toolUses: Array<Extract<LLMContentBlock, { type: "tool_use" }>>;
  toolsByName: Map<string, Tool>;
  agent: Agent;
  opts: SpawnChildOptions;
  abortSignal: AbortSignal;
  agentModel: string;
  childTranscript: TranscriptEntry[];
}

interface ChildToolResult {
  toolUseId: string;
  content: string;
  isError: boolean;
}

async function runChildTools(
  args: ChildToolRunInput,
): Promise<ChildToolResult[]> {
  const { toolUses, toolsByName } = args;
  const results: ChildToolResult[] = [];
  let readOnlyBatch: Array<Extract<LLMContentBlock, { type: "tool_use" }>> = [];

  const flushReadOnlyBatch = async (): Promise<void> => {
    if (readOnlyBatch.length === 0) return;
    const batch = readOnlyBatch;
    readOnlyBatch = [];
    results.push(...(await Promise.all(batch.map((tu) => runOneChildTool(args, tu)))));
  };

  for (const tu of toolUses) {
    const tool = toolsByName.get(tu.name);
    if (tool && isChildConcurrencySafe(tu.name, tool)) {
      readOnlyBatch.push(tu);
      continue;
    }
    await flushReadOnlyBatch();
    results.push(await runOneChildTool(args, tu));
  }
  await flushReadOnlyBatch();
  return results;
}

function isChildConcurrencySafe(toolName: string, tool: Tool): boolean {
  if (tool.isConcurrencySafe === true) return true;
  if (tool.isConcurrencySafe === false) return false;
  if (tool.mutatesWorkspace === true) return false;
  return isReadOnlyTool(toolName, tool);
}

function recordChildToolTranscript(input: {
  transcript: TranscriptEntry[];
  turnId: string;
  toolUseId: string;
  toolName: string;
  input: unknown;
  result: ToolResult;
  output: string;
}): void {
  const ts = Date.now();
  input.transcript.push({
    kind: "tool_call",
    ts,
    turnId: input.turnId,
    toolUseId: input.toolUseId,
    name: input.toolName,
    input: input.input,
  });
  input.transcript.push({
    kind: "tool_result",
    ts: ts + 1,
    turnId: input.turnId,
    toolUseId: input.toolUseId,
    status: input.result.status,
    output: input.output,
    isError: input.result.status !== "ok",
    ...(input.result.metadata ? { metadata: input.result.metadata } : {}),
  });
}

function deniedToolResult(message: string): ToolResult {
  return {
    status: "permission_denied",
    errorCode: "permission_denied",
    errorMessage: message,
    durationMs: 0,
  };
}

async function runOneChildTool(
  args: ChildToolRunInput,
  tu: Extract<LLMContentBlock, { type: "tool_use" }>,
): Promise<ChildToolResult> {
  const { toolsByName, agent, opts, abortSignal, agentModel, childTranscript } = args;
  const hooks = getChildHookRegistry(agent);

  await opts.lifecycle?.toolRequest({ requestId: tu.id, toolName: tu.name });
  const tool = toolsByName.get(tu.name);
  if (!tool) {
    return {
      toolUseId: tu.id,
      content: `error:unknown_tool ${tu.name} not in child toolset`,
      isError: true,
    };
  }

  let effectiveInput = tu.input;
  const bypass = opts.permissionMode === "bypass";
  const hookCtx = makeChildHookContext(
    agent,
    opts,
    abortSignal,
    agentModel,
    childTranscript,
  );
  const turnId = childTurnId(opts);
  if (!bypass && hooks) {
    const preTool = await hooks.runPre(
      "beforeToolUse",
      { toolName: tu.name, toolUseId: tu.id, input: tu.input },
      hookCtx,
    );
    if (preTool.action === "block") {
      const result = deniedToolResult(`blocked by hook: ${preTool.reason}`);
      const content = summariseToolOutput(result);
      recordChildToolTranscript({
        transcript: childTranscript,
        turnId,
        toolUseId: tu.id,
        toolName: tu.name,
        input: effectiveInput,
        result,
        output: content,
      });
      return { toolUseId: tu.id, content, isError: true };
    }
    if (preTool.action === "continue") {
      effectiveInput = preTool.args.input;
    }
  }

  const scopeViolation = childScopeViolation(tu.name, effectiveInput, opts);
  if (scopeViolation) {
    await opts.lifecycle?.permissionDecision({
      decision: "deny",
      reason: scopeViolation,
    });
    const result = deniedToolResult(scopeViolation);
    const content = summariseToolOutput(result);
    recordChildToolTranscript({
      transcript: childTranscript,
      turnId,
      toolUseId: tu.id,
      toolName: tu.name,
      input: effectiveInput,
      result,
      output: content,
    });
    return { toolUseId: tu.id, content, isError: true };
  }

  if (tu.name === "Bash" && childWorkspacePolicy(opts) !== "trusted") {
    const boundary = classifyChildBashBoundary(effectiveInput);
    if (!boundary.safe) {
      await opts.lifecycle?.permissionDecision({
        decision: "deny",
        reason: boundary.reason,
      });
      const result = deniedToolResult(boundary.reason ?? "child Bash boundary denied");
      const content = summariseToolOutput(result);
      recordChildToolTranscript({
        transcript: childTranscript,
        turnId,
        toolUseId: tu.id,
        toolName: tu.name,
        input: effectiveInput,
        result,
        output: content,
      });
      return { toolUseId: tu.id, content, isError: true };
    }
  }

  const permission = await decideRuntimePermission({
    mode: opts.permissionMode ?? "default",
    source: "child-agent",
    toolName: tu.name,
    input: effectiveInput,
    tool,
    workspaceRoot: childWorkspaceRoot(opts),
  });
  await opts.lifecycle?.permissionDecision({
    decision: permission.decision,
    reason: permission.reason,
  });
  if (permission.decision === "ask") {
    const denied = await askDangerousChildToolConsent(opts, tu.name);
    if (denied !== null) {
      await opts.lifecycle?.permissionDecision({
        decision: "deny",
        reason: permission.reason,
      });
      const result = deniedToolResult(permission.reason);
      const content = summariseToolOutput(result);
      recordChildToolTranscript({
        transcript: childTranscript,
        turnId,
        toolUseId: tu.id,
        toolName: tu.name,
        input: effectiveInput,
        result,
        output: content,
      });
      return { toolUseId: tu.id, content, isError: true };
    }
    await opts.lifecycle?.permissionDecision({
      decision: "allow",
      reason: permission.reason,
    });
    effectiveInput = permission.proposedInput ?? effectiveInput;
  } else if (permission.decision !== "allow") {
    const result = deniedToolResult(permission.reason);
    const content = summariseToolOutput(result);
    recordChildToolTranscript({
      transcript: childTranscript,
      turnId,
      toolUseId: tu.id,
      toolName: tu.name,
      input: effectiveInput,
      result,
      output: content,
    });
    return { toolUseId: tu.id, content, isError: true };
  }
  if (permission.decision === "allow") {
    effectiveInput = permission.updatedInput ?? effectiveInput;
  }

  const childCtx: ToolContext = {
    botId: opts.botId,
    sessionKey: childSessionKey(opts.parentSessionKey, opts.persona, 0),
    turnId,
    workspaceRoot: childWorkspaceRoot(opts),
    memoryMode: opts.memoryMode,
    spawnWorkspace: childSpawnWorkspace(opts),
    abortSignal,
    currentUserMessage: childUserMessage(opts),
    emitProgress: () => {
      /* child progress folded into spawn_result — no per-step SSE */
    },
    emitAgentEvent: opts.onAgentEvent,
    askUser:
      opts.askUser ??
      (async () => {
        throw new Error("askUser not available in a spawned child");
      }),
    staging: {
      stageFileWrite: () => {
        /* children don't stage atomic writes */
      },
      stageTranscriptAppend: () => {
        /* no-op */
      },
      stageAuditEvent: () => {
        /* no-op */
      },
    },
    spawnDepth: opts.parentSpawnDepth + 1,
    executionContract: opts.executionContract,
  };

  const started = Date.now();
  let result: ToolResult;
  try {
    result = await (tool as Tool<unknown, unknown>).execute(effectiveInput, childCtx);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    result = {
      status: "error",
      errorCode: "tool_threw",
      errorMessage: msg,
      durationMs: Date.now() - started,
    };
  }

  if (!bypass && hooks) {
    void hooks.runPost(
      "afterToolUse",
      { toolName: tu.name, toolUseId: tu.id, input: effectiveInput, result },
      hookCtx,
    );
  }

  const content = summariseToolOutput(result);
  recordChildToolTranscript({
    transcript: childTranscript,
    turnId,
    toolUseId: tu.id,
    toolName: tu.name,
    input: effectiveInput,
    result,
    output: content,
  });
  return { toolUseId: tu.id, content, isError: result.status !== "ok" };
}

function commandOf(input: unknown): string {
  if (input && typeof input === "object" && "command" in input) {
    const command = (input as { command?: unknown }).command;
    return typeof command === "string" ? command : "";
  }
  if (input && typeof input === "object" && "cmd" in input) {
    const command = (input as { cmd?: unknown }).cmd;
    return typeof command === "string" ? command : "";
  }
  return "";
}

function childUserMessage(opts: SpawnChildOptions): UserMessage {
  return {
    text: opts.prompt,
    receivedAt: Date.now(),
  };
}
