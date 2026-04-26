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
 * is tightly coupled to Session (transcript append, hook registry,
 * permission modes, audit log). Children are ephemeral (§7.12.d): no
 * session, no transcript, no hooks. The child-tool runner here is a
 * simpler direct-execute loop that still honours:
 *   • allowedTools intersection (filtered at child-tool selection time)
 *   • MAX_SPAWN_DEPTH (parent enforces at SpawnAgent.execute entry)
 *   • PRE-01 workspace isolation (ctx.workspaceRoot = spawnDir)
 *   • timeout + parent abort propagation via a merged AbortController
 *
 * Invariants preserved 1:1 from the inline implementation:
 *   • CHILD_MAX_ITERATIONS=25 loop cap (`errorMessage: child exceeded N iterations`)
 *   • deadline → `{ status:"error", errorMessage:"child timeout" }`
 *   • parent abort → `{ status:"aborted" }`
 *   • `askUser` throws inside the child
 *   • child staging hooks are all no-ops (no transcript / audit / staging)
 */

import type { Agent } from "../Agent.js";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type {
  LLMContentBlock,
  LLMMessage,
  LLMToolDef,
} from "../transport/LLMClient.js";
import { StubSseWriter } from "../transport/SseWriter.js";
import type { Workspace } from "../storage/Workspace.js";
import { readOne } from "../turn/LLMStreamReader.js";
import { summariseToolOutput } from "../util/toolResult.js";

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

export interface SpawnChildResult {
  status: "ok" | "error" | "aborted";
  finalText: string;
  toolCallCount: number;
  errorMessage?: string;
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
  /**
   * Parent's workspace root. Retained on the options for audit / debug
   * context but NOT used as the child's tool context (PRE-01 fix).
   * Child tool contexts receive `spawnDir` as their workspaceRoot.
   */
  workspaceRoot: string;
  /**
   * Ephemeral subdirectory for the child — `workspace/.spawn/{taskId}/`.
   * Child tool contexts are scoped here (PRE-01).
   */
  spawnDir: string;
  /** Workspace instance rooted at `spawnDir`. */
  spawnWorkspace: Workspace;
  taskId: string;
  /** Called when child emits tool events — wired to the parent's SSE. */
  onAgentEvent?(event: unknown): void;
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

  const system = [
    `[Persona: ${opts.persona}]`,
    `[Spawn: parent=${opts.parentTurnId} depth=${opts.parentSpawnDepth + 1}]`,
    "",
    `You are a focused sub-agent spawned by the main agent. Your task is a single discrete unit of work. Complete it and return your result succinctly.`,
  ].join("\n");

  const messages: LLMMessage[] = [{ role: "user", content: opts.prompt }];

  const assistantBlocks: LLMContentBlock[] = [];
  let toolCallCount = 0;
  let deferralRetryUsed = false;
  const deadline = Date.now() + opts.timeoutMs;

  // Merge timeout + parent abort into one signal for nested tools.
  const controller = new AbortController();
  const onParentAbort = (): void => controller.abort();
  opts.abortSignal.addEventListener("abort", onParentAbort, { once: true });
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs);

  // Stub SSE writer so LLMStreamReader's sse.agent / sse.legacyDelta
  // calls are silent — children only surface structured events
  // (spawn_started / spawn_result / tool_start / tool_end emitted by
  // the tools themselves via ctx.emitAgentEvent).
  const sse = new StubSseWriter();

  try {
    for (let iter = 0; iter < CHILD_MAX_ITERATIONS; iter++) {
      if (controller.signal.aborted) {
        return {
          status: "aborted",
          finalText: collectText(assistantBlocks),
          toolCallCount,
        };
      }
      if (Date.now() > deadline) {
        return {
          status: "error",
          finalText: collectText(assistantBlocks),
          toolCallCount,
          errorMessage: "child timeout",
        };
      }

      const { blocks, stopReason } = await readOne(
        {
          llm: agent.llm,
          model: agent.config.model,
          sse,
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
        return {
          status: "ok",
          finalText,
          toolCallCount,
        };
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
        return {
          status: "ok",
          finalText,
          toolCallCount,
        };
      }

      toolCallCount += toolUses.length;
      const results = await runChildTools({
        toolUses,
        toolsByName,
        opts,
        abortSignal: controller.signal,
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

    return {
      status: "error",
      finalText: collectText(assistantBlocks),
      toolCallCount,
      errorMessage: `child exceeded ${CHILD_MAX_ITERATIONS} iterations`,
    };
  } finally {
    clearTimeout(timer);
    opts.abortSignal.removeEventListener("abort", onParentAbort);
  }
}

interface ChildToolRunInput {
  toolUses: Array<Extract<LLMContentBlock, { type: "tool_use" }>>;
  toolsByName: Map<string, Tool>;
  opts: SpawnChildOptions;
  abortSignal: AbortSignal;
}

async function runChildTools(
  args: ChildToolRunInput,
): Promise<Array<{ toolUseId: string; content: string; isError: boolean }>> {
  const { toolUses, toolsByName, opts, abortSignal } = args;

  const runs = toolUses.map(async (tu) => {
    const tool = toolsByName.get(tu.name);
    if (!tool) {
      return {
        toolUseId: tu.id,
        content: `error:unknown_tool ${tu.name} not in child toolset`,
        isError: true,
      };
    }

    const childCtx: ToolContext = {
      botId: opts.botId,
      sessionKey: childSessionKey(opts.parentSessionKey, opts.persona, 0),
      turnId: `${opts.parentTurnId}::spawn::${opts.taskId}`,
      // PRE-01 (audit 02): child's workspaceRoot is the ephemeral spawnDir,
      // NOT the parent's PVC root. Combined with `allowed_tools`, this
      // makes the security boundary real.
      workspaceRoot: opts.spawnDir,
      spawnWorkspace: opts.spawnWorkspace,
      abortSignal,
      emitProgress: () => {
        /* child progress folded into spawn_result — no per-step SSE */
      },
      emitAgentEvent: opts.onAgentEvent,
      askUser: async () => {
        throw new Error("askUser not available in a spawned child");
      },
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
      result = await (tool as Tool<unknown, unknown>).execute(tu.input, childCtx);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      result = {
        status: "error",
        errorCode: "tool_threw",
        errorMessage: msg,
        durationMs: Date.now() - started,
      };
    }

    const content = summariseToolOutput(result);
    return { toolUseId: tu.id, content, isError: result.status !== "ok" };
  });

  return Promise.all(runs);
}
