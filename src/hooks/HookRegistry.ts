/**
 * HookRegistry — stores + runs hook handlers per lifecycle point.
 * Design reference: §7.12.
 *
 * Execution model (per §7.12):
 *   Pre-hooks (beforeXxx):
 *     - Run sequentially ordered by priority asc.
 *     - Each hook sees the args as produced by the previous hook's
 *       `replace` action; chain composes.
 *     - First `block` return aborts the phase with the given reason.
 *     - `skip` bypasses the phase only (rare — used for caching / dry runs).
 *     - Non-blocking pre-hooks run in parallel with the phase itself,
 *       outputs are ignored.
 *   Post-hooks (afterXxx / onXxx):
 *     - Fire-and-forget observers, Promise.all, outputs ignored.
 *     - Failure logs but doesn't affect the turn.
 */

import type {
  HookArgs,
  HookContext,
  HookPoint,
  RegisteredHook,
} from "./types.js";
import type {
  AskUserQuestionInput,
  AskUserQuestionOutput,
} from "../Tool.js";
import { runHookWithGuards } from "./runHookWithGuards.js";
import type { HookOutcome } from "./outcome.js";
import {
  parseRule,
  matchesRule,
  type ParsedRule,
  type RuleMatchContext,
} from "./ruleMatcher.js";

/**
 * Default timeout for the human-in-the-loop portion of a
 * `permission_decision: "ask"` hook result. Deliberately generous so
 * the user has time to read and respond; can be tuned per-hook via
 * `hook.timeoutMs` in a future follow-up.
 *
 * Mutable export for tests — production code should not rewrite this;
 * the flat-object escape hatch keeps T2-07 unit tests from needing
 * vi.useFakeTimers ceremony.
 */
export const permissionConfig = {
  askTimeoutMs: 60_000,
};
export const PERMISSION_ASK_TIMEOUT_MS = permissionConfig.askTimeoutMs;

const PERMISSION_ASK_TIMEOUT_SENTINEL = "__permission_ask_timeout__";

