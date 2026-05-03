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
import type { ChildAgentLifecycle } from "./ChildAgentHarness.js";
import type { Workspace } from "../storage/Workspace.js";
import { readOne } from "../turn/LLMStreamReader.js";
import type { PermissionMode } from "../turn/ToolDispatcher.js";
import { summariseToolOutput } from "../util/toolResult.js";
import { buildChildSystemPrompt } from "./ChildSystemPrompt.js";

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

/**
 * Deferral narrative patterns — text the child emits just before it
 * stops without actually executing the promised action. When a non-
 * tool_use stop is preceded by one of these patterns, we re-prompt the
 * child once to FORCE the follow-through instead of returning a
 * half-finished result. Mirrors the parent-side DeferralBlocker hook.
 * Pattern-match is substring (case-insensitive).
 */
const DEFERRAL_NARRATIVE_PATTERNS: readonly RegExp[] = [
  /\bnow let me\b/i,
  /\blet me now\b/i,
  /\bi(?:'| wi)ll now\b/i,
  /\bnext,? (?:i|let me)\b/i,
  /\bnext step/i,
  /\b(?:creating|writing|building|generating) (?:the|a) (?:report|file|output|analysis)/i,
  /이제 (?:작성|분석|정리|보고|리포트)/,
  /다음[은으로]/,
  /결과를? (?:작성|정리|보내)/,
  /리포트(?:를| )?(?:작성|정리)/,
];

/**
 * Exported for tests. True when `finalText` ends with a deferral narrative
 * (text promising follow-up action immediately before the loop exits).
 */
export function hasTrailingDeferralNarrative(finalText: string): boolean {
  if (!finalText) return false;
  // Only inspect the last ~200 chars — narrative is typically at the tail.
  const tail = finalText.slice(-200);
  return DEFERRAL_NARRATIVE_PATTERNS.some((re) => re.test(tail));
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
}

export type SpawnWorkspacePolicy = "trusted" | "isolated";

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
  /** Trusted by default; isolated preserves the legacy spawnDir sandbox. */
  workspacePolicy?: SpawnWorkspacePolicy;
  taskId: string;
  /** Override the LLM model for this child (e.g. "gpt-5.4", "gemini-3.1-pro-preview"). */
  modelOverride?: string;
  /** Called when child emits tool events — wired to the parent's SSE. */
  onAgentEvent?(event: unknown): void;
  /** Parent turn's askUser delegate, when the child can request consent. */
  askUser?(input: AskUserQuestionInput): Promise<AskUserQuestionOutput>;
  /** Parent session permission mode at spawn time. */
  permissionMode?: PermissionMode;
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
  return childWorkspacePolicy(opts) === "isolated" ? opts.spawnDir : opts.workspaceRoot;
}

