/**
 * runHookWithGuards — execute a single registered hook under a
 * timeout + error guard, returning a {@link HookOutcome} discriminated
 * union. Callers (HookRegistry.runPre / runPost, built-in harnesses,
 * tests) inspect the outcome kind and decide policy: fail-open hooks
 * treat timeout / error as `continue`, fail-closed hooks treat them
 * as `block`.
 *
 * Extracted in R6 from the copy-pasted Promise.race + try/catch + log
 * blocks that both runPre and runPost previously carried. This file
 * intentionally has no dependency on registry internals beyond the
 * hook + context + result types — it is safe to unit-test in
 * isolation and can later be reused by any bespoke hook runner.
 */
import type { HookOutcome } from "./outcome.js";
import type {
  HookArgs,
  HookContext,
  HookHandler,
  HookPoint,
  HookResult,
  RegisteredHook,
} from "./types.js";

/**
 * Fallback per-hook timeout when neither `hook.timeoutMs` nor
 * `ctx.deadlineMs` is supplied. Matches the hardcoded 5000 that the
 * pre-R6 `runWithTimeout` used, preserved as a named constant so
 * tests and future tuning have a single source of truth.
 */
export const DEFAULT_HOOK_TIMEOUT_MS = 5_000;

/**
 * Sentinel thrown by the timeout leg of `Promise.race` so the caller
 * can distinguish a timeout from an unrelated handler error without
 * relying on message regex. Never leaks out of `runHookWithGuards`.
 */
const HOOK_TIMEOUT_SENTINEL = Symbol("magi.hook.timeout");

interface HookGuardOptions<Point extends HookPoint> {
  /**
   * Optional per-invocation applicable predicate. When supplied and
   * returning false, the hook is not executed and the outcome is
   * `{ kind: "skipped" }` with the provided reason (or
   * `"not-applicable"` if none given). Runs synchronously before
   * any timer is armed.
   */
  applicable?: (args: HookArgs[Point], ctx: HookContext) => boolean;
  /** Reason string to attach when `applicable` returns false. */
  skipReason?: string;
}

function resolveTimeoutMs<Point extends HookPoint>(
  hook: RegisteredHook<Point>,
  ctx: HookContext,
): number {
  return hook.timeoutMs ?? ctx.deadlineMs ?? DEFAULT_HOOK_TIMEOUT_MS;
}

/**
 * Run a hook handler under the standard guard envelope:
 *
 *   1. Short-circuit with `skipped` if the optional `applicable`
 *      predicate returns false.
 *   2. Race the handler against a `setTimeout(timeoutMs)` — the
 *      timeout leg rejects with {@link HOOK_TIMEOUT_SENTINEL}.
 *   3. On successful settle: `{ kind: "ok", result }`.
 *   4. On timeout: `{ kind: "timeout", hookName, ms }`.
 *   5. On any other thrown/rejected value: `{ kind: "error",
 *      hookName, error }` — never re-thrown.
 *
 * No audit / log emission happens here — the caller owns that policy
 * so log levels stay identical to the pre-R6 behavior (pre-hooks log
 * "error", post-hooks log "warn", non-blocking pre-hooks log "warn").
 */
export async function runHookWithGuards<Point extends HookPoint>(
  hook: RegisteredHook<Point>,
  args: HookArgs[Point],
  ctx: HookContext,
  opts: HookGuardOptions<Point> = {},
): Promise<HookOutcome<Point>> {
  if (opts.applicable && !opts.applicable(args, ctx)) {
    return {
      kind: "skipped",
      hookName: hook.name,
      reason: opts.skipReason ?? "not-applicable",
    };
  }

  const timeoutMs = resolveTimeoutMs(hook, ctx);
  let timer: NodeJS.Timeout | null = null;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(HOOK_TIMEOUT_SENTINEL), timeoutMs);
    timer.unref?.();
  });

  try {
    const handler = hook.handler as HookHandler<Point>;
    const result = (await Promise.race([handler(args, ctx), timeout])) as
      | HookResult<HookArgs[Point]>
      | void;
    return { kind: "ok", result };
  } catch (err) {
    if (err === HOOK_TIMEOUT_SENTINEL) {
      return { kind: "timeout", hookName: hook.name, ms: timeoutMs };
    }
    return { kind: "error", hookName: hook.name, error: err };
  } finally {
    if (timer) clearTimeout(timer);
  }
}