async function askUserWithTimeout(
  askUser: (q: AskUserQuestionInput) => Promise<AskUserQuestionOutput>,
  input: AskUserQuestionInput,
  timeoutMs: number,
): Promise<AskUserQuestionOutput> {
  let timer: NodeJS.Timeout | null = null;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(PERMISSION_ASK_TIMEOUT_SENTINEL)),
      timeoutMs,
    );
    timer.unref?.();
  });
  try {
    return await Promise.race([askUser(input), timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export interface HookStats {
  totalRuns: number;
  timeouts: number;
  errors: number;
  blocks: number;
  avgDurationMs: number;
  lastRunAt: number;
}

export interface HookInfo {
  name: string;
  point: HookPoint;
  priority: number;
  blocking: boolean;
  enabled: boolean;
  source: "builtin" | "custom" | "runtime";
  failOpen: boolean;
  timeoutMs?: number;
  stats: HookStats;
}

export type PrePhaseOutcome<Point extends HookPoint> =
  | { action: "continue"; args: HookArgs[Point] }
  | { action: "block"; reason: string }
  | { action: "skip" };

export class HookRegistry {
  private readonly hooks = new Map<HookPoint, RegisteredHook[]>();
  /**
   * Cache of parsed `if:` rules, keyed by hook identity. We parse once
   * on first dispatch, so bots with dozens of hooks don't re-parse the
   * same literal strings on every turn. Hooks registered with no `if`
   * field never land in this map; lookup is O(1).
   *
   * Sentinel `MALFORMED_LOGGED` flag lives on a side map so we can
   * rate-limit the warn-level log to "once per hook identity" without
   * mutating the hook object.
   */
  private readonly parsedIfCache = new WeakMap<RegisteredHook, ParsedRule>();
  private readonly malformedLogged = new WeakSet<RegisteredHook>();
  private readonly stats = new Map<string, HookStats>();

  register<Point extends HookPoint>(hook: RegisteredHook<Point>): void {
    const list = this.hooks.get(hook.point) ?? [];
    list.push(hook as unknown as RegisteredHook);
    list.sort((a, b) => (a.priority ?? 100) - (b.priority ?? 100));
    this.hooks.set(hook.point, list);
  }

  /**
   * Resolve (parse + cache) the `if:` rule for a hook. Returns `null`
   * when the hook has no rule declared, meaning "always run."
   */
  private getParsedIfRule(hook: RegisteredHook): ParsedRule | null {
    // Only `undefined` means "no rule declared — always run". An empty
    // or whitespace-only string is surfaced to parseRule() which marks
    // it malformed, so the caller can log + skip rather than silently
    // approving every event (a subtle way a typo could neuter a gate).
    if (hook.if === undefined) return null;
    const cached = this.parsedIfCache.get(hook);
    if (cached) return cached;
    const parsed = parseRule(hook.if);
    this.parsedIfCache.set(hook, parsed);
    return parsed;
  }

  /**
   * Check whether a hook's `if:` rule (if any) matches the current
   * event. Returns true when the rule matches, when the rule is
   * absent, or when there is no rule. On a malformed rule we log once
   * per hook and return false so the hook is skipped — a misconfigured
   * rule never accidentally makes a hook run for every event.
   */
  private hookRuleMatches(
    hook: RegisteredHook,
    baseCtx: HookContext,
    matchCtx: RuleMatchContext,
  ): boolean {
    const parsed = this.getParsedIfRule(hook);
    if (!parsed) return true;
    if (parsed.kind === "malformed") {
      if (!this.malformedLogged.has(hook)) {
        this.malformedLogged.add(hook);
        baseCtx.log("warn", `hook if: rule is malformed, skipping hook`, {
          hook: hook.name,
          rule: parsed.raw,
          reason: parsed.reason,
        });
      }
      return false;
    }
    return matchesRule(parsed, matchCtx);
  }

  /**
   * Project a HookArgs payload into the minimal RuleMatchContext the
   * matcher needs. Centralised so runPre/runPost agree on what gets
   * exposed to `if:` rules.
   */
  private buildMatchCtx<Point extends HookPoint>(
    point: Point,
    args: HookArgs[Point],
  ): RuleMatchContext {
    // Tool-use points carry `toolName` + `input` on their args.
    if (point === "beforeToolUse" || point === "afterToolUse") {
      const a = args as HookArgs["beforeToolUse"] | HookArgs["afterToolUse"];
      return { point, toolName: a.toolName, toolArgs: a.input };
    }
    return { point };
  }

  list(point?: HookPoint): RegisteredHook[] {
    if (point) return this.hooks.get(point) ?? [];
    const out: RegisteredHook[] = [];
    for (const list of this.hooks.values()) out.push(...list);
    return out;
  }

  unregister(name: string): boolean {
    let found = false;
    for (const [point, list] of this.hooks.entries()) {
      const filtered = list.filter((h) => {
        if (h.name === name) {
          // Only allow unregistering custom/runtime hooks
          if (h.source === "builtin") return true;
          found = true;
          return false;
        }
        return true;
      });
      if (filtered.length !== list.length) this.hooks.set(point, filtered);
    }
    return found;
  }

  enable(name: string): void {
    for (const list of this.hooks.values()) {
      for (const hook of list) {
        if (hook.name === name) hook.enabled = true;
      }
    }
  }

  disable(name: string): void {
    for (const list of this.hooks.values()) {
      for (const hook of list) {
        if (hook.name === name) hook.enabled = false;
      }
    }
  }

  getStats(name: string): HookStats {
    return this.stats.get(name) ?? { totalRuns: 0, timeouts: 0, errors: 0, blocks: 0, avgDurationMs: 0, lastRunAt: 0 };
  }

  listDetailed(point?: HookPoint): HookInfo[] {
    const hooks = point ? (this.hooks.get(point) ?? []) : Array.from(this.hooks.values()).flat();
    return hooks.map((h) => ({
      name: h.name,
      point: h.point,
      priority: h.priority ?? 100,
      blocking: h.blocking !== false,
      enabled: h.enabled !== false,
      source: h.source ?? "builtin",
      failOpen: h.failOpen === true,
      timeoutMs: h.timeoutMs,
      stats: this.getStats(h.name),
    }));
  }

  private recordStats(name: string, outcome: HookOutcome): void {
    const s = this.stats.get(name) ?? { totalRuns: 0, timeouts: 0, errors: 0, blocks: 0, avgDurationMs: 0, lastRunAt: 0 };
    s.totalRuns++;
    s.lastRunAt = Date.now();
    if (outcome.kind === "timeout") s.timeouts++;
    if (outcome.kind === "error") s.errors++;
    if (outcome.kind === "ok" && outcome.result && "action" in outcome.result && outcome.result.action === "block") s.blocks++;
    this.stats.set(name, s);
  }

  /**
   * Run pre-hooks for `point`. Returns either the (possibly mutated)
   * args to use for the phase, a block reason, or a skip signal.
   *
   * Non-blocking hooks are spawned in parallel and not awaited.
   */
  async runPre<Point extends HookPoint>(
    point: Point,
    args: HookArgs[Point],
    baseCtx: HookContext,
  ): Promise<PrePhaseOutcome<Point>> {
    const list = this.hooks.get(point) ?? [];
    if (list.length === 0) return { action: "continue", args };

    let current: HookArgs[Point] = args;
    const matchCtx = this.buildMatchCtx(point, current);

    for (const hook of list) {
      if (hook.enabled === false) continue;
      if (baseCtx.abortSignal.aborted) {
        return { action: "block", reason: "aborted" };
      }
      // `if:` gate — short-circuit before timer/error accounting so a
      // non-matching hook costs nothing beyond a WeakMap lookup.
      if (!this.hookRuleMatches(hook, baseCtx, matchCtx)) {
        continue;
      }
      const isBlocking = hook.blocking !== false;
      const ctx: HookContext = { ...baseCtx, deadlineMs: hook.timeoutMs ?? 5_000 };

      if (!isBlocking) {
        // fire-and-forget — don't await. Still guarded so a non-blocking
        // handler error / timeout only logs (pre-R6 semantics preserved).
        void runHookWithGuards(hook as unknown as RegisteredHook<Point>, current, ctx).then(
          (outcome) => {
            if (outcome.kind === "ok") return;
            baseCtx.log("warn", `non-blocking hook failed: ${hook.name}`, {
              point,
              error: outcomeErrorString(outcome),
            });
          },
        );
        continue;
      }

      const outcome = await runHookWithGuards(
        hook as unknown as RegisteredHook<Point>,
        current,
        ctx,
      );
      this.recordStats(hook.name, outcome);
      if (outcome.kind === "error" || outcome.kind === "timeout") {
        const errString = outcomeErrorString(outcome);
        if (hook.failOpen) {
          baseCtx.log("warn", `blocking hook ${outcome.kind} (fail-open): ${hook.name}`, {
            point,
            error: errString,
          });
          continue;
        }
        baseCtx.log("error", `blocking hook threw: ${hook.name}`, {
          point,
          error: errString,
        });
        return {
          action: "block",
          reason: `hook:${hook.name} threw: ${errString}`,
        };
      }
      if (outcome.kind === "skipped") continue;
      const result = outcome.result;

      if (!result || result.action === "continue") continue;
      if (result.action === "block") {
        baseCtx.log("warn", `hook blocked phase: ${hook.name}`, { point, reason: result.reason });
        return { action: "block", reason: result.reason };
      }
      if (result.action === "skip") {
        baseCtx.log("info", `hook skipped phase: ${hook.name}`, { point });
        return { action: "skip" };
      }
      if (result.action === "replace") {
        current = result.value;
        // If a pre-hook rewrote the args, downstream `if:` checks
        // should see the new values (e.g. if a router rewrites
        // toolName or input). Rebuilding is cheap.
        Object.assign(matchCtx, this.buildMatchCtx(point, current));
        continue;
      }
      if (result.action === "permission_decision") {
        const permOutcome = await this.resolvePermissionDecision(
          hook,
          result,
          point,
          baseCtx,
        );
        if (permOutcome === "continue") continue;
        return permOutcome;
      }
    }

    return { action: "continue", args: current };
  }

  /**
   * Translate a `permission_decision` hook result into a concrete
   * PrePhaseOutcome. `approve` = continue; `deny` = block with
   * `[PERMISSION:DENY]`; `ask` = synchronously prompt the user via
   * `baseCtx.askUser` (60s default), mapping to continue / block with
   * `[PERMISSION:USER_DENIED]` / block with `[PERMISSION:TIMEOUT]`.
   *
   * If `ask` is returned but no `askUser` delegate is wired into the
   * context, we fail closed (block with `[PERMISSION:NO_DELEGATE]`) so
   * that a misconfigured phase never silently approves a sensitive
   * tool call.
   */
  private async resolvePermissionDecision(
    hook: RegisteredHook,
    result: {
      action: "permission_decision";
      decision: "approve" | "deny" | "ask";
      reason?: string;
    },
    point: HookPoint,
    baseCtx: HookContext,
  ): Promise<"continue" | { action: "block"; reason: string }> {
    const hookName = hook.name;
    if (result.decision === "approve") {
      baseCtx.log("info", `hook permission approve: ${hookName}`, {
        point,
        reason: result.reason,
      });
      return "continue";
    }
    if (result.decision === "deny") {
      const reason = `[PERMISSION:DENY] ${result.reason ?? `hook:${hookName}`}`;
      baseCtx.log("warn", `hook permission deny: ${hookName}`, { point, reason });
      return { action: "block", reason };
    }
    // "ask"
    const askUser = baseCtx.askUser;
    if (!askUser) {
      const reason = `[PERMISSION:NO_DELEGATE] hook:${hookName} requested ask but no askUser available on phase=${point}`;
      baseCtx.log("error", reason, { point });
      return { action: "block", reason };
    }
    const timeoutMs = permissionConfig.askTimeoutMs;
    const question = result.reason ?? `Allow action requested by ${hookName}?`;
    try {
      const answer = await askUserWithTimeout(
        askUser,
        {
          question,
          choices: [
            { id: "approve", label: "Approve" },
            { id: "deny", label: "Deny" },
          ],
        },
        timeoutMs,
      );
      const approved = answer.selectedId === "approve";
      if (approved) {
        baseCtx.log("info", `hook permission user_approve: ${hookName}`, {
          point,
          answer: answer.selectedId,
        });
        baseCtx.emit({
          type: "rule_check",
          ruleId: "permission-decision",
          verdict: "ok",
          detail: `hook=${hookName} decision=user_approved`,
        });
        return "continue";
      }
      const reason = `[PERMISSION:USER_DENIED] hook:${hookName}`;
      baseCtx.log("warn", `hook permission user_deny: ${hookName}`, {
        point,
        answer: answer.selectedId,
      });
      baseCtx.emit({
        type: "rule_check",
        ruleId: "permission-decision",
        verdict: "violation",
        detail: `hook=${hookName} decision=user_denied`,
      });
      return { action: "block", reason };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg === PERMISSION_ASK_TIMEOUT_SENTINEL) {
        const reason = `[PERMISSION:TIMEOUT] hook:${hookName} askUser exceeded ${timeoutMs}ms`;
        baseCtx.log("warn", reason, { point });
        return { action: "block", reason };
      }
      const reason = `[PERMISSION:ASK_FAILED] hook:${hookName}: ${msg}`;
      baseCtx.log("error", reason, { point });
      return { action: "block", reason };
    }
  }

  /**
   * Run post-hooks for `point`. All handlers fire concurrently;
   * failures are logged but never bubble.
   */
  async runPost<Point extends HookPoint>(
    point: Point,
    args: HookArgs[Point],
    baseCtx: HookContext,
  ): Promise<void> {
    const list = this.hooks.get(point) ?? [];
    if (list.length === 0) return;

    const matchCtx = this.buildMatchCtx(point, args);
    const applicable = list.filter((hook) =>
      hook.enabled !== false && this.hookRuleMatches(hook, baseCtx, matchCtx),
    );
    if (applicable.length === 0) return;

    await Promise.allSettled(
      applicable.map(async (hook) => {
        const ctx: HookContext = { ...baseCtx, deadlineMs: hook.timeoutMs ?? 5_000 };
        const outcome = await runHookWithGuards(
          hook as unknown as RegisteredHook<Point>,
          args,
          ctx,
        );
        if (outcome.kind === "error" || outcome.kind === "timeout") {
          baseCtx.log("warn", `post-hook failed: ${hook.name}`, {
            point,
            error: outcomeErrorString(outcome),
          });
        }
      }),
    );
  }
}

/**
 * Format a non-ok HookOutcome into the `error: <string>` payload that
 * pre-R6 registry logs used. Timeout outcomes deliberately mirror the
 * old `Error("hook timeout: <name> (<ms>ms)")` shape so log consumers
 * (and the `block` reason returned to callers) see no change.
 */
function outcomeErrorString(outcome: HookOutcome): string {
  if (outcome.kind === "timeout") {
    return `Error: hook timeout: ${outcome.hookName} (${outcome.ms}ms)`;
  }
  if (outcome.kind === "error") {
    return String(outcome.error);
  }
  // "skipped" / "ok" shouldn't reach here, but be defensive rather than
  // throw — registry logging is non-critical.
  return outcome.kind;
}