function childSpawnWorkspace(opts: SpawnChildOptions): Workspace | undefined {
  return childWorkspacePolicy(opts) === "isolated" ? opts.spawnWorkspace : undefined;
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
): HookContext {
  return {
    botId: opts.botId,
    userId: agent.config.userId,
    sessionKey: opts.parentSessionKey,
    turnId: `${opts.parentTurnId}::spawn::${opts.taskId}`,
    contextId: childSessionKey(opts.parentSessionKey, opts.persona, 0),
    llm: agent.llm,
    transcript: [],
    emit: (event) => opts.onAgentEvent?.(event),
    log: () => {
      /* child hook logs stay local; rule_check events still emit */
    },
    agentModel,
    abortSignal,
    deadlineMs: 5_000,
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
  const toolDefs: LLMToolDef[] = childTools.map((t) => ({
    name: t.name,
    description: t.description,
    input_schema: t.inputSchema,
  }));

  const system = await buildChildSystemPrompt({
    persona: opts.persona,
    prompt: opts.prompt,
    parentTurnId: opts.parentTurnId,
    parentSpawnDepth: opts.parentSpawnDepth,
    parentWorkspaceRoot: opts.workspaceRoot,
    spawnDir: opts.spawnDir,
    workspacePolicy: childWorkspacePolicy(opts),
  });

  const messages: LLMMessage[] = [{ role: "user", content: opts.prompt }];
  const resolvedModel =
    typeof (agent as { resolveRuntimeModel?: () => Promise<string> }).resolveRuntimeModel === "function"
      ? await agent.resolveRuntimeModel()
      : agent.config.model;
  const effectiveModel = opts.modelOverride ?? resolvedModel;

  const assistantBlocks: LLMContentBlock[] = [];
  let toolCallCount = 0;
  let deferralRetryUsed = false;
  const deadline = Date.now() + opts.timeoutMs;

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
          };
        }
        await opts.lifecycle?.cancelled("parent aborted");
        return {
          status: "aborted",
          finalText: collectText(assistantBlocks),
          toolCallCount,
        };
      }
      if (Date.now() > deadline) {
        await opts.lifecycle?.failed("child timeout");
        return {
          status: "error",
          finalText: collectText(assistantBlocks),
          toolCallCount,
          errorMessage: "child timeout",
        };
      }
      await opts.lifecycle?.progress(`iteration ${iter + 1}`);

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

      if (stopReason !== "tool_use") {
        // DeferralBlocker for children: if the last assistant text ends
        // with a narrative promising further action ("Now let me write
        // the report") but the loop is stopping with no tool_use, give
        // the child ONE chance to actually execute. Otherwise the
        // parent sees finalText with unfulfilled promises and no
        // artifacts. 1 retry, fail-open. Mirrors parent DeferralBlocker.
        const finalText = collectText(assistantBlocks);
        if (!deferralRetryUsed && hasTrailingDeferralNarrative(finalText)) {
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
        if (!deferralRetryUsed && hasTrailingDeferralNarrative(finalText)) {
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
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const partialResult: SpawnChildResult = {
      status: controller.signal.aborted && abortReason === "parent" ? "aborted" : "error",
      finalText: collectText(assistantBlocks),
      toolCallCount,
      errorMessage: msg,
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
}

async function runChildTools(
  args: ChildToolRunInput,
): Promise<Array<{ toolUseId: string; content: string; isError: boolean }>> {
  const { toolUses, toolsByName, agent, opts, abortSignal, agentModel } = args;
  const hooks = getChildHookRegistry(agent);

  const runs = toolUses.map(async (tu) => {
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
    const hookCtx = makeChildHookContext(agent, opts, abortSignal, agentModel);
    if (!bypass && hooks) {
      const preTool = await hooks.runPre(
        "beforeToolUse",
        { toolName: tu.name, toolUseId: tu.id, input: tu.input },
        hookCtx,
      );
      if (preTool.action === "block") {
        return {
          toolUseId: tu.id,
          content: `blocked by hook: ${preTool.reason}`,
          isError: true,
        };
      }
      if (preTool.action === "continue") {
        effectiveInput = preTool.args.input;
      }
    }

    if (tu.name === "Bash" && childWorkspacePolicy(opts) === "isolated") {
      const boundary = classifyChildBashBoundary(effectiveInput);
      if (!boundary.safe) {
        await opts.lifecycle?.permissionDecision({
          decision: "deny",
          reason: boundary.reason,
        });
        return {
          toolUseId: tu.id,
          content: `permission_denied: ${boundary.reason ?? "child Bash boundary denied"}`,
          isError: true,
        };
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
        return {
          toolUseId: tu.id,
          content: `permission_denied: ${permission.reason}`,
          isError: true,
        };
      }
      await opts.lifecycle?.permissionDecision({
        decision: "allow",
        reason: permission.reason,
      });
      effectiveInput = permission.proposedInput ?? effectiveInput;
    } else if (permission.decision !== "allow") {
      return {
        toolUseId: tu.id,
        content: `permission_denied: ${permission.reason}`,
        isError: true,
      };
    }
    if (permission.decision === "allow") {
      effectiveInput = permission.updatedInput ?? effectiveInput;
    }

    const childCtx: ToolContext = {
      botId: opts.botId,
      sessionKey: childSessionKey(opts.parentSessionKey, opts.persona, 0),
      turnId: `${opts.parentTurnId}::spawn::${opts.taskId}`,
      workspaceRoot: childWorkspaceRoot(opts),
      spawnWorkspace: childSpawnWorkspace(opts),
      abortSignal,
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
    return { toolUseId: tu.id, content, isError: result.status !== "ok" };
  });

  return Promise.all(runs);
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
