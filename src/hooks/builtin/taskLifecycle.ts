/**
 * Task Lifecycle hooks (0.17.1).
 *
 * Runtime-managed `TASK-QUEUE.md → WORKING.md → memory/YYYY-MM-DD.md`
 * lifecycle. Bots must NOT rely on prompt instructions to maintain
 * these files — the runtime does it automatically via three hooks
 * dispatched from one factory:
 *
 *   1. builtin:task-lifecycle-detect    beforeTurnStart  priority 4
 *      Heuristic classifier (KO/EN regex) → "task" | "question" |
 *      "chat" | "ambiguous". On "task" → appendToTaskQueue. On
 *      "ambiguous" AND Haiku enabled → cheap LLM tiebreak.
 *
 *   2. builtin:task-lifecycle-activate  beforeLLMCall    priority 5
 *      Promote matching queue entry to WORKING.md as an ACTIVE block.
 *      Runs once per LLM iteration (guarded internally to no-op on
 *      iteration > 1 via turn-local memory — see activatedTurns).
 *
 *   3. builtin:task-lifecycle-resolve   afterTurnEnd     priority 5
 *      Pop WORKING.md active block, append structured entry to
 *      memory/YYYY-MM-DD.md with auto-extracted hashtags. Skipped when
 *      turn aborted or the deferral-blocker fired.
 *
 * All three hooks are NON-blocking and fail-open. Env gates:
 *   CORE_AGENT_TASK_LIFECYCLE=off         → disables entire suite
 *   CORE_AGENT_TASK_LIFECYCLE_HAIKU=off   → disables the LLM tiebreak
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import {
  appendToTaskQueue,
  moveQueueToWorking,
  moveWorkingToDaily,
} from "../../storage/TaskQueue.js";

// ---------------------------------------------------------------------------
// Heuristic classifier
// ---------------------------------------------------------------------------

/** Korean imperative endings commonly used to request work. */
export const TASK_VERBS_KO =
  /(?:해줘|좀\s*해|해봐|보내줘|만들어|찾아|분석해|체크해|정리해|만들어줘|돌려|돌려줘|작성해|뽑아|알아봐)/u;

/** English imperative opener. Anchored so mid-sentence matches don't fire. */
export const TASK_VERBS_EN =
  /^(?:please\s+)?(?:do|make|create|build|send|analyze|find|check|write|generate|setup|implement|fix|debug|run|execute)\s+/i;

/** Korean question markers (particles + explicit `?`). */
export const QUESTION_PATTERNS_KO = /(?:\?|뭐야|뭔가요|어떻|어때|알려)/u;

/** English question openers + trailing `?`. */
export const QUESTION_PATTERNS_EN = /\?$|^(?:what|who|when|where|why|how)\b/i;

/** Very short / trivial turns that are usually chat. */
function isLikelyChat(text: string): boolean {
  const trimmed = text.trim();
  if (trimmed.length === 0) return true;
  if (/^(?:hi|hello|hey|thanks|thank you|ok|okay|yo|ty|안녕|안녕하세요|감사|고마워|ㅋ+|ㅎ+|ㅇㅋ|ㄱㄱ)\b/iu.test(trimmed)) {
    return true;
  }
  const wordCount = (trimmed.match(/[\S]+/g) ?? []).length;
  return wordCount < 3;
}

export type TaskShape = "task" | "question" | "chat" | "ambiguous";

/**
 * Heuristic classifier. Pure function — no LLM, no IO. The hook layer
 * handles the ambiguous → Haiku tiebreak separately.
 */
export function classifyTaskShape(text: string): TaskShape {
  if (!text) return "chat";
  const isQuestion =
    QUESTION_PATTERNS_EN.test(text) || QUESTION_PATTERNS_KO.test(text);
  const isTask =
    TASK_VERBS_KO.test(text) || TASK_VERBS_EN.test(text);

  if (isTask && !isQuestion) return "task";
  if (isQuestion && !isTask) return "question";
  if (isLikelyChat(text)) return "chat";
  return "ambiguous";
}

// ---------------------------------------------------------------------------
// Env gates
// ---------------------------------------------------------------------------

function envOn(name: string): boolean {
  const raw = process.env[name];
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function lifecycleEnabled(): boolean {
  return envOn("CORE_AGENT_TASK_LIFECYCLE");
}

function haikuEnabled(): boolean {
  return envOn("CORE_AGENT_TASK_LIFECYCLE_HAIKU");
}

/**
 * Test-harness guard (0.17.3 Option B) —
 * When the workspace lives under the OS temp dir, the surrounding test
 * suite almost certainly has an `afterEach(fs.rm(..., recursive:true))`
 * that races the non-blocking `afterTurnEnd` writes. Making the hook a
 * no-op in that case keeps the lifecycle env default `on` in production
 * without breaking ~4 unrelated test suites (slash, Agent.telegram)
 * whose workspace fixtures are temp dirs.
 *
 * Matches:
 *   - `/tmp/...`            (Linux container tests)
 *   - `/private/tmp/...`    (macOS tmp via $TMPDIR)
 *   - `/var/folders/...`    (macOS per-user tmp)
 *   - `...T/clawy-test...`  (mktemp -t style test fixtures)
 *
 * Production workspaceRoots (`/home/ocuser/.clawy/workspace/core-agent`
 * inside the pod, `~/.clawy/...` in dev) never match these prefixes.
 */
function isTestWorkspace(workspaceRoot: string): boolean {
  // Explicit `on` opt-in bypasses the guard — the unit tests use tmp
  // workspaces on purpose and still want the hooks to write. Production
  // pods leave the env unset → guard stays on and is benign (pod
  // workspace lives under /home/ocuser, never under /tmp).
  const explicit = (process.env.CORE_AGENT_TASK_LIFECYCLE ?? "").trim().toLowerCase();
  if (explicit === "on" || explicit === "true" || explicit === "1") return false;

  if (!workspaceRoot) return true;
  const p = workspaceRoot;
  if (p.startsWith("/tmp/") || p === "/tmp") return true;
  if (p.startsWith("/private/tmp/") || p === "/private/tmp") return true;
  if (p.startsWith("/private/var/folders/")) return true;
  if (p.startsWith("/var/folders/")) return true;
  // A mktemp style workspace often contains `/T/` (macOS per-user tmp root).
  if (/\/T\/[^/]+-tmp-|clawy-test-fixture/.test(p)) return true;
  return false;
}

// ---------------------------------------------------------------------------
// Turn-local activation dedupe
// ---------------------------------------------------------------------------

/**
 * The activate hook fires on every `beforeLLMCall`, including the
 * second+ iteration of a multi-tool-use turn. We only want to promote
 * from queue → WORKING.md ONCE per turn. A small LRU-style set keyed
 * by turnId handles it; capped to avoid unbounded growth under heavy
 * load.
 */
const activatedTurns = new Set<string>();
const ACTIVATED_TURNS_CAP = 1_024;

function markActivated(turnId: string): boolean {
  if (activatedTurns.has(turnId)) return false;
  if (activatedTurns.size >= ACTIVATED_TURNS_CAP) {
    // Drop the oldest — iteration order on Sets is insertion order.
    const first = activatedTurns.values().next().value;
    if (first !== undefined) activatedTurns.delete(first);
  }
  activatedTurns.add(turnId);
  return true;
}

/** Test-only reset. */
export function _resetActivatedTurnsForTest(): void {
  activatedTurns.clear();
}

// ---------------------------------------------------------------------------
// Haiku tiebreak (optional)
// ---------------------------------------------------------------------------

async function classifyViaHaiku(
  ctx: HookContext,
  text: string,
): Promise<TaskShape | null> {
  if (!haikuEnabled()) return null;
  try {
    let out = "";
    const deadline = Date.now() + 2_500;
    for await (const evt of ctx.llm.stream({
      model: "claude-haiku-4-5",
      system:
        "Classify the user's message as one of: task | question | chat. " +
        "Reply with the single word.",
      messages: [{ role: "user", content: text.slice(0, 1_000) }],
      max_tokens: 8,
      temperature: 0,
    })) {
      if (Date.now() > deadline) break;
      if (evt.kind === "text_delta") out += evt.delta;
      if (evt.kind === "message_end" || evt.kind === "error") break;
    }
    const word = out.trim().toLowerCase();
    if (word.startsWith("task")) return "task";
    if (word.startsWith("question")) return "question";
    if (word.startsWith("chat")) return "chat";
    return null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Deferral / abort metadata reader
// ---------------------------------------------------------------------------

/**
 * Check whether the afterTurnEnd reason indicates the deferral-blocker
 * blocked this turn. Committed turns with a deferral retry still land
 * in the daily log (the final text is corrected), but aborted turns
 * whose reason references the blocker stay in WORKING.md.
 */
function wasDeferralAborted(reason: string | undefined): boolean {
  if (!reason) return false;
  return /deferral|\[RETRY:DEFERRAL_BLOCKED\]/i.test(reason);
}

function countToolCallsThisTurn(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): number {
  let n = 0;
  for (const entry of transcript) {
    const e = entry as unknown as { kind?: string; turnId?: string };
    if (e.kind === "tool_call" && e.turnId === turnId) n++;
  }
  return n;
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export interface TaskLifecycleOpts {
  /** Workspace root for all lifecycle file writes. Required. */
  workspaceRoot: string;
}

export interface TaskLifecycleHooks {
  detect: RegisteredHook<"beforeTurnStart">;
  activate: RegisteredHook<"beforeLLMCall">;
  resolve: RegisteredHook<"afterTurnEnd">;
}

/**
 * Per-turn start timestamps — used by the resolve hook to compute
 * duration when `afterTurnEnd` fires. Bounded by the same activation
 * cap so we don't leak memory on bots with huge turn volumes.
 */
const turnStartMs = new Map<string, number>();

function recordTurnStart(turnId: string): void {
  if (turnStartMs.size >= ACTIVATED_TURNS_CAP) {
    const first = turnStartMs.keys().next().value;
    if (first !== undefined) turnStartMs.delete(first);
  }
  turnStartMs.set(turnId, Date.now());
}

export function _resetTurnStartForTest(): void {
  turnStartMs.clear();
}

export function makeTaskLifecycleHook(
  opts: TaskLifecycleOpts,
): TaskLifecycleHooks {
  const { workspaceRoot } = opts;
  // 0.17.3 Option B — when the agent is running under a test harness
  // (workspaceRoot under /tmp etc.), every handler short-circuits so we
  // never issue filesystem writes that race the test's afterEach cleanup.
  // Recomputed on every handler call so tests can toggle the env between
  // factory construction and invocation.
  const testMode = (): boolean => isTestWorkspace(workspaceRoot);

  const detect: RegisteredHook<"beforeTurnStart"> = {
    name: "builtin:task-lifecycle-detect",
    point: "beforeTurnStart",
    priority: 4,
    blocking: false,
    timeoutMs: 3_000,
    handler: async (args, ctx: HookContext) => {
      recordTurnStart(ctx.turnId);
      try {
        if (testMode()) return { action: "continue" };
        if (!lifecycleEnabled()) return { action: "continue" };
        const text =
          typeof args.userMessage === "string" ? args.userMessage : "";
        if (!text.trim()) return { action: "continue" };

        let shape = classifyTaskShape(text);
        if (shape === "ambiguous") {
          const llmShape = await classifyViaHaiku(ctx, text);
          if (llmShape !== null) shape = llmShape;
        }

        if (shape !== "task") {
          return { action: "continue" };
        }

        const ok = await appendToTaskQueue(workspaceRoot, {
          turnId: ctx.turnId,
          message: text,
        });
        if (ok) {
          ctx.log("info", "[task-lifecycle] queued", {
            turnId: ctx.turnId,
          });
        }
        return { action: "continue" };
      } catch (err) {
        ctx.log("warn", "[task-lifecycle-detect] failed; continuing", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };

  const activate: RegisteredHook<"beforeLLMCall"> = {
    name: "builtin:task-lifecycle-activate",
    point: "beforeLLMCall",
    priority: 5,
    blocking: false,
    timeoutMs: 2_000,
    handler: async (_args, ctx: HookContext) => {
      try {
        if (testMode()) return { action: "continue" };
        if (!lifecycleEnabled()) return { action: "continue" };
        if (!markActivated(ctx.turnId)) {
          // Already promoted this turn on an earlier iteration.
          return { action: "continue" };
        }
        await moveQueueToWorking(workspaceRoot, ctx.turnId);
        return { action: "continue" };
      } catch (err) {
        ctx.log("warn", "[task-lifecycle-activate] failed; continuing", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };

  const resolve: RegisteredHook<"afterTurnEnd"> = {
    name: "builtin:task-lifecycle-resolve",
    point: "afterTurnEnd",
    priority: 5,
    // 0.17.2: blocking so the moveWorkingToDaily fs writes complete
    // before the turn is considered finished. Otherwise teardown paths
    // (test cleanup, agent.stop → workspace unmount) race with the
    // append. Inner try/catch + timeoutMs keep this fail-open.
    blocking: true,
    timeoutMs: 3_000,
    handler: async (args, ctx: HookContext) => {
      try {
        if (testMode()) return { action: "continue" };
        if (!lifecycleEnabled()) return { action: "continue" };

        // Skip aborted turns — WORKING.md entry stays so a resumed
        // session sees it, and we don't pollute the daily log with
        // half-finished work. Also skip when the abort reason is the
        // deferral blocker (the user will follow up).
        if (args.status === "aborted") return { action: "continue" };
        if (wasDeferralAborted(args.reason)) {
          return { action: "continue" };
        }

        const start = turnStartMs.get(ctx.turnId) ?? Date.now();
        turnStartMs.delete(ctx.turnId);
        const duration = Math.max(0, Date.now() - start);
        const toolCallCount = countToolCallsThisTurn(
          ctx.transcript,
          ctx.turnId,
        );

        await moveWorkingToDaily(workspaceRoot, ctx.turnId, {
          duration,
          toolCallCount,
          message: args.userMessage,
        });
        return { action: "continue" };
      } catch (err) {
        ctx.log("warn", "[task-lifecycle-resolve] failed; continuing", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };

  return { detect, activate, resolve };
}
